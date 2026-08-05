"""읽을 보고서를 고르는 순서 — 거래소 서식 > 연차보고서, 원본 > [기재정정]."""

from __future__ import annotations

from open_proxy_mcp.services.corp_gov_report import _pick_filing


def _f(rcept_dt: str, report_nm: str) -> dict:
    return {
        "rcept_no": f"{rcept_dt}800001",
        "rcept_dt": rcept_dt,
        "report_nm": report_nm,
        "is_correction": report_nm.startswith("[기재정정]"),
        "is_annual_report": "연차보고서" in report_nm,
    }


def test_picks_the_newest_when_nothing_is_deprioritised() -> None:
    filings = [_f("20260601", "기업지배구조보고서공시"), _f("20250530", "기업지배구조보고서공시")]
    assert _pick_filing(filings)["rcept_dt"] == "20260601"


def test_prefers_the_original_over_a_correction() -> None:
    filings = [_f("20260610", "[기재정정]기업지배구조보고서공시"), _f("20260601", "기업지배구조보고서공시")]
    assert _pick_filing(filings)["rcept_dt"] == "20260601"


def test_prefers_the_exchange_form_when_both_cover_the_same_year() -> None:
    filings = [
        _f("20260601", "기업지배구조보고서공시"),
        _f("20260305", "기업지배구조보고서공시(연차보고서)"),
    ]
    assert _pick_filing(filings)["rcept_dt"] == "20260601"


def test_an_older_exchange_form_never_beats_this_years_annual_report() -> None:
    """KB금융은 2024년까지 거래소 서식을 냈고 이후 연차보고서로 바꿨다 —
    연도를 넘어 미루면 2024년 보고서를 최신인 양 가리키게 된다."""
    filings = [
        _f("20260305", "기업지배구조보고서공시(연차보고서)"),
        _f("20250305", "기업지배구조보고서공시(연차보고서)"),
        _f("20240229", "기업지배구조보고서공시"),
    ]
    assert _pick_filing(filings)["rcept_dt"] == "20260305"


def test_annual_report_is_still_returned_when_it_is_the_only_one() -> None:
    """금융회사는 연차보고서만 낸다 — 걸러 버리면 그해 공시가 통째로 사라진다."""
    filings = [_f("20260305", "기업지배구조보고서공시(연차보고서)")]
    assert _pick_filing(filings)["rcept_dt"] == "20260305"


def test_annual_report_beats_a_correction_of_the_exchange_form() -> None:
    """미루는 순서는 종류(연차) 가 먼저, 그다음이 정정 여부다."""
    filings = [
        _f("20260610", "[기재정정]기업지배구조보고서공시"),
        _f("20260305", "기업지배구조보고서공시(연차보고서)"),
    ]
    assert _pick_filing(filings)["report_nm"] == "[기재정정]기업지배구조보고서공시"


def test_corrected_annual_report_is_returned_when_it_is_all_there_is() -> None:
    filings = [_f("20260410", "[기재정정]기업지배구조보고서공시(연차보고서)")]
    assert _pick_filing(filings)["rcept_dt"] == "20260410"


def test_no_filings_yields_nothing() -> None:
    assert _pick_filing([]) is None
