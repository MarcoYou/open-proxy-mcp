#!/usr/bin/env python3
"""법령 corpus **온전성** 검사 — 조문이 있나가 아니라 조문이 온전한가를 묻는다.

260817 신설. 계기: 원문 소스(legalize-kr)가 자본시장법·공정거래법 **법률** 파일에서
「목」(가./나./다.…)을 통째로 누락한 채 갱신됐다. 공정거래법 §2조 정의가

    6\\. "임원"이란 다음 각 목의 어느 하나에 해당하는 사람을 말한다.
    7\\. "지주회사"란 ...                      ← 이사·대표이사·감사가 사라짐

로 끝난다. 「금융기관」·「기업집단」 정의도 같다. 법 자체는 그대로이고 원문 파일만
빠졌다(법제처 파싱 결함으로 보임 — 원본 레포 1,484개 파일 중 51개가 같은 증상).

**기존 게이트 셋이 전부 이걸 통과시켰다:**
  · 조문 수      137 → 137   (조 제목은 남아 있다)
  · verify_law_against_corpus  조문 *번호*만 대조
  · 공포일자 검사  8-04 > 5-12 이라 「최신」

셋 다 「있나」만 물었지 「온전한가」를 안 물었다. 그래서 이 검사가 필요하다.

판정: 「**다음** 각 목」이라 스스로 목을 여는 줄 바로 뒤에 목 줄이 오는가.
「제5조 각 목」처럼 남을 가리키는 상호참조는 세지 않는다(오탐 방지).
실측 교정 — 온전본 8개 파일 0%(432건 중 1건, 0.2%) / 손상본 두 파일 100%.
분리가 완전해서 임계값은 넉넉히 잡아도 된다.

용례:
  python3 scripts/check_law_corpus_integrity.py                  # 커밋된 corpus
  python3 scripts/check_law_corpus_integrity.py --corpus /tmp/legalize-kr  # 원문(kr/ 하위)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "wiki" / "rules" / "laws" / "corpus"

# 스스로 목을 여는 형태만. 「같은 항 각 목」·「제5조 각 목」은 남을 가리키므로 제외.
OPENS_MOK = re.compile(r"다음\s*각\s*목")
IS_MOK = re.compile(r"^\s*[가-하]\\?\.\s")

# 온전본 실측 0.2% · 손상본 100%. 사이가 텅 비어 있어 어디를 잘라도 같다.
FAIL_RATIO = 0.20
FAIL_MIN = 3  # 파일이 작아 1~2건일 때 비율만으로 흔들리지 않게


def dangling(text: str) -> tuple[int, int, list[str]]:
    """(목을 여는 줄 수, 그 중 목이 안 따라오는 수, 예시)"""
    lines = text.split("\n")
    total = bad = 0
    samples: list[str] = []
    for i, line in enumerate(lines):
        if not OPENS_MOK.search(line):
            continue
        total += 1
        nxt = next((x for x in lines[i + 1 : i + 4] if x.strip()), "")
        if not IS_MOK.match(nxt):
            bad += 1
            if len(samples) < 3:
                samples.append(line.strip()[:90])
    return total, bad, samples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", help="검사 대상 루트(미지정 시 커밋된 corpus)")
    args = ap.parse_args()

    base = Path(args.corpus).expanduser() if args.corpus else CORPUS
    # 원문 소스는 kr/ 하위에, 우리 corpus 는 평탄하게 둔다
    if (base / "kr").is_dir():
        base = base / "kr"
    if not base.is_dir():
        print(f"SKIP: 경로 없음 ({base})")
        return 0

    # 🔴 법 목록은 sync_law_corpus.TARGETS 가 단일 출처다 — 여기 따로 적지 않는다.
    #   260902 실측: 코퍼스를 4법 → 10법으로 넓혔는데 이 목록은 4법인 채였다. 스크립트는
    #   「OK: 8개 파일 온전」을 찍었고, 새 6법 12파일(「다음 각 목」 199건)은 검사 밖이었다.
    #   이 검사가 생긴 이유가 바로 원문 소스가 목을 통째로 잃은 사고인데, 그 사고가 새 법에서
    #   나면 아무도 못 잡는 상태였다. 목록을 복제하면 다시 갈라진다.
    sys.path.insert(0, str(ROOT / "scripts"))
    from sync_law_corpus import TARGETS  # noqa: E402  (source 폴더, law_short)
    laws = [folder for folder, _short in TARGETS]
    expected = len(laws) * 2
    print(f"=== 법령 corpus 온전성 검사 ({base}) — {len(laws)}법 {expected}파일 ===\n")
    print(f"  {'파일':34s} {'다음각목':>8s} {'목없음':>7s} {'비율':>6s}  판정")

    failed = []
    missing: list[str] = []
    checked = 0
    for law in laws:
        for kind in ("법률", "시행령"):
            p = base / law / f"{kind}.md"
            if not p.exists():
                missing.append(f"{law}/{kind}.md")
                continue
            checked += 1
            total, bad, samples = dangling(p.read_text(encoding="utf-8"))
            ratio = bad / total if total else 0.0
            hit = bad >= FAIL_MIN and ratio > FAIL_RATIO
            mark = "✗ 손상" if hit else "✓"
            print(f"  {law[:20]:20s}/{kind:4s} {total:8d} {bad:7d} {ratio:6.0%}  {mark}")
            if hit:
                failed.append((f"{law}/{kind}", total, bad, samples))

    if not checked:
        print("\n::error::검사한 파일이 0개 — 경로·구조가 바뀌었는지 확인")
        return 1
    # 커밋된 corpus 는 TARGETS 전부가 있어야 한다. 하나라도 비면 「온전」이 아니라 「일부만 봤다」다.
    #   원문 소스(--corpus) 쪽은 아직 안 받은 법이 있을 수 있어 경고만 한다.
    if missing:
        level = "warning" if args.corpus else "error"
        print(f"\n::{level}::{len(missing)}개 파일이 없어 검사하지 못했다: {', '.join(missing)}")
        if not args.corpus:
            return 1

    if failed:
        print(f"\n✗ FAIL: {len(failed)}개 파일에서 목(가./나./다.)이 누락됐다.")
        for name, total, bad, samples in failed:
            print(f"\n  [{name}] {total}건 중 {bad}건이 목 없이 끝난다:")
            for s in samples:
                print(f"      {s}")
        print("\n  원문 소스의 결함이다 — 반영하면 law_lookup 이 정의 조문을 반쪽만 돌려주고,")
        print("  40룰이 인용하는 조문의 하위 요건이 사라진다. 원문이 고쳐질 때까지 보류한다.")
        return 1

    print(f"\nOK: {checked}/{expected}개 파일 온전")
    return 0


if __name__ == "__main__":
    sys.exit(main())
