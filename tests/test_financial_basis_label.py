"""「회사 재무 (참고)」 숫자에는 **어느 자로 쟀는지**가 붙어야 한다.

한 응답 안에 같은 이름의 숫자가 둘 나온다 — 이 블록은 정기보고서 기준이고,
안건 분석부는 소집공고·확정 재무제표 기준이다. 기준이 없으면 읽는 AI 가 둘 중
아무 거나 집는다. 2026-08-28 실측 태광산업 — 여기 「영업이익 -271억」,
안건부 「영업이익 -360억」. U 는 -360억을 집었고 채점기는 어긋남으로 표시했다.
"""

from __future__ import annotations

from open_proxy_mcp.tools.proxy_advise_before_meeting import _fin_basis_line


def test_basis_carries_year_and_consolidation() -> None:
    line = _fin_basis_line({"fiscal_year": 2025, "fs_div": "CFS", "reprt_code": "11011"})
    assert "2025 사업연도" in line
    assert "연결" in line
    assert "사업보고서(연간)" in line
    assert "지배주주" in line


def test_standalone_and_quarterly_are_named() -> None:
    line = _fin_basis_line({"year": 2026, "fs_div": "OFS", "reprt_code": "11014"})
    assert "별도" in line
    assert "3분기보고서(1~9월 누적)" in line


def test_non_december_fiscal_end_is_shown() -> None:
    line = _fin_basis_line({"fiscal_year": 2025, "fs_div": "CFS", "fiscal_year_end_month": 3})
    assert "3월 결산" in line


def test_missing_pieces_are_not_invented() -> None:
    """조각이 없으면 비워 둔다 — 「연결」을 기본값으로 지어내지 않는다."""
    line = _fin_basis_line({})
    assert "연결" not in line
    assert "사업연도" not in line
    assert line  # 최소한 순이익 기준은 남는다
