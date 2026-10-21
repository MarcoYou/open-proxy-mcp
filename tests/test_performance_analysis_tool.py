"""performance_analysis 공개 겉면(`tools/performance_analysis.py`) — 도구 등록·설명 형식·md/json 렌더 계약 (261022).

서비스 계산은 `test_performance_analysis.py` 가 증명한다. 여기서는 ① 런타임 도구 목록에 있고 설명의 `ref:` 토큰이 모두 런타임 도구인지(카탈로그 검사와 같은 규칙)
② md·json 이 같은 payload 에서 나오고 json 키 집합이 계약대로인지 ③ 실패 상태의 md 를 본다. 모듈이 없던 main(39e6a672)에서 실패한다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import re

import pytest

from open_proxy_mcp.tools import performance_analysis as T
from test_performance_analysis import SEMI, W, X, Y, World, run  # noqa: F401  (대역 시장 재사용)

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Capture:
    """`mcp.tool()` 데코레이터를 흉내 내 등록된 코루틴을 잡는다."""

    def __init__(self):
        self.fns = {}

    def tool(self):
        def deco(fn):
            self.fns[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


@pytest.fixture
def tool():
    cap = _Capture()
    T.register_tools(cap)
    return cap.fns["performance_analysis"]


def call(tool, **kw):
    kw.setdefault("target", f"{X}, {Y}")
    kw.setdefault("since", "20260105")
    return asyncio.run(tool(**kw))


# ── 등록·설명 형식 ───────────────────────────────────────────────────────────────

def test_registered_in_runtime_with_catalog_style_description():
    from open_proxy_mcp.server import build_mcp
    app = build_mcp()
    tools = asyncio.run(app.list_tools())
    names = {t.name for t in tools}
    assert "performance_analysis" in names
    mine = next(t for t in tools if t.name == "performance_analysis")
    desc = mine.description or ""
    for key in ("desc:", "when:", "rule:", "status:", "params:", "ref:"):
        assert any(ln.strip().startswith(key) for ln in desc.splitlines()), key
    # 카탈로그 검사(`scripts/check_tool_catalog.py::_description_problems`)와 같은 규칙 — ref 토큰이 모두 런타임 도구, 은퇴한 이름이 없다
    spec = importlib.util.spec_from_file_location("check_tool_catalog", ROOT / "scripts" / "check_tool_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._description_problems([mine], names) == []
    ref = next(ln for ln in desc.splitlines() if ln.strip().startswith("ref:"))
    assert set(re.findall(r"[a-z_]+", ref.split(":", 1)[1])) == {"trading_data", "price_multiple_data", "dividend_data"}
    props = mine.input_schema["properties"]
    assert list(props)[:1] == ["target"] and mine.input_schema["required"] == ["target"]
    assert {"weights", "since", "as_of", "benchmark", "scope", "group_by", "basis", "rebalance", "rf", "freq", "format"} <= set(props)
    assert props["weights"]["default"] == "시총" and props["scope"]["default"] == "summary" and props["group_by"]["default"] == "중분류"
    assert props["basis"]["default"] == "price" and props["rebalance"]["default"] == "none" and props["format"]["default"] == "md"


# ── md ───────────────────────────────────────────────────────────────────────────

def test_summary_markdown_has_the_tables_and_numbers(world, tool):
    md = call(tool)
    assert md.startswith("# 성과 분석 — ") and "기간 2026-01-05 ~ 2026-01-09 · 일별 4개 구간" in md
    assert "| 누적 수익률 | +10.00% |" in md and "| 최대 낙폭 | -3.23% (2026-01-08 → 2026-01-09) |" in md
    assert "연율 수익률 | - |" in md and "기간이 365일 미만이라 연율 수익률은 내지 않았습니다" in md
    assert "## 벤치마크 대비 — 코스피 (공식 지수)" in md and "| 초과수익(누적) | +9.00% |" in md and "| 맞대어 본 구간 | 4 / 4 |" in md
    assert "무위험 금리: 국고채 1년" in md and "ACT/365" in md
    assert "## 커버리지" in md and "가격 수익률" in md
    assert "## 기여 분해" not in md and "## 업종 분해" not in md and "## 누적 수익률 추이" not in md


def test_contribution_attribution_series_markdown(world, tool):
    c = call(tool, scope="contribution", group_by="종목")
    assert "## 기여 분해 — 종목별" in c and f"`{X}`" in c
    assert re.search(rf"`{X}` \| .*\| \+21\.00% \|", c)
    grp = call(tool, scope="contribution", group_by="대분류")
    assert "묶음 | 코드" in grp and "| IT |" in grp
    a = call(tool, scope="attribution", group_by="중분류")
    assert "## 업종 분해 — Brinson-Fachler (중분류, 초과 Carino 연결)" in a and "배분 " in a and "오차 +0.000000%p" in a
    assert "| 반도체와반도체장비 |" in a and "비중은 기간 평균 기초 비중" in a
    s = call(tool, scope="series")
    assert "## 누적 수익률 추이 (월말 발췌)" in s and "| 2026-01-09 | +10.00% | +1.00% |" in s and "전 구간 5점" in s


def test_blend_markdown_lists_components_and_warnings(world, tool):
    md = call(tool, target=f"{X}=60; {Y}=40", basis="total")
    assert "## 구성요소" in md and "| 900010 60% | 60.0%" in md and "총수익(세전)" in md
    assert "> ⚠ 총수익은 세전" in md and "배당(세전): 반영 비중" in md


def test_top20_cap_points_to_json(world, tool):
    names = [f"{900100 + i:06d}" for i in range(25)]
    for t in names:
        world.stocks[t] = ("KS", 1, [1000, 1001, 1002, 1003, 1004])
    world.universe_rows["코스피 시총 상위 25"] = names
    md = call(tool, target="코스피 시총 상위 25", scope="contribution", group_by="종목")
    assert "상위 20개" in md and "전체 25개는 format=\"json\"" in md and md.count("| `9001") == 20


# ── json 계약 ────────────────────────────────────────────────────────────────────

def test_json_has_fixed_contract_keys(world, tool):
    p = json.loads(call(tool, format="json", scope="contribution", group_by="종목"))
    assert p["tool"] == "performance_analysis" and p["status"] == "ok" and set(p) >= {"tool", "status", "subject", "data", "warnings", "warning_codes"}
    d = p["data"]
    assert set(d["period"]) == {"start", "end", "requested_start", "requested_end", "freq", "n_obs", "calendar_days", "annualization"}
    assert set(d["target"]) == {"label", "spec", "n_members", "membership_asof", "weights_basis", "rebalance", "components"}
    assert set(d["target"]["components"][0]) == {"label", "spec", "weight_pct", "n_members", "sector_asof", "classification_retro", "membership_asof"}
    assert set(d["metrics"]) == {"return_cum_pct", "return_ann_pct", "vol_ann_pct", "downside_vol_ann_pct", "mdd_pct", "mdd_from", "mdd_to", "sharpe", "sortino"}
    assert set(d["relative"]) == {"benchmark", "benchmark_source", "benchmark_series", "benchmark_reason", "benchmark_return_cum_pct", "excess_cum_pct", "excess_ann_pct",
                                  "beta", "alpha_ann_pct", "corr", "tracking_error_pct", "info_ratio", "n_obs_aligned"}
    assert set(d["relative"]["benchmark_series"]) == {"src", "name", "kind"}
    assert set(d["coverage"]) == {"price_adjusted", "adj_events_used", "tick_rounding_skipped", "cmp_missing_rows", "excluded_flags", "delisted_in_period",
                                  "not_priced_at_start", "cold", "dividend"}
    assert set(d["coverage"]["cold"]) == {"used", "seam", "reason"}
    assert set(d["rf"]) == {"series", "source", "avg_pct", "asof", "convention", "missing"}
    assert set(d["contributions"][0]) == {"ticker", "name", "component", "sector", "weight_start_pct", "weight_end_pct", "return_pct", "contribution_pct"}
    a = json.loads(call(tool, format="json", scope="attribution"))["data"]["attribution"]
    assert set(a) == {"group_by", "linking", "by_group", "allocation_pct", "selection_pct", "interaction_pct", "active_pct", "residual_pct", "w_basis", "r_basis",
                      "classification"}
    assert set(a["by_group"][0]) == {"code", "name", "w_p_pct", "w_b_pct", "r_p_pct", "r_b_pct", "allocation_pct", "selection_pct", "interaction_pct"}
    assert set(a["classification"]) == {"first_snapshot", "retro", "taxonomy_break"}
    ser = json.loads(call(tool, format="json", scope="series"))["data"]["series"]
    assert set(ser[0]) == {"dd", "cum_pct", "bench_cum_pct"}


def test_md_and_json_come_from_the_same_numbers(world, tool):
    j = json.loads(call(tool, format="json"))
    md = call(tool)
    assert f"| 누적 수익률 | {j['data']['metrics']['return_cum_pct']:+,.2f}% |" in md
    assert f"{j['data']['relative']['excess_cum_pct']:+,.2f}%" in md


# ── 실패 상태 ────────────────────────────────────────────────────────────────────

def test_failure_statuses_render_a_short_markdown_and_json_keeps_codes(world, tool):
    md = call(tool, target="반도체 업종=60; 대분류 금융=39")
    assert md.startswith("# performance_analysis: 반도체 업종=60; 대분류 금융=39 — 입력 오류") and "합이 100(±0.5)이 아닙니다(현재 99)" in md
    js = json.loads(call(tool, target="반도체 업종=60; 대분류 금융=39", format="json"))
    assert js["status"] == "invalid" and js["tool"] == "performance_analysis"
    nf = call(tool, target="없는 업종 문구")
    assert "조회 결과 없음" in nf
    attr = call(tool, scope="attribution", benchmark="코스피")
    assert "입력 오류" in attr and "계산 벤치마크만 됩니다" in attr
