"""반기·분기 결산 리츠 — 사업보고서 한 권의 당기를 「연간 12개월」로 적지 않는다 (재무 지표 도구, DATA-FY3). network 0 · DB 0.

롯데리츠(330590)는 6월·12월에 사업보고서를 따로 낸다. FY2025 사업보고서(제14기)의 당기는 2025.07~12 여섯 달이다:
  종전: 순이익 196.27억을 「손익=연간(사업보고서, 12개월)」으로 적고 ROE 1.70%(여섯 달 값)를 연간 ROE 로 냈다.
  이제: 기간 표기는 6개월(연환산 아님) · 새 키 net_income_12m_krw 361.74억(2025.01~12) · roe_12m_pct 3.13%. 옛 키 값(순이익 196.27억·ROE 1.70%)은 그대로.
12월 결산 일반 종목은 응답이 한 글자도 바뀌지 않는다(새 키·새 경고 없음, DB 읽기 0).
"""
from __future__ import annotations

import asyncio

import pytest

import open_proxy_mcp.db as pgdb
import open_proxy_mcp.services.financial_metrics as fm

# ── 롯데리츠 사업보고서 주요계정 (DART 2026-10-21 직접 조회 — FY2026 제15기 2026.01~06, FY2025 제14기 2025.07~12, FY2024 제12기 2024.07~12) ──
def _rows(span, bs_dt, *, rev, op, ni, assets, liab, equity, term):
    base = {"fs_div": "OFS", "thstrm_nm": term, "ord": "1"}
    is_ = {**base, "sj_div": "IS", "thstrm_dt": span}
    bs = {**base, "sj_div": "BS", "thstrm_dt": bs_dt}
    return [
        {**bs, "account_nm": "자산총계", "thstrm_amount": f"{assets:,}"},
        {**bs, "account_nm": "부채총계", "thstrm_amount": f"{liab:,}"},
        {**bs, "account_nm": "자본총계", "thstrm_amount": f"{equity:,}"},
        {**is_, "account_nm": "매출액", "thstrm_amount": f"{rev:,}"},
        {**is_, "account_nm": "영업이익", "thstrm_amount": f"{op:,}"},
        {**is_, "account_nm": "당기순이익(손실)", "thstrm_amount": f"{ni:,}"},
    ]


FY2025 = _rows("2025.07.01 ~ 2025.12.31", "2025.12.31 현재", rev=70_879_702_538, op=46_254_900_872, ni=19_627_460_882,
               assets=2_594_407_041_849, liab=1_454_908_461_475, equity=1_139_498_580_374, term="제 14 기")
FY2024 = _rows("2024.07.01 ~ 2024.12.31", "2024.12.31 현재", rev=65_629_118_705, op=42_006_230_943, ni=10_613_567_528,
               assets=2_623_648_647_194, liab=1_454_150_863_969, equity=1_169_497_783_225, term="제 12 기")
FY2026 = _rows("2026.01.01 ~ 2026.06.30", "2026.06.30 현재", rev=82_109_520_073, op=55_652_468_317, ni=23_770_182_540,
               assets=2_871_825_466_968, liab=1_744_099_876_786, equity=1_127_725_590_182, term="제 15 기")
NORMAL_2024 = _rows("2024.01.01 ~ 2024.12.31", "2024.12.31 현재", rev=1000, op=100, ni=90, assets=2000, liab=1000, equity=1000, term="제 20 기")
NORMAL_2023 = _rows("2023.01.01 ~ 2023.12.31", "2023.12.31 현재", rev=900, op=80, ni=70, assets=1900, liab=1000, equity=900, term="제 19 기")

