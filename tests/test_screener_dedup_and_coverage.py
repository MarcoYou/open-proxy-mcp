"""screener 의 모수 정직성 — 건수가 틀리는데 원문 링크로는 안 드러나는 자리들. 네트워크 0.

배경(260908~09):
- dedup 키가 `corp_code:type_code:subtype` 뿐인데 subtype 이 사건 식별자가 아니다. 공급계약 matcher 는
  「체결」·「해지」 둘뿐이라 한 회사가 낸 계약 3건이 전부 같은 키로 수렴해 카드 1장이 됐다.
  값이 아니라 **건수**가 틀리는데 카드의 원문 링크를 눌러도 그 1건은 맞으니 오류가 드러나지 않는다.
- 스캔 페이지 상한에 걸려도 「상한 도달」 여섯 글자만 나가 **무엇이 빠졌는지**를 알 수 없었다.
"""
import asyncio

import open_proxy_mcp.services.screener as S


def _item(rcept_no: str, name: str, corp: str = "00126380", *, correction: bool = False,
          dt: str = "20260820") -> dict:
    title = "단일판매ㆍ공급계약 체결"
    return {"rcept_no": rcept_no, "report_nm": ("[기재정정]" + title) if correction else title,
            "corp_code": corp, "corp_name": name, "stock_code": "005930",
            "corp_cls": "Y", "flr_nm": name, "rcept_dt": dt}


class _FakeClient:
    """DART 클라이언트를 세우지 않는다 — CI 에는 키가 없고, 이 테스트는 네트워크 0 이다."""

    def api_call_snapshot(self):
        return 0


def _run(items: list[dict]):
    async def _fake_scan(client, code, bgn, end, mx):
        return S._scan_result(items if code == "I001" else [], len(items), 1, 1)

    orig_scan, orig_client, orig_cap = S._scan_code, S.get_dart_client, S._krx_mktcap_map
    S._scan_code = _fake_scan
    S.get_dart_client = lambda: _FakeClient()
    S._krx_mktcap_map = lambda codes, dd: {}
    try:
        return asyncio.run(S.build_screener_payload(
            types="order", period="custom", custom_start="20260818",
            custom_end="20260820", universe="all", details=False))
    finally:
        S._scan_code, S.get_dart_client, S._krx_mktcap_map = orig_scan, orig_client, orig_cap


def test_three_separate_contracts_stay_three_cards():
    """같은 회사의 **별개 사건**은 접히지 않는다 — 이게 원래 결함이었다."""
    p = _run([_item("20260801000001", "테스트"),
              _item("20260810000002", "테스트"),
              _item("20260820000003", "테스트")])
    assert p["counts"]["matched"] == 3, "별개 공급계약 3건이 1건으로 접혔다"
    assert p["counts"]["deduped_away"] == 0


def test_a_correction_still_supersedes_its_original():
    """원본↔정정 수렴은 원래 의도라 유지한다 — 정정본만 대체한다."""
    p = _run([_item("20260801000001", "테스트"),
              _item("20260805000002", "테스트", correction=True)])
    assert p["counts"]["matched"] == 1, "정정본이 원본을 대체하지 않았다"
    assert p["counts"]["deduped_away"] == 1
    hit = p["hits"][0]
    assert hit.get("supersedes_rcept_no") == "20260801000001"


def test_a_later_original_is_not_swallowed_by_an_earlier_correction():
    """정정이 온 뒤 **새 사건**이 오면 그건 별개다(접힘이 뒤 사건까지 먹으면 안 된다)."""
    p = _run([_item("20260801000001", "테스트"),
              _item("20260805000002", "테스트", correction=True),
              _item("20260812000003", "테스트")])
    assert p["counts"]["matched"] == 2
    assert p["counts"]["deduped_away"] == 1


def test_different_companies_never_collapse():
    p = _run([_item("20260801000001", "가", corp="00000001"),
              _item("20260802000002", "나", corp="00000002")])
    assert p["counts"]["matched"] == 2


