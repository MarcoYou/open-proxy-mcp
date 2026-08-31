#!/usr/bin/env python3
"""wiki_index.md 폴더-앵커 카운트 자동 동기화 (생성기).

260712 패널 결정(Musk 최고 레버리지 항목): 손으로 유지하던 인덱스 카운트를 filesystem에서
파생시켜 count-drift 버그 클래스를 제거한다. 이 생성기는 `wiki_lint`에서 카운트 추출 정규식과
`_direct_md_count`를 **그대로 import**하므로, 생성기와 lint [4]는 같은 로직을 공유해 절대
불일치하지 않는다(= generate-then-verify가 항상 일치).

다루는 대상(= lint [4]가 검사하는 것과 정확히 동일 집합):
- 폴더-앵커 헤더 `### X (N) - `folder/`` 의 N (괄호 안이 순수 숫자일 때만 — `(20 진입점)`처럼
  큐레이션 라벨이 붙은 카운트는 정규식이 매칭 안 하므로 건드리지 않는다)
- `총 N markdown` 총계

건드리지 않는 것(의도적): Data(14)·시스템 설계(6)·Tools(20 진입점) 등 **선별 목록 수**(폴더 파생이
아니라 큐레이션된 의미). 카테고리 인벤토리 표의 compound 카운트(`29 binary + 4 md` 등)도 v1 범위 밖.

사용:
    python3 scripts/gen_index.py          # 카운트 재계산해 wiki_index.md 갱신
    python3 scripts/gen_index.py --check  # 갱신 필요하면 exit 1 (파일 안 고침, CI용)
"""

from __future__ import annotations

import argparse
import sys

# scripts/ 가 sys.path[0] — 같은 폴더의 wiki_lint에서 SSOT 로직을 재사용(DRY).
from wiki_lint import (
    INDEX_MD,
    HEADER_FOLDER_COUNT,
    TOTAL_CLAIM,
    collect_pages,
    _direct_md_count,
)


def sync_counts(text: str, pages) -> str:
    """text 안의 폴더-앵커 카운트를 filesystem 실측으로 재계산해 반환.

    digit span만 정확히 splice(리버스 순서)하므로 헤더의 다른 내용은 불변.
    """
    edits: list[tuple[int, int, str]] = []  # (start, end, new_digits)

    for m in HEADER_FOLDER_COUNT.finditer(text):
        actual = _direct_md_count(m.group(2), pages)  # group(2)=folder
        if actual >= 0:
            s, e = m.span(1)  # group(1)=count digits
            edits.append((s, e, str(actual)))

    for m in TOTAL_CLAIM.finditer(text):
        s, e = m.span(1)
        edits.append((s, e, str(len(pages))))

    for s, e, val in sorted(edits, reverse=True):
        text = text[:s] + val + text[e:]
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="wiki_index.md 카운트 자동 동기화")
    ap.add_argument("--check", action="store_true",
                    help="갱신 필요하면 exit 1 (파일 안 고침, CI용)")
    args = ap.parse_args()

    if not INDEX_MD.exists():
        print(f"✗ {INDEX_MD} 없음")
        sys.exit(1)

    pages = collect_pages()
    orig = INDEX_MD.read_text(encoding="utf-8")
    new = sync_counts(orig, pages)

    if new == orig:
        print("✓ wiki_index.md 카운트 동기 상태 (변경 없음)")
        return

    if args.check:
        print("✗ wiki_index.md 카운트가 filesystem과 어긋남 — `python3 scripts/gen_index.py` 실행 필요")
        sys.exit(1)

    INDEX_MD.write_text(new, encoding="utf-8")
    print("✓ wiki_index.md 카운트 재동기화 완료")


if __name__ == "__main__":
    main()
