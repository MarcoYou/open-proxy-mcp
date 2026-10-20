"""forward_estimates — 칸 이름 표·단위(원)·배수 게이팅·블록 분리 회귀 (DB/네트워크 0콜).

이 테스트가 지키는 것: 칸 이름 표(`_FIELDS`)는 운영 `fwd` 뷰의 **물리 칸 이름 그대로**다. 옛 이름 후보·억원(`_eok`)
칸 분기는 걷어냈으므로, 표에 옛 이름이 다시 들어오거나 억원 칸이 나오면 여기서 막힌다.
"""
from open_proxy_mcp.services import forward_estimates as fe


# ── ① 칸 이름 표 = 물리 칸 이름 ───────────────────────────────────────────────
def test_fields_are_physical_names_with_no_legacy_candidates():
    assert all(len(f) == 4 for f in fe._FIELDS)                  # (이름, kind, block, bundle) — 후보 목록이 없다
    names = [n for n, *_ in fe._FIELDS]
    assert len(names) == len(set(names))
    assert not [n for n in names if n.endswith("_eok")]         # 억원 칸이 밖으로 나가지 않는다
    assert not [n for n in names if n in ("fwd_per", "fwd_pbr", "fwd_psr", "div_yield_pct", "div_yield_own_pct",
                                          "rev_yoy_pct", "rev_eok", "op_eok", "ni_ctrl_eok")]


def test_fetch_selects_exactly_the_declared_columns(monkeypatch):
    seen = []

    def fake(sql, params=()):
        seen.append((sql, params))
        return []
    monkeypatch.setattr(fe, "pg_rows", fake)
    assert fe._fetch("005930") == []
    sql = seen[0][0]
    select_list = sql[len("SELECT "):sql.index(" FROM fwd ")].split(", ")
    assert select_list == [n for n, *_ in fe._FIELDS]
    assert seen[0][1] == ("005930", "005930")


def test_missing_column_is_a_db_error_not_absent(monkeypatch):
    """필수 칸이 DB 에 없으면 질의가 실패한다 — 그 경로는 장애(None)로 가고, 자료 없음으로 뭉개지 않는다."""
    monkeypatch.setattr(fe, "pg_rows", lambda sql, params=(): None)
    assert fe._fetch("005930") is None


# ── ② 단위 — 응답은 언제나 원(KRW). 억원은 밖으로 안 나간다 ──────────────────
def test_money_is_whole_krw():
    assert fe._to_krw(738_467_530_000_000) == 738_467_530_000_000
    assert fe._to_krw(1.6) == 2
    assert fe._to_krw(None) is None


def test_fetch_converts_money_cells_only(monkeypatch):
    names = [n for n, *_ in fe._FIELDS]
    row = [None] * len(names)
    row[names.index("rev_krw")] = 7384675000000.4
    row[names.index("name")] = "삼성전자"
    monkeypatch.setattr(fe, "pg_rows", lambda sql, params=(): [tuple(row)])
    rec = fe._fetch("005930")[0]
    assert rec["rev_krw"] == 7384675000000
    assert rec["name"] == "삼성전자" and rec["per"] is None


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
