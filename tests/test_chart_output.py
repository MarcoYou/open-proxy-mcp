"""Financial chart invariants and backwards-compatible MCP content."""
import asyncio
import json
import math

from open_proxy_mcp.services.chart_images import _dividend, _governance, _performance
from open_proxy_mcp.tools._charts import with_chart_output


def test_performance_gap_and_separate_bases():
    payload = {"data": {
        "target": {"label": "A"}, "relative": {"benchmark": "B", "period": {"start": "20260302"}},
        "period": {"start": "20260301", "end": "20260304"}, "coverage": {"dividend": {"basis": "price"}},
        "series": [
            {"dd": "20260301", "cum_pct": 0, "bench_cum_pct": None},
            {"dd": "20260302", "cum_pct": 4, "bench_cum_pct": 0},
            {"dd": "20260303", "cum_pct": 3, "bench_cum_pct": None},
            {"dd": "20260304", "cum_pct": 5, "bench_cum_pct": -1},
        ],
    }}
    fig, summary = _performance(payload)
    assert len(fig.axes) == 2
    bench = fig.axes[1].lines[0].get_ydata()
    assert math.isnan(bench[2]) and bench[1] == 0 and bench[3] == -1
    assert "비교계열 기준일 20260302" in summary


def test_dividend_keeps_classes_missing_and_zero_separate():
    row = {"bsns_year": 2025, "stlm_dt": "2025-12-31", "row_status": "확정", "rcept_no": "test"}
    rows = [
        dict(row, stock_knd_raw="보통주", dps_krw=0),
        dict(row, stock_knd_raw="우선주A", dps_krw=600),
        dict(row, stock_knd_raw="우선주B", dps_krw=None, row_status="항목없음"),
    ]
    payload = {"subject": "A", "data": {"annual": rows}}
    fig, text = _dividend(payload)
    assert sorted(b.get_height() for b in fig.axes[0].patches) == [0, 600]
    assert "우선주B | — | 항목없음" in text
    rows.append(rows[0])
    assert _dividend(payload) is None


def test_governance_missing_is_distinct_from_not_applicable_and_noncompliance():
    rows = [{"label": "A", "prior": "-", "current": "해당 없음"}, {"label": "B", "prior": "○", "current": "×"}]
    _, text = _governance({"data": {"metrics": rows}})
    assert "| A | 미상 | 해당 없음 |" in text
    assert "| B | 준수 | 미준수 |" in text


def test_chart_dispatch_preserves_result_and_skips_images_for_missing_data():
    calls = []
    async def dividend_data(*, scope="firm", format="md"):
        calls.append((scope, format))
        return json.dumps({"status": "ok", "data": {"annual": []}}) if format == "json" else "기존 본문"

    tool = with_chart_output(dividend_data)
    assert asyncio.run(tool()) == "기존 본문"
    result = asyncio.run(tool(format="chart"))
    assert calls == [("firm", "md"), ("firm", "json")]
    assert [b.type for b in result.content] == ["text"]
    assert json.loads(result.structured_content["result"])["data"] == {"annual": []}
    assert "`firm` 조회" in asyncio.run(tool(format="chart", scope="market"))
    assert len(calls) == 2


def test_preferred_dividend_keeps_comparison_style_when_common_stock_is_absent():
    row = {"bsns_year": 2025, "row_status": "확정", "stock_kind": "우선", "dps_krw": 600}
    def style(rows):
        fig, _ = _dividend({"subject": "A", "data": {"annual": rows}})
        bar = next(b for b in fig.axes[0].containers if b.get_label() == "우선").patches[0]
        return bar.get_facecolor(), bar.get_hatch()
    preferred_only = style([row])
    assert preferred_only[1]
    assert preferred_only == style([row, dict(row, stock_kind="보통")])


def test_vote_chart_uses_only_votes_present_and_preserves_conflicting_remarks():
    from open_proxy_mcp.services.chart_images import _vote_results
    items = [
        {"agenda": "A", "passed": "가결", "approval_rate_issued": "75", "remark": "무효",
         "remark_contradicts_pass": True},
        {"agenda": "B", "passed": "부결", "approval_rate_voted": "0"},
    ]
    fig, summary = _vote_results({"subject": "회사", "data": {"results": {"items": items}}})
    assert [bar.get_width() for bar in fig.axes[0].patches] == [0]
    assert "비율 미확인 | 가결 (비고 확인) | 무효" in summary


