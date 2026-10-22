"""performance_analysis 공개 겉면(`tools/performance_analysis.py`) — 도구 등록·설명 형식·md/json 렌더 계약 (261022).

서비스 계산은 `test_performance_analysis.py` 가 증명한다. 여기서는 ① 런타임 도구 목록에 있고 설명의 `ref:` 토큰이 모두 런타임 도구인지(카탈로그 검사와 같은 규칙)
② md·json 이 같은 payload 에서 나오고 json 키 집합이 계약대로인지 ③ 실패 상태의 md 를 본다. 모듈이 없던 main(39e6a672)에서 실패한다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import pathlib
import re

import pytest

from open_proxy_mcp.services import perf_data as D
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


def test_catalog_check_script_passes_with_the_new_tool():
    """`scripts/check_tool_catalog.py` 전체(페이지·표·수·릴리스 제목 짝)가 통과한다 — 수는 하드코딩하지 않는다."""
    spec = importlib.util.spec_from_file_location("check_tool_catalog_main", ROOT / "scripts" / "check_tool_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 0


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
                                  "beta", "alpha_ann_pct", "corr", "tracking_error_pct", "info_ratio", "n_obs_aligned",
                                  # 261022 추가만: 같은 겹친 기간 값·벤치마크 위험지표·지수 포인트
                                  "period", "target_return_cum_pct", "benchmark_vol_ann_pct", "benchmark_mdd_pct", "benchmark_mdd_from", "benchmark_mdd_to",
                                  "benchmark_sharpe", "benchmark_start_point", "benchmark_end_point", "benchmark_points_reason"}
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
    assert set(ser[0]) == {"dd", "cum_pct", "bench_cum_pct", "bench_point"}


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


# ═══ 지수 target·B2·B3 의 md ═══════════════════════════════════════════════════════
def test_index_target_markdown_shows_points_resolution_and_no_self_comparison(world, tool):
    world.index_by_sid[1] = [(dd, v) for dd, v in zip(["20260105", "20260106", "20260107", "20260108", "20260109"], (5000.0, 5100.0, 5200.0, 5300.0, 5735.66))]
    md = call(tool, target="코스피")
    assert md.startswith("# 성과 분석 — 코스피")
    assert "대상 공식 지수 「코스피」(가격지수) · 시작 5,000.00 → 끝 5,735.66 포인트 · 해상도 일별 · 관측 4개" in md and "표준 252" in md
    assert "| 누적 수익률 | +14.71% |" in md
    assert "## 벤치마크 대비" not in md and "benchmark_same_as_target" in md and "자기 자신과 비교" in md
    assert "종목 ·" not in md.splitlines()[3]                                               # 「대상 0종목」으로 쓰지 않는다
    js = json.loads(call(tool, target="코스피", format="json"))
    assert js["data"]["target"]["index"]["end_point"] == 5735.66 and js["data"]["relative"] is None
    other = call(tool, target="코스피", benchmark="코스닥")
    assert "## 벤치마크 대비 — 코스닥 (공식 지수)" in other and "| 벤치마크 지수 포인트(시작 → 끝) |" in other


def test_index_target_series_markdown_has_point_columns(world, tool):
    md = call(tool, target="코스피", scope="series")
    assert "| 날짜 | 대상 | 대상 포인트 | 벤치마크 |" in md and "| 2026-01-09 | +1.00% | 1,010.00 | - |" in md
    stock = call(tool, scope="series")
    assert "| 날짜 | 대상 | 벤치마크 | 벤치마크 포인트 |" in stock and "| 2026-01-09 | +10.00% | +1.00% | 1,010.00 |" in stock
    both = call(tool, target="지수:코스피 200", scope="series", benchmark="코스피")
    assert "| 날짜 | 대상 | 대상 포인트 | 벤치마크 | 벤치마크 포인트 |" in both and "| 2026-01-09 | +1.00% | 1,010.00 | +1.00% | 1,010.00 |" in both


def test_relative_table_names_the_period_of_every_row_and_the_overlap_differs_from_full(world, tool):
    rows = [("20231020", 100, 1), ("20251226", 200, 1), ("20260102", 210, 1), ("20260109", 220, 1)]
    world.weekly = D.WeeklyRead(rows={X: rows, Y: rows}, floor="20231016")
    world.index = [("20251226", 1000.0), ("20260102", 1050.0), ("20260109", 1100.0)]
    md = call(tool, target=f"{X}:50, {Y}:50", since="20231020")
    assert "| 누적 수익률 | +120.00% |" in md                                                  # 전체 기간(2023-10-20 ~)
    rel = [ln for ln in md.splitlines() if ln.startswith("| ") and ("대상 누적(같은 기간)" in ln or "벤치마크 누적" in ln or "초과수익(누적)" in ln)]
    assert len(rel) == 3 and all(ln.endswith("| 2025-12-26 ~ 2026-01-09 |") for ln in rel), rel
    assert any("| 대상 누적(같은 기간) | +10.00% |" in ln for ln in rel) and any("| 초과수익(누적) | +0.00% |" in ln for ln in rel)
    assert "겹친 기간(2025-12-26 ~ 2026-01-09) 기준" in md and "전체 기간(2023-10-20 ~ 2026-01-09)" in md


def _attr_payload(world, tool, groups):
    p = json.loads(call(tool, format="json", scope="attribution"))
    p["data"]["attribution"]["by_group"] = groups
    return p


def _groups(n_held, n_unheld):
    """업종 n_held 개는 대상이 담았고(비중 합 100) n_unheld 개는 벤치마크에만 있다. 담은 업종의 효과는 일부러 작게 해 상위 N 자르기로는 밀려나게 한다."""
    gs = []
    for i in range(n_unheld):                                   # 효과가 큰 비보유 업종
        gs.append({"code": f"U{i}", "name": f"비보유{i:02d}", "w_p_pct": 0.0, "w_b_pct": 100.0 / (n_held + n_unheld) , "r_p_pct": None, "r_b_pct": 5.0,
                   "allocation_pct": 1.0 - i * 0.01, "selection_pct": 0.0, "interaction_pct": 0.0})
    for i in range(n_held):
        gs.append({"code": f"H{i}", "name": "은행" if i == 0 else f"보유{i:02d}", "w_p_pct": 100.0 / n_held, "w_b_pct": 1.0, "r_p_pct": 3.0, "r_b_pct": 2.0,
                   "allocation_pct": 0.001, "selection_pct": 0.001, "interaction_pct": 0.0})
    return gs


def _table_rows(md):
    out = {}
    for ln in md.splitlines():
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if ln.startswith("| ") and len(cells) == 8 and cells[1].endswith("%") and not cells[0].startswith("업종"):
            out[cells[0]] = cells
    return out


def test_attribution_md_keeps_every_held_sector_and_lumps_the_rest_into_one_line(world, tool, monkeypatch):
    groups = _groups(n_held=7, n_unheld=25)                     # 합 32 > 상위 20 — 종전엔 보유 업종(효과 작음)이 잘려 나갔다
    p = _attr_payload(world, tool, groups)
    a = p["data"]["attribution"]
    a.update(allocation_pct=sum(g["allocation_pct"] for g in groups), selection_pct=sum(g["selection_pct"] for g in groups),
             interaction_pct=sum(g["interaction_pct"] for g in groups))                           # 총계 = 행 합(Carino 연결 오차 0 인 장부)
    md = T._render(p)
    rows = _table_rows(md)
    assert "은행" in rows and all(f"보유{i:02d}" in rows for i in range(1, 7))                # 담은 업종 7개 모두
    other = next(v for k, v in rows.items() if k.startswith("기타("))
    assert other[0] == "기타(12개 업종)"                                                     # 비보유 25개 중 13개가 자리(20−7)를 차지하고 12개가 기타
    num = lambda c: float(c.replace("%", "").replace("+", "").replace(",", ""))
    body = [v for k, v in rows.items() if not k.startswith("합계") and not k.startswith("**합계")]
    assert sum(num(v[1]) for v in body) == pytest.approx(100.0, abs=0.01)                      # 기타 포함 대상 비중 합 100%
    tot = next(v for k, v in rows.items() if k.endswith("합계**") or k.startswith("합계"))
    assert num(tot[1]) == pytest.approx(100.0, abs=0.01)
    for col, key in ((5, "allocation_pct"), (6, "selection_pct"), (7, "interaction_pct")):
        assert sum(num(v[col]) for v in body) == pytest.approx(sum(g[key] for g in groups), abs=0.01)      # 열 합 = 전체 행 합
        assert sum(num(v[col]) for v in body) == pytest.approx(num(tot[col]), abs=0.01)                  # 열 합 = 합계 줄(총계)
    assert len(body) == 7 + 13 + 1


def test_attribution_md_without_overflow_has_no_other_line_and_has_a_total_line(world, tool):
    md = call(tool, scope="attribution", group_by="중분류")
    assert "기타(" not in md and "**합계**" in md


def test_series_md_says_when_the_benchmark_cumulative_starts_later(world, tool):
    rows = [("20231020", 100, 1), ("20251226", 200, 1), ("20260102", 210, 1), ("20260109", 220, 1)]
    world.weekly = D.WeeklyRead(rows={X: rows, Y: rows}, floor="20231016")
    world.index = [("20251226", 1000.0), ("20260102", 1050.0), ("20260109", 1100.0)]
    md = call(tool, target=f"{X}:50, {Y}:50", since="20231020", scope="series")
    assert "벤치마크 누적은 벤치마크가 시작하는 2025-12-26 을 0% 로 센다" in md
    assert "| 2023-10-20 |" not in md or "| 2023-10-20 | +0.00% | - |" in md           # 월말 발췌 — 첫 행은 대상만 있다
