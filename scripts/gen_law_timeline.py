#!/usr/bin/env python3
"""상법 개정 '시행 타임라인' 표를 원본에서 자동생성한다.

원본  : open_proxy_mcp/data/laws/law_provisions.json  (시행일 SSOT · 260814 패키지로 이동)
대상  : wiki/rules/laws/상법-2025-2026-종합.md  의 AUTOGEN:law-timeline 마커 사이 표

원본을 고친 뒤 이 스크립트를 돌리면 md 표가 원본과 일치하게 다시 써진다.
표를 손으로 고치지 말 것 — wiki_lint.py[7]가 원본과 표의 불일치를 CI에서 실패시킨다.

사용:
  python3 scripts/gen_law_timeline.py            # md 표를 원본으로 갱신(쓰기)
  python3 scripts/gen_law_timeline.py --check     # 갱신 없이 일치 여부만(불일치 시 exit 1)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "open_proxy_mcp" / "data" / "laws" / "law_provisions.json"
DOC = REPO / "wiki" / "rules" / "laws" / "상법-2025-2026-종합.md"

START = "<!-- AUTOGEN:law-timeline START"
END = "<!-- AUTOGEN:law-timeline END -->"
# START 마커는 뒤에 설명이 붙으므로 줄 전체를 정규식으로 잡는다.
BLOCK_RE = re.compile(
    r"<!-- AUTOGEN:law-timeline START.*?-->\n(.*?)\n<!-- AUTOGEN:law-timeline END -->",
    re.DOTALL,
)


def render_table(registry: dict) -> str:
    """원본 provisions를 파일 순서 그대로 md 표 본문(헤더 포함)으로 렌더."""
    lines = ["| 시행일 | 차수 | 내용 | 적용 대상 |", "|---|---|---|---|"]
    for p in registry["provisions"]:
        lines.append(
            f"| {p['effective_date']} | {p['amendment_round_label']} | "
            f"{p['table_content']} | {p['table_applies_to']} |"
        )
    return "\n".join(lines)


def build_block(registry: dict) -> str:
    """마커를 포함한 전체 블록 문자열."""
    start_line = (
        "<!-- AUTOGEN:law-timeline START — 원본: law_provisions.json · "
        "생성기: scripts/gen_law_timeline.py · 이 표는 직접 수정하지 말고 원본을 고친 뒤 "
        "생성기를 돌린다 -->"
    )
    return f"{start_line}\n{render_table(registry)}\n{END}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="갱신 없이 일치만 검사(불일치 exit 1)")
    args = ap.parse_args()

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    doc = DOC.read_text(encoding="utf-8")

    if START not in doc or END not in doc:
        print(f"ERROR: {DOC.name}에 AUTOGEN:law-timeline 마커가 없다.", file=sys.stderr)
        return 2

    new_block = build_block(registry)
    updated = BLOCK_RE.sub(lambda _m: new_block, doc, count=1)

    if updated == doc:
        print("이미 최신 — 표가 원본과 일치한다.")
        return 0

    if args.check:
        print(f"불일치: {DOC.name}의 시행 타임라인 표가 원본과 다르다. "
              f"`python3 scripts/gen_law_timeline.py`로 갱신하라.", file=sys.stderr)
        return 1

    DOC.write_text(updated, encoding="utf-8")
    print(f"갱신 완료: {DOC.name} 시행 타임라인 표 ({len(registry['provisions'])}행).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
