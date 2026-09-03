"""law_lookup — 정관↔법령 양방향 조회 (company-agnostic·DART 0콜).

두 방향:
  A) 정관/자유텍스트 → 관련 법령 조문 (clause_to_law)
  B) 법령 조문번호/키워드 → 조문 전문 + 관련 정관 변경유형·우회·안건 (law_to_clause)
카디널리티 1:1 / N:1 / 1:N / N:N 전부 — τ_emit 이상 후보 전부 랭킹(first-match 아님).

데이터: legalize-kr 원문(상법·자본시장법·공정거래법·외부감사법 + 260902 확장분
지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법, 각 법률+시행령)을
`wiki/rules/laws/corpus/`에 vendored + `scripts/sync_law_corpus.py`가 만든 `law_index.json`.
매칭 3신호: E(정확 조문 튜플) + B(40룰 bridge, `_agenda_pattern_match` 재사용) + C(corpus 전문
형태소 BM25). 보수적: substring 금지(튜플 exact), false-friend guard(E/B), difflib 없음.

C(corpus)는 260714 이전 '폐쇄 어휘 133개 overlap'이었으나 자유질의 recall@10 24%(자연어 패러프레이즈가
어휘 밖이라 토큰이 ∅) → **kiwipiepy 형태소 + 조문 전문 BM25**로 교체해 146개 held-out 실측 recall@10 80%.
게이트는 형태소 anchor(≥2 형태소 또는 희소 형태소 1개)로 두루뭉술 질의만 차단. 근거·회귀게이트:
[[law-recall-harness-260714]] · `scripts/law_recall_harness.py`. BM25 인덱스는 `law_bm25.json`(sync가 생성).

이 모듈의 토큰화 primitive(normalize/extract_tokens/morph_tokens/load_synonyms)는 sync 스크립트가
import해 인덱스 빌드와 질의 정규화를 **동일 로직**으로 맞춘다.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import time
from collections import Counter
from datetime import date, datetime, timezone
import logging
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable

from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_usage,
)
from open_proxy_mcp.services.proxy_advise import (
    _agenda_pattern_match,
    _load_law_layer_rules,
    _load_law_provisions,
)

logger = logging.getLogger(__name__)

# ── 경로 ────────────────────────────────────────────────────────────────
# 260814: 두 부류를 다르게 다룬다.
#   · 작은 규칙 데이터(사전 13KB)  → **패키지 데이터**. 코드와 함께 배포되고 cwd 에 무관하다.
#   · corpus(11MB 원문·인덱스)     → repo 경로 유지. 휠에 11MB 를 싣지 않는다.
#     대신 없으면 **소리를 낸다**(아래 로더) — 종전에는 조용히 빈 결과였다.
_LAWS_DIR = Path(__file__).resolve().parent.parent.parent / "wiki" / "rules" / "laws"
_CORPUS_DIR = _LAWS_DIR / "corpus"
_INDEX_PATH = _CORPUS_DIR / "law_index.json"
_BM25_PATH = _CORPUS_DIR / "law_bm25.json"


def _synonyms_path():
    """법령 동의어 사전 — 패키지 데이터."""
    return files("open_proxy_mcp.data.laws") / "law_lookup_synonyms.json"

# ── 원형숫자 → 항 int (①-⑳ 연속 U+2460-2473, ㉑-㉟ U+3251-325F, ㊱-㊿ U+32B1-32BF) ──
_CIRCLED: dict[str, int] = {}
for _i in range(20):
    _CIRCLED[chr(0x2460 + _i)] = _i + 1          # ① .. ⑳
for _i in range(15):
    _CIRCLED[chr(0x3251 + _i)] = _i + 21         # ㉑ .. ㉟
for _i in range(15):
    _CIRCLED[chr(0x32B1 + _i)] = _i + 36         # ㊱ .. ㊿
CIRCLED_TO_INT = _CIRCLED

_MASK = ""  # guard masking placeholder (PUA, not in vocabulary)

# ── 법령명 별칭 → law_short ─────────────────────────────────────────────
# law_short는 부모 법 4개(상법/자본시장법/공정거래법/외부감사법). 시행령은 law_key로만 구분하고
# law_short는 부모와 동일 → law= 필터가 법률+시행령을 함께 포함(사용자 "상법 관련" 의도).
LAW_ALIASES: list[tuple[str, str]] = [
    ("자본시장과금융투자업에관한법률", "자본시장법"), ("자본시장과 금융투자업", "자본시장법"),
    ("자본시장법", "자본시장법"), ("자통법", "자본시장법"),
    ("독점규제및공정거래에관한법률", "공정거래법"), ("독점규제", "공정거래법"),
    ("공정거래법", "공정거래법"), ("공정거래", "공정거래법"),
    ("주식회사등의외부감사에관한법률", "외부감사법"), ("주식회사 등의 외부감사", "외부감사법"),
    ("외부감사법", "외부감사법"), ("외감법", "외부감사법"),
    # ── 260902 확장 6법. 긴 정식 명칭이 먼저, 약칭이 뒤 ──
    #   260903 실측: 코퍼스는 10법인데 이 표와 _KNOWN_LAWS 는 4법인 채라 `law="보험업법"` 이
    #   「인식되지 않는 법령」으로 거절되고 상법 §106 이 돌아왔다. 도구 설명은 10법을 필터로
    #   받는다고 적혀 있었다 — 말과 코드가 갈린 자리.
    ("금융회사의지배구조에관한법률", "지배구조법"), ("금융회사 지배구조법", "지배구조법"),
    ("금융회사지배구조법", "지배구조법"), ("지배구조법", "지배구조법"),
    ("상속세및증여세법", "상증세법"), ("상속세 및 증여세법", "상증세법"), ("상증세법", "상증세법"),
    ("금융지주회사법", "금융지주회사법"),
    ("금융산업의구조개선에관한법률", "금산법"), ("금융산업 구조개선", "금산법"), ("금산법", "금산법"),
    ("은행법", "은행법"),
    ("보험업법", "보험업법"),
    ("상법", "상법"),  # 마지막(가장 짧아 다른 매치 우선). '상법 시행령'도 여기서 상법으로.
]
#: law= 필터로 받는 법. 코퍼스(`sync_law_corpus.TARGETS`)와 같은 집합이어야 한다 — 위 별칭표의
#: law_short 전부. 여기만 4법으로 남으면 코퍼스에 있는 법을 필터로 못 고른다(260903 실측).
_KNOWN_LAWS = {short for _alias, short in LAW_ALIASES}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── 텍스트 정규화 (인덱스·질의 공유) ────────────────────────────────────
def normalize(text: str) -> str:
    """NFC + 공백/제어문자 제거. `_agenda_pattern_match`의 replace(' ','')와 정합."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", "", t)


# ── 동의어/guard 로더 (캐시) ────────────────────────────────────────────
_SYNONYMS_CACHE: dict[str, Any] | None = None