# 수집기가 저장한 「결산기 12개월」 칸 (운영 DB 2026-10-21 읽기 — dart_finstat_q): (fy, quarter, 순이익 합, 자본, 근거)
CELLS = [
    (2025, 4, 36_174_226_674.0, 1_139_498_580_374.0, "결산기 12개월(261020) 공시 20260310 — 사업연도 2025 = 202506~202512 결산기 2개 합 · 간격 6개월"),
    (2024, 4, 16_455_032_132.0, 1_169_497_783_225.0, "결산기 12개월(261020) 공시 20250312 — 사업연도 2024 = 202406~202412 결산기 2개 합 · 간격 6개월"),
    (2026, 2, 23_770_182_540.0, 1_127_725_590_182.0, "결산기 12개월(261020) 공시 20260921 — 202512 다음 결산기 누적(202606) · 자본 202606 · 간격 6개월"),
    (2025, 2, 16_546_765_792.0, 1_153_680_478_920.0, "결산기 12개월(261020) 공시 20250911 — 202412 다음 결산기 누적(202506) · 자본 202506 · 간격 6개월"),
]


def _fake_pg(cells):
    """두 읽는 쪽의 질의를 모두 받는다 — 이 도구(fy·quarter IN (4, q)) 와 시장 밸류 도구(quarter=4 고정, `_stored_period_fy0`)."""
    def pg_rows(sql, params=()):
        assert "dart_finstat_q" in sql and "restate_why LIKE" in sql
        if len(params) == 5:
            _t, y, y1, q, _like = params
            quarters = (4, q)
        else:
            _t, y, y1, _like = params
            quarters = (4,)
        rows = [c for c in cells if c[0] in (y, y1) and c[1] in quarters]
        if "SELECT fy, ni_cum_restated, eq_restated, restate_why" in sql:      # 시장 밸류 도구의 열 순서
            return [(c[0], c[2], c[3], c[4]) for c in rows]
        return rows
    return pg_rows


def _patch(monkeypatch, by_year, *, cells=CELLS, pg=None):
    """DART 호출은 연도별 가짜 응답, DB 는 가짜 칸(또는 pg 함수)."""
    async def acnt(corp_code, year, reprt_code, fs_div):
        rows = by_year.get(year)
        return (list(rows), None) if rows else ([], "no_filing")

    async def acnt_all(corp_code, year, reprt_code, fs_div):
        return [], "no_filing"

    async def none(*a, **k):
        return None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", none)
    monkeypatch.setattr(fm, "_accrual_payout_pct", none)
    monkeypatch.setattr(pgdb, "pg_rows", pg or _fake_pg(cells))


def _run(year, stock_code="330590", **kw):
    return asyncio.run(fm._fetch_year_metrics("01363818", year, "OFS", allow_quarterly_fallback=False,
                                              stock_code=stock_code, **kw))


# ── 1. 롯데리츠 FY2025 — 196.27억 · 기간 표기 · 12개월 361.74억 ──────────────────────────────────────

def test_lotte_reit_fy2025_period_label_and_12m(monkeypatch):
    _patch(monkeypatch, {2025: FY2025, 2024: FY2024})
    m, ws, _ = _run(2025, is_reit=True)

    # 옛 키 값은 그대로 — 사업보고서 한 결산기(2025.07~12) 값
    assert m["net_income_krw"] == 19_627_460_882                      # 196.27억
    assert m["roe_pct"] == pytest.approx(19_627_460_882 / ((1_139_498_580_374 + 1_169_497_783_225) / 2) * 100, abs=0.01)
    # 기간 표기만 바로잡는다
    assert m["period_months"] == 6 and m["is_annualized_basis"] is False
    assert m["period_basis"] == "period_6m"
    assert m["period_span"] == {"start": "2025-07-01", "end": "2025-12-31", "months": 6}
    assert "연간(사업보고서, 12개월)" not in m["basis_note"]
    assert "6개월" in m["basis_note"] and "2025-07-01~2025-12-31" in m["basis_note"] and "연간 12개월 아님" in m["basis_note"]
    assert any("이 결산기는 6개월" in w and "연환산이 아니다" in w for w in ws)
    # 12개월 새 키 — 2025.01~12 (제13기 165.47억 + 제14기 196.27억)
    assert m["net_income_12m_krw"] == 36_174_226_674                  # 361.74억
    assert m["roe_12m_pct"] == 3.13
    tw = m["twelve_month_basis"]
    assert tw["status"] == "ok" and tw["window"] == "2025-01~2025-12" and "202506~202512 결산기 2개 합" in tw["terms"]
    assert tw["equity_end_krw"] == 1_139_498_580_374 and tw["equity_prev_krw"] == 1_169_497_783_225
    assert any("361" not in w and "net_income_12m_krw(36,174,226,674원)" in w for w in ws)
    # 같은 해의 같은 기간 1년 전(2024.07~12)과 비교 — 전년비 경고 없음
    assert "yoy_comparable" not in m


