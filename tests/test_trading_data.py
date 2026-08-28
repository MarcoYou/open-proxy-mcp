"""trading_data 회귀 — 260824 신설 때 실제로 밟은 함정들을 고정한다.

network 0콜 · DB 0콜. 순수 로직과 렌더러만 검사한다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.tools.trading import (  # noqa: E402
    _monthly,
    _render_firm,
    _render_market,
    _render_sector,
)


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. 스냅샷 시대 분할 — 소급 구간이 비면 과거 전체가 섹터 없이 남는다 ──
def test_eras_cover_history_before_first_snapshot():
    """WICS 첫 관측(2026-08)보다 이른 구간도 **덮여야** 한다.

    시대를 `snap_dd` 부터 시작하게 짜면 2015~2026 이 통째로 빠지고, 섹터 합이 시장 합보다
    한참 작아진다. 그때 에러는 안 난다 — 그냥 숫자가 작아질 뿐이다.
    """
    m = _load_script("krx_cap_agg")
    eras = m._eras(["20260821"], "20151230")
    assert eras == [("20260821", "20151230", "99999999")]


def test_eras_split_at_each_snapshot():
    """관측이 쌓이면 시대가 갈리고, 각 날짜는 **정확히 한 시대**에만 속해야 한다(중복 집계 방지)."""
    m = _load_script("krx_cap_agg")
    eras = m._eras(["20260821", "20260930", "20261031"], "20200101")
    assert eras == [("20260821", "20200101", "20260930"),
                    ("20260930", "20260930", "20261031"),
                    ("20261031", "20261031", "99999999")]
    for probe in ("20200101", "20260821", "20260929", "20260930", "20261031", "20301231"):
        hits = [s for s, a, b in eras if a <= probe < b]
        assert len(hits) == 1, f"{probe} 가 {len(hits)}개 시대에 속함"


# ── 2. tool 개명 접기 — 통계가 두 계열로 갈라지지 않는다 ──
def test_tool_alias_folds_old_name():
    u = _load_script("usage_tracker")
    assert u.canon_tool("valuation") == "price_multiple_data"
    assert u.canon_tool("company") == "company"
    assert u.canon_tool(None) is None


def test_merge_drained_folds_tool_column():
    """DB 행과 드레인 행이 **같은 함수**를 지나므로 접기는 한 곳이면 된다."""
    u = _load_script("usage_tracker")
    rows = [("valuation", "h1", 10, 0), ("company", "h2", 5, 0)]
    got = [r[0] for r in u.merge_drained(rows, u._TL_COLS)][:2]
    assert got == ["price_multiple_data", "company"]


# ── 3. 다운샘플 — 555주를 md 표에 쏟지 않는다 ──
def test_monthly_downsample_keeps_last_observation_of_month():
    series = [{"asof": "20260703", "v": 1}, {"asof": "20260731", "v": 2},
              {"asof": "20260807", "v": 3}]
    out = _monthly(series)
    assert [s["asof"] for s in out] == ["20260731", "20260807"]
    assert out[0]["v"] == 2, "같은 달이면 마지막 관측이 남아야 한다"


# ── 4. 렌더러가 payload 를 실제로 쓴다 (260731 교훈: payload 에 있어도 안 쓰면 안 보인다) ──
def _firm_payload(adj_events=()):
    series = [{"asof": f"2026{m:02d}01", "close_krw": 1000 + m,
               "mktcap_krw": (1000 + m) * 10**9, "list_shrs": 10**9} for m in range(1, 9)]
    return {"tool": "trading_data", "status": "ok", "subject": "테스트(000000)",
            "data": {"scope": "firm", "ticker": "000000", "market": "KS",
                     "as_of": series[-1]["asof"], "from": series[0]["asof"],
                     "points": len(series), "latest": series[-1], "series": series,
                     "price_adjusted": False, "adj_events": list(adj_events),
                     "method": "M"},
            "warnings": ["W"]}


def test_firm_render_shows_shares_and_marks_unadjusted():
    md = _render_firm(_firm_payload())
    assert "상장주식수" in md and "1,000,000,000" in md
    assert "`data.series`" in md, "전 구간 곡선의 소재를 알려야 한다"
    assert "W" in md


def test_firm_render_surfaces_adjustment_events():
    """조정 이벤트가 있으면 **반드시 보여야** 한다 — 이게 안 보이면 사용자가 불연속인
    가격 시계열을 연속으로 읽는다."""
    md = _render_firm(_firm_payload([{"event_dd": "20260315", "adj_factor": 0.02}]))
    assert "20260315" in md and "기준가 조정" in md


def test_sector_render_trims_when_bucket_requested():
    """bucket 을 물었으면 전체 57행을 앞에 쏟지 않는다."""
    buckets = [{"market": "KS", "bucket": f"B{i}", "label": f"섹터{i}",
                "cap_krw": (60 - i) * 10**12, "n": i + 1} for i in range(30)]
    p = {"status": "ok", "subject": "s", "data": {
        "scope": "sector", "scheme": "wics_industry", "scheme_desc": "d",
        "as_of": "20260821", "sector_asof": "20260821", "buckets": buckets,
        "bucket": "섹터20", "series": [], "method": "M"}, "warnings": []}
    md = _render_sector(p)
    assert "섹터20 ◀" in md, "지정 섹터는 반드시 표에 있어야 한다"
    assert "섹터25" not in md, "상위 5 + 지정 섹터만 남아야 한다"
    full = dict(p); full["data"] = {**p["data"], "bucket": None}
    assert "섹터25" in _render_sector(full), "bucket 없으면 전체 표"


def test_market_render_totals_both_markets():
    p = {"status": "ok", "subject": "s", "data": {
        "scope": "market", "scheme": "market", "scheme_desc": "d", "as_of": "20260821",
        "points": 2,
        "latest": [{"asof": "20260821", "market": "KS", "cap_krw": 5_713 * 10**12, "n": 942},
                   {"asof": "20260821", "market": "KQ", "cap_krw": 443 * 10**12, "n": 1822}],
        "series": [], "method": "M"}, "warnings": []}
    md = _render_market(p)
    assert "6,156.0조" in md and "2,764" in md


# ── 5. quote 캐시가 배수 산출용 캐시를 오염시키지 않는다 ──
def test_quote_cache_is_separate_from_krx_snapshot_cache():
    """임의 과거일 조회가 `_KRX_CACHE`(32MB, 오늘 스냅샷) 를 밀어내면 배수 산출이 매 요청마다
    KRX 를 다시 부른다. 장부를 나눠 뒀는지 확인한다."""
    from open_proxy_mcp.services import trading
    from open_proxy_mcp.services.valuation import _KRX_CACHE
    assert trading._QUOTE_CACHE is not _KRX_CACHE
    assert isinstance(trading._QUOTE_CACHE_MAX, int) and trading._QUOTE_CACHE_MAX > 0


@pytest.mark.parametrize("bad", ["nope", "history", "explain"])
def test_unknown_scope_is_rejected_not_silently_defaulted(bad):
    """오타를 조용히 firm 으로 보내면 의도 밖 DART 콜이 난다 — 명시 거절."""
    import asyncio

    from open_proxy_mcp.services.trading import build_cap_agg_payload
    p = asyncio.run(build_cap_agg_payload(bad))
    assert p["status"] == "invalid"


# ── 6. KRX 가 저장분에 있는 날짜를 0행으로 돌려줄 때 ──
def test_quote_does_not_cache_empty_result():
    """빈 응답을 캐시하면 상류가 복구돼도 이 프로세스는 영영 못 본다.

    260824 실측: `krx_weekly` 에 20260821 이 있는데 KRX API 는 그 날짜에 0행을 준다
    (20260820 은 2,763행). 260703 에도 같은 일이 있었다 — 일시 소실은 상수다.
    """
    import inspect

    from open_proxy_mcp.services import trading
    src = inspect.getsource(trading._krx_quote_row)
    assert "if row:" in src, "빈 결과를 캐시에 넣으면 안 된다"
    assert "_QUOTE_CACHE[(basDd, ticker)] = row\n" in src


def test_quote_default_date_uses_krx_reality_not_db_latest():
    """기본 날짜는 **KRX 가 실제로 가진** 최신 거래일이어야 한다 — 저장분 최신일이 아니라.

    저장분(`_ensure_krx_fresh`)을 그대로 물으면 위 0행 케이스에서 '데이터 없음' 이 난다.
    """
    import inspect

    from open_proxy_mcp.services import trading
    src = inspect.getsource(trading.build_quote_payload)
    assert "await _fetch_live_snapshot()" in src
    assert "await _ensure_krx_fresh()" not in src, "저장분 최신일을 KRX 에 그대로 묻지 않는다"


# ── 7. 다운샘플이 시장을 잡아먹지 않는다 ──
def test_downsample_keeps_both_markets_per_month():
    """시장·섹터 시계열은 (asof, market) 이 키다. 한 덩어리로 접으면 같은 달의 KOSPI 가
    KOSDAQ 에 덮여 **한 시장이 통째로 사라진다** — 에러 없이 숫자만 반토막 난다."""
    from open_proxy_mcp.services.trading import _downsample
    series = [{"asof": "20260703", "market": "KS", "cap_krw": 1},
              {"asof": "20260703", "market": "KQ", "cap_krw": 2},
              {"asof": "20260731", "market": "KS", "cap_krw": 3},
              {"asof": "20260731", "market": "KQ", "cap_krw": 4}]
    out = _downsample(series, "monthly")
    assert len(out) == 2, "한 달 × 두 시장 = 2행이 남아야 한다"
    assert {s["market"] for s in out} == {"KS", "KQ"}
    assert {s["cap_krw"] for s in out} == {3, 4}, "그 달 마지막 관측"


def test_downsample_weekly_is_identity():
    from open_proxy_mcp.services.trading import _downsample
    series = [{"asof": "20260703", "market": "KS"}, {"asof": "20260710", "market": "KS"}]
    assert _downsample(series, "weekly") == series


def test_krw_scaled_keeps_small_cap_visible():
    """260828 T 재검토 — 시총이 「0.0조」로 나오면 200억원 쟁점을 판단할 수 없다.

    조 고정 표기가 소형주를 지웠다. 값 크기에 맞는 단위로 찍는지 본다.
    """
    from open_proxy_mcp.tools._shared import krw_scaled

    assert krw_scaled(19_800_000_000) == "198억원"      # 관리종목 시총 200억 미달 구간
    assert krw_scaled(1_234_500_000_000) == "1.23조원"  # 1~10조는 소수 2자리
    assert krw_scaled(6_156_000_000_000_000).startswith("6,156.0조")
    assert krw_scaled(None) == "-"
    assert krw_scaled(0) == "0원"
