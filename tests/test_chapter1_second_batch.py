"""값이 틀리게 나가던 것 2차 (2026-11-15) — 표본으로 재현된 것만. 아래 숫자·문구는 그 공시들의 뼈대다."""
from open_proxy_mcp.services.disclosure_flow import order_coverage
from open_proxy_mcp.services.fiscal_period import period_metadata
from open_proxy_mcp.services.forward_estimates import _apply_multiples
from open_proxy_mcp.services.ownership_structure import _change_overview
from open_proxy_mcp.services.risk_events import _body_after_correction, _parse_embezzlement
from open_proxy_mcp.services.treasury_share import _normalize_trust
from open_proxy_mcp.tools.financial_metrics import _chg

# 남양유업 20260130801451 — 정정 표가 라벨을 늘어놓고 값을 몰아 준 뒤, 정정 후 본문이 다시 온다.
NAMYANG = ("횡령ㆍ배임사실확인\n정정신고(보고)\n정정일자 2026-01-30\n1. 정정관련 공시서류\n횡령ㆍ배임사실확인\n2. 정정관련 공시서류제출일\n2026-01-29\n"
           "3. 정정사유\n판결문 수령\n4. 정정사항\n정정항목 정정전 정정후\n1. 사실확인 내용 가. 1심 판결(총 2건)입니다.\n"
           "2. 횡령 등 금액\n-사실확인금액(원)\n-자기자본대비(%)\n25,629,168,333\n3.78\n25,783,168,333\n3.80\n"
           "횡령ㆍ배임사실확인\n1. 사실확인 내용\n가. 1심 판결입니다.\n2. 횡령 등 금액\n사실확인금액(원)\n6,805,430,626\n"
           "자기자본(원)\n631,785,721,012\n자기자본대비(%)\n1.1\n3. 향후대책\n-")


def test_correction_is_read_from_the_restated_body():
    body = _body_after_correction(NAMYANG)
    assert body.startswith("횡령ㆍ배임사실확인\n1. 사실확인 내용")
    got = _parse_embezzlement(body, body.split("\n"), "사실확인")
    assert got["amount_won"] == "6,805,430,626" and got["equity_ratio_pct"] == "1.1"       # 종전: 금액 「1」
    plain = "횡령ㆍ배임혐의발생\n1. 사고발생내용\n2. 횡령 등 금액\n혐의발생금액(원)\n100"
    assert _body_after_correction(plain) is plain                                           # 정정본이 아니면 그대로


def test_unsigned_delta_pct_takes_its_sign_from_before_and_after():
    def grid(before, after, delta):
        return [["보고", "보고일자", "구분", "주식수", "비율"],
                ["직전보고서제출일", "2026-06-19", "합계", *before], ["이번보고서제출일", "2026-08-28", "합계", *after],
                ["증감", "-", "합계", *delta]]
    got = _change_overview(grid(("33,983,040", "42.63"), ("31,983,040", "40.12"), ("-2,000,000", "2.51")))   # 롯데관광개발
    assert got["delta"]["pct"] == -2.51 and got["delta"]["pct_sign_from"] == "before_after"
    got = _change_overview(grid(("100,000", "24.94"), ("96,056", "26.63"), ("-3,944", "1.69")))              # 현대엘리베이터 — 주식은 줄고 비율은 올랐다
    assert got["delta"]["pct"] == 1.69 and "pct_sign_from" not in got["delta"]


def test_short_fiscal_year_is_not_called_a_year():
    sk = period_metadata({"start": "2026-07-01", "end": "2026-09-30"}, annual=True)        # SK리츠 20261104800448 — 3개월 결산
    assert sk["period_kind"] == "annual" and sk["fiscal_year_months"] == 3
    assert "fiscal_year_months" not in period_metadata({"start": "2025-10-01", "end": "2026-09-30"}, annual=True)
    assert "fiscal_year_months" not in period_metadata({"start": "2026-07-01", "end": "2026-09-30"})


def test_today_price_dividend_yield_is_dropped_from_old_actual_rows():
    rows = [{"period": "2023.12A", "period_type": "FY", "div_yield_at_price_pct": 0.7, "div_yield_why": "ok"},
            {"period": "2025.12A", "period_type": "FY", "div_yield_at_price_pct": 0.81, "div_yield_why": "ok"},
            {"period": "2026.12E", "period_type": "FY", "is_estimate": True, "div_yield_at_price_pct": 4.64, "div_yield_why": "ok"}]
    _apply_multiples(rows, 1.2e15, 206000, "20261113")
    assert rows[0]["div_yield_at_price_pct"] is None and "과거 연도" in rows[0]["div_yield_why"]
    assert rows[1]["div_yield_at_price_pct"] == 0.81 and rows[2]["div_yield_at_price_pct"] == 4.64


def test_trust_rows_have_no_share_count_and_loss_yoy_gets_a_word():
    assert _normalize_trust({"rcept_no": "20261112000001", "ctr_prc": "1,000,000,000"}, "trust_contract", "신탁계약 체결")["shares"] is None
    assert _chg(66.6, "적자 축소") == "+66.6% (적자 축소)" and _chg(None, "흑자 전환") == "흑자 전환" and _chg(12.34) == "+12.3%"


def test_order_coverage_keeps_correction_deltas_apart_from_new_contract_sum():
    from datetime import date
    base = {"kind": "order", "corp_name": "가", "stock_code": "000001", "rcept_dt": date(2026, 5, 1), "subtype": "체결"}
    corr = {**base, "is_correction": True, "ratio": 7.0}
    cov = order_coverage([{**base, "amount": 100.0, "ratio": 10.0},
                          {**corr, "amount": 70.0, "amount_before": 100.0},       # −30
                          {**corr, "amount": 70.0},                               # 금액 정정이 아니거나 못 읽음 — 건수만
                          {**corr, "amount": 500.0, "amount_before": 100.0, "via_sub": True}],   # 모회사가 다시 낸 자회사 정정 — 뺀다
                         (date(2026, 1, 1), date(2026, 12, 31)))
    row = cov["rows"][0]
    assert row["new"] == 1 and row["amount_krw"] == 100                           # 합은 새 계약만
    assert (row["corrections"], row["corrections_read"], row["correction_delta_krw"]) == (2, 1, -30)


def test_extract_order_takes_the_before_amount_only_from_its_own_filing():
    from open_proxy_mcp.services.screener import _extract_order
    cd = {"amount_before_won": 100, "amount_after_won": 70}
    orders = [{"rcept_no": "A", "contract_amount_won": 70, "correction_diff": cd}, {"rcept_no": "B", "contract_amount_won": 50}]
    assert _extract_order({"data": {"orders": orders}}, "A")["amount_before_won"] == 100
    assert _extract_order({"data": {"orders": orders}}, "B")["amount_before_won"] is None
    assert _extract_order({"data": {"orders": orders}}, "Z") == {}                               # 다른 공시의 값으로 물러나지 않는다
    orders[0]["contract_amount_won"] = 999                                                       # 정정 후 값이 금액과 다르면 증감을 낼 수 없다
    assert _extract_order({"data": {"orders": orders}}, "A")["amount_before_won"] is None