def test_roe_12m_equals_the_market_valuation_tool_roe(monkeypatch):
    """같은 값이어야 한다 — price_multiple_data(firm) 가 쓰는 저장 칸 도우미(`_stored_period_fy0`)와 같은 입력·같은 식."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    _patch(monkeypatch, {2025: FY2025, 2024: FY2024})
    m, _ws, _ = _run(2025, is_reit=True)
    win, prev_eq = asyncio.run(pmd._stored_period_fy0("330590", 2025, "20251231"))
    firm_roe = round(win["ni"] / ((1_139_498_580_374 + prev_eq) / 2) * 100, 2)
    assert m["net_income_12m_krw"] == win["ni"]
    assert m["roe_12m_pct"] == firm_roe == 3.13


# ── 2. FY2026 제15기(2026.01~06) — 전기는 직전 결산기 ──────────────────────────────────────────

def test_fy2026_prior_report_is_the_previous_settlement_not_a_year_ago(monkeypatch):
    _patch(monkeypatch, {2026: FY2026, 2025: FY2025})
    m, ws, _ = _run(2026, is_reit=True)
    assert m["period_months"] == 6 and m["period_span"]["start"] == "2026-01-01" and m["period_span"]["end"] == "2026-06-30"
    assert m["yoy_comparable"] is False
    assert m["prior_period_span"] == {"start": "2025-07-01", "end": "2025-12-31", "months": 6}
    assert "1년 전이 아니다" in m["yoy_basis_note"] and "직전 기간 대비" in m["yoy_basis_note"]
    assert any("2025-07-01~2025-12-31" in w and "1년 전이 아니다" in w for w in ws)
    # 옛 키(*_yoy_pct)는 값 그대로 — 기준만 밝힌다
    assert m["net_income_yoy_pct"] == pytest.approx((23_770_182_540 - 19_627_460_882) / 19_627_460_882 * 100, abs=0.01)
    # 12개월(2025.07~2026.06) = 2025 연간 361.74억 + 2026 반기 누적 237.70억 − 2025 반기 누적 165.47억 = 433.98억
    assert m["net_income_12m_krw"] == 36_174_226_674 + 23_770_182_540 - 16_546_765_792 == 43_397_643_422
    assert m["twelve_month_basis"]["window"] == "2025-07~2026-06"
    assert m["roe_12m_pct"] == round(43_397_643_422 / ((1_127_725_590_182 + 1_153_680_478_920) / 2) * 100, 2) == 3.8


# ── 3. 12개월을 못 만들면 빈칸 + 까닭 (옛 키는 그대로) ─────────────────────────────────────────

@pytest.mark.parametrize("pg,reason", [
    (lambda sql, params=(): None, "DB 를 읽지 못함"),
    (lambda sql, params=(): [], "저장된 결산기 12개월 칸이 없음"),
])
def test_unavailable_12m_is_blank_with_reason(monkeypatch, pg, reason):
    _patch(monkeypatch, {2025: FY2025, 2024: FY2024}, pg=pg)
    m, ws, _ = _run(2025, is_reit=True)
    assert m["net_income_krw"] == 19_627_460_882 and m["period_months"] == 6          # 옛 키·기간 표기는 DB 와 무관
    assert m["net_income_12m_krw"] is None and m["roe_12m_pct"] is None
    assert m["twelve_month_basis"]["status"] == "unavailable" and reason in m["twelve_month_basis"]["reason"]
    assert any("12개월 순이익·ROE" in w and "비웠다" in w and reason in w for w in ws)


def test_db_exception_never_breaks_the_response(monkeypatch):
    def boom(sql, params=()):
        raise RuntimeError("db down")
    _patch(monkeypatch, {2025: FY2025, 2024: FY2024}, pg=boom)
    m, _ws, _ = _run(2025, is_reit=True)
    assert m["net_income_krw"] == 19_627_460_882 and m["net_income_12m_krw"] is None
    assert "RuntimeError" in m["twelve_month_basis"]["reason"]


def test_missing_ticker_and_stale_cell_are_unavailable(monkeypatch):
    _patch(monkeypatch, {2025: FY2025, 2024: FY2024})
    m, _ws, _ = _run(2025, stock_code=None, is_reit=True)
    assert m["net_income_12m_krw"] is None and "종목코드" in m["twelve_month_basis"]["reason"]
    # 저장 칸의 마지막 결산기가 이 보고서의 결산기와 다르면 쓰지 않는다
    stale = [(2025, 4, 1.0, 2.0, "결산기 12개월(261020) 공시 20260310 — 사업연도 2025 = 202406~202412 결산기 2개 합")] + CELLS[1:]
    _patch(monkeypatch, {2025: FY2025, 2024: FY2024}, cells=stale)
    m2, _ws2, _ = _run(2025, is_reit=True)
    assert m2["net_income_12m_krw"] is None and "결산기" in m2["twelve_month_basis"]["reason"]


# ── 3-2. 분기 결산(SK리츠 3개월)·11월 결산기(0030R0) — 칸은 달력 분기로 묶인다 ─────────────────────────

SK_2026 = _rows("2026.04.01 ~ 2026.06.30", "2026.06.30 현재", rev=100, op=60, ni=40, assets=5000, liab=3000, equity=2000, term="제 21 기")
SK_2025 = _rows("2025.10.01 ~ 2025.12.31", "2025.12.31 현재", rev=100, op=60, ni=30, assets=4900, liab=2950, equity=1950, term="제 19 기")
SK_CELLS = [
    (2025, 4, 82_577_747_206.0, 1_950.0e9, "결산기 12개월(261020) 공시 20260311 — 사업연도 2025 = 202503~202512 결산기 4개 합 · 간격 3개월"),
    (2026, 2, 56_603_993_731.0, 2_000.0e9, "결산기 12개월(261020) 공시 20260908 — 202512 다음 결산기 누적(202603+202606) · 자본 202606 · 간격 3개월"),
    (2025, 2, 31_104_098_614.0, 1_900.0e9, "결산기 12개월(261020) 공시 20250922 — 202412 다음 결산기 누적(202503+202506) · 자본 202506 · 간격 3개월"),
]


def test_quarterly_settlement_reit_uses_the_cumulative_terms_list(monkeypatch):
    """누적 칸의 근거가 「누적(202603+202606)」 꼴(결산기 여럿)이어도 마지막 결산기로 읽는다. 3개월 결산기 → 6월 말 12개월 = 연간 + 반기 누적 − 전년 반기 누적."""
    _patch(monkeypatch, {2026: SK_2026, 2025: SK_2025}, cells=SK_CELLS)
    m, _ws, _ = _run(2026, stock_code="395400", is_reit=True)
    assert m["period_months"] == 3 and m["period_span"] == {"start": "2026-04-01", "end": "2026-06-30", "months": 3}
    assert m["net_income_12m_krw"] == 82_577_747_206 + 56_603_993_731 - 31_104_098_614 == 108_077_642_323
    assert m["twelve_month_basis"]["window"] == "2025-07~2026-06"
    assert m["yoy_comparable"] is False and m["prior_period_span"]["months"] == 3     # 전기 = 직전 분기(2025.10~12)


def test_cumulative_cell_that_ends_elsewhere_is_not_used(monkeypatch):
    stale = [SK_CELLS[0], (2026, 2, 1.0, 2.0, "결산기 12개월(261020) 공시 20260908 — 202512 다음 결산기 누적(202603) · 자본 202603 · 간격 3개월"), SK_CELLS[2]]
    _patch(monkeypatch, {2026: SK_2026, 2025: SK_2025}, cells=stale)
    m, _ws, _ = _run(2026, stock_code="395400", is_reit=True)
    assert m["net_income_12m_krw"] is None and "에서 끝나지 않음" in m["twelve_month_basis"]["reason"]


def test_november_settlement_maps_to_the_fourth_calendar_quarter_cell_and_blank_sum_is_explained(monkeypatch):
    nov = _rows("2025.09.01 ~ 2025.11.30", "2025.11.30 현재", rev=1, op=1, ni=1, assets=3000, liab=2700, equity=300, term="제 7 기")
    blank = [(2025, 4, None, 292_471_995_899.0, "결산기 12개월(261020) 공시 20260204 — 사업연도 2025 = 202502~202511 결산기 4개 중 빈 결산기 — 순이익 합 없음 · 간격 3개월")]
    _patch(monkeypatch, {2025: nov}, cells=blank)
    m, _ws, _ = _run(2025, stock_code="0030R0", is_reit=True, include_prev=False)
    assert m["period_months"] == 3 and m["net_income_12m_krw"] is None
    assert "합이 비어 있음" in m["twelve_month_basis"]["reason"]
    ok = [(2025, 4, 5_000_000_000.0, 292_471_995_899.0, "결산기 12개월(261020) 공시 20260204 — 사업연도 2025 = 202502~202511 결산기 4개 합 · 간격 3개월")]
    _patch(monkeypatch, {2025: nov}, cells=ok)
    m2, _ws2, _ = _run(2025, stock_code="0030R0", is_reit=True, include_prev=False)
    assert m2["net_income_12m_krw"] == 5_000_000_000 and m2["twelve_month_basis"]["window"] == "2024-12~2025-11"


# ── 4. 12월 결산 일반 종목 — 변화 0 ──────────────────────────────────────────────────────────

def test_december_company_response_is_unchanged(monkeypatch):
    def forbidden(sql, params=()):
        raise AssertionError("12개월 결산 회사는 결산기 칸을 읽지 않는다")
    _patch(monkeypatch, {2024: NORMAL_2024, 2023: NORMAL_2023}, pg=forbidden)
    m, ws, _ = _run(2024, stock_code="005930")
    assert m["period_months"] == 12 and m["is_annualized_basis"] is True and m["period_basis"] == "annual"
    assert m["basis_note"] == "손익=연간(사업보고서, 12개월), 회전일수=연간 기준."
    for k in ("net_income_12m_krw", "roe_12m_pct", "twelve_month_basis", "period_span", "yoy_comparable",
              "prior_period_span", "yoy_basis_note"):
        assert k not in m, k
    assert not any("개월" in w for w in ws)


def test_rows_without_a_period_range_stay_twelve_months(monkeypatch):
    """기간 표기가 「2024.12.31」뿐인 보고서(기간을 못 읽는 꼴) — 종전 12개월 그대로."""
    plain = [{**r, "thstrm_dt": "2024.12.31"} for r in NORMAL_2024]
    _patch(monkeypatch, {2024: plain}, pg=lambda *a, **k: (_ for _ in ()).throw(AssertionError("읽으면 안 됨")))
    m, _ws, _ = _run(2024, stock_code="005930", include_prev=False)
    assert m["period_months"] == 12 and "period_span" not in m


# ── 5. 작은 단위 ────────────────────────────────────────────────────────────────────────────────

def test_period_months_uses_the_real_months_for_annual_report():
    assert fm._period_months("11011", cumulative=True) == 12
    assert fm._period_months("11011", cumulative=True, annual_months=6) == 6
    assert fm._period_months("11011", cumulative=False, annual_months=3) == 3
    assert fm._period_months("11012", cumulative=True, annual_months=6) == 6        # 반기보고서는 보고서 코드 그대로
    assert fm._period_months("11014", cumulative=False) == 3


@pytest.mark.parametrize("span,months", [
    ("2025.07.01 ~ 2025.12.31", 6), ("2025.10.01 ~ 2025.12.31", 3), ("2025.01.01 ~ 2025.12.31", 12),
    ("2024.01.01 ~ 2024.12.31", 12),                  # 윤년 366일
    ("2025.01.01 ~ 2025.12.30", 12), ("2024.12.31 ~ 2025.12.27", 12),   # 52주 결산(361~364일)
    ("2021.04.01 ~ 2021.12.31", 9),                   # 첫 결산기 · 결산월 변경 전환기
])
def test_report_period_reads_months(span, months):
    rows = [{"sj_div": "BS", "thstrm_dt": "2025.12.31 현재"}, {"sj_div": "IS", "thstrm_dt": span}]
    got = fm._report_period(rows)
    assert got["months"] == months and got["start"] == span[:10].replace(".", "-")


def test_report_period_gives_up_on_unreadable_rows():
    assert fm._report_period([{"sj_div": "BS", "thstrm_dt": "2025.12.31 현재"}]) is None
    assert fm._report_period([{"sj_div": "IS", "thstrm_dt": "2025.12.31"}]) is None
    assert fm._report_period([{"sj_div": "IS", "thstrm_dt": "2025.12.31 ~ 2025.01.01"}]) is None
    assert fm._report_period([{"sj_div": "IS", "thstrm_dt": "2025.02.30 ~ 2025.12.31"}]) is None
    assert fm._report_period(None) is None


# ── 6. 도구 경로 — 요약(FY2026 제15기)의 fiscal_period·전년비 경고 ───────────────────────────────

def test_summary_fy2026_payload_carries_the_real_period_and_the_yoy_warning(monkeypatch):
    from open_proxy_mcp.dart import client as dart_client
    from open_proxy_mcp.dart.client import DartClientError
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    _patch(monkeypatch, {2026: FY2026, 2025: FY2025, 2024: FY2024})
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def no_data(self, endpoint, params):
        raise DartClientError("013", "가짜 응답")
    monkeypatch.setattr(dart_client.DartClient, "_request", no_data)

    lotte = {"corp_code": "01363818", "corp_name": "롯데리츠", "stock_code": "330590"}

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(lotte), candidates=[])

    async def company_info(cc):
        return {"acc_mt": "12", "induty_code": "681"}, None

    monkeypatch.setattr(fm, "resolve_company_query", resolve)
    monkeypatch.setattr(fm, "_safe_company_info", company_info)
    monkeypatch.setattr(fm, "_lookup_induty_code", lambda *a, **k: "681")
    monkeypatch.setattr(fm, "_FM_CACHE", type(fm._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-reit-period"))

    p = asyncio.run(fm.build_financial_metrics_payload("롯데리츠", scope="summary", year=2026))
    d = p["data"]
    assert d["fiscal_period"]["label"] == "2026-01-01~2026-06-30 · 6개월 결산기" and d["fiscal_period"]["period_months"] == 6
    assert d["fiscal_period"]["start"] == "2026-01-01" and d["fiscal_period"]["end"] == "2026-06-30"
    assert d["summary"]["yoy_comparable"] is False and d["summary"]["net_income_12m_krw"] == 43_397_643_422
    assert any("1년 전이 아니다" in w for w in p["warnings"])

    # FY2025 는 같은 반기(2024.07~12)와 비교되므로 경고 없이 12개월 값만 더해진다
    monkeypatch.setattr(fm, "_FM_CACHE", type(fm._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-reit-period-2"))
    p2 = asyncio.run(fm.build_financial_metrics_payload("롯데리츠", scope="summary", year=2025))
    assert p2["data"]["fiscal_period"]["label"] == "2025-07-01~2025-12-31 · 6개월 결산기"
    assert "yoy_comparable" not in p2["data"]["summary"] and p2["data"]["summary"]["net_income_12m_krw"] == 36_174_226_674
    assert not any("1년 전이 아니다" in w for w in p2["warnings"])
