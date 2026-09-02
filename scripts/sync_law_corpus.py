#!/usr/bin/env python3
"""law_lookup corpus 동기화 + 인덱스 빌드 (dev-time, prod 아님).

legalize-kr(github.com/MarcoYou/legalize-kr) 원문에서 거버넌스 핵심 4법(각 법률+시행령)을
`wiki/rules/laws/corpus/`로 vendored 복사 + 조 단위 인덱스(`law_index.json`) + 재현성 manifest 생성.

토큰화는 `open_proxy_mcp.services.law_lookup`의 primitive(normalize/extract_tokens)를 import해
**런타임 질의와 동일 로직**으로 맞춘다(인덱스≡질의 정합).

corpus 경로: --corpus 인자 또는 OPM_LEGALIZE_KR 환경변수. 없으면 graceful-skip(exit 0) — CI 안 깸.

용례:
  python scripts/sync_law_corpus.py --corpus D:/Projects/legalize-kr
  OPM_LEGALIZE_KR=~/legalize-kr python scripts/sync_law_corpus.py --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:  # Windows cp949 콘솔에서도 한글·기호 출력 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from open_proxy_mcp.services.law_lookup import (  # noqa: E402
    CIRCLED_TO_INT,
    extract_tokens,
    morph_tokens,
    normalize,
)

CORPUS_DIR = ROOT / "wiki" / "rules" / "laws" / "corpus"

# (source 폴더, law_short) — 각 폴더의 법률.md·시행령.md 대상
TARGETS = [
    # ── 거버넌스 핵심 4법 (260714~) ──
    ("상법", "상법"),
    ("자본시장과금융투자업에관한법률", "자본시장법"),
    ("독점규제및공정거래에관한법률", "공정거래법"),
    ("주식회사등의외부감사에관한법률", "외부감사법"),
    # ── 260902 확장 ──
    #
    # 왜 넓혔나 — 4법만으로는 **은행·보험·지주의 지배구조**와 **승계·세제**를 못 짚는다.
    # 실측(260902): 「금융회사 지배구조법 사외이사 자격」·「상증세법 최대주주 할증평가」가
    # 코퍼스에 없어 어휘만 겹친 딴 법 조문이 후보로 올라왔다.
    #
    # 🔴 넣은 법은 `law_lookup._OUT_OF_CORPUS_STATUTES` 에서 **빼야 한다.** 안 빼면
    #    코퍼스에 있는데도 「범위 밖」이라고 답한다 — 있는 것을 없다고 말하는 쪽이 더 나쁘다.
    ("금융회사의지배구조에관한법률", "지배구조법"),
    ("상속세및증여세법", "상증세법"),
    ("금융지주회사법", "금융지주회사법"),
    ("금융산업의구조개선에관한법률", "금산법"),
    ("은행법", "은행법"),
    ("보험업법", "보험업법"),
]
FILE_KINDS = ["법률", "시행령"]

# ── 파싱 정규식 ─────────────────────────────────────────────────────────
HEAD_RE = re.compile(r"^(#{1,5})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
ART_HEAD_RE = re.compile(r"^(제(\d+)조(?:의(\d+))?)(?:[ \t]*\((.+)\))?[ \t]*$")
HANG_RE = re.compile(r"^\*\*(.)\*\*[ \t]*(.*)$")
HO_RE = re.compile(r"^(\d+)\\?\.[ \t]*(.*)$")
AMEND_RE = re.compile(r"<개정\s*([0-9.,·\s]+)>")
DELETE_RE = re.compile(r"<삭제\s*([0-9.]+)>")
MARKER_RE = re.compile(r"<(?:개정|삭제|신설|본조신설|제목개정)[^>]*>")
DATE_TOK_RE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")


def _parse_dates(s: str) -> list[str]:
    out = []
    for y, m, d in DATE_TOK_RE.findall(s or ""):
        out.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    return out


def _clean(text: str) -> str:
    """개정/삭제/신설 마커 제거 (토큰·판정용 — 원문 .md는 verbatim 보존)."""
    return MARKER_RE.sub("", text or "").strip()


def parse_frontmatter(text: str) -> tuple[dict, int]:
    """YAML 프론트매터 최소 파싱 (필요 키만). (dict, body_start)."""
    fm: dict[str, str] = {}
    if not text.startswith("---"):
        return fm, 0
    end = text.find("\n---", 3)
    if end < 0:
        return fm, 0
    block = text[3:end]
    for line in block.splitlines():
        m = re.match(r"^([^:\s][^:]*):\s*(.*)$", line)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip().strip("'\"")
            if k and v:
                fm[k] = v
    return fm, end + len("\n---")


def parse_law_md(text: str) -> list[dict]:
    """법령 .md → 조 단위 레코드(법령 메타 제외). char offset은 text 기준."""
    heads = [(len(m.group(1)), m.group(2).strip(), m.start(), m.end())
             for m in HEAD_RE.finditer(text)]
    records: list[dict] = []
    crumb: dict[int, str] = {}
    for i, (level, title, start, end) in enumerate(heads):
        if level < 5:
            # 편/장/절/관 breadcrumb — '제'로 시작하는 것만(법령 제목 라인 스킵)
            if title.startswith("제"):
                crumb[level] = title
                for L in [x for x in crumb if x > level]:
                    del crumb[L]
            continue
        am = ART_HEAD_RE.match(title)
        if not am:
            continue
        art_start = start
        art_end = heads[i + 1][2] if i + 1 < len(heads) else len(text)
        body = text[end:art_end]
        article_no = am.group(1)
        article_int = int(am.group(2))
        sub_int = int(am.group(3)) if am.group(3) else None
        article_title = (am.group(4) or "").strip()

        hang: list[dict] = []
        ho: dict[str, list[dict]] = {}
        cur_hang = None
        for raw_ln in body.splitlines():
            s = raw_ln.strip()
            if not s:
                continue
            mh = HANG_RE.match(s)
            if mh and mh.group(1) in CIRCLED_TO_INT:
                num = CIRCLED_TO_INT[mh.group(1)]
                txt = _clean(mh.group(2))
                hang.append({"no": num, "text": txt,
                             "deleted": normalize(txt).startswith("삭제")})
                cur_hang = str(num)
                continue
            mo = HO_RE.match(s)
            if mo:
                ho.setdefault(cur_hang or "0", []).append(
                    {"no": int(mo.group(1)), "text": _clean(mo.group(2))})
                continue

        # 조-level 삭제 탐지 — corpus 실제 포맷은 '삭제 <2010.5.14>'(단어 밖·날짜 안).
        # '<삭제 날짜>' 변형도 함께 대응. (260713 멀티에이전트 적발: 0/2725 오탐)
        body_no_dates = re.sub(r"<[0-9.\s]+>", "", body)  # 날짜만 든 <> 제거
        norm_body = normalize(_clean(body_no_dates))
        deleted = norm_body.startswith("삭제") and len(norm_body) <= 4
        dm = (re.search(r"삭제\s*<\s*([0-9.\s]+)>", body)
              or re.search(r"<삭제\s*([0-9.]+)>", body))
        del_dates = _parse_dates(dm.group(1)) if dm else []
        amend_dates = []
        for chunk in AMEND_RE.findall(body):
            amend_dates.extend(_parse_dates(chunk))

        token_src = f"{article_title} {_clean(body)}"
        records.append({
            "article_no": article_no, "article_int": article_int, "sub_int": sub_int,
            "article_title": article_title,
            "path": [crumb[L] for L in sorted(crumb) if L < 5],
            "hang": hang, "ho": ho,
            "deleted": deleted, "deleted_date": (del_dates[0] if deleted and del_dates else None),
            "amended_dates": sorted(set(amend_dates)),
            "tokens": sorted(extract_tokens(token_src)),
            "title_tokens": sorted(extract_tokens(article_title)),
            "char_start": art_start, "char_end": art_end,
        })
    return records


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_commit(base: Path) -> tuple[str, str]:
    try:
        sha = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        dt = subprocess.run(["git", "-C", str(base), "log", "-1", "--format=%cI"],
                            capture_output=True, text=True).stdout.strip()
        return sha, dt
    except Exception:
        return "", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.environ.get("OPM_LEGALIZE_KR"))
    ap.add_argument("--strict", action="store_true",
                    help="대상 파일 누락 시 비정상 종료")
    args = ap.parse_args()

    if not args.corpus:
        print("SKIP: legalize-kr corpus 경로 미지정(--corpus 또는 OPM_LEGALIZE_KR). 생성 생략.")
        return 0
    base = Path(args.corpus).expanduser()
    if not (base / "kr").exists():
        print(f"SKIP: corpus에 kr/ 없음 ({base}). 경로 확인.")
        return 0

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    commit_sha, commit_dt = _source_commit(base)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    all_articles: list[dict] = []
    bm25_docs: list[dict] = []  # 조문 전문 형태소 BM25 (Signal C) — law_bm25.json
    laws_meta: list[dict] = []
    manifest_files: list[dict] = []
    missing: list[str] = []

    for folder, law_short in TARGETS:
        for kind in FILE_KINDS:
            src = base / "kr" / folder / f"{kind}.md"
            if not src.exists():
                missing.append(f"{folder}/{kind}.md")
                print(f"  ⚠️ 없음: {src}")
                continue
            text = src.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
            law_key = law_short if kind == "법률" else f"{law_short}시행령"
            rel = f"{folder}/{kind}.md"

            # vendored 복사 (verbatim)
            dst = CORPUS_DIR / folder / f"{kind}.md"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text, encoding="utf-8")

            recs = parse_law_md(text)
            law_name = fm.get("제목", f"{law_short} {kind}")
            law_tier = 1 if kind != "법률" else 0  # 법률(0) < 시행령(1) — governing 법률 우선 랭크
            for r in recs:
                r.update({
                    "id": f"{law_key}:{r['article_no']}",
                    "law_key": law_key, "law_short": law_short, "law_name": law_name,
                    "law_tier": law_tier,
                    "law_id": fm.get("법령ID", ""), "law_mst": fm.get("법령MST", ""),
                    "promulgation": fm.get("공포일자", ""), "enforcement": fm.get("시행일자", ""),
                    "file": rel,
                })
            all_articles.extend(recs)
            # BM25: 조문 전문(heading+body) 형태소 tf. 런타임 질의와 동일 morph_tokens.
            for r in recs:
                if r.get("deleted"):
                    continue  # 삭제 조문은 corpus 매칭 대상 아님
                span = text[r["char_start"]:r["char_end"]]
                toks = morph_tokens(span)
                if not toks:
                    continue
                tf: dict[str, int] = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                bm25_docs.append({
                    "k": [r["law_key"], r["article_no"]], "ls": law_short,
                    "tf": {t: tf[t] for t in sorted(tf)}, "dl": len(toks),
                })
            law_meta = {
                "law_key": law_key, "law_short": law_short, "law_name": law_name,
                "law_id": fm.get("법령ID", ""), "law_mst": fm.get("법령MST", ""),
                "promulgation": fm.get("공포일자", ""), "enforcement": fm.get("시행일자", ""),
                "status": fm.get("상태", ""), "file": rel, "n_articles": len(recs),
            }
            laws_meta.append(law_meta)
            manifest_files.append({
                "law_key": law_key, "rel_path": rel, "sha256": _sha256(text),
                "frontmatter": {k: fm.get(k, "") for k in
                                ("제목", "법령ID", "법령MST", "공포일자", "시행일자", "상태")},
            })
            print(f"  ✓ {law_key:14s} {len(recs):4d}조  ({rel})")

    if missing and args.strict:
        print(f"\nFAIL(strict): 대상 파일 {len(missing)}건 누락: {missing}")
        return 1
    if not all_articles:
        print("FAIL: 파싱된 조문 0건.")
        return 1

    # df / idf / anchor — 키 정렬(결정적): set 순회는 해시 랜덤화로 실행마다 순서가 달라 가짜 diff 유발.
    N = len(all_articles)
    df_raw: dict[str, int] = {}
    for r in all_articles:
        for t in set(r["tokens"]):
            df_raw[t] = df_raw.get(t, 0) + 1
    df = {t: df_raw[t] for t in sorted(df_raw)}
    idf = {t: round(math.log(N / df[t]), 4) for t in sorted(df)}
    anchor_df_max = max(3, math.ceil(0.05 * N))
    for r in all_articles:
        r["anchor_tokens"] = [t for t in r["tokens"] if df.get(t, 0) <= anchor_df_max]

    # NOTE: synced_at(벽시계)은 index에 넣지 않는다 — 넣으면 매 실행마다 색인이 바뀌어
    # 주간 자동 재복사(law-corpus-weekly)가 내용 무변화에도 커밋한다. 복사 시점은 _manifest.json에만.
    index = {
        "meta": {
            "version": "v1",
            "source_commit": commit_sha, "source_committed_date": commit_dt,
            "n_articles": N, "anchor_df_max": anchor_df_max,
            "df": df, "idf": idf, "laws": laws_meta,
        },
        "articles": all_articles,
    }
    (CORPUS_DIR / "law_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── BM25 인덱스(Signal C) — 결정적: docs 키정렬·tf키정렬·df키정렬 ──
    bm25_docs.sort(key=lambda d: (d["k"][0], d["k"][1]))
    bm_n = len(bm25_docs)
    bm_df_raw: dict[str, int] = {}
    for d in bm25_docs:
        for t in d["tf"]:
            bm_df_raw[t] = bm_df_raw.get(t, 0) + 1
    bm_df = {t: bm_df_raw[t] for t in sorted(bm_df_raw)}
    bm_avgdl = round(sum(d["dl"] for d in bm25_docs) / max(bm_n, 1), 4)
    bm_anchor = max(3, math.ceil(0.05 * bm_n))   # 두루뭉술 게이트: ≥2 형태소 or 희소
    bm_rare = max(3, math.ceil(0.022 * bm_n))    # 희소 단독 통과 임계(≈60 @2725) — 흔한 단독어 차단
    bm25 = {
        "meta": {
            "version": "v1", "source_commit": commit_sha,
            "n": bm_n, "avgdl": bm_avgdl, "k1": 1.5, "b": 0.75,
            "anchor_df_max": bm_anchor, "rare_df_max": bm_rare, "df": bm_df,
        },
        "docs": bm25_docs,
    }
    (CORPUS_DIR / "law_bm25.json").write_text(
        json.dumps(bm25, ensure_ascii=False, indent=1), encoding="utf-8")
    bm_mb = (CORPUS_DIR / "law_bm25.json").stat().st_size / 1e6
    print(f"  BM25: {bm_n}조 · 어휘 {len(bm_df)} · avgdl {bm_avgdl} · "
          f"anchor≤{bm_anchor} rare≤{bm_rare} · {bm_mb:.2f}MB")

    # 260817: source_repo 가 죽은 포크(MarcoYou/legalize-kr, 7-02 에 멈춤)를 가리키고
    #   있었다. 주간 배치도 같은 포크를 clone 해 6주간 초록불로 헛돌았다(7aa883c3).
    #   source_promulgated_date 는 **커밋일이 아닌 공포일** — 런타임이 자료 기준일로
    #   쓴다. 커밋일을 남겨두되 그건 재현용 좌표일 뿐 최신성 지표가 아니다.
    promulgated = max(
        (f["frontmatter"].get("공포일자", "")[:10] for f in manifest_files
         if f.get("frontmatter", {}).get("공포일자")), default="")
    manifest = {
        "source_repo": "github.com/legalize-kr/legalize-kr",
        "source_commit_sha": commit_sha, "source_committed_date": commit_dt,
        "source_promulgated_date": promulgated,
        "synced_at": now, "n_articles": N, "files": manifest_files,
    }
    (CORPUS_DIR / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    size_mb = (CORPUS_DIR / "law_index.json").stat().st_size / 1e6
    print(f"\nOK: {N}조 · {len(laws_meta)}개 법령파일 · anchor_df_max={anchor_df_max} · "
          f"index {size_mb:.2f}MB · commit {commit_sha[:8]}")
    if missing:
        print(f"  (누락 {len(missing)}건: {missing})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
