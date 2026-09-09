"""기능통화(비KRW) 회사에서 금액이 원화 라벨로 새지 않는지 — 네트워크 0.

배경(260908 실측). 두산밥캣은 기능통화가 USD 라 DART 원행이 `currency: "USD"` 로 온다.
그런데 `financial_metrics` 는 그 값을 그대로 `*_krw` 라벨로 내보내 자릿수가 약 1,400배 틀렸고
(매출 62.7억 vs 실제 9.2조), `asset_holdings` 는 그 USD 장부가를 **KRW 시총으로 나눠**
잉여자산배수가 0.00배로 나왔다(실제 0.346). 경쟁 서버 둘은 원행을 안 건드려 이 함정을 통과했다.

입력은 **DART 응답 경계**(`fnlttSinglAcntAll` 의 list)다 — 중간 함수 결과를 쓰지 않는다(CLAUDE.md #15).
환율은 네트워크를 타므로 주입한다.
"""
import asyncio

from open_proxy_mcp.dart.fx import statement_currency
from open_proxy_mcp.services import financial_metrics as fm


def _rows(currency: str) -> list[dict]:
    """fnlttSinglAcntAll 응답 경계 모양 — 통화만 다른 두 벌."""
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
         "thstrm_amount": "8169804000", "ord": "7", "currency": currency, "fs_div": "CFS"},
        {"sj_div": "BS", "account_id": "ifrs-full_CurrentAssets", "account_nm": "유동자산",
         "thstrm_amount": "3052266000", "ord": "1", "currency": currency, "fs_div": "CFS"},
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "thstrm_amount": "6269305000", "ord": "1", "currency": currency, "fs_div": "CFS"},
    ]


def test_statement_currency_reads_the_declaration():
    assert statement_currency(_rows("USD")) == "USD"
    assert statement_currency(_rows("KRW")) == "KRW"
    # 선언이 아예 없으면 KRW 로 본다(대다수 국내사) — 다만 그 가정을 테스트로 고정해 둔다.
    assert statement_currency([{"account_nm": "자산총계"}]) == "KRW"


def test_krw_company_is_never_touched(monkeypatch):
    """대조군. KRW 회사는 값도 안 바뀌고 환율 조회도 하지 않는다."""
    called = []

    async def _never(*a, **k):
        called.append(a)
        return 1400.0

    monkeypatch.setattr(fm, "fx_to_krw", _never)
    metrics = {"revenue_krw": 300, "total_assets_krw": 500, "roe_pct": 9.03}
    ws = asyncio.run(fm._normalize_currency(metrics, _rows("KRW"), "2024-12-31", 2024))
    assert metrics["revenue_krw"] == 300 and metrics["total_assets_krw"] == 500
    assert metrics["functional_currency"] == "KRW"
    assert ws == [] and called == []


def test_usd_company_is_converted_with_basis(monkeypatch):
    async def _rate(cur, date):
        assert cur == "USD" and date == "20241231"   # 기준일은 회계기말
        return 1470.0

    monkeypatch.setattr(fm, "fx_to_krw", _rate)
    metrics = {
        "revenue_krw": 6269305000, "total_assets_krw": 8169804000, "eps_krw": 4,
        "roe_pct": 5.83, "revenue_yoy_pct": -16.14, "asset_turnover_ratio": 0.74,
        "standalone": {"revenue_krw": 1000},                  # 중첩 dict 도 따라와야 한다
        "borrowing_detail": {"by_canonical_id": {"OPM_ST": 100}},  # 접미어 없는 금액 예외
    }
    ws = asyncio.run(fm._normalize_currency(metrics, _rows("USD"), "2024-12-31", 2024))

    assert metrics["revenue_krw"] == round(6269305000 * 1470)
    assert metrics["total_assets_krw"] == round(8169804000 * 1470)
    assert metrics["eps_krw"] == round(4 * 1470)
    assert metrics["standalone"]["revenue_krw"] == round(1000 * 1470)
    # 접미어가 없어도 금액이면 환산된다 — 안 하면 total_debt_krw 와 환율배 어긋난다.
    assert metrics["borrowing_detail"]["by_canonical_id"]["OPM_ST"] == round(100 * 1470)
    # 비율·배수는 통화 불변이라 손대지 않는다.
    assert metrics["roe_pct"] == 5.83
    assert metrics["revenue_yoy_pct"] == -16.14
    assert metrics["asset_turnover_ratio"] == 0.74
    # 기준이 값 옆에 붙어 다닌다.
    assert metrics["functional_currency"] == "USD"
    assert metrics["fx_rate_to_krw"] == 1470.0
    assert "USD" in metrics["fx_basis"]
    assert ws and "USD" in ws[0]


def test_fx_failure_leaves_values_alone_and_says_so(monkeypatch):
    """환산 못 하면 조용히 원화 라벨을 남기지 않고 「환산 못 했다」를 말한다."""
    async def _fail(cur, date):
        return None

    monkeypatch.setattr(fm, "fx_to_krw", _fail)
    metrics = {"revenue_krw": 6269305000}
    ws = asyncio.run(fm._normalize_currency(metrics, _rows("USD"), "2024-12-31", 2024))
    assert metrics["revenue_krw"] == 6269305000      # 값은 건드리지 않는다
    assert metrics["fx_rate_to_krw"] is None
    assert ws and "환율 조회 실패" in ws[0] and "원화가 아니다" in ws[0]


def test_amount_name_invariant_holds_for_the_whole_payload():
    """이름 기반 환산의 전제 — 금액은 `_krw` 로 끝난다 — 의 예외를 상수로 고정한다.

    예외가 늘면 이 테스트가 아니라 `_AMOUNT_MAP_FIELDS` 를 고쳐야 한다(그게 SSOT).
    """
    assert fm._AMOUNT_MAP_FIELDS == {"by_canonical_id"}