def test_scan_result_reports_what_it_actually_saw():
    """절단 시 「무엇을 봤는지」를 준다.

    DART 기본 정렬이 접수일 내림차순이라는 것은 **문서화된 계약이 아니다** — 그래서
    「빠진 쪽은 창의 과거」라고 단정하지 않고, 실제로 받은 행의 접수일 범위만 적는다.
    """
    items = [_item("20260820000003", "가", dt="20260820"),
             _item("20260810000002", "나", dt="20260810"),
             _item("20260801000001", "다", dt="20260801")]
    res = S._scan_result(items, total=500, total_pages=5, fetched_pages=1)
    assert res["truncated"] is True and res["complete"] is False
    assert res["seen_from"] == "20260801" and res["seen_to"] == "20260820"
    assert res["fetched_pages"] == 1 and res["total_pages"] == 5

    full = S._scan_result(items, total=3, total_pages=1, fetched_pages=1)
    assert full["truncated"] is False and full["complete"] is True


def test_truncation_is_counted_as_a_degradation():
    """절단은 에러가 아니라 **대체**다 — 세지 않으면 발생률을 영영 모른다.
    이 계기가 페이지 상한을 얼마로 올릴지 정하는 근거다(먼저 재고 나서 올린다)."""
    from open_proxy_mcp.dart.client import DEGRADATION_KINDS
    assert "scan_page_truncated" in DEGRADATION_KINDS
    assert "period_clamped" in DEGRADATION_KINDS


def test_a_correction_does_not_overwrite_the_wrong_original():
    """원본이 창 안에 둘이면 어느 것의 정정인지 알 수 없다 — 접지 않고 후보를 실어 남긴다.

    종전 판은 무조건 **가장 최근** 원본을 덮어, 정정이 옛 건의 정정일 때 그 사이의
    최신 계약이 통째로 사라졌다(건수는 맞는데 살아남는 판이 틀렸다).
    """
    p = _run([_item("20260801000001", "테스트"),
              _item("20260810000002", "테스트"),
              _item("20260815000003", "테스트", correction=True)])
    assert p["counts"]["matched"] == 3, "원본 둘 사이에서 하나가 사라졌다"
    amb = [h for h in p["hits"] if h.get("ambiguous_correction")]
    assert len(amb) == 1
    assert set(amb[0]["correction_candidates"]) == {"20260801000001", "20260810000002"}


def test_the_same_filing_never_becomes_two_cards():
    """키를 완화하면서 사라졌던 그물 — 같은 접수번호는 한 장이다."""
    dup = _item("20260801000001", "테스트")
    p = _run([dup, dict(dup)])
    assert p["counts"]["matched"] == 1


def test_a_hole_is_reported_without_throwing_the_data_away():
    """3페이지가 죽고 4페이지가 살아온 경우 — 원문은 남기고 모수만 정직하게 적는다."""
    import asyncio as _a
    from open_proxy_mcp.dart.client import DartClientError

    class _C:
        async def search_filings(self, *, page_no, **kw):
            if page_no == 3:
                raise DartClientError("020", "스캔 실패")
            return {"total_count": 400, "list": [{"rcept_no": f"p{page_no}", "rcept_dt": "20260801"}]}

    res = _a.run(S._scan_code_uncached(_C(), "B001", "20260101", "20260131", 4))
    assert [it["rcept_no"] for it in res["items"]] == ["p1", "p2", "p4"], "받은 원문을 버리면 안 된다"
    assert res["missing_pages"] == [3]
    assert res["fetched_pages"] == 2, "연속으로 받은 데까지만 완전하다고 말한다"
    assert res["complete"] is False and res["error"] == "020"


def test_a_failed_scan_code_is_never_reported_complete():
    """정직하려고 만든 표가 실패할 때만 거짓말을 하면 읽는 쪽은 경고 대신 이 표를 믿는다."""
    dead = S._scan_result([], 0, 0, 0, error="transport:ConnectError")
    assert dead["complete"] is False and dead["error"] == "transport:ConnectError"
    # 013(해당 없음)은 진짜 무자료다 — 에러가 아니라 완전한 0건.
    empty = S._scan_result([], 0, 0, 0)
    assert empty["complete"] is True and empty["error"] is None