def test_block_chart_distinguishes_reported_zero_and_absent_ratio():
    from open_proxy_mcp.services.chart_images import _block_ownership
    base = {"report_date": "20251101", "reporter": "기관", "ownership_pct": 0}
    rows = [dict(base, ownership_pct_raw="-"), dict(base, ownership_pct_raw="0.00")]
    fig, summary = _block_ownership({"subject": "회사", "data": {"timeline": rows}})
    points = fig.axes[0].collections[0].get_offsets()
    assert len(points) == 1 and points[0][1] == 0
    assert "미확인" in summary and "0%" in summary


def test_treasury_chart_keeps_decisions_separate_from_reported_shares():
    from open_proxy_mcp.services.chart_images import _treasury
    events = [
        {"event": "cancelation_decision", "shares": 700},
        {"event": "disposal_result", "planned_shares": 300},
        {"event": "acquisition_result", "cumulative_shares": 22},
    ]
    for row in events:
        row["rcept_dt"] = "20250917"
    fig, summary = _treasury({"subject": "회사", "data": {"events": events}})
    assert [bar.get_width() for bar in fig.axes[0].patches] == [22]
    assert "소각 결정 | 결정 공시" in summary and "처분 결과 | 수량 미확인" in summary


def test_quarter_chart_respects_fiscal_gaps_cumulative_and_currency():
    from open_proxy_mcp.services.chart_images import _quarterly_performance
    base = dict(fiscal_year=2025, fiscal_quarter="Q1", revenue_krw=12, operating_margin_pct=-5)
    rows = [base, dict(base, fiscal_quarter="Q3", functional_currency="USD", fx_rate_to_krw=None),
            dict(base, fiscal_quarter="Q4", basis="annual_cumulative")]
    fig, summary = _quarterly_performance({"subject": "회사", "data": {"quarterly": rows}})
    assert [b.get_height() for b in fig.axes[0].patches][:1] == [12]
    assert all(math.isnan(b.get_height()) for b in fig.axes[0].patches[1:])
    margins = fig.axes[1].lines[0].get_ydata()
    assert list(margins[::2]) == [-5, -5] and all(math.isnan(v) for v in margins[1::2])
    assert "2025 Q2" in summary


def test_cash_charts_only_use_full_year_flows_and_reported_balances():
    from open_proxy_mcp.services.chart_images import _annual_finance
    annual = dict(year=2024, reprt_code="11011", period_basis="annual", fcf_krw=-20,
                  dividend_paid_krw=0, cash_and_equivalents_krw=100, total_debt_krw=200,
                  net_cash_krw=-100, is_financial_company=True)
    rows = [annual, dict(annual, year=2025, reprt_code="11012")]
    payload = {"subject": "회사", "data": {"yearly": rows}}
    fig, summary = _annual_finance(payload, cash_flow=True)
    assert "금융업" in summary and "-20 | 0" in summary
    assert fig.axes[0].patches[0].get_height() == -20 and math.isnan(fig.axes[0].patches[1].get_height())
    _, summary = _annual_finance(payload, cash_flow=False)
    assert "100 | 200 | -100" in summary and "— | — | —" in summary


def test_financial_chart_selects_one_query_and_rejects_conflicting_scope():
    calls = []
    async def financial_metrics(**params):
        calls.append(params)
        return json.dumps({"status": "ok", "data": {}})
    tool = with_chart_output(financial_metrics)
    for chart, scope in [("cash_debt", "yearly"), ("cash_flow", "yearly"), ("quarterly_performance", "quarterly")]:
        result = asyncio.run(tool(format="chart", chart=chart))
        assert calls[-1]["scope"] == scope and len(result.content) == 1
    assert "yearly" in asyncio.run(tool(format="chart", chart="cash_flow", scope="quarterly"))
    assert "선택" in asyncio.run(tool(format="chart", chart="unknown"))
    assert len(calls) == 3


def test_transfer_donut_uses_report_grid_including_adjacent_zero_cells():
    from pathlib import Path
    from open_proxy_mcp.services.chart_images import _transfer_donut
    from open_proxy_mcp.services.control_change import event_from_document

    original = next((Path(__file__).parent / "dart_responses/dart").glob("control_transfer_contract*"))
    event = event_from_document({"report_nm": "최대주주변경을수반하는주식양수도계약체결",
                                 "rcept_no": "20260915900001"}, original.read_text())
    payload = {"subject": "회사", "data": {"control_events": [event]}}
    fig, text = _transfer_donut(payload)
    assert "파마앤바이오 신기술조합 | 0% | 27.84%" in text
    assert [ax.get_title() for ax in fig.axes[:2]] == ["계약 전", "계약 이행 예정"]
    assert len(fig.axes[0].patches) == len(fig.axes[1].patches) == 5
    assert "그 밖의 지분" in text


