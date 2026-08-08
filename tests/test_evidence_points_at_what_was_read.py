"""안건의 근거 공시는 **그 안건을 실제로 읽은 공고**를 가리켜야 한다.

접수번호는 `data.notice.rcept_no` 에 있는데 `data.rcept_no` 를 찾고 있어 항상 None 이었고,
그래서 **다른 도구(후보 평가)가 고른 공고로 폴백**했다. 주총이 잦은 회사(리츠 등)는 그게 아예
다른 회차다 — 실측 SK리츠: 안건은 20260602000425 에서 왔는데 근거는 20260304001363(3월 회차)을
가리켰다. 사용자가 그 링크를 열면 이 안건이 없다.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "open_proxy_mcp" / "services" / "proxy_advise.py"


def test_evidence_uses_the_notice_that_was_actually_parsed() -> None:
    src = _SRC.read_text(encoding="utf-8")
    m = re.search(r'"evidence_rcept_no":\s*(.+?),\n', src)
    assert m, "evidence_rcept_no 할당을 찾지 못했다"
    expr = m.group(1)
    # 실제로 문서를 받아 파싱한 접수번호(`agm_rcept`)를 쓴다
    assert "agm_rcept" in expr, expr
    # 다른 도구가 고른 회차로 폴백하지 않는다 — 회차가 다를 수 있다
    assert "director_data" not in expr, expr


def test_the_parsed_notice_is_the_one_we_fetched() -> None:
    """`agm_rcept` 는 소집공고 payload 의 notice 에서 오고, 그 번호로 문서를 받는다."""
    src = _SRC.read_text(encoding="utf-8")
    assert re.search(r'agm_rcept\s*=\s*notice_dict\.get\("rcept_no"\)', src)
    assert re.search(r'get_document_cached\(agm_rcept\)', src)
