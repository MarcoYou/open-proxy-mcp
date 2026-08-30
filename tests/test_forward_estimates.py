"""forward_estimates — 칸 개명 매핑·단위 통일·배수 게이팅·블록 분리 회귀 (DB/네트워크 0콜).

이 테스트가 지키는 것은 **매핑 표 한 곳**이다. `fwd` 표는 개명이 진행 중이라
(`fwd_per`→`per` · `*_eok`→`*_krw` · `div_yield_pct`→`div_yield_at_period_end_pct`)
코드가 아는 이름과 DB 의 이름이 어긋날 수 있는데, 어긋난 순간 조용히 죽지 않고
**있는 쪽을 고르는지**를 여기서 고정한다.
"""
from open_proxy_mcp.services import forward_estimates as fe


# ── ① 개명 전/후 어느 쪽 스키마에서도 칸을 찾아낸다 ────────────────────────────
_OLD = frozenset({
    "period", "period_type", "is_estimate", "basis", "as_of", "price_krw", "price_dd",
    "mktcap_krw", "rev_eok", "op_eok", "ni_ctrl_eok", "eps_krw", "bps_krw",
    "fwd_per", "fwd_pbr", "fwd_psr", "div_yield_pct", "div_yield_own_pct", "rev_yoy_pct",
})
_NEW = frozenset({
    "period", "period_type", "is_estimate", "basis", "as_of", "price_krw", "price_dd",
    "mktcap_krw", "rev_krw", "op_krw", "ni_ctrl_krw", "eps_krw", "bps_krw",
    "per", "pbr", "psr", "per_basis", "pbr_basis", "psr_basis",
    "div_yield_at_period_end_pct", "div_yield_at_price_pct", "rev_yoy_vendor_pct",
})


def test_resolve_columns_old_schema():
    m = fe.resolve_columns(_OLD)
    assert m["rev_krw"] == "rev_eok"           # 옛 이름을 새 출력칸에 물린다
    assert m["per"] == "fwd_per"
    assert m["div_yield_at_period_end_pct"] == "div_yield_pct"
    assert m["div_yield_at_price_pct"] == "div_yield_own_pct"
    assert m["rev_yoy_vendor_pct"] == "rev_yoy_pct"
    assert "per_basis" not in m                # 옛 스키마엔 없다 — 없는 칸을 SELECT 하지 않는다


def test_resolve_columns_new_schema():
    m = fe.resolve_columns(_NEW)
    assert m["rev_krw"] == "rev_krw"
    assert m["per"] == "per"
    assert m["per_basis"] == "per_basis"
    assert m["div_yield_at_period_end_pct"] == "div_yield_at_period_end_pct"


def test_new_name_wins_when_both_exist():
    """개명 도중 두 이름이 함께 있으면 **새 이름**을 쓴다."""
    m = fe.resolve_columns(_OLD | _NEW)
    assert m["rev_krw"] == "rev_krw"
    assert m["per"] == "per"


# ── ② 단위 — 응답은 언제나 원(KRW). 억원은 밖으로 안 나간다 ──────────────────
def test_eok_is_scaled_to_krw():
    assert fe._to_krw(7384675.3, "rev_eok") == 738_467_530_000_000
    assert fe._to_krw(738_467_530_000_000, "rev_krw") == 738_467_530_000_000
    assert fe._to_krw(None, "rev_eok") is None


def test_no_eok_field_escapes():
    """출력칸 이름에 `_eok` 가 하나라도 있으면 단위 통일이 깨진 것이다."""
    assert not [n for n, *_ in fe._FIELDS if n.endswith("_eok")]


# ── ③ 배수 — 정의 정렬(시총÷지배순이익) + 뜻 없는 배수 제거 ──────────────────
def _rows():
    return [
        {"period": "2023.12A", "period_type": "FY", "is_estimate": False,
         "ni_ctrl_krw": 14_473_400_000_000, "bps_krw": 52002.0, "per": 120.6, "pbr": 4.94, "psr": 5.8},
        {"period": "2025.12A", "period_type": "FY", "is_estimate": False,
         "ni_ctrl_krw": 44_260_960_000_000, "bps_krw": 63997.0, "per": 39.15, "pbr": None, "psr": 4.5},
        {"period": "2026.12E", "period_type": "FY", "is_estimate": True,
         "ni_ctrl_krw": 321_104_920_000_000, "bps_krw": 109013.0, "per": 5.34, "pbr": 2.358, "psr": 2.03},
        {"period": "2026.12E", "period_type": "Q", "is_estimate": True,
         "ni_ctrl_krw": 103_559_560_000_000, "bps_krw": 109013.0, "per": None, "pbr": None, "psr": None},
    ]