def test_transfer_chart_links_corrections_to_one_original_and_matching_parties():
    from open_proxy_mcp.services.chart_images import _latest_transfer_revision
    fields = {"contract_on": "2026-06-30", "transferor": {"name": "갑"}, "transferee": {"name": "을"}}
    original = {"rcept_no": "20260630900001", "fields": fields}
    revised = {**original, "rcept_no": "20261014900192", "amendment": {"original_filed_date": "2026-06-30"}}
    assert _latest_transfer_revision([revised, original]) is revised
    assert _latest_transfer_revision([original, revised, {**original, "rcept_no": "20260630900002"}]) is None
    assert _latest_transfer_revision([original, {**revised, "fields": {**fields, "transferee": {"name": "병"}}}]) is None


def test_transfer_donut_selects_exact_receipt_and_never_fabricates_missing_stakes():
    from open_proxy_mcp.services.chart_images import _transfer_donut
    columns = ["성명", "관계", "변경전 주식수", "변경전 지분율", "변경후 주식수", "변경후 지분율"]
    row = ["양수인", "", "0", "-", "35", "35"]
    event = {"kind": "transfer_contract", "rcept_no": "20260630900888", "fields": {},
             "holders": {"columns": columns, "rows": [row]}}
    other = dict(event, rcept_no="20260630901591")
    payload = {"subject": "회사", "data": {"control_events": [other, event]}}
    fig, text = _transfer_donut(payload)
    assert fig is None and all(e["rcept_no"] in text for e in (event, other))
    payload["chart_rcept_no"] = event["rcept_no"]
    fig, text = _transfer_donut(payload)
    assert fig is not None and other["rcept_no"] not in text
    assert "양수인 | 0% | 35%" in text
    for shares, pct in (("1", "-"), ("0", ""), ("1", "105"), ("1", "NaN")):
        row[2:4] = [shares, pct]
        assert _transfer_donut(payload)[0] is None
    row[2:4] = ["1", "90"]
    event["holders"]["rows"].append(["다른 주주", "", "1", "20", "2", "2"])
    assert _transfer_donut(payload)[0] is None
    payload["chart_rcept_no"] = "20260630900000"
    assert _transfer_donut(payload)[0] is None


def test_widget_groups_the_same_holder_across_both_contract_rings():
    from open_proxy_mcp.services.chart_images import render_chart

    columns = ["성명", "변경전 주식수", "변경전 지분율", "변경후 주식수", "변경후 지분율"]
    event = {"kind": "transfer_contract", "rcept_no": "20260630900888", "holders": {
        "columns": columns, "rows": [["양수인", "1", "1", "40", "40"], ["양도인", "39", "39", "0", "-"]]}}
    vector, summary, view = render_chart("transfer_donut", {"subject": "회사", "data": {"control_events": [event]}})
    assert "<svg" in vector and "<image" not in vector and "양수인 | 1% | 40%" in summary
    groups = view["groups"]
    assert groups[0]["detail"] == "양수인 · 계약 전 1% → 계약 이행 예정 40% · +39%p"
    shapes = view["shapes"]
    assert len([m for m in shapes if m["group"] == 0 and "path" in m]) == 2
    assert len([m for m in shapes if m["group"] == 1 and "path" in m]) == 1
    assert len([m for m in shapes if m["group"] == 1 and "rect" in m]) == 3


def test_widget_price_table_keeps_each_trading_day_and_missing_benchmark():
    rows = [{"dd": f"202603{day:02}", "cum_pct": day - 1, "bench_cum_pct": None}
            for day in range(1, 9)]
    data = {"target": {"label": "A"}, "relative": {"benchmark": "B"}, "series": rows,
            "period": {"start": rows[0]["dd"], "end": rows[-1]["dd"]},
            "coverage": {"dividend": {"basis": "price"}}}
    fig, summary = _performance({"data": data})
    table = fig.chart_marks.table
    assert table["columns"] == ["거래일", "A", "B"]
    assert [row[0] for row in table["rows"]] == [day["dd"] for day in rows]
    assert table["rows"][1] == ("20260302", "1.00%", "자료 없음")
    assert "20260302" not in summary
