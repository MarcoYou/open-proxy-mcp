#!/usr/bin/env python3
"""law_provisions.json(SSOT) ↔ legalize-kr 원문 대조 검증 (a: SSOT 검증·보강).

목적: 손으로 관리하는 상법 개정 조항 대장(law_provisions.json)의 **조문번호·시행일**이 권위 원문
(legalize-kr `kr/상법/법률.md`·`시행령.md` 부칙)과 실제로 일치하는지 자동 대조한다. 에이전트 웹추정·
수기 편집 drift를 차단하고, 엔진이 인용하는 거버넌스 조문이 SSOT에 누락됐는지(§34조5항7호 등) 잡는다.

corpus 경로: --corpus 인자 또는 OPM_LEGALIZE_KR 환경변수. 없으면 graceful-skip(SKIP 종료코드 0)
— 남의 CI를 깨지 않는다(wiki_lint 패턴). repo: https://github.com/MarcoYou/legalize-kr

용례:
  OPM_LEGALIZE_KR=~/legalize-kr python3 scripts/verify_law_against_corpus.py
  python3 scripts/verify_law_against_corpus.py --corpus /path/to/legalize-kr [--strict]
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSOT = ROOT / "wiki" / "rules" / "laws" / "law_provisions.json"

# 엔진이 인용하지만 SSOT에 없을 수 있는 거버넌스/시장 핵심 조문(원문 대조 대상 = 보강 후보).
# (조문, 소속 법령파일, 설명) — 대조로 존재 확인해 SSOT 편입 여부를 판단.
REFERENCE_PROVISIONS = [
    ("제34조제5항제7호", "시행령", "사외이사 재직기간 결격(동일회사 6년/계열 9년) — proxy_advise 장기연임 인용"),
    ("제542조의8제2항제7호", "법률", "위 시행령의 위임 모법"),
]


def load_corpus(base: Path) -> dict[str, str]:
    out = {}
    for key, rel in (("법률", "kr/상법/법률.md"), ("시행령", "kr/상법/시행령.md")):
        p = base / rel
        out[key] = p.read_text(encoding="utf-8") if p.exists() else ""
    return out


def _article_tokens(article: str) -> list[str]:
    """'제529조의2·제530조의13' → ['제529조의2','제530조의13']. 항/호는 별도 확인."""
    return [a.strip() for a in re.split(r"[·,]", article) if a.strip()]


def _base_article(tok: str) -> str:
    """'제542조의8제1항' → '제542조의8' (조 단위, 항/호 접미 제거)."""
    m = re.match(r"(제\d+조(?:의\d+)?)", tok)
    return m.group(1) if m else tok


def verify_provision(p: dict, corpus: dict[str, str]) -> tuple[str, list[str]]:
    notes = []
    # 법률 vs 시행령 판별(현 SSOT는 전부 법률; article/threshold_decree에 '시행령' 있으면 시행령)
    blob = json.dumps(p, ensure_ascii=False)
    text = corpus["시행령"] if "시행령" in blob and "제34조" in blob else corpus["법률"]
    src = "시행령" if text is corpus["시행령"] else "법률"

    art_ok = True
    for tok in _article_tokens(p.get("article", "")):
        base = _base_article(tok)
        if base and base in text:
            notes.append(f"조문 {base} ✓({src})")
        else:
            art_ok = False
            notes.append(f"조문 {base} ✗ 원문 미발견({src})")

    # 시행일 대조: law_no(제20991호) 또는 effective_date가 부칙에 등장하는지(둘 중 하나면 통과)
    date_ok = True
    law_no = p.get("law_no") or ""
    eff = p.get("effective_date") or ""
    eff_ko = ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", eff)
    if m:
        eff_ko = f"{int(m.group(2))}. {int(m.group(3))}."  # 부칙 표기 '2025. 7. 22.' 유사
        eff_ko2 = f"{m.group(1)}년 {int(m.group(2))}월 {int(m.group(3))}일"
    hit = (law_no and law_no in text) or (eff_ko and eff_ko in text) or (eff_ko and eff_ko2 in text)
    if hit:
        notes.append(f"시행일/법령호 ✓ ({law_no or eff})")
    else:
        # 미래시행(2026~2027) 조항은 corpus 최신본에 부칙이 아직 없을 수 있음 → WARN(FAIL 아님)
        date_ok = False
        notes.append(f"시행일/법령호 △ 부칙 미발견({law_no or eff}) — corpus 최신성 확인 필요")

    status = "PASS" if (art_ok and date_ok) else ("ARTICLE_FAIL" if not art_ok else "DATE_WARN")
    return status, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.environ.get("OPM_LEGALIZE_KR"))
    ap.add_argument("--strict", action="store_true", help="ARTICLE_FAIL 있으면 비정상 종료")
    args = ap.parse_args()

    if not args.corpus:
        print("SKIP: legalize-kr corpus 경로 미지정(--corpus 또는 OPM_LEGALIZE_KR). 검증 생략.")
        return 0
    base = Path(args.corpus).expanduser()
    if not (base / "kr" / "상법").exists():
        print(f"SKIP: corpus에 kr/상법/ 없음 ({base}). 경로 확인.")
        return 0

    corpus = load_corpus(base)
    ssot = json.loads(SSOT.read_text(encoding="utf-8"))
    provs = ssot["provisions"]
    print(f"=== SSOT {len(provs)}개 조항 ↔ legalize-kr 원문 대조 ===\n")

    fails = 0
    for p in provs:
        status, notes = verify_provision(p, corpus)
        mark = {"PASS": "✓", "DATE_WARN": "△", "ARTICLE_FAIL": "✗"}[status]
        print(f"{mark} [{status}] {p.get('provision_id')}")
        for n in notes:
            print(f"      {n}")
        if status == "ARTICLE_FAIL":
            fails += 1

    # 참조 조문(엔진 인용, SSOT 미편입) 존재 확인 → 보강 후보
    print(f"\n=== 엔진 인용 조문 원문 존재 확인(SSOT 편입 후보) ===")
    existing_articles = " ".join(p.get("article", "") for p in provs)
    for art, law, desc in REFERENCE_PROVISIONS:
        base_art = _base_article(art)
        text = corpus["시행령"] if law == "시행령" else corpus["법률"]
        in_corpus = base_art in text
        in_ssot = base_art in existing_articles
        flag = "원문✓ / SSOT " + ("있음" if in_ssot else "**누락 → 편입 검토**")
        print(f"  {'✓' if in_corpus else '✗'} {art} ({law}) — {flag}\n      {desc}")

    print(f"\n{'FAIL: 조문 불일치 '+str(fails)+'건' if fails else 'OK: 조문 대조 통과'}")
    return 1 if (args.strict and fails) else 0


if __name__ == "__main__":
    sys.exit(main())