_CAP = 1_502_493_602_256_000
_PRICE = 257000.0


def test_per_realigned_to_house_definition():
    rows = _rows()
    fe._apply_multiples(rows, _CAP, _PRICE, "20260828")
    latest_actual, estimate = rows[1], rows[2]
    # 시총 ÷ 지배순이익 — `price_multiple_data` 와 같은 정의 (벤더 원본 39.15 / 5.34 가 아니다)
    assert latest_actual["per"] == 33.95
    assert estimate["per"] == 4.68
    assert "지배주주순이익" in latest_actual["per_basis"]
    assert "20260828" in latest_actual["per_basis"]     # 자가 줄마다 붙는다


def test_stale_actual_rows_lose_multiples_with_reason():
    """「오늘 주가 ÷ 몇 년 전 EPS」에는 PER 이라는 이름을 주지 않는다."""
    rows = _rows()
    fe._apply_multiples(rows, _CAP, _PRICE, "20260828")
    stale = rows[0]
    assert stale["per"] is None and stale["pbr"] is None and stale["psr"] is None
    assert "최신 확정 FY" in stale["per_why"]           # 빈칸이 아니라 **이유**를 남긴다


def test_quarter_rows_lose_multiples_with_reason():
    rows = _rows()
    fe._apply_multiples(rows, _CAP, _PRICE, "20260828")
    assert rows[3]["per"] is None
    assert "분기" in rows[3]["per_why"]


def test_pbr_backfilled_from_price_and_bps():
    """최신 확정 FY 에 PBR 만 비어 있으면 메운다 — BPS 가 있는데 비우면 「자료 없음」으로 읽힌다."""
    rows = _rows()
    fe._apply_multiples(rows, _CAP, _PRICE, "20260828")
    assert rows[1]["pbr"] == round(257000 / 63997, 2)


def test_vendor_gap_is_reported():
    gaps = fe._apply_multiples(_rows(), _CAP, _PRICE, "20260828")
    assert any("2025.12A" in g for g in gaps)          # 13.3% 차 — 조용히 넘기지 않는다


# ── ④ 응답 블록 — 실적/추정이 아니라 원천/파생으로 가른다 ───────────────────
def test_row_split_reported_vs_derived():
    rec = {"period": "2026.12E", "period_type": "FY", "is_estimate": True, "basis": "IFRS연결",
           "rev_krw": 738_467_530_000_000, "eps_krw": 48139.0, "per": 4.68,
           "per_basis": "보통주 시총 ÷ 지배주주순이익 @20260828 종가", "roe_pct": 55.0}
    out = fe._shape_row(rec, {"core"})
    assert out["row_kind"] == "estimate"
    assert out["reported"]["rev_krw"] == 738_467_530_000_000
    assert out["derived"]["per"] == 4.68
    assert "roe_pct" not in out["reported"]            # quality 묶음 — core 에선 안 나간다


def test_nulls_are_dropped_not_zero_filled():
    out = fe._shape_row({"period": "2026.12E", "period_type": "FY", "is_estimate": True,
                         "rev_krw": None, "eps_krw": 48139.0}, {"core"})
    assert "rev_krw" not in out["reported"]            # 0 으로도 "미상" 으로도 채우지 않는다


def test_keys_bundle_hides_conflicting_year_columns():
    rec = {"period": "2026.12E", "period_type": "FY", "is_estimate": True,
           "fiscal_year": 2026, "fy_end": 2026, "fy_major": 2025, "fy_canonical": 2026}
    assert "keys" not in fe._shape_row(rec, {"core"})
    assert fe._shape_row(rec, {"keys"})["keys"]["fy_canonical"] == 2026


# ── ⑤ bundle 파싱 ────────────────────────────────────────────────────────────
def test_parse_bundles():
    assert fe.parse_bundles("core") == ({"core"}, [])
    assert fe.parse_bundles("core,growth")[0] == {"core", "growth"}
    assert fe.parse_bundles("all")[0] == set(fe._BUNDLES)
    assert fe.parse_bundles("")[0] == {"core"}
    want, bad = fe.parse_bundles("core,없는묶음")
    assert want == {"core"} and bad == ["없는묶음"]


def test_absent_by_design_fields_exist_in_field_table():
    """채움률 0% 안내가 실제 출력칸 이름과 어긋나면 안내가 거짓말이 된다."""
    names = {n for n, *_ in fe._FIELDS}
    assert set(fe._ABSENT_ON_ESTIMATE) <= names
