#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""렌더러를 고친 뒤 산출물이 오염되지 않았는지 before/after 로 대조한다. DART 호출 0.

사용법: python3 scripts/diff_tool_output.py <before.json> <after.json>
  입력: [{"tool": ..., "company": ..., "text": <마크다운 산출물>}, ...]

왜 있나 (260728): 15개 파일에 일괄 치환을 넣었더니 `shareholder_meeting_notice` 가
import 누락으로 19,637자 → 100자 크래시가 됐다. 테스트 354개는 전부 통과했다 —
렌더 함수를 실제로 호출해 봐야 터지는 지연 오류였다.

무엇을 보나 (기계가 볼 수 있는 것만 — 의미 검수는 사람·에이전트 몫):
  ① 응답 소실·분량 급감   ② 숫자 소실(값이 사라졌나)   ③ 표 행 감소(표가 깨졌나)
  ④ 크래시 문자열

분량이 줄어드는 것 자체는 정상일 수 있다(헤더·라벨 축약). **숫자와 표 행이 유지되는지**가
오염 판정의 핵심이다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 금액·비율·배수 — 값이 사라지면 정보가 사라진 것이다
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d+\.\d+")
_CRASH = re.compile(r"Error executing tool|Traceback|\[JSONRPC-ERROR\]|is not defined")

SHRINK_RATIO = 0.7      # after 가 before 의 70% 미만이면 급감으로 본다


def load(p: str) -> dict[tuple[str, str], str]:
    rows = json.loads(Path(p).read_text(encoding="utf-8"))
    return {(r["tool"], r.get("company", "")): r["text"] for r in rows}


def table_rows(t: str) -> int:
    return sum(1 for ln in t.splitlines() if ln.startswith("|"))


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    before, after = load(sys.argv[1]), load(sys.argv[2])
    missing = sorted(set(before) - set(after))
    alarms: list[tuple] = []

    print(f"{'도구/회사':40s} {'before':>8} {'after':>8} {'Δ':>7} {'표행':>5}  판정")
    for k in sorted(before):
        tb, ta = before[k], after.get(k, "")
        nb, na = set(_NUM.findall(tb)), set(_NUM.findall(ta))
        rb, ra = table_rows(tb), table_rows(ta)
        lost = nb - na
        flag = ""
        if _CRASH.search(ta):
            flag = "❌ 크래시"
        elif not ta:
            flag = "❌ 응답 없음"
        elif len(ta) < len(tb) * SHRINK_RATIO:
            flag = f"❌ 분량 급감 ({len(ta)/max(len(tb),1)*100:.0f}%)"
        elif lost:
            flag = f"⚠ 숫자 소실 {len(lost)}건"
        elif ra < rb:
            flag = f"⚠ 표 행 감소 {rb - ra}"
        if flag:
            alarms.append((k, flag, sorted(lost)[:8]))
        label = f"{k[0][:22]}/{k[1][:14]}"
        print(f"{label:40s} {len(tb):>8,} {len(ta):>8,} {len(ta)-len(tb):>+7,} {ra:>5}  {flag}")

    if missing:
        print(f"\n❌ after 에 없는 조합 {len(missing)}건: {missing[:10]}")
    print(f"\n경보 {len(alarms)}건 / {len(before)}건")
    for k, f, lost in alarms:
        print(f"  {k[0]}/{k[1]}  {f}" + (f"  소실 예: {lost}" if lost else ""))
    if alarms or missing:
        sys.exit(1)
    print("✓ 오염 없음 — 숫자·표 행 유지, 크래시 0")


if __name__ == "__main__":
    main()
