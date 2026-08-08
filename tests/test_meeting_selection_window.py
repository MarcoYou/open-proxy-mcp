"""회차 선택 구간은 「아직 열리지 않은 주총」을 잘라내면 안 된다.

소집공고는 회의 前에 나오고 의결권도 회의 前에 행사한다. 그런데 후보 필터는 **회의일** 기준이라
(`_candidate_notices_in_meeting_window`), 구간 끝을 오늘로 자르면 하필 **지금 표를 던져야 하는
회차만** 탈락하고 이미 끝난 회차만 남는다. 필요한 것과 정확히 반대다.

실측 애경케미칼: 2026-07-30 임시주총 소집공고 / 회의일 2026-08-14. 08-08에 연도 없이 조회하면
공고를 DART에서 받아온 **뒤** 회의일이 오늘을 넘는다는 이유로 버리고, 3월 정기주총을
「후보가 1개라 자동 선택했다」며 내놓았다. 접수번호(URL)를 직접 주면 이 선택 단계를 건너뛰어
정상 동작했기 때문에 겉으로는 검색이 되는 것처럼 보였다.
"""

from __future__ import annotations

from datetime import date, timedelta

from open_proxy_mcp.services.shareholder_meeting import (
    _NOTICE_LEAD_BUFFER_DAYS,
    _round_year,
    _selection_window,
)


def test_upcoming_meetings_are_not_cut_off() -> None:
    """의결권 행사 대상은 미래 회의다 — 구간이 오늘에서 끝나면 그것만 사라진다."""
    _, end, _ = _selection_window(None)
    assert end > date.today()
    # 애경케미칼처럼 공고~회의 2주짜리는 물론, 한 분기 뒤 회의도 잡혀야 한다.
    assert end >= date.today() + timedelta(days=90)
    assert end == date.today() + timedelta(days=_NOTICE_LEAD_BUFFER_DAYS)


def test_the_past_window_does_not_shrink() -> None:
    """끝을 앞으로 밀면서 시작까지 함께 밀면 작년 하반기 주총을 잃는다.

    `resolve_date_window`는 start를 end에서 역산하므로, default_end에 버퍼를 더해 넘기면
    과거 구간이 통째로 90일 밀린다. 그래서 버퍼는 역산이 끝난 **뒤** end에만 더한다.
    """
    start, _, _ = _selection_window(None)
    assert start <= date.today() - timedelta(days=360)


def test_an_explicit_year_covers_that_whole_year() -> None:
    """연도를 주면 12/31까지라 예정 회차가 원래 잡혔다 — 기본 조회만 달랐던 이유다."""
    assert _selection_window(2024)[:2] == (date(2024, 1, 1), date(2024, 12, 31))
    assert _selection_window(date.today().year)[1] > date.today()


def test_explicit_dates_are_left_alone() -> None:
    """사용자가 구간을 직접 지정했으면 늘리는 것이 곧 지시 위반이다."""
    start, end, _ = _selection_window(None, start_date="20250101", end_date="20250630")
    assert (start, end) == (date(2025, 1, 1), date(2025, 6, 30))


def test_the_round_year_comes_from_the_meeting_not_the_window() -> None:
    """「2026년 임시주총」의 연도는 회의가 열리는 해다 — 구간이 어디서 끝나는지가 아니다.

    구간 끝을 오늘+90일로 연 뒤 새로 생긴 위험: 10월 3일부터 구간이 다음 해로 넘어가므로,
    구간 끝의 연도를 쓰면 **2026년 12월 주총이 2027년 회차로** 찍힌다. 회의일을 보면 안 생긴다.
    """
    assert _round_year(None, date(2026, 12, 18), "20261201000111") == 2026

    # 반대 방향 — 12개월 lookback 으로 작년 회의를 고른 경우. 오늘의 연도를 쓰면 올해로 찍힌다.
    assert _round_year(None, date(2025, 12, 20), "20251205000222") == 2025


def test_an_explicit_year_still_wins() -> None:
    """사용자가 연도를 지정했으면 그게 곧 회차 지정이다."""
    assert _round_year(2024, date(2024, 3, 29), "20240311000333") == 2024


def test_it_falls_back_to_the_filing_year_not_today() -> None:
    """회의일을 못 읽어도 공고 접수연도가 오늘보다 회의에 가깝다 — 공고는 회의 몇 주 前이다."""
    assert _round_year(None, None, "20251205000222") == 2025
    assert _round_year(None, None, "") == date.today().year


def test_proxy_advise_does_not_default_to_annual_only() -> None:
    """구간을 넓혀도 `annual` 고정이면 임시주총은 후보에 오르지도 못한다.

    회의일 구간과는 **별개의** 두 번째 원인이었다. `shareholder_meeting_notice`는 `auto`라
    구간 수정만으로 애경케미칼 임시주총을 잡았지만, 정작 의결권 메모를 내는
    `proxy_advise_before_meeting`은 `annual` 고정이라 그대로 3월 정기주총(종료된 회차)을
    분석했다. 「회의 전(before_meeting)」 tool이 다가오는 회의를 못 보는 상태였다.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "open_proxy_mcp/tools/proxy_advise_before_meeting.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "proxy_advise_before_meeting"
    ]
    assert len(fns) == 1, "tool 함수를 찾지 못했다 — 이름이 바뀌었으면 이 테스트도 갱신할 것"

    args = fns[0].args
    names = [a.arg for a in args.args]
    defaults = dict(zip(names[len(names) - len(args.defaults):], args.defaults))
    assert isinstance(defaults["meeting_type"], ast.Constant)
    assert defaults["meeting_type"].value == "auto"
