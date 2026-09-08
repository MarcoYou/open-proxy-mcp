"""디제스트가 **못 본 것**을 사람 눈에 보이게 적는지 — 렌더러 계약. 네트워크 0.

json 에만 실린 사실은 사용자가 못 본다. 260909 이전엔 절단 신호가 푸터의 「스캔 페이지 상한 도달」
여섯 글자뿐이라, 어느 유형이 몇 페이지 중 몇을 봤는지도 무엇이 빠졌는지도 알 수 없었다.
"""
from open_proxy_mcp.tools.screener import _render_digest


def _payload(coverage: list[dict], **counts) -> dict:
    base = {"scanned": 100, "classified": 10, "matched": 8, "hits": 8, "returned": 8,
            "truncated_scan": any(not c["complete"] for c in coverage),
            "truncated_details": False, "truncated_paging": False, "deduped_away": 0}
    base.update(counts)
    return {"status": "ok", "as_of": "2026-09-09T09:00:00",
            "period": {"bgn_de": "20260611", "end_de": "20260909", "days": 90},
            "universe": {"label": "전체시장"}, "counts": base,
            "paging": {"offset": 0, "page_size": 200, "matched": base["matched"],
                       "returned": base["returned"], "has_more": False},
            "coverage": coverage, "hits": [], "warnings": []}


def _complete(code: str = "I001") -> dict:
    return {"code": code, "total": 50, "total_pages": 1, "fetched_pages": 1,
            "received_pages": 1, "missing_pages": [], "seen_from": "20260901",
            "seen_to": "20260909", "complete": True, "error": None}


def test_a_complete_scan_says_nothing_about_gaps():
    out = _render_digest(_payload([_complete()]))
    assert "이 응답이 못 본 것" not in out


def test_a_truncated_code_names_what_it_missed():
    out = _render_digest(_payload([
        _complete("B001"),
        {"code": "I001", "total": 8800, "total_pages": 88, "fetched_pages": 20,
         "received_pages": 20, "missing_pages": [], "seen_from": "20260706",
         "seen_to": "20260909", "complete": False, "error": None}]))
    assert "이 응답이 못 본 것" in out
    assert "I001" in out and "20/88" in out
    assert "20260706" in out and "20260909" in out
    assert "기간을 나눠" in out
    # 완전한 코드는 이 절에 끼지 않는다.
    assert "`B001`" not in out.split("이 응답이 못 본 것")[1]


def test_a_failed_code_is_named_even_though_it_returned_nothing():
    """빈 결과를 성공으로 읽으면 안 된다 — 실패한 코드가 화면에서 사라지는 게 가장 나쁘다."""
    out = _render_digest(_payload([
        {"code": "I001", "total": 0, "total_pages": 0, "fetched_pages": 0,
         "received_pages": 0, "missing_pages": [], "seen_from": None, "seen_to": None,
         "complete": False, "error": "transport:ConnectError"}]))
    assert "I001" in out and "transport:ConnectError" in out
    assert "받은 행 없음" in out


def test_a_page_hole_is_spelled_out():
    out = _render_digest(_payload([
        {"code": "B001", "total": 400, "total_pages": 4, "fetched_pages": 2,
         "received_pages": 3, "missing_pages": [3], "seen_from": "20260801",
         "seen_to": "20260820", "complete": False, "error": "020"}]))
    assert "빠진 페이지 [3]" in out


def test_superseded_count_is_visible():
    out = _render_digest(_payload([_complete()], deduped_away=324))
    assert "324" in out


def test_zero_matches_still_reports_a_truncated_scan():
    """스캔이 잘려서 0건인데 「새 공시 없음 (조회는 정상)」만 보이면 가장 나쁜 침묵이다."""
    p = _payload([
        {"code": "I001", "total": 8800, "total_pages": 88, "fetched_pages": 1,
         "received_pages": 1, "missing_pages": [], "seen_from": "20260901",
         "seen_to": "20260909", "complete": False, "error": None}], matched=0, returned=0)
    p["no_new"] = True
    out = _render_digest(p)
    assert "이 응답이 못 본 것" in out and "1/88" in out
    assert "(조회는 정상)" not in out, "잘렸는데 정상이라고 말하면 안 된다"


def test_zero_matches_with_a_clean_scan_says_so():
    p = _payload([_complete()], matched=0, returned=0)
    p["no_new"] = True
    out = _render_digest(p)
    assert "(조회는 정상)" in out and "이 응답이 못 본 것" not in out
