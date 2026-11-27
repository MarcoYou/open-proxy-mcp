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