def load_synonyms() -> dict[str, Any]:
    """law_lookup_synonyms.json → 정규화된 매칭 자산.

    반환:
      vocabulary: set[str] (canonical 도메인 용어)
      surface_to_canonical: {surface: canonical}  (date-gated 변형은 제외 — 별개 토큰 유지)
      guards: list[list[str]] (false-friend 그룹)
      guarded: set[str]
      date_gated: list[(a, b, after_iso)]  (질의 시점 확장용)
    """
    global _SYNONYMS_CACHE
    if _SYNONYMS_CACHE is not None:
        return _SYNONYMS_CACHE
    try:
        raw = json.loads(_synonyms_path().read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    vocab = set(raw.get("vocabulary") or [])
    surface_to_canonical: dict[str, str] = {}
    date_gated: list[tuple[str, str, str]] = []
    for term in vocab:
        surface_to_canonical[normalize(term)] = term  # canonical → self
    for grp in raw.get("synonyms") or []:
        canon = grp.get("canonical")
        if not canon:
            continue
        vocab.add(canon)
        gate = grp.get("date_gated")
        for v in grp.get("variants") or []:
            if gate:
                # date-gated: 변형을 canonical로 병합하지 않고 별개 토큰으로 유지,
                # 질의 시점(as_of ≥ 시행일)에만 확장.
                vocab.add(v)
                surface_to_canonical.setdefault(normalize(v), v)
                date_gated.append((canon, v, gate.get("equivalent_after", "9999-12-31")))
            else:
                surface_to_canonical[normalize(v)] = canon
    guards = [list(g) for g in (raw.get("guards") or [])]
    guarded = {t for g in guards for t in g}
    # BM25 질의확장(구어→법률): trigger 부분문자열(정규화·무공백) → add 법률형태소.
    # Signal C(BM25) 전용 — E/B/guard/폐쇄어휘와 무관. add는 corpus 실존 형태소여야 효과.
    bm25_expansions: list[tuple[list[str], list[str]]] = []
    for rule in (raw.get("bm25_query_expansions") or {}).get("rules", []):
        trigs = [normalize(t).replace(" ", "") for t in (rule.get("triggers") or [])]
        adds = [a for a in (rule.get("add") or [])]
        trigs = [t for t in trigs if t]
        if trigs and adds:
            bm25_expansions.append((trigs, adds))
    # 범위 밖 주제(4법 원문 밖) → 근거 법령 안내용. _note 등 밑줄 키 제외.
    out_of_corpus = {k: v for k, v in (raw.get("out_of_corpus") or {}).items()
                     if not k.startswith("_")}
    # 통합 tokenizer용: 전체 surface를 길이 내림차순(긴 형제 먼저 마스킹).
    # 동일 길이 타이는 사전순으로 확정 — vocab이 set이라 (-len)만으론 실행마다 순서가 달라져
    # 마스킹 결과·토큰화가 비결정(서버 재시작·색인 재빌드마다 다른 결과). (-len, s)로 완전 결정화.
    surfaces_by_len = sorted((s for s in surface_to_canonical if s), key=lambda s: (-len(s), s))
    _SYNONYMS_CACHE = {
        "vocabulary": vocab,
        "surface_to_canonical": surface_to_canonical,
        "surfaces_by_len": surfaces_by_len,
        "guards": guards,
        "guarded": guarded,
        "date_gated": date_gated,
        "out_of_corpus": out_of_corpus,
        "bm25_expansions": bm25_expansions,
    }
    return _SYNONYMS_CACHE


def _expand_query_for_bm25(query: str, morphs: list[str]) -> list[str]:
    """구어→법률 질의확장: 정규화 질의에 trigger가 있으면 법률형태소를 append(중복 가중=강조).
    anchor 게이트 통과 후에만 호출 — vague 거동 보존. 정답 조문 본문 실어휘로만 매핑(근거)."""
    rules = load_synonyms().get("bm25_expansions") or []
    if not rules:
        return morphs
    nq = normalize(query).replace(" ", "")
    extra: list[str] = []
    for trigs, adds in rules:
        if any(t in nq for t in trigs):
            extra.extend(adds)
    return morphs + extra if extra else morphs


# 복합어 매칭용 조사(particle) 제거 — 한글 사이의 조사만 제거(어두/어말 조사 보존).
# 폐쇄 어휘에만 매칭하므로 거짓 복합어를 만들지 않는다("이사의 보수"→"이사보수"만 성립).
_PARTICLE_RE = re.compile(r"(?<=[가-힣])(의|를|을|은|는|이|가|과|와|및|에|로|으로)(?=[가-힣])")


def _departicle(norm: str) -> str:
    return _PARTICLE_RE.sub("", norm)


# ── 토큰 추출 (인덱스·질의 공유 — 보수적) ───────────────────────────────
def extract_tokens(text: str) -> set[str]:
    """text에서 canonical 도메인 토큰 집합 추출 (폐쇄 어휘만).

    **통합 longest-first 마스킹**: 전체 어휘 surface를 긴 것부터 norm·조사제거본 양쪽에
    매칭하고 매치 span을 마스킹 → ① false-friend(사외이사→이사 조각) ② 복합어 누출
    (감사보고서→감사) ③ 조사 분리 복합어(이사의 보수→이사보수) 세 문제를 한 번에 해결.
    별도 guard 그룹 불필요(긴 vocab 형제가 자동으로 짧은 조각을 마스킹). vocabulary에 없는
    fragment는 드롭. date-gate는 질의 시점 확장(매처)에서만 — 여기선 별개 토큰 유지.
    """
    syn = load_synonyms()
    norm = normalize(text)
    if not norm:
        return set()
    s2c: dict[str, str] = syn["surface_to_canonical"]
    found: set[str] = set()
    work_norm = norm
    work_dep = _departicle(norm)
    for surf in syn["surfaces_by_len"]:  # 길이 내림차순 precomputed
        if not surf:
            continue
        if surf in work_norm or surf in work_dep:
            found.add(s2c[surf])
            mask = _MASK * len(surf)
            work_norm = work_norm.replace(surf, mask)
            work_dep = work_dep.replace(surf, mask)
    return found


def expand_query_tokens(tokens: set[str], as_of_iso: str) -> set[str]:
    """질의 토큰에 date-gated 등가쌍을 조건부 확장 (as_of ≥ 시행일일 때만)."""
    syn = load_synonyms()
    out = set(tokens)
    for a, b, after in syn["date_gated"]:
        if as_of_iso >= after:
            if a in out:
                out.add(b)
            if b in out:
                out.add(a)
    return out


# ── corpus 인덱스 로더 (캐시) ───────────────────────────────────────────
_INDEX_CACHE: dict[str, Any] | None = None
_FULLTEXT_CACHE: dict[str, str] = {}


def load_index() -> dict[str, Any]:
    """law_index.json 로드 (모듈 캐시). 없으면 빈 인덱스.

    로드 1회에 파생 조회 인덱스를 함께 구축한다(전수 재순회 제거):
      `_by_key`  {(law_key, article_no): record}   — _record_by_key O(N)→O(1)
      `_by_int`  {article_int: [record, ...]}       — _signal_exact O(refs·N)→O(refs·k)
    _ 접두 키라 직렬화·기존 순회(idx["articles"])에 영향 없음.
    """
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    try:
        idx = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        # 260814: 조용히 빈 인덱스를 돌려줬다 — law_lookup 이 「조문을 못 찾음」을 정상 응답으로
        #   내보내 **없는 것과 못 읽은 것이 구분되지 않았다.** corpus 는 11MB 라 패키지에
        #   싣지 않고 repo 경로를 유지하되, 실패는 로그와 헬스체크로 밖에서 보이게 한다.
        logger.exception("법령 corpus 인덱스 로드 실패 — law_lookup 이 조문을 못 찾게 된다: %s",
                         _INDEX_PATH)
        idx = {"meta": {"df": {}, "idf": {}, "anchor_df_max": 0,
                        "n_articles": 0, "laws": []}, "articles": []}
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    by_int: dict[int, list[dict[str, Any]]] = {}
    for rec in idx.get("articles", []):
        by_key[_article_key(rec)] = rec
        by_int.setdefault(rec.get("article_int"), []).append(rec)
    idx["_by_key"] = by_key
    idx["_by_int"] = by_int
    _INDEX_CACHE = idx
    return _INDEX_CACHE


# ── 형태소 토큰화 (Signal C: BM25) ──────────────────────────────────────
# kiwipiepy = 순수 C++·오프라인·결정적(Java 불필요). 인덱스 빌드(sync)와 질의를 동일 로직으로.
# 품사: 체언/용언/어근/외국어/숫자/기호수/일반부사. 조사·어미·문장부호는 버림.
_KIWI_KEEP = ("NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "VX", "XR", "SL", "SN", "SH", "MAG")
_KIWI = None  # lazy 싱글턴 (init ~0.7s, 최초 호출 시 1회)


def _get_kiwi():
    """kiwipiepy 형태소 분석기 lazy 싱글턴. 미설치면 None(→ C는 폐쇄어휘 legacy로 degrade)."""
    global _KIWI
    if _KIWI is None:
        try:
            from kiwipiepy import Kiwi
            _KIWI = Kiwi()
        except Exception:
            _KIWI = False  # 미설치 표식(재시도 안 함)
    return _KIWI or None


def morph_tokens(text: str) -> list[str]:
    """text → 형태소 토큰 리스트(BM25용). 숫자(SN/NR)는 1글자도 유지, 나머지는 2글자+.
    sync 인덱스 빌드와 런타임 질의가 반드시 같은 함수를 써야 통계가 정합한다."""
    kiwi = _get_kiwi()
    if not kiwi:
        return []
    out: list[str] = []
    for m in kiwi.tokenize(text or ""):
        if (m.tag in _KIWI_KEEP and len(m.form) > 1) or m.tag in ("SN", "NR"):
            out.append(m.form)
    return out


# ── BM25 인덱스 로더 (Signal C) ─────────────────────────────────────────
_BM25_CACHE: dict[str, Any] | None = None


def load_bm25() -> dict[str, Any] | None:
    """law_bm25.json 로드(모듈 캐시). 파생: idf + {(law_key,article_no): doc} 맵.
    파일 없으면 None → _signal_corpus가 폐쇄어휘 legacy로 degrade."""
    global _BM25_CACHE
    if _BM25_CACHE is not None:
        return _BM25_CACHE or None
    try:
        raw = json.loads(_BM25_PATH.read_text(encoding="utf-8"))
    except Exception:
        _BM25_CACHE = {}
        return None
    meta = raw.get("meta", {})
    df: dict[str, int] = meta.get("df", {})
    n = max(int(meta.get("n", 0)), 1)
    # BM25 idf = ln(1 + (N-df+0.5)/(df+0.5)) — 음수 없는 표준형(Lucene류)
    idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    docs = raw.get("docs", [])
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for d in docs:
        k = d.get("k") or ["", ""]
        by_key[(k[0], k[1])] = d
    _BM25_CACHE = {
        "meta": meta, "idf": idf, "df": df, "n": n,
        "avgdl": float(meta.get("avgdl", 1.0)) or 1.0,
        "k1": float(meta.get("k1", 1.5)), "b": float(meta.get("b", 0.75)),
        "anchor_df_max": int(meta.get("anchor_df_max", 0)),
        "rare_df_max": int(meta.get("rare_df_max", 0)),
        "docs": docs, "by_key": by_key,
    }
    return _BM25_CACHE


def _query_has_morph_anchor(qmorphs: list[str], bm: dict[str, Any]) -> bool:
    """두루뭉술 게이트: 아는 형태소 ≥2개 또는 희소 형태소(df ≤ rare) 1개면 통과.
    단일 흔한 단어('이사'·'회사' 등)는 차단(vague → requires_review 보존)."""
    df = bm["df"]
    known = [t for t in set(qmorphs) if t in df]
    if len(known) >= 2:
        return True
    rare = bm["rare_df_max"]
    return any(df.get(t, 0) <= rare for t in known)


_MANIFEST_CACHE: dict[str, Any] | None = None
_STALE_DAYS = 30  # 주간 자동 재복사(law-corpus-weekly)면 <7일. 넘으면 자동배치 중단 의심 → 안내.


def load_manifest() -> dict[str, Any]:
    """corpus/_manifest.json 로드 (모듈 캐시). 원문 기준일·복사 시점 provenance."""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE
    try:
        _MANIFEST_CACHE = json.loads((_CORPUS_DIR / "_manifest.json").read_text(encoding="utf-8"))
    except Exception:
        _MANIFEST_CACHE = {}
    return _MANIFEST_CACHE


def _promulgation_asof(m: dict[str, Any]) -> str:
    """corpus 가 반영한 **가장 최근 공포일**. 8개 파일 frontmatter 의 `공포일자` 중 최대.

    260817: 종전에는 `source_committed_date`(원문 레포의 커밋 날짜)를 자료 기준일로
    보여줬는데 **그 값이 내용을 대변하지 못한다.** legalize-kr 은 히스토리를 다시 쓰는
    방식이라, 8-04 공포분까지 담은 스냅샷의 커밋일이 2026-02-10 으로 찍혔다. 반대로
    5-12 에서 멎은 포크는 커밋일이 7-02 로 더 최신이었다. 커밋일로 재면 낡은 쪽이
    최신으로 보인다 — 사용자에게 「188일 전 자료」라고 거짓 경고하거나, 낡은 자료를
    최신이라 안심시킨다. 공포일은 내용에서 직접 온다.

    sync 가 이미 계산해 둔 값을 쓴다(독자 재계산 금지). 옛 manifest 는 그 필드가 없어
    files[] 를 훑는 폴백을 남긴다 — 둘 다 같은 frontmatter 에서 나오므로 어긋날 수 없다.
    """
    top = (m.get("source_promulgated_date") or "")[:10]
    if top:
        return top
    dates = [
        (f.get("frontmatter") or {}).get("공포일자", "")[:10]
        for f in (m.get("files") or [])
    ]
    return max((d for d in dates if d), default="")


def corpus_freshness(as_of_iso: str | None = None) -> dict[str, Any]:
    """법령 자료가 얼마나 최신인지 → {asof(원문 기준일), synced(복사시점), age_days, stale}.

    `asof` 는 **공포일** 기준이다(위 `_promulgation_asof` 참조). manifest 가 옛 형식이라
    공포일을 못 구하면 커밋일로 물러선다 — 없는 것보다는 낫다.
    """
    m = load_manifest()
    src = _promulgation_asof(m) or (m.get("source_committed_date") or "")[:10]
    synced = (m.get("synced_at") or "")[:10]
    age = None
    stale = False
    if src:
        try:
            ref = date.fromisoformat((as_of_iso or date.today().isoformat())[:10])
            age = (ref - date.fromisoformat(src)).days
            stale = age is not None and age > _STALE_DAYS
        except Exception:
            pass
    return {"asof": src, "synced": synced, "age_days": age, "stale": stale}


def get_full_text(record: dict[str, Any]) -> str:
    """record의 (file, char_start, char_end)로 vendored .md에서 원문 슬라이스 (on-demand)."""
    rel = record.get("file")
    if not rel:
        return ""
    if rel not in _FULLTEXT_CACHE:
        try:
            _FULLTEXT_CACHE[rel] = (_CORPUS_DIR / rel).read_text(encoding="utf-8")
        except Exception:
            _FULLTEXT_CACHE[rel] = ""
    text = _FULLTEXT_CACHE[rel]
    cs, ce = record.get("char_start", 0), record.get("char_end", 0)
    return text[cs:ce].strip() if text else ""


# ── 조문 참조 추출 (Signal E) ───────────────────────────────────────────
_ARTICLE_REF_RE = re.compile(
    r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제\s*(\d+)\s*항)?(?:\s*제\s*(\d+)\s*호)?"
)


def detect_laws(text: str, law_param: str = "") -> list[str]:
    """text(또는 law_param)에서 언급된 law_short 목록. law_param 우선."""
    if law_param:
        norm_p = normalize(law_param)
        for alias, short in LAW_ALIASES:
            if normalize(alias) in norm_p:
                return [short]
        return [law_param]
    norm = normalize(text)
    found: list[str] = []
    for alias, short in LAW_ALIASES:
        if normalize(alias) in norm and short not in found:
            found.append(short)
    return found


def extract_article_refs(text: str) -> list[dict[str, Any]]:
    """text에서 제N조(의M)(제N항)(제N호) 참조 추출 → 튜플 키."""
    refs = []
    for m in _ARTICLE_REF_RE.finditer(text or ""):
        art, sub, hang, ho = m.groups()
        article_no = f"제{int(art)}조" + (f"의{int(sub)}" if sub else "")
        refs.append({
            "article_no": article_no,
            "article_int": int(art),
            "sub_int": int(sub) if sub else None,
            "hang": int(hang) if hang else None,
            "ho": int(ho) if ho else None,
        })
    return refs


# ── 매칭 ────────────────────────────────────────────────────────────────
def _article_key(rec: dict[str, Any]) -> tuple[str, str]:
    return (rec.get("law_key", ""), rec.get("article_no", ""))


def _iter_articles(law_filter: Iterable[str] | None = None) -> Iterable[dict[str, Any]]:
    idx = load_index()
    lf = set(law_filter) if law_filter else None
    for rec in idx.get("articles", []):
        if lf and rec.get("law_short") not in lf:
            continue
        yield rec


def _signal_exact(refs: list[dict[str, Any]], laws: list[str]) -> list[tuple[dict, dict]]:
    """정확 조문 튜플 매칭 (substring 절대 금지). (record, ref) 목록. laws 미지정+중복 시 전부."""
    hits: list[tuple[dict, dict]] = []
    if not refs:
        return hits
    by_int = load_index().get("_by_int", {})
    law_set = set(laws) if laws else None
    for ref in refs:
        for rec in by_int.get(ref["article_int"], []):
            if rec.get("sub_int") != ref["sub_int"]:
                continue
            if law_set and rec.get("law_short") not in law_set:
                continue
            hits.append((rec, ref))
    return hits


def _rule_articles(rule: dict[str, Any]) -> list[tuple[str, str]]:
    """룰 → 관련 (law_short, article_no) 목록.

    ① provision FK → law_provisions.json → article(상법). ② law_reference 문자열 안의
    조문 regex(공정거래법·자본시장법 등 free-text-only provision 복원). 법령명은 문자열 문맥으로 추정.
    """
    out: list[tuple[str, str]] = []
    prov_id = rule.get("provision")
    if prov_id:
        p = _load_law_provisions().get(prov_id)
        if p:
            for tok in re.split(r"[·,]", p.get("article", "")):
                m = _ARTICLE_REF_RE.search(tok)
                if m:
                    art, sub = m.group(1), m.group(2)
                    out.append(("상법", f"제{int(art)}조" + (f"의{int(sub)}" if sub else "")))
    ref_str = rule.get("law_reference", "") or ""
    laws_in_ref = detect_laws(ref_str)
    for m in _ARTICLE_REF_RE.finditer(ref_str):
        art, sub = m.group(1), m.group(2)
        article_no = f"제{int(art)}조" + (f"의{int(sub)}" if sub else "")
        law = laws_in_ref[0] if laws_in_ref else "상법"
        if (law, article_no) not in out:
            out.append((law, article_no))
    return out


def _signal_bridge(query: str, direction: str) -> dict[tuple[str, str], dict[str, Any]]:
    """40룰 bridge. query를 title로 `_agenda_pattern_match` → 매치 룰 → 관련 조문.

    반환: {(law_short, article_no): {conf, rules:[rule_id...], decisions:[...]}}.
    conf = A/B 0.9, C 0.6. 자산·시행일 게이트는 조회 tool이라 적용 안 함(범용 조회).
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for rule in _load_law_layer_rules():
        pattern = rule.get("agenda_pattern") or {}
        signal_pattern = rule.get("signal_pattern") or {}
        matched = False
        if pattern:
            matched = _agenda_pattern_match(query, "", pattern)
        elif signal_pattern:
            # C layer: keywords 단순 포함 (신호). name은 사람용 라벨이라 매칭 제외.
            kws = signal_pattern.get("keywords") or []
            nq = normalize(query)
            matched = any(normalize(k) in nq for k in kws if k)
        if not matched:
            continue
        layer = rule.get("layer", "")
        conf = 0.6 if layer == "C" else 0.9
        for key in _rule_articles(rule):
            slot = out.setdefault(key, {"conf": 0.0, "rules": [], "decisions": []})
            slot["conf"] = max(slot["conf"], conf)
            slot["rules"].append(rule.get("id", ""))
            slot["decisions"].append({
                "rule_id": rule.get("id", ""), "layer": layer,
                "decision": rule.get("decision", ""),
                "reason": rule.get("reason_template", ""),
                "law_reference": rule.get("law_reference", ""),
                "keywords": (pattern.get("all_of") or []) + (pattern.get("any_of") or []),
            })
    return out


_C_EMIT_TOPN = 20  # BM25 상위 N만 emit(top_k=10 충분 여유). total_candidates 비대·노이즈 방지.


def _signal_corpus(query: str, query_tokens: set[str],
                   law_filter: list[str] | None) -> dict[tuple[str, str], float]:
    """corpus 전문 형태소 BM25. 두루뭉술 게이트(형태소 anchor) 통과 시 상위 N을 max정규화(0,1]로.
    BM25 인덱스/kiwi 미가용이면 폐쇄어휘 legacy overlap으로 degrade(하위호환)."""
    bm = load_bm25()
    qmorphs = morph_tokens(query)
    if not bm or not qmorphs:
        return _signal_corpus_legacy(query_tokens, law_filter)
    # 구어→법률 확장을 게이트 前에 적용: 특정 법률개념을 트리거한 질의는 anchor로 인정해야
    # 게이트에 막히지 않는다(예 "주식을 쪼개는 거" — 알려진 형태소 1개라 게이트 탈락하나 '분할'
    # 확장으로 구제). vague 7단어는 어떤 트리거도 안 걸려 여전히 차단(spot 보존).
    qmorphs = _expand_query_for_bm25(query, qmorphs)
    if not _query_has_morph_anchor(qmorphs, bm):
        return {}
    idf = bm["idf"]
    k1, b, avgdl = bm["k1"], bm["b"], bm["avgdl"]
    lf = set(law_filter) if law_filter else None
    qc = Counter(qmorphs)
    scored: list[tuple[float, tuple[str, str]]] = []
    for d in bm["docs"]:
        if lf and d.get("ls") not in lf:
            continue
        tf = d.get("tf") or {}
        dl = d.get("dl", 0) or 0
        s = 0.0
        for t in qc:
            f = tf.get(t, 0)
            if not f:
                continue
            s += idf.get(t, 0.0) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if s > 0:
            k = d.get("k") or ["", ""]
            scored.append((s, (k[0], k[1])))
    if not scored:
        return {}
    scored.sort(key=lambda x: -x[0])
    top = scored[:_C_EMIT_TOPN]
    smax = top[0][0] or 1.0
    return {key: (raw / smax) for raw, key in top}  # max정규화 → (0,1]


def _signal_corpus_legacy(query_tokens: set[str],
                          law_filter: list[str] | None) -> dict[tuple[str, str], float]:
    """폐쇄어휘 overlap(구 C). BM25 인덱스/kiwi 미가용 시 fallback."""
    idx = load_index()
    meta = idx.get("meta", {})
    idf: dict[str, float] = meta.get("idf", {})
    df: dict[str, float] = meta.get("df", {})
    anchor_max = meta.get("anchor_df_max", 0)
    has_anchor = any(df.get(t, 0) <= anchor_max for t in query_tokens if t in idf)
    if not has_anchor:
        return {}
    den = sum(idf.get(t, 0.0) for t in query_tokens) or 1.0
    out: dict[tuple[str, str], float] = {}
    for rec in _iter_articles(law_filter):
        rtok = set(rec.get("tokens") or [])
        common = query_tokens & rtok
        if not common:
            continue
        ttok = set(rec.get("title_tokens") or [])
        num = sum(idf.get(t, 0.0) * (2.0 if t in ttok else 1.0) for t in common)
        sim = num / den
        if sim > 0:
            out[_article_key(rec)] = sim
    return out


def _record_by_key(key: tuple[str, str]) -> dict[str, Any] | None:
    return load_index().get("_by_key", {}).get(key)


# ── 후보 융합·랭킹 ──────────────────────────────────────────────────────
TAU_EMIT = 0.30
TAU_STRONG = 0.60
#: 질의 용어가 조문 제목에 전부 들어 있을 때의 가산. 0.25 면 corpus 단독(≤0.5)이
#: TAU_STRONG 을 넘어 전문이 붙는다 — 그 아래면 아무 효과가 없다.
_TITLE_ANCHOR_BONUS = 0.25


def _fuse(query: str, query_tokens: set[str], refs: list[dict], laws: list[str],
          direction: str, law_filter: list[str] | None) -> list[dict[str, Any]]:
    """3신호 융합 → 랭킹된 후보 리스트."""
    cand: dict[tuple[str, str], dict[str, Any]] = {}

    def _slot(rec: dict[str, Any]) -> dict[str, Any]:
        k = _article_key(rec)
        return cand.setdefault(k, {"record": rec, "e": 0.0, "b": 0.0,
                                   "c": 0.0, "signals": set(), "bridge": None, "refs": []})

    # E
    for rec, ref in _signal_exact(refs, laws):
        s = _slot(rec)
        s["e"] = 1.0
        s["signals"].add("exact")
        s["refs"].append(ref)
    # B
    bridge = _signal_bridge(query, direction)
    for key, info in bridge.items():
        rec = _record_by_key(key)
        if not rec:
            continue
        if law_filter and rec.get("law_short") not in law_filter:
            continue
        s = _slot(rec)
        s["b"] = max(s["b"], info["conf"])
        s["signals"].add("bridge")
        s["bridge"] = info
    # C
    for key, sim in _signal_corpus(query, query_tokens, law_filter).items():
        rec = _record_by_key(key)
        if not rec:
            continue
        s = _slot(rec)
        s["c"] = sim
        s["signals"].add("corpus")

    # ── 법 우선순위(260902) ─────────────────────────────────────────────
    #
    # 코퍼스를 4법 → 10법으로 넓히자 **기존 4법 질의의 정확도가 떨어졌다** —
    # recall@1 45%→43% · recall@10 86%→83% · MRR 0.602→0.570 (harness N=242).
    # 새 법이 나쁜 게 아니라, 「감사위원」·「사외이사」 같은 말이 여러 법에 나와
    # 상위 자리를 나눠 갖기 때문이다. 넓힌 대가를 옛 질의가 치른 셈이다.
    #
    # 그래서 **질의가 그 영역을 가리킬 때만** 확장 법을 앞세운다. 가리키는 말이
    # 없으면 4법이 먼저다 — 대부분의 질문은 여전히 상법·자본시장법 이야기다.
    #
    # 🔴 후보에서 빼지 않는다. 순서만 낮춘다 — 빼면 「그 법에 있는데 못 찾는」
    #   경우가 생기고, 그건 순위가 밀리는 것보다 나쁘다.
    _domain_hint = _expanded_law_hint(query)
    def _law_prior(rec: dict) -> float:
        ls = rec.get("law_short", "")
        if ls not in _EXPANDED_LAWS:
            return 0.0
        return 0.0 if ls in _domain_hint else -_EXPANDED_LAW_PENALTY

    # ── 제목 앵커 승격(260902) ────────────────────────────────────────
    #
    # corpus 신호(C)는 가중치 0.5 라 단독으로는 TAU_STRONG(0.60)을 절대 못 넘는다.
    # 4법 시절엔 bridge(B)가 대부분을 받쳐 줬는데, 확장 6법에는 bridge 룰이 없다 —
    # 그래서 「적기시정조치 요건」처럼 **조문 제목이 질문 그대로인** 경우조차 약매칭으로
    # 떨어져 전문이 안 붙었다(260902 실측).
    #
    # 질의의 도메인 용어가 **조문 제목에 전부** 들어 있으면 그건 그 조문이다. 그때만
    # 올린다 — 본문에 섞여 나오는 것과는 다르다.
    _q_anchor = {t for t in query_tokens if t}
    def _title_anchor_bonus(rec: dict) -> float:
        # 🔴 **확장 6법에만** 준다. 4법에 주면 「집중투표」·「자기주식 소각」처럼 한 단어짜리
        #   질의가 전부 강매칭이 되어 전문 10건이 통째로 붙는다(260828 에 3건으로 줄여 둔
        #   그 결함이 되살아난다). 4법은 bridge(B)가 이미 받쳐 주므로 이 가산이 필요 없다 —
        #   필요한 쪽은 bridge 룰이 없는 확장 6법이다.
        if not _q_anchor or rec.get("law_short") not in _EXPANDED_LAWS:
            return 0.0
        tt = set(rec.get("title_tokens") or [])
        return _TITLE_ANCHOR_BONUS if tt and _q_anchor <= tt else 0.0

    out = []
    for k, s in cand.items():
        score = (1.0 * s["e"] + 0.9 * s["b"] + 0.5 * s["c"]
                 + _law_prior(s["record"]) + _title_anchor_bonus(s["record"]))
        # E/B/C(BM25 top-N) 신호가 있으면 emit — 랭킹+top_k 슬라이스가 상위만 노출.
        # (구 TAU_EMIT 게이트는 폐쇄어휘 legacy에서만 의미. BM25는 signal 자체가 상위 N 필터.)
        if not (s["e"] or s["b"] or s["c"] > 0):
            continue
        s["score"] = round(score, 4)
        out.append(s)

    def _sort_key(s: dict[str, Any]) -> tuple:
        rec = s["record"]
        return (
            -s["score"],
            rec.get("law_tier", 0),  # 법률(0) < 시행령(1) — governing 법률을 시행령보다 위로
            0 if "title" in _match_field(s, rec) else 1,
            0 if not rec.get("deleted") else 1,
            rec.get("law_short", ""), rec.get("article_int", 0), rec.get("sub_int") or 0,
        )

    out.sort(key=_sort_key)
    return out


def _match_field(s: dict[str, Any], rec: dict[str, Any]) -> str:
    """corpus 매치가 title에 걸렸는지 대략 판정 (tie-break용)."""
    return "title" if s.get("c", 0) > 0 and (set(rec.get("title_tokens") or [])) else "body"


def _law_version_future(rec: dict[str, Any], as_of_iso: str | None = None) -> bool:
    """이 조문이 속한 **법령 스냅샷(전문) 자체의 시행일**이 as_of보다 미래인가.

    corpus의 `enforcement`는 법 '전문'의 시행일자(공포본 단위)라 개별 조문의 현행 여부로
    쓰면 안 된다 — 미래 시행 개정본을 vendored하면 그 법의 **모든 조문**이 거짓 '미시행'으로
    찍힌다(260713 자본시장법 599/599 오탐). 그래서 조문별 in_force를 여기서 '단정'하지 않고,
    전문이 시행예정본이면 True만 돌려 '현행 여부 불명(확인필요)'으로 표시한다. 진짜 조문별
    미래시행은 SSOT(law_provisions.json)의 effective_date로만 단정한다.
    """
    enf = rec.get("enforcement") or ""
    if not enf:
        return False
    ref = as_of_iso or date.today().isoformat()
    return enf > ref


# ── 방향 B: 조문 → 관련 룰(정관 변경유형·우회·안건) ──────────────────────
def _reverse_bridge(rec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """이 조문에 붙는 40룰 역방향 조회 → layer별 그룹."""
    key = _article_key(rec)
    target = (rec.get("law_short", ""), rec.get("article_no", ""))
    groups: dict[str, list[dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for rule in _load_law_layer_rules():
        arts = _rule_articles(rule)
        if target not in arts:
            continue
        layer = (rule.get("layer") or "")[0:1] or "?"
        bucket = layer if layer in groups else "B"
        pat = rule.get("agenda_pattern") or {}
        groups[bucket].append({
            "rule_id": rule.get("id", ""), "layer": rule.get("layer", ""),
            "decision": rule.get("decision", ""),
            "reason": rule.get("reason_template", ""),
            "law_reference": rule.get("law_reference", ""),
            "keywords": (pat.get("all_of") or []) + (pat.get("any_of") or []),
        })
    return groups


# ── 조문별 시행 게이트 — SSOT(law_provisions.json) ↔ 후보 조문, **항 단위** ──────────
# 260903 실측: as_of=2026-09-03 에 §542의12·§542의7 을 조문번호로 조회하면 '현행'만 찍히고
# §542의12②(분리선출 2명, 2026-09-10 시행)·§542의7③(정관 배제 금지, 2026-09-10 시행)에 아무
# 표지가 없었다. 종전 flag 경로는 **bridge 룰의 첫 번째 룰**의 provision 만 봤다 — 조문번호 직접
# 조회는 bridge 가 아예 없어 진입조차 안 했고, bridge 가 있어도 두 번째 룰(다른 조항)은 버렸다.
# 게다가 룰의 law_reference 가 §382의2 를 가리키면 §382의2 에 §542의7③ 의 날짜가 붙었다(오귀속).
# 이제 SSOT 조항을 **조문번호로 직접** 후보에 맞추고, article 의 '제N항'·`paragraphs` 로 항까지
# 좁힌다. corpus 스냅샷은 개정 조문을 이미 담고 있으므로(상법 법률 2026-03-06 시행본) 전문이
# '현행'이어도 그 안의 어떤 항은 as_of 시점 미시행일 수 있다 — 그걸 항 옆에 표지로 붙인다.
_INT_TO_CIRCLED: dict[int, str] = {v: k for k, v in CIRCLED_TO_INT.items()}
_PROVISIONS_BY_ARTICLE_CACHE: dict[str, list[tuple[dict[str, Any], list[int]]]] | None = None


def _provision_paragraphs(prov: dict[str, Any]) -> dict[str, list[int]]:
    """SSOT 조항 → {article_no: [항 번호...]}. article 문자열의 '제N항' + 선택 `paragraphs` 합집합.
    항이 하나도 없으면 [] = 조문 전체."""
    out: dict[str, list[int]] = {}
    for ref in extract_article_refs(prov.get("article", "") or ""):
        paras = out.setdefault(ref["article_no"], [])
        if ref.get("hang") and ref["hang"] not in paras:
            paras.append(ref["hang"])
    explicit = [int(x) for x in (prov.get("paragraphs") or [])]
    if explicit:
        for art in out:
            out[art] = sorted(set(out[art]) | set(explicit))
    return out


def _provisions_by_article() -> dict[str, list[tuple[dict[str, Any], list[int]]]]:
    """{상법 article_no: [(provision, paragraphs)]} — SSOT 는 상법 개정 대장이라 법은 상법 하나."""
    global _PROVISIONS_BY_ARTICLE_CACHE
    if _PROVISIONS_BY_ARTICLE_CACHE is not None:
        return _PROVISIONS_BY_ARTICLE_CACHE
    table: dict[str, list[tuple[dict[str, Any], list[int]]]] = {}
    for prov in _load_law_provisions().values():
        for art, paras in _provision_paragraphs(prov).items():
            table.setdefault(art, []).append((prov, paras))
    _PROVISIONS_BY_ARTICLE_CACHE = table
    return table


def _paragraph_label(paras: list[int]) -> str:
    return ("".join(_INT_TO_CIRCLED.get(n, f"({n})") for n in paras) + "항") if paras else "조문 전체"


def provision_gates(rec: dict[str, Any], as_of_iso: str) -> list[dict[str, Any]]:
    """이 조문에 걸린 SSOT 조항들의 as_of 기준 상태(항 단위).

    state: pending(시행일 前) · grace(시행됐으나 유예 종료 前 — 미이행이 아직 위반 아님) · in_force.
    label 은 md 에 그대로 붙는 표지: 「시행예정 YYYY-MM-DD」·「유예 종료 YYYY-MM-DD」. 상법 **법률**
    조문에만 — 시행령은 SSOT 밖(threshold_decree 는 임계 출처일 뿐 개정 대장이 아니다).
    """
    if rec.get("law_short") != "상법" or (rec.get("law_tier") or 0) != 0:
        return []
    gates: list[dict[str, Any]] = []
    for prov, paras in _provisions_by_article().get(rec.get("article_no") or "", []):
        eff = prov.get("effective_date") or ""
        obl = prov.get("obligation_date") or ""
        if eff and eff > as_of_iso:
            state, label = "pending", f"시행예정 {eff}"
        elif obl and obl > as_of_iso:
            state, label = "grace", f"유예 종료 {obl}"
        else:
            state, label = "in_force", ""
        gates.append({
            "provision_id": prov.get("provision_id"),
            "paragraphs": list(paras),
            "paragraph_label": _paragraph_label(paras),
            "effective_date": eff or None,
            "obligation_date": obl or None,
            "first_agm_trigger": bool(prov.get("first_agm_trigger")),
            "amendment": f"{prov.get('amendment_round_label') or ''} 개정 {prov.get('law_no') or ''}".strip(),
            "content": prov.get("table_content"),
            "applies_to": prov.get("table_applies_to"),
            "state": state,
            "label": label,
        })
    return gates


def _apply_provision_gates(item: dict[str, Any], gates: list[dict[str, Any]]) -> None:
    """게이트를 공개 item 에 새긴다 — flags(사람용 한 줄) · hang[*].gates(항 옆 표지) ·
    gate_status(조문 요약: pending/partial_pending/grace/None) · gate_summary(표 '시행' 열용)."""
    if not gates:
        return
    item["provision_gates"] = gates
    active = [g for g in gates if g["state"] != "in_force"]
    if not active:
        return
    # 항 옆 표지 — rec 의 hang 리스트는 인덱스 캐시와 공유되므로 **복사한 뒤** 새긴다.
    hang = [dict(h) for h in (item.get("hang") or [])]
    for g in active:
        for h in hang:
            if g["paragraphs"] and h.get("no") in g["paragraphs"]:
                h.setdefault("gates", []).append(g["label"])
    item["hang"] = hang
    summaries: list[str] = []
    for g in active:
        head = f"{g['paragraph_label']} {g['label']}"
        summaries.append(head)
        note = f"{head} — {g['content'] or g['provision_id']} ({g['amendment']}"
        if g.get("applies_to"):
            note += f" · 적용 {g['applies_to']}"
        note += ")"
        if g["state"] == "pending" and g["first_agm_trigger"]:
            note += " · 시행 후 최초 이사선임 주총부터 적용(주총일 기준)"
        if g["state"] == "grace":
            note += " · 시행됐으나 유예 종료 전 미이행은 위반 아님"
        item.setdefault("flags", []).append(note)
    whole_pending = any(g["state"] == "pending" and not g["paragraphs"] for g in active)
    if whole_pending:
        item["gate_status"] = "pending"
    elif any(g["state"] == "pending" for g in active):
        item["gate_status"] = "partial_pending"
    else:
        item["gate_status"] = "grace"
    item["gate_summary"] = " · ".join(summaries)


# ── payload ─────────────────────────────────────────────────────────────
def _record_public(rec: dict[str, Any], *, include_full_text: bool, as_of_iso: str) -> dict[str, Any]:
    """후보 record → 공개 dict (조문 상세)."""
    out = {
        "law": rec.get("law_short"), "law_name": rec.get("law_name"),
        "article_no": rec.get("article_no"), "article_title": rec.get("article_title"),
        "path": rec.get("path") or [],
        # in_force 3-상태: True=현행, None=전문 시행예정본이라 조문별 현행여부 불명(확인필요).
        # False로 '단정'하지 않는다 — 진짜 조문별 미시행은 SSOT effective_date로만 flag.
        "enforcement": rec.get("enforcement"),
        "in_force": None if _law_version_future(rec, as_of_iso) else True,
        "deleted": bool(rec.get("deleted")), "deleted_date": rec.get("deleted_date"),
        "amended_dates": rec.get("amended_dates") or [],
        "hang": rec.get("hang") or [], "ho": rec.get("ho") or {},
    }
    if include_full_text:
        out["full_text"] = get_full_text(rec)
    return out


# ── 폴백 유형 분류 (검색이 '깨끗한 정답'이 아닐 때 이유별 안내) ─────────────
_LAWS_LABEL = ("상법·자본시장법·공정거래법·외부감사법 + 지배구조법·상증세법·"
               "금융지주회사법·금산법·은행법·보험업법")   # 260902 4법 → 10법

#: **질의 용어 자체를 알아보지 못한** 유형. 이때는 조문 전문·항·호를 붙이지 않는다 —
#: "법령 용어를 알아보지 못했어요" 라고 말하면서 조문 20건을 쏟는 것이 260828 U 지적 A-4 의
#: 실체다(md 23,513자). 알아본 게 없으면 붙일 근거도 없다. 표(법령·조문·제목·score)까지만.
_NO_FULLTEXT_FALLBACKS = {"too_vague", "too_generic", "no_match"}
#: 용어는 알아봤지만(희소 anchor 있음) 정확한 조문은 못 짚은 유형. 여기서는 전문을 **버리지 않고
#: 상위 몇 건으로 줄인다** — `집중투표`·`대량보유` 같은 정상 키워드 조회가 전부 이쪽이라
#: 통째로 막으면 도구의 주 용도가 죽는다.
_LIMIT_FULLTEXT_FALLBACKS = {"weak_match", "deleted_only"}
#: 각각의 상한.
_NO_FULLTEXT_SHOW_MAX = 5
_LIMIT_FULLTEXT_TOPN = 3


#: 260902 확장분. 4법과 달리 **질의가 그 영역을 가리킬 때만** 앞세운다(위 `_law_prior`).
_EXPANDED_LAWS = frozenset({
    "지배구조법", "지배구조법시행령", "상증세법", "상증세법시행령",
    "금융지주회사법", "금융지주회사법시행령", "금산법", "금산법시행령",
    "은행법", "은행법시행령", "보험업법", "보험업법시행령",
})
#: 가리키는 말이 없을 때 확장 법에 매기는 감점. 0.35 는 harness 로 고른 값이다 —
#: 더 키우면 확장 법이 아예 안 뜨고, 더 줄이면 옛 질의 정확도가 안 돌아온다.
_EXPANDED_LAW_PENALTY = 0.35
#: 질의 → 어느 확장 법 영역인가. 「글자가 겹치나」가 아니라 **그 법을 가리키는 말**만 싣는다.
_EXPANDED_LAW_CUES: dict[str, tuple[str, ...]] = {
    "지배구조법": ("금융회사", "금융지주", "지배구조법", "임원자격", "적격성",
                "은행", "보험회사", "금융투자업자"),
    "상증세법": ("상속", "증여", "상증", "세법", "할증평가", "명의신탁", "가업승계", "평가심의"),
    "금융지주회사법": ("금융지주", "지주회사법", "자회사편입", "손자회사"),
    "금산법": ("금산법", "적기시정조치", "부실금융기관", "계약이전", "구조개선"),
    "은행법": ("은행", "동일차주", "은행업", "여신"),
    "보험업법": ("보험", "지급여력", "책임준비금", "방카슈랑스", "보험회사"),
}


#: cue 바로 뒤에 이 말이 붙으면 그 cue 는 **그 영역이 아니라는 뜻**이다.
#:   260902 실측 — 「금융회사 **아닌** 회사의 사외이사 자격」이 「금융회사」에 걸려 지배구조법이
#:   1위로 올라왔다(힌트를 끄면 상법·자본시장법 §382). 부정문을 못 읽고 글자만 본 것이다.
_EXPANDED_LAW_NEGATIONS: tuple[str, ...] = ("아닌", "아니", "제외", "이외", "외의", "말고", "빼고")
#: cue 글자를 품고 있지만 그 법과 무관한 합성어. 매칭 전에 질의에서 지운다.
#:   「임원배상책임**보험** 가입 안건」은 주총 안건이지 보험업법 이야기가 아니다.
_EXPANDED_LAW_FALSE_FRIENDS: tuple[str, ...] = (
    "배상책임보험", "책임보험", "보험료", "고용보험", "산재보험", "건강보험", "보험가입",
)


def _cue_hits(nq: str, cue: str) -> bool:
    """cue 가 질의에 있고, 그 바로 뒤가 부정 표현이 아닌 자리가 하나라도 있나."""
    start = 0
    while True:
        i = nq.find(cue, start)
        if i < 0:
            return False
        tail = nq[i + len(cue): i + len(cue) + 4]   # 「가아닌」·「를제외한」까지 잡히게 4자
        if not any(n in tail for n in _EXPANDED_LAW_NEGATIONS):
            return True
        start = i + 1


def _expanded_law_hint(query: str) -> frozenset[str]:
    """질의가 가리키는 확장 법 집합(시행령 포함). 없으면 빈 집합.

    글자가 겹치는 것과 그 법을 가리키는 것은 다르다 — 부정문(「금융회사 아닌」)과
    다른 뜻의 합성어(「배상책임보험」)는 걸러 낸다. 그 밖의 부분 일치는 그대로 둔다:
    한국어 질의에는 어절 경계가 없고, 「은행 차입금」처럼 두 영역이 섞인 질의는 힌트가
    켜져도 4법이 같은 자리에서 겨루므로 순위만 조금 흔들릴 뿐이다.
    """
    nq = normalize(query)
    for ff in _EXPANDED_LAW_FALSE_FRIENDS:
        nq = nq.replace(normalize(ff), "")
    hit: set[str] = set()
    for law, cues in _EXPANDED_LAW_CUES.items():
        if any(_cue_hits(nq, normalize(c)) for c in cues):
            hit.add(law)
            hit.add(f"{law}시행령")
    return frozenset(hit)


#: corpus 밖 **규범 체계** — 한국거래소 자율규정(상장·공시·업무규정) 영역. 사전(JSON)의
#: `out_of_corpus`(차등의결권 등 개별 제도)와 목적이 같고 층위만 다르다: 이쪽은 "그 법이 아니라
#: 거래소 규정" 이라는 답을 준다.
#:
#: 260828 U 실사용 지적 A-4 — "코스닥 관리종목 지정 시가총액 요건"을 물었더니 외부감사법 §14 ·
#: 자본시장법 §246 · §86 전문이 23KB 쏟아졌다. 4법 원문에 그 요건이 **없는데도** BM25 가 어휘만
#: 겹치는 조문을 끌어온 것이다. 모르는 영역은 모른다고 말한다.
#:
#: ★ 이 표는 **강한 매칭(E/B/강C)이 없을 때만** 본다(`_classify_fallback` 의 `if strong: return None`
#:   아래). 진짜 그 법 조문이 강하게 걸리면 그건 그대로 준다 — 예: 자본시장법 §390(상장규정 위임).
_EXCHANGE_RULE_TOPICS: dict[str, str] = {
    "관리종목": "한국거래소 유가증권·코스닥시장 상장규정 (자본시장법 제390조가 거래소에 위임)",
    "투자주의환기종목": "한국거래소 코스닥시장 상장규정",
    "상장적격성": "한국거래소 상장규정(상장적격성 실질심사)",
    "실질심사": "한국거래소 상장규정(상장적격성 실질심사)",
    "상장폐지": "한국거래소 유가증권·코스닥시장 상장규정",
    "상장유지": "한국거래소 유가증권·코스닥시장 상장규정",
    "상장예비심사": "한국거래소 유가증권·코스닥시장 상장규정",
    "우회상장": "한국거래소 유가증권·코스닥시장 상장규정",
    "상장규정": "한국거래소 유가증권·코스닥시장 상장규정",
    "불성실공시": "한국거래소 유가증권·코스닥시장 공시규정",
    "공시불이행": "한국거래소 유가증권·코스닥시장 공시규정",
    "공시번복": "한국거래소 유가증권·코스닥시장 공시규정",
    "정리매매": "한국거래소 유가증권·코스닥시장 업무규정",
    "매매거래정지": "한국거래소 유가증권·코스닥시장 업무규정",
    "투자경고종목": "한국거래소 시장감시규정(시장경보)",
    "투자위험종목": "한국거래소 시장감시규정(시장경보)",
    "단기과열종목": "한국거래소 시장감시규정(시장경보)",
}

#: corpus 밖 **법률** — 거래소 자율규정(위 표)과 층위가 다르다. 이쪽은 「법이긴 한데 우리가
#: 안 읽은 법」이다.
#:
#: 260902 실측 — 「보험회사의 자산운용 한도 규제 근거 조문」을 물었더니 자본시장법 §86(계열회사
#: 증권 취득제한) · 외부감사법 §38(손해배상책임보험 가입)이 후보로 올라왔다. 뒤엣것은 「보험」
#: 이라는 **글자만 겹친** 것이고 질문과 아무 상관이 없다. 전문을 안 붙인 건 잘한 처리지만,
#: 「보험업법은 여기 없다」고 말해 주지 않으면 읽는 쪽이 **엉뚱한 법을 근거로 삼는다.**
#:
#: 거래소 표는 「규정」만 막고 있었다 — 법률에는 같은 가드가 없었다. 규범 체계가 아니라
#: **법 이름**으로 걸러야 하는 자리다.
#:
#: ★ 여기 실린 법을 corpus 에 넣으면 **그 줄을 지운다.** 안 지우면 코퍼스에 있는데도
#:   「범위 밖」이라고 답한다 — 있는 것을 없다고 말하는 쪽이 더 나쁘다.
#:   260902 에 지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법을 넣으면서
#:   그 여섯과 딸린 용어(지급여력·적기시정조치·할증평가 등)를 이 표에서 걷어 냈다.
_OUT_OF_CORPUS_STATUTES: dict[str, str] = {
    # 세법 — 상증세법은 260902 에 corpus 로 들어갔다. 나머지는 아직 밖이다.
    "법인세법": "법인세법 (이 도구는 안 읽는다)",
    "소득세법": "소득세법 (이 도구는 안 읽는다)",
    # 금융업권 — 은행·보험·지주·금산법·지배구조법은 260902 에 들어갔다. 남은 것만.
    "여신전문금융업법": "여신전문금융업법 (이 도구는 안 읽는다)",
    # 그 밖
    "신탁법": "신탁법 (이 도구는 안 읽는다)",
    "자산유동화에관한법률": "자산유동화에 관한 법률",
    "근로기준법": "근로기준법 (이 도구는 안 읽는다)",
    "근로자참여및협력증진에관한법률": "근로자참여 및 협력증진에 관한 법률",
}


def _out_of_corpus_hit(q: str, tokens: set[str]) -> tuple[str, str] | None:
    """질의가 corpus 밖을 가리키나 → (용어, 근거).

    세 층을 함께 본다 — 개별 제도(사전 `out_of_corpus`) · 거래소 자율규정 · **법률**.
    셋 다 「우리가 안 읽은 것」이라는 점에서 같고, 답에 실을 근거 문구만 다르다.
    """
    ooc = {**load_synonyms().get("out_of_corpus", {}), **_EXCHANGE_RULE_TOPICS,
           **_OUT_OF_CORPUS_STATUTES}
    nq = normalize(q)
    for term, where in ooc.items():
        if term.startswith("_"):          # 사전의 `_note` 같은 메타 키는 매칭 대상이 아니다
            continue
        if term in tokens or normalize(term) in nq:
            return term, where
    return None


def _classify_fallback(
    *, q: str, tokens: set[str], refs: list[dict], law_filter: list[str] | None,
    candidates: list[dict], shown: list[dict], collision: bool, collision_laws: list[str],
    has_anchor: bool, exact_hits: bool,
) -> dict[str, Any] | None:
    """검색이 강한 매칭(E/B/강C)이 아닐 때, **왜 안 잡혔는지**를 유형화하고
    유형별 안내 문구(message)와 다음 행동(actions)을 돌려준다. 깨끗한 정답이면 None.

    유형(우선순위 순): law_collision → article_not_found → out_of_corpus_topic →
    filtered_empty → too_vague → too_generic → deleted_only → weak_match → no_match.
    (unknown_law_filter는 결과 유무와 무관하게 항상 별도 경고로 이미 표시 — 여기서 제외.)
    """
    strong = any(c["e"] or c["b"] for c in shown) or any(c["score"] >= TAU_STRONG for c in shown)

    # 조문번호 관련 — 결과 강도와 무관하게 먼저(가장 구체적 안내).
    if collision:
        laws_txt = ", ".join(collision_laws) if collision_laws else "여러 법"
        nums = ", ".join(r.get("article_no", "") for r in refs) or "그 조문번호"
        return {
            "type": "law_collision",
            "message": f"{nums}는 {laws_txt}에 모두 있어요. 어느 법인지 알려주시면 그 조문만 정확히 보여드릴게요.",
            "actions": [f"예: law=상법 처럼 법을 붙여 다시 물어봐 주세요 ({laws_txt} 중에서요)."],
        }
    if refs and not exact_hits:
        nums = ", ".join(r.get("article_no", "") for r in refs)
        return {
            "type": "article_not_found",
            "message": f"{nums}를 지금 자료에서 찾지 못했어요. 번호가 조금 다르거나, 지금 담긴 4개 법 밖의 조문일 수 있어요.",
            "actions": [f"번호를 한 번 더 확인해 주세요. 지금 찾을 수 있는 법은 {_LAWS_LABEL}(각 시행령 포함)이에요.",
                        "번호가 헷갈리시면 키워드로 찾아드릴게요 — 예: 집중투표 · 대량보유 · 감사위원."],
        }

    if strong:
        return None  # 강한 매칭 있음 → 폴백 안내 불필요

    # 이하: 강한 매칭이 없을 때만.
    ooc = _out_of_corpus_hit(q, tokens)
    if ooc:
        term, where = ooc
        return {
            "type": "out_of_corpus_topic",
            "message": f"'{term}'은(는) 이 도구의 범위 밖이에요. 이 도구가 담고 있는 법은 "
                       f"{_LAWS_LABEL}(각 시행령 포함)뿐이고, 물어보신 근거는 {where}에 있습니다. "
                       f"거래소 상장규정·공시규정·업무규정은 여기 들어 있지 않아요.",
            # ★ 어휘만 겹친 조문을 붙이지 않는다. 붙이면 「범위 밖」이라고 말해 놓고 화면은 그 반대가
            #   된다 — 260828 U 실사용: 범위 밖 질의에 무관 조문 전문 23KB.
            "actions": [f"이 도구로 답할 수 있는 것은 {_LAWS_LABEL} 안의 주제예요.",
                        "거래소 규정은 KRX 홈페이지 또는 국가법령정보센터에서 확인하셔야 해요."],
        }
    if law_filter and not candidates:
        law_txt = ", ".join(law_filter)
        return {
            "type": "filtered_empty",
            "message": f"'{law_txt}' 안에서는 맞는 조문을 못 찾았어요.",
            "actions": ["법 지정(law=)을 빼고 전체에서 찾아보거나, 다른 법으로 바꿔 물어봐 주세요."],
        }
    if not refs and not tokens:
        return {
            "type": "too_vague",
            "message": "법령 용어를 알아보지 못했어요. 조문번호나 아래 같은 용어로 다시 물어봐 주시면 찾아드릴게요.",
            "actions": ["예: 제388조 · 집중투표 · 자기주식 · 대량보유 · 감사위원"],
        }
    if tokens and not has_anchor:
        return {
            "type": "too_generic",
            "message": "'이사'·'주식'처럼 넓은 단어는 걸리는 조문이 너무 많아요. 조금만 좁혀 주시면 정확히 찾아드릴게요.",
            "actions": ["예: '이사 보수한도' · '자기주식 소각' · '집중투표 배제' 또는 조문번호를 넣어 주세요."],
        }
    if shown and all(c["record"].get("deleted") for c in shown):
        return {
            "type": "deleted_only",
            "message": "찾은 조문이 모두 지금은 삭제된 조문이에요. 대체된 현행 조문은 개정 연혁에서 확인하시는 게 좋아요.",
            "actions": ["개정 뒤 바뀐 조문번호로 다시 물어봐 주세요."],
        }
    if shown:
        return {
            "type": "weak_match",
            "message": "딱 맞는 조문은 못 찾고 비슷한 후보만 나왔어요. 아래는 참고로 봐 주세요.",
            "actions": ["더 정확히 찾으려면 조문번호를 직접 넣거나, 용어를 조금 더 구체적으로 바꿔 주세요."],
        }
    return {
        "type": "no_match",
        "message": "조건에 맞는 조문을 찾지 못했어요.",
        "actions": [f"용어를 바꾸거나 조문번호로 다시 물어봐 주세요. 지금 찾을 수 있는 법은 {_LAWS_LABEL}이에요."],
    }


def build_law_lookup_payload(
    query: str,
    *,
    direction: str = "auto",
    law: str = "",
    as_of: str = "",
    include_full_text: bool = True,
    top_k: int = 10,
    format: str = "md",
) -> dict[str, Any]:
    q = (query or "").strip()
    warnings: list[str] = []
    as_of_iso = as_of.strip() or date.today().isoformat()
    _t0 = time.perf_counter()  # DART 0콜 인메모리 매칭 — 병목 관측용(실측 warm ~1ms/query, 인덱스 전역캐시)

    idx = load_index()
    if not idx.get("articles"):
        return ToolEnvelope(
            tool="law_lookup", status=AnalysisStatus.ERROR, subject=q,
            warnings=["law corpus 인덱스가 없습니다 — scripts/sync_law_corpus.py 실행 필요."],
            data={"usage": build_usage(0)},
        ).to_dict()

    if not q:
        return ToolEnvelope(
            tool="law_lookup", status=AnalysisStatus.REQUIRES_REVIEW, subject=q,
            warnings=["query가 비었습니다."],
            next_actions=["조문번호(예: 제542조의8) 또는 키워드/정관 조항 텍스트를 넣으세요."],
            data={"usage": build_usage(0)},
        ).to_dict()

    laws = detect_laws(q, law)
    bogus_law = False
    if law and not any(lw in _KNOWN_LAWS for lw in laws):
        bogus_law = True
        warnings.append(f"law='{law}'는 인식되지 않는 법령입니다 — 지원: {', '.join(sorted(_KNOWN_LAWS))} "
                        f"(시행령 포함). 필터를 무시하고 전체 검색합니다.")
        laws = []
    law_filter = laws if (law or (laws and _has_explicit_law(q))) else None
    refs = extract_article_refs(q)
    tokens = expand_query_tokens(extract_tokens(q), as_of_iso)

    # direction 결정
    resolved_dir = direction
    if direction == "auto":
        resolved_dir = "law_to_clause" if (refs or _has_explicit_law(q)) else "clause_to_law"

    candidates = _fuse(q, tokens, refs, laws, resolved_dir, law_filter)

    # 조문번호 중복(법령 미지정) → ambiguous
    collision = False
    collision_laws: list[str] = []
    if refs and not laws and not law:
        distinct_laws = {c["record"].get("law_short") for c in candidates
                         if any(c["record"].get("article_int") == r["article_int"] for r in refs)}
        if len(distinct_laws) > 1:
            collision = True
            collision_laws = sorted(lw for lw in distinct_laws if lw)

    total = len(candidates)
    shown = candidates[:max(1, top_k)]
    # "상위 N건만 표시" 경고는 **자르기가 다 끝난 뒤**에 낸다(아래 약한-매칭 트림이 N을 더 줄인다).

    # status 도출
    _meta = idx.get("meta", {})
    _df = _meta.get("df", {})
    _anchor_max = _meta.get("anchor_df_max", 0)
    has_anchor = any(_df.get(t, 0) <= _anchor_max for t in tokens if t in _df)
    if not candidates:
        # 앵커(희소) 토큰이 있으면 '못 찾음'(partial), 두루뭉술(앵커 0)이면 requires_review
        status = AnalysisStatus.PARTIAL if has_anchor else AnalysisStatus.REQUIRES_REVIEW
    elif collision:
        status = AnalysisStatus.AMBIGUOUS
    elif any(c["e"] or c["b"] for c in shown):
        status = AnalysisStatus.EXACT
    elif any(c["score"] >= TAU_STRONG for c in shown):
        status = AnalysisStatus.EXACT
    else:
        status = AnalysisStatus.AMBIGUOUS

    # ── 폴백 유형을 **직렬화 前에** 분류한다 ────────────────────────────────────
    # 종전에는 조문 전문을 다 만든 뒤에 분류했다. 그래서 「범위 밖이에요」·「용어를 알아보지
    # 못했어요」라고 말하면서 동시에 조문 전문 20건을 붙여 내보냈다(260828 U 실사용 A-4:
    # md 23,513자). 무엇을 붙일지는 **왜 약한지를 알고 난 뒤**에 정해야 한다.
    exact_hits = any(c["e"] for c in candidates)
    fallback = _classify_fallback(
        q=q, tokens=tokens, refs=refs, law_filter=law_filter,
        candidates=candidates, shown=shown, collision=collision, collision_laws=collision_laws,
        has_anchor=has_anchor, exact_hits=exact_hits)
    ftype = (fallback or {}).get("type")

    # 범위 밖 주제 → 조문을 **아예 붙이지 않는다.** 어휘만 겹친 후보라 붙일수록 해롭다.
    if ftype == "out_of_corpus_topic":
        shown = []
        status = AnalysisStatus.REQUIRES_REVIEW
    # 용어를 못 알아본 경우 → 표까지만. 화면을 한참 넘기게 만드는 것이 전문이고,
    #   그 전문이 질의와 무관하면 넘긴 만큼이 통째로 손해다.
    elif ftype in _NO_FULLTEXT_FALLBACKS:
        shown = shown[:_NO_FULLTEXT_SHOW_MAX]
        include_full_text = False
    trimmed_weak = ftype in _NO_FULLTEXT_FALLBACKS
    # 용어는 알아봤으나 약한 매칭 → 전문은 상위 몇 건만.
    limit_ft = _LIMIT_FULLTEXT_TOPN if ftype in _LIMIT_FULLTEXT_FALLBACKS else None
    # 강한 매칭(exact)이라도 전문은 **강매칭 전부 + 최소 3건**까지만. 그 아래 꼬리는 표로만.
    #   260902 실측 — 「적기시정조치 요건」은 1·2위(0.75·0.68)가 금산법 §10·§11 인데, 3위부터
    #   0.38 아래로 절벽이고 그 뒤 6건은 「시정조치」 글자만 겹친 공정거래법 조문이었다. 그런데
    #   status=exact 라는 이유로 10건 전부에 전문이 붙어 21KB 가 나갔다. 전문 10건 통째 붙이기는
    #   4법 시절에도 있었지만 코퍼스가 10법이 되며 꼬리에 **다른 법**이 섞이기 시작했다.
    #   약한 매칭에 이미 쓰는 자(_LIMIT_FULLTEXT_TOPN)와 같은 자를 쓴다 — 강한 것은 다 주고,
    #   약한 꼬리는 약한 매칭과 같이 다룬다. 표에는 그대로 남으므로 「못 찾는」 일은 없다.
    if limit_ft is None and ftype is None and shown:
        n_strong = sum(1 for c in shown if c["score"] >= TAU_STRONG)
        keep = max(n_strong, _LIMIT_FULLTEXT_TOPN)
        if keep < len(shown):
            limit_ft = keep
    if total > len(shown) and shown:      # 통째로 뺀 경우(범위 밖)는 위 안내가 이미 이유를 말한다
        warnings.append(f"후보 {total}건 중 상위 {len(shown)}건만 표시"
                        + (f" (용어를 못 알아봐 {_NO_FULLTEXT_SHOW_MAX}건으로 줄임)." if trimmed_weak
                           else f"(top_k={top_k})."))

    # 후보 직렬화
    results = []
    version_warned: set[str] = set()  # 전문 시행예정본 경고는 법령당 1회만
    for _i, c in enumerate(shown):
        rec = c["record"]
        tail = limit_ft is not None and _i >= limit_ft   # 약한 매칭의 꼬리 — 표에만 남긴다
        item = _record_public(rec, include_full_text=include_full_text and not tail,
                              as_of_iso=as_of_iso)
        if trimmed_weak or tail:
            item["hang"], item["ho"] = [], {}   # 표만 남긴다 — 약한 후보의 본문은 노이즈다
        item["score"] = c["score"]
        item["signals"] = sorted(c["signals"])
        # ① 진짜 조문별 미래시행·유예는 SSOT(조항 대장)를 **조문번호로 직접** 맞춰 항 단위로 표시.
        #    bridge 유무·룰 순서와 무관하다(260903 — 종전엔 bridge 첫 룰만 봐서 조문번호 조회에
        #    표지가 전혀 안 붙었다). 표에만 남기는 약한 후보(hang 비움)에도 flags 는 남긴다.
        _apply_provision_gates(item, provision_gates(rec, as_of_iso))
        # ② 전문(스냅샷) 자체가 시행예정본이면 조문별 현행여부 불명 — 확인필요 + 법령당 1회 경고.
        #    ①과 독립이다: 스냅샷이 미래본이어도 SSOT 가 아는 항의 시행 상태는 그대로 말한다.
        if item["in_force"] is None:
            item.setdefault("flags", []).append("현행 여부 확인필요(전문 시행예정본)")
            lw = rec.get("law_short") or ""
            if lw not in version_warned:
                version_warned.add(lw)
                warnings.append(
                    f"'{rec.get('law_name') or lw}' 원문은 {rec.get('enforcement')} 시행 예정본 스냅샷입니다 — "
                    f"전문 시행일을 조문 전체에 적용하지 않습니다. 개별 조문의 현행 여부는 별도 확인 필요.")
        if rec.get("deleted"):
            item.setdefault("flags", []).append(f"삭제된 조문({rec.get('deleted_date') or ''})")
        if c.get("bridge"):
            item["bridge"] = {"rules": c["bridge"]["rules"], "decisions": c["bridge"]["decisions"]}
        # 방향 B: 조문 → 관련 룰 역방향
        if resolved_dir == "law_to_clause" or c["e"]:
            rev = _reverse_bridge(rec)
            if any(rev.values()):
                item["related"] = {
                    "정관_변경유형": rev["A"], "우회_시나리오": rev["B"], "주총_안건신호": rev["C"],
                }
        results.append(item)

    evidence_refs = []
    meta = idx.get("meta", {})
    seen_laws = {r["law"] for r in results}
    for lw in seen_laws:
        linfo = next((L for L in meta.get("laws", []) if L.get("law_short") == lw), {})
        evidence_refs.append(EvidenceRef(
            evidence_id=f"law_corpus:{lw}", source_type=SourceType.INTERNAL,
            section="law_corpus",
            note=f"{linfo.get('law_name', lw)} 법령ID {linfo.get('law_id', '')} "
                 f"@commit {meta.get('source_commit', '')[:8]} 시행 {linfo.get('enforcement', '')}",
        ))

    # 폴백 안내 배치 (문구는 warnings 맨 앞, 행동은 next_actions). 분류 자체는 위에서 끝났다.
    next_actions: list[str] = []
    if fallback:
        warnings.insert(0, fallback["message"])  # 사람이 읽는 친근한 문구(유형 태그는 data.fallback.type에)
        next_actions.extend(fallback["actions"])
    if trimmed_weak and results:
        warnings.insert(1, f"강한 매칭이 아니라 **조문 전문을 붙이지 않았습니다** — 아래 {len(results)}건은 "
                           f"어휘가 겹친 참고 후보입니다(전체 {total}건). 전문이 필요하면 조문번호"
                           f"(예: 제542조의8)로 다시 물어보세요.")
    elif limit_ft is not None and len(results) > limit_ft and ftype is None:
        # exact 인데 꼬리를 잘랐다 — 「못 찾았다」가 아니라 「찾았고, 약한 꼬리만 표로 남겼다」.
        #   260902 live 실측: 금산법 §10 을 정확히 짚고도 「딱 맞는 조문을 짚지 못해」라고 말했다.
        warnings.insert(1, f"강하게 맞는 조문에는 전문을 붙였고(상위 {limit_ft}건), 그 아래 어휘만 겹친 "
                           f"후보 {len(results) - limit_ft}건은 표에만 있습니다. 전문이 더 필요하면 "
                           f"조문번호로 다시 물어보세요.")
    elif limit_ft is not None and len(results) > limit_ft:
        warnings.insert(1, f"딱 맞는 조문을 짚지 못해 **전문은 상위 {limit_ft}건만** 붙였습니다 — "
                           f"나머지는 표에만 있습니다. 전문이 더 필요하면 조문번호로 다시 물어보세요.")
    next_actions.append("정관 본문↔안건 판단은 proxy_advise_before_meeting")

    fresh = corpus_freshness(as_of_iso)
    if fresh["stale"]:
        warnings.append(
            f"법령 자료가 {fresh['asof']} 기준({fresh['age_days']}일 전)이에요 — 그 뒤 개정은 아직 안 담겼을 수 있어요. "
            f"(원문 소스가 갱신을 멈췄거나, 온전성 게이트가 결함 스냅샷을 막고 있을 수 있어요)")

    data = {
        "query": q, "direction": resolved_dir, "as_of": as_of_iso,
        "law_filter": law_filter, "detected_laws": laws,
        "query_tokens": sorted(tokens), "article_refs": refs,
        "total_candidates": total, "results": results,
        # 전문을 뺐는지/후보를 통째로 뺐는지를 payload 에도 남긴다 — md 만 보고 "조문이 없다"로
        # 읽히면 안 된다. 없는 것이 아니라 **약해서 안 붙인 것**이다.
        "full_text_suppressed": bool(trimmed_weak) or ftype == "out_of_corpus_topic",
        "full_text_limited_to": limit_ft,
        "results_suppressed": ftype == "out_of_corpus_topic",
        "fallback": fallback,  # None이면 깨끗한 정답
        "corpus_asof": fresh["asof"], "corpus_age_days": fresh["age_days"],
        "usage": build_usage(0),
        "timing_ms": {"build": round((time.perf_counter() - _t0) * 1000, 1)},
    }
    return ToolEnvelope(
        tool="law_lookup", status=status, subject=q,
        warnings=warnings, data=data, evidence_refs=evidence_refs, next_actions=next_actions,
    ).to_dict()


def _has_explicit_law(text: str) -> bool:
    norm = normalize(text)
    return any(normalize(a) in norm for a, _ in LAW_ALIASES)
