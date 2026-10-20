from datetime import date

from open_proxy_mcp.services.financial_metrics import _quarterly_status


def test_missing_q4_after_due_date_is_explicitly_unsubmitted():
    rows = [
        {"fiscal_year": 2026, "fiscal_quarter": "Q1"},
        {"fiscal_year": 2026, "fiscal_quarter": "Q2"},
        {"fiscal_year": 2026, "fiscal_quarter": "Q3"},
    ]

    status = _quarterly_status(rows, 2026, 6, as_of=date(2026, 10, 1))

    assert status["missing"] == [{
        "fiscal_quarter": "Q4",
        "period_end": "2026-06-30",
        "status": "미제출",
    }]


def test_missing_q4_after_period_end_is_unsubmitted_even_before_statutory_deadline():
    status = _quarterly_status([], 2026, 6, as_of=date(2026, 8, 24))

    q4 = next(item for item in status["missing"] if item["fiscal_quarter"] == "Q4")
    assert q4["period_end"] == "2026-06-30"
    assert q4["status"] == "미제출"


def test_future_quarters_are_not_reported_as_missing():
    status = _quarterly_status([
        {"fiscal_year": 2026, "fiscal_quarter": "Q1"},
        {"fiscal_year": 2026, "fiscal_quarter": "Q2"},
    ], 2026, 12, as_of=date(2026, 8, 24))

    assert status["missing"] == []


# ── 분기 범위 순이익의 기준 표시 (DATA-NI2) ─────────────────────────────────────────

def test_quarterly_rows_label_net_income_as_total_including_nci(monkeypatch):
    """000230 2023 3분기: 주요계정 당기순이익 = 비지배 포함 총계 −574.41억(지배는 −255.5억). 값은 그대로, 기준을 행에 싣는다."""
    import asyncio

    from open_proxy_mcp.services import financial_metrics as FM
    from open_proxy_mcp.tools.financial_metrics import _render_quarterly, _render_qoq

    base = {"fs_div": "CFS", "thstrm_dt": "2023.09.30", "ord": "1"}
    rows = [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000000000000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500000000000"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "100000000000", "thstrm_add_amount": "300000000000"},
        {**base, "sj_div": "IS", "account_nm": "영업이익(손실)", "thstrm_amount": "-1000000000", "thstrm_add_amount": "-2000000000"},
        {**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": "-57441304690",
         "thstrm_add_amount": "-107771897498"},
    ]

    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        return (rows, None) if (year, reprt_code) == (2023, "11014") else ([], "no_filing")

    monkeypatch.setattr(FM, "_safe_fetch_acnt", fake_acnt)
    out, _w = asyncio.run(FM._build_quarterly("00000000", 2023, "CFS", fiscal_month=12))
    q3 = next(r for r in out if r["fiscal_quarter"] == "Q3")
    assert q3["net_income_krw"] == -57441304690          # 3개월 총계 그대로 — 값은 바뀌지 않는다
    assert q3["net_income_basis"] == "total_incl_nci"
    assert all(r["net_income_basis"] == "total_incl_nci" for r in out)

    md = "\n".join(_render_quarterly({"quarterly": out}))
    assert "순이익(총계·비지배 포함)" in md
    assert "net_income_basis" in md
    qoq_md = "\n".join(_render_qoq({"qoq": {"current": out[-1], "prior": out[-2] if len(out) > 1 else None, "alerts": []}}))
    assert "순이익(총계·비지배 포함)" in qoq_md


# ── 업종코드 조회: 공용 접속 풀로 질의 한 번 (B10 — 종전: 질의마다 새 접속 + 두 질의) ─────────────

def test_induty_lookup_uses_the_shared_pool_with_one_query(monkeypatch):
    import psycopg

    from open_proxy_mcp import db
    from open_proxy_mcp.services import financial_metrics as FM

    calls: list[tuple] = []

    def fake_pg_rows(sql, params=()):
        calls.append((sql, params))
        return [("471",)]

    def no_direct_connect(*a, **k):
        raise AssertionError("질의마다 새 접속을 열지 않는다")

    monkeypatch.setattr(db, "pg_rows", fake_pg_rows)
    monkeypatch.setattr(psycopg, "connect", no_direct_connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-used/for-test")

    assert FM._lookup_induty_code("00126380", "005930") == "471"
    assert len(calls) == 1                                  # 종전 두 질의(종목코드 → 법인코드)가 하나로
    sql, params = calls[0]
    assert "ticker = %s OR corp_code = %s" in sql and "ORDER BY CASE WHEN ticker = %s" in sql
    assert params == ("005930", "00126380", "005930")       # 종목코드 행을 우선(종전과 같은 순서)


def test_induty_lookup_distinguishes_no_row_from_outage(monkeypatch):
    from open_proxy_mcp import db
    from open_proxy_mcp.services import financial_metrics as FM

    monkeypatch.setattr(db, "pg_rows", lambda sql, params=(): [])       # 데이터 없음
    assert FM._lookup_induty_code("00126380", "005930") is None
    monkeypatch.setattr(db, "pg_rows", lambda sql, params=(): None)     # 미설정·장애
    assert FM._lookup_induty_code("00126380", "") is None


def test_induty_lookup_without_ticker_matches_by_corp_code_only(monkeypatch):
    from open_proxy_mcp import db
    from open_proxy_mcp.services import financial_metrics as FM

    calls: list[tuple] = []
    monkeypatch.setattr(db, "pg_rows", lambda sql, params=(): calls.append(params) or [("471",)])
    assert FM._lookup_induty_code("00126380", "") == "471"
    # 빈 종목코드는 NULL 로 넘긴다 — 「ticker = ''」 로 빈 종목코드 행을 고르지 않는다.
    assert calls == [(None, "00126380", None)]
