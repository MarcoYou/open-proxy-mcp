"""law_lookup — 정관↔법령 양방향 조회 (21번째 tool, company-agnostic·DART 0콜).

두 방향:
  A) 정관/자유텍스트 → 관련 법령 조문 (clause_to_law)
  B) 법령 조문번호/키워드 → 조문 전문 + 관련 정관 변경유형·우회·안건 (law_to_clause)
카디널리티 1:1 / N:1 / 1:N / N:N 전부 — τ_emit 이상 후보 전부 랭킹(first-match 아님).

데이터: legalize-kr 원문(상법·자본시장법·공정거래법·외부감사법 각 법률+시행령)을
`wiki/rules/laws/corpus/`에 vendored + `scripts/sync_law_corpus.py`가 만든 `law_index.json`.
매칭 3신호: E(정확 조문 튜플) + B(40룰 bridge, `_agenda_pattern_match` 재사용) + C(corpus 키워드,
idf·anchor 게이트). 보수적: substring 금지(튜플 exact), 폐쇄 어휘, false-friend guard, difflib 없음.

이 모듈의 토큰화 primitive(normalize/extract_tokens/load_synonyms)는 sync 스크립트가 import해
인덱스 빌드와 질의 정규화를 **동일 로직**으로 맞춘다.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date, datetime, timezone
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
    _law_provision_detail,
)

# ── 경로 ────────────────────────────────────────────────────────────────
_LAWS_DIR = Path(__file__).resolve().parent.parent.parent / "wiki" / "rules" / "laws"
_CORPUS_DIR = _LAWS_DIR / "corpus"
_INDEX_PATH = _CORPUS_DIR / "law_index.json"
_SYNONYMS_PATH = _LAWS_DIR / "law_lookup_synonyms.json"

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
    ("상법", "상법"),  # 마지막(가장 짧아 다른 매치 우선). '상법 시행령'도 여기서 상법으로.
]
_KNOWN_LAWS = {"상법", "자본시장법", "공정거래법", "외부감사법"}


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
        raw = json.loads(_SYNONYMS_PATH.read_text(encoding="utf-8"))
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
    }
    return _SYNONYMS_CACHE


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


def corpus_freshness(as_of_iso: str | None = None) -> dict[str, Any]:
    """법령 자료가 얼마나 최신인지 → {asof(원문 기준일), synced(복사시점), age_days, stale}."""
    m = load_manifest()
    src = (m.get("source_committed_date") or "")[:10]
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


def _signal_corpus(query_tokens: set[str], law_filter: list[str] | None) -> dict[tuple[str, str], float]:
    """corpus 키워드 유사도. anchor 게이트: 질의에 anchor(희소) 토큰 없으면 빈 결과."""
    idx = load_index()
    meta = idx.get("meta", {})
    idf: dict[str, float] = meta.get("idf", {})
    df: dict[str, float] = meta.get("df", {})
    anchor_max = meta.get("anchor_df_max", 0)
    # anchor 게이트
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
    for key, sim in _signal_corpus(query_tokens, law_filter).items():
        rec = _record_by_key(key)
        if not rec:
            continue
        s = _slot(rec)
        s["c"] = sim
        s["signals"].add("corpus")

    out = []
    for k, s in cand.items():
        score = 1.0 * s["e"] + 0.9 * s["b"] + 0.5 * s["c"]
        if s["e"] or s["b"]:
            pass  # exact/bridge는 항상 emit
        elif score < TAU_EMIT:
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
_LAWS_LABEL = "상법·자본시장법·공정거래법·외부감사법"


def _out_of_corpus_hit(q: str, tokens: set[str]) -> tuple[str, str] | None:
    """질의가 현재 4법 범위 밖 주제(차등의결권 등)를 가리키나 → (용어, 근거법령)."""
    ooc = load_synonyms().get("out_of_corpus", {})
    if not ooc:
        return None
    nq = normalize(q)
    for term, where in ooc.items():
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
            "message": f"'{term}'은 지금 담긴 4개 법에는 없는 주제예요. 관련 근거는 {where}에 있습니다.",
            "actions": [f"지금 찾을 수 있는 법은 {_LAWS_LABEL}이에요. 이 안의 비슷한 주제로 물어보셔도 돼요."],
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
    if total > len(shown):
        warnings.append(f"후보 {total}건 중 상위 {len(shown)}건만 표시(top_k={top_k}).")

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

    # 후보 직렬화
    results = []
    version_warned: set[str] = set()  # 전문 시행예정본 경고는 법령당 1회만
    for c in shown:
        rec = c["record"]
        item = _record_public(rec, include_full_text=include_full_text, as_of_iso=as_of_iso)
        item["score"] = c["score"]
        item["signals"] = sorted(c["signals"])
        # ① 진짜 조문별 미래시행은 SSOT(조항 대장)의 effective_date로만 단정.
        det = _law_provision_detail((c.get("bridge") or {}).get("rules", [None])[0]) if c.get("bridge") else None
        eff = (det or {}).get("effective_date") or ""
        if eff and eff > as_of_iso:
            note = f"미시행(시행 {eff})"
            if det.get("first_agm_trigger"):
                note += " · 시행 후 최초 이사선임 주총부터 적용(주총일 기준)"
            item.setdefault("flags", []).append(note)
        # ② 전문(스냅샷) 자체가 시행예정본이면 조문별 현행여부 불명 — 확인필요 + 법령당 1회 경고.
        elif item["in_force"] is None:
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

    # 폴백 유형 분류 → 유형별 안내(문구는 warnings 맨 앞, 행동은 next_actions).
    exact_hits = any(c["e"] for c in candidates)
    fallback = _classify_fallback(
        q=q, tokens=tokens, refs=refs, law_filter=law_filter,
        candidates=candidates, shown=shown, collision=collision, collision_laws=collision_laws,
        has_anchor=has_anchor, exact_hits=exact_hits)
    next_actions: list[str] = []
    if fallback:
        warnings.insert(0, fallback["message"])  # 사람이 읽는 친근한 문구(유형 태그는 data.fallback.type에)
        next_actions.extend(fallback["actions"])
    next_actions.append("정관 본문↔안건 판단은 proxy_advise_before_meeting")

    fresh = corpus_freshness(as_of_iso)
    if fresh["stale"]:
        warnings.append(
            f"법령 자료가 {fresh['asof']} 기준({fresh['age_days']}일 전)이에요 — 그 뒤 개정은 아직 안 담겼을 수 있어요. "
            f"(자동 재복사가 멈췄는지 확인 필요)")

    data = {
        "query": q, "direction": resolved_dir, "as_of": as_of_iso,
        "law_filter": law_filter, "detected_laws": laws,
        "query_tokens": sorted(tokens), "article_refs": refs,
        "total_candidates": total, "results": results,
        "fallback": fallback,  # None이면 깨끗한 정답
        "corpus_asof": fresh["asof"], "corpus_age_days": fresh["age_days"],
        "usage": build_usage(0),
    }
    return ToolEnvelope(
        tool="law_lookup", status=status, subject=q,
        warnings=warnings, data=data, evidence_refs=evidence_refs, next_actions=next_actions,
    ).to_dict()


def _has_explicit_law(text: str) -> bool:
    norm = normalize(text)
    return any(normalize(a) in norm for a, _ in LAW_ALIASES)
