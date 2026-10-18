"""분기 재무 수집 파서(`scripts/market_fund_quarterly.py`)의 공백 — 응답은 있는데(fetched='ok') 지배 순이익·자본이 비던 행 (261018).

10-18 운영 실측(분기 표 1~3분기 ok 행 가운데 순이익·자본이 빈 75행, DART 원문 직접 대조):
  · 순이익 총계 행을 표준 계정코드 없이 낸 회사(「Ⅸ.반기순손실」 꼴, 036480 2026 1·2분기 등 40행) → 이름으로 찾는다.
  · 지배자본 행은 있는데 칸이 빈 보고서(011300 2026 1분기 등 26행) → 값이 있는 행만 지배자본 행으로 보고 총계 규칙으로.
  · 응답에서 재무상태표가 빠졌거나(365330·489460 2026 반기) 한 줄만 남았거나(000540 흥국화재 2026 반기), 손익에 순이익 행이
    아예 없는 보고서(084180 2023 반기 등) → 같은 접수번호의 재무제표 원본 XBRL(fnlttXbrl, 보고서당 1콜)에서 찾는다.
종전에 값이 나던 행은 그대로다 — 새 길은 종전에 None 이던 갈래에서만 탄다(아래 「종전 결과 불변」, 운영 원문 전 종목 대조는 PR 본문).

DART 0콜 — 가짜 클라이언트. 수집 통합 시험은 Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date

import pytest

from scripts import market_fund_quarterly as Q


def row(sj, aid, nm, ordv, cur="KRW", **amt):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": cur,
            **{k: ("" if v is None else str(v)) for k, v in amt.items()}}


NOSTD = "-표준계정코드 미사용-"


# ── 순이익: 이름으로 찾는 총계 행 ────────────────────────────────────────────

def _ofs_036480_h1():
    """036480 2026 반기(별도) 모양 — 순이익 총계 행이 표준 코드 없이 「Ⅸ.반기순손실」."""
    return [
        row("BS", "ifrs-full_Equity", "자 본 총 계", 32, thstrm_amount=34491416728),
        row("CIS", "dart_NonOperatingProfitLoss", "Ⅵ.영업외손익", 6, thstrm_amount=85912301, thstrm_add_amount=342957805),
        row("CIS", NOSTD, "Ⅸ.반기순손실", 13, thstrm_amount=-436633856, thstrm_add_amount=-645045944),
        row("CIS", "ifrs-full_ProfitLossBeforeTax", "Ⅶ.법인세비용차감전순이익(손실)", 26,
            thstrm_amount=-386299142, thstrm_add_amount=-645045944),
    ]


def test_ni_total_found_by_name_when_no_standard_code():
    assert Q.extract_controlling_ni_cum(_ofs_036480_h1()) == (-645045944.0, "TOTAL_NM")   # 누적(thstrm_add)
    assert Q.extract_controlling_eq(_ofs_036480_h1()) == (34491416728.0, "TOTAL")         # 자본은 종전 그대로


@pytest.mark.parametrize("nm", ["Ⅸ.당기순손실", "VII. 당기순이익(손실)", "분기순이익(손실)", "반기순이익", "당기순이익",
                                "Ⅵ.당기순이익(손실)", "9.당기순손익", "당분기순이익"])
def test_ni_total_name_forms(nm):
    rows = [row("CIS", NOSTD, nm, 5, thstrm_amount=7, thstrm_add_amount=11), row("BS", "ifrs-full_Equity", "자본총계", 1, thstrm_amount=1)]
    assert Q.extract_controlling_ni_cum(rows) == (11.0, "TOTAL_NM")


@pytest.mark.parametrize("nm", ["법인세비용차감전순이익(손실)", "Ⅶ.법인세비용차감전순손실", "계속영업이익(손실)", "총포괄손익",
                                "중단영업이익(손실)", "계속반기순이익의 귀속", "기본주당순이익", "영업이익(손실)"])
def test_ni_name_does_not_pick_other_lines(nm):
    rows = [row("CIS", NOSTD, nm, 5, thstrm_amount=7, thstrm_add_amount=11), row("BS", "ifrs-full_Equity", "자본총계", 1, thstrm_amount=1)]
    assert Q.extract_controlling_ni_cum(rows) == (None, "NO_INCOME_ROW")


def test_ni_name_row_mistagged_as_comprehensive_income_still_net_income():
    """298690 2024 1분기 — 「분기순손익」 행이 ComprehensiveIncome 코드로 태깅됐다. 이름이 순이익 꼴이면 순이익(XBRL ProfitLoss 와 같은 값)."""
    rows = [row("CIS", "ifrs-full_ComprehensiveIncome", "분기순손익", 9, thstrm_amount=22732987801, thstrm_add_amount=22732987801),
            row("CIS", "ifrs-full_ProfitLossBeforeTax", "법인세비용차감전순이익(손실)", 8, thstrm_amount=29850572446)]
    assert Q.extract_controlling_ni_cum(rows) == (22732987801.0, "TOTAL_NM")


def test_ni_name_total_minus_minority_and_blank_rows_skipped():
    rows = [row("CIS", NOSTD, "당기순이익(손실)", 3, thstrm_amount="", thstrm_add_amount=""),     # 빈 칸 — 건너뛴다
            row("CIS", NOSTD, "반기순이익(손실)", 4, thstrm_amount=50, thstrm_add_amount=100),
            row("CIS", NOSTD, "비지배지분", 6, thstrm_amount=5, thstrm_add_amount=10)]
    assert Q.extract_controlling_ni_cum(rows) == (90.0, "TOTAL_NM_MINUS_MINORITY")


# ── 자본: 지배자본 행이 있는데 칸이 빈 보고서 ─────────────────────────────────

def test_eq_blank_controlling_row_falls_through_to_total():
    """011300 2026 1분기 모양 — 「지배기업의 소유주에게 귀속되는 자본」 빈칸, 자본총계 306.1억(XBRL 원본도 Equity 만 같은 값)."""
    rows = [row("BS", "ifrs-full_Equity", "자본총계", 20, thstrm_amount=30613447879),
            row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 자본", 21, thstrm_amount=""),
            row("BS", "ifrs-full_IssuedCapital", "자본금", 25, thstrm_amount=15856381900)]
    assert Q.extract_controlling_eq(rows) == (30613447879.0, "TOTAL")
    rows.append(row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 30, thstrm_amount=613447879))
    assert Q.extract_controlling_eq(rows) == (30000000000.0, "TOTAL_MINUS_MINORITY")


def test_eq_blank_first_id_row_uses_next_valued_id_row_and_blank_nm_row_falls_through():
    rows = [row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", 3, thstrm_amount=""),
            row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분 합계", 9, thstrm_amount=700),
            row("BS", "ifrs-full_Equity", "자본총계", 10, thstrm_amount=800)]
    assert Q.extract_controlling_eq(rows) == (700.0, "BS_ID")
    rows = [row("BS", NOSTD, "지배지분", 3, thstrm_amount=""), row("BS", "ifrs-full_Equity", "자본총계", 10, thstrm_amount=800)]
    assert Q.extract_controlling_eq(rows) == (800.0, "TOTAL")      # 069460 2021 반기 모양


# ── 종전 결과 불변: 종전에 값이 나던 모양은 값·케이스 그대로 ──────────────────

@pytest.mark.parametrize("rows, ni, eq", [
    # 명시 지배행(코드)
    ([row("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배", 1, thstrm_amount=1, thstrm_add_amount=2),
      row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배", 1, thstrm_amount=9),
      row("BS", "ifrs-full_Equity", "자본총계", 2, thstrm_amount=10)], (2.0, "IS_ID"), (9.0, "BS_ID")),
    # 이름 지배행
    ([row("CIS", NOSTD, "지배기업 소유주지분", 1, thstrm_amount=3, thstrm_add_amount=4),
      row("BS", NOSTD, "지배기업소유주지분", 1, thstrm_amount=8)], (4.0, "CIS_NM"), (8.0, "BS_NM")),
    # 총계 − 비지배 (코드로 찾은 총계 — 이름 꼴 행이 함께 있어도 코드가 이긴다)
    ([row("IS", "ifrs-full_ProfitLoss", "당기순이익", 1, thstrm_amount=10, thstrm_add_amount=20),
      row("IS", NOSTD, "당기순이익(손실)", 2, thstrm_amount=99, thstrm_add_amount=99),
      row("IS", "ifrs-full_ProfitLossAttributableToNoncontrollingInterests", "비지배", 3, thstrm_amount=1, thstrm_add_amount=2),
      row("BS", "ifrs-full_Equity", "자본총계", 1, thstrm_amount=50),
      row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 2, thstrm_amount=5)],
     (18.0, "TOTAL_MINUS_MINORITY"), (45.0, "TOTAL_MINUS_MINORITY")),
    # 총계만
    ([row("CIS", "ifrs-full_ProfitLoss", "분기순이익", 1, thstrm_amount=7),
      row("BS", NOSTD, "자본총계", 1, thstrm_amount=70)], (7.0, "TOTAL"), (70.0, "TOTAL")),
    # 손익·재무상태 둘 다 없음
    ([row("CF", "x", "현금", 1, thstrm_amount=1)], (None, "NODATA"), (None, "NODATA")),
])
def test_previous_results_unchanged(rows, ni, eq):
    assert Q.extract_controlling_ni_cum(rows) == ni
    assert Q.extract_controlling_eq(rows) == eq


# ── XBRL 원본 ───────────────────────────────────────────────────────────────

def _xbrl_zip(facts, contexts, units=("KRW",), name="entity01135084_2026-06-30.xbrl", extra_files=()):
    """facts = [(요소, 맥락 id, 값, 통화)] · contexts = {id: (차원 {축: 멤버}, 기간)} — 기간 ('I', 'YYYY-MM-DD') | ('D', 시작, 끝)."""
    ctx_xml = []
    for cid, (dims, per) in contexts.items():
        seg = "".join(f'<xbrldi:explicitMember dimension="ifrs-full:{a}">ifrs-full:{m}</xbrldi:explicitMember>' for a, m in dims.items())
        seg = f"<xbrli:segment>{seg}</xbrli:segment>" if seg else ""
        p = (f"<xbrli:instant>{per[1]}</xbrli:instant>" if per[0] == "I"
             else f"<xbrli:startDate>{per[1]}</xbrli:startDate><xbrli:endDate>{per[2]}</xbrli:endDate>")
        ctx_xml.append(f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">01135084</xbrli:identifier>{seg}'
                       f"</xbrli:entity><xbrli:period>{p}</xbrli:period></xbrli:context>")
    unit_xml = "".join(f'<xbrli:unit id="{u}"><xbrli:measure>iso4217:{u}</xbrli:measure></xbrli:unit>' for u in units)
    fact_xml = "".join(f'<ifrs-full:{n} contextRef="{c}" unitRef="{u}" decimals="0">{v}</ifrs-full:{n}>' for n, c, v, u in facts)
    doc = ('<?xml version="1.0" encoding="UTF-8"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
           'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2023-03-23/ifrs-full" '
           'xmlns:iso4217="http://www.xbrl.org/2003/iso4217">' + "".join(ctx_xml) + unit_xml + fact_xml + "</xbrli:xbrl>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, doc)
        for n in extra_files:
            z.writestr(n, "<x/>")
    return buf.getvalue()


CONS, SEP = {"ConsolidatedAndSeparateFinancialStatementsAxis": "ConsolidatedMember"}, \
    {"ConsolidatedAndSeparateFinancialStatementsAxis": "SeparateMember"}
END, START = date(2026, 6, 30), date(2026, 1, 1)


def _ctx_365330():
    comp = dict(CONS, ComponentsOfEquityAxis="RetainedEarningsMember")
    return {
        "C_I": (CONS, ("I", "2026-06-30")), "S_I": (SEP, ("I", "2026-06-30")),
        "P_I": (CONS, ("I", "2025-12-31")), "C_COMP": (comp, ("I", "2026-06-30")),
        "C_YTD": (CONS, ("D", "2026-01-01", "2026-06-30")), "C_3M": (CONS, ("D", "2026-04-01", "2026-06-30")),
        "S_YTD": (SEP, ("D", "2026-01-01", "2026-06-30")),
    }


def test_xbrl_consolidated_controlling_equity_and_ytd_net_income():
    """365330 2026 반기 — API 응답엔 재무상태표가 없다. 원본: 자본총계 908.32억 · 지배 904.68억 · 비지배 3.64억."""
    data = _xbrl_zip([
        ("Equity", "C_I", 90832017423, "KRW"), ("EquityAttributableToOwnersOfParent", "C_I", 90467706469, "KRW"),
        ("NoncontrollingInterests", "C_I", 364310954, "KRW"), ("Equity", "S_I", 90331592868, "KRW"),
        ("Equity", "P_I", 95895022709, "KRW"), ("Equity", "C_COMP", 17148153411, "KRW"),
        ("ProfitLoss", "C_YTD", -3881565508, "KRW"), ("ProfitLoss", "C_3M", -3158607519, "KRW"),
        ("ProfitLossAttributableToNoncontrollingInterests", "C_YTD", 1551783, "KRW"),
        ("ProfitLoss", "S_YTD", -4112277651, "KRW"),
    ], _ctx_365330())
    got = Q.xbrl_controlling(data, "CFS", END, START)
    assert got["eq"] == (90467706469.0, "XBRL_ID", "KRW")
    assert got["ni"] == (-3881565508.0 - 1551783.0, "XBRL_TOTAL_MINUS_MINORITY", "KRW")   # 누적(1/1~6/30)
    sep = Q.xbrl_controlling(data, "OFS", END, START)
    assert sep["eq"] == (90331592868.0, "XBRL_TOTAL", "KRW") and sep["ni"] == (-4112277651.0, "XBRL_TOTAL", "KRW")


def test_xbrl_needs_exact_period_end_and_rejects_conflicts_and_currency_mix():
    ctx = _ctx_365330()
    # 488060 2025 3분기 모양 — 그 분기 말 자본이 없고 전기 말 것만 → 쓰지 않는다
    only_prior = _xbrl_zip([("Equity", "P_I", 9351733110, "KRW")], ctx)
    assert Q.xbrl_controlling(only_prior, "CFS", END, START) == {}
    # 같은 요소·맥락에 다른 값 둘 → 버린다(총계로 내려가지도 않는다)
    clash = _xbrl_zip([("EquityAttributableToOwnersOfParent", "C_I", 1, "KRW"), ("EquityAttributableToOwnersOfParent", "C_I", 2, "KRW"),
                       ("Equity", "C_I", 5, "KRW")], ctx)
    assert "eq" not in Q.xbrl_controlling(clash, "CFS", END, START)
    # 총계와 비지배의 통화가 다르면 빼지 않는다
    mix = _xbrl_zip([("Equity", "C_I", 5, "KRW"), ("NoncontrollingInterests", "C_I", 1, "USD")], ctx, units=("KRW", "USD"))
    assert "eq" not in Q.xbrl_controlling(mix, "CFS", END, START)
    # 회계연도 첫날이 다른 누적(3개월)만 있으면 순이익을 쓰지 않는다
    q_only = _xbrl_zip([("ProfitLoss", "C_3M", 9, "KRW")], ctx)
    assert "ni" not in Q.xbrl_controlling(q_only, "CFS", END, START)
    # 인스턴스가 없거나 둘이면 아무것도 쓰지 않는다
    assert Q.xbrl_controlling(_xbrl_zip([], ctx, name="a.xml"), "CFS", END, START) == {}
    assert Q.xbrl_controlling(_xbrl_zip([("Equity", "C_I", 5, "KRW")], ctx, extra_files=("b.xbrl",)), "CFS", END, START) == {}


def test_xbrl_separate_only_filer_without_dimension():
    ctx = {"I": ({}, ("I", "2026-06-30")), "D": ({}, ("D", "2026-01-01", "2026-06-30"))}
    data = _xbrl_zip([("Equity", "I", 1096435000000, "KRW"), ("ProfitLoss", "D", 26167000000, "KRW")], ctx)
    assert Q.xbrl_controlling(data, "OFS", END, START) == {"eq": (1096435000000.0, "XBRL_TOTAL", "KRW"),
                                                         "ni": (26167000000.0, "XBRL_TOTAL", "KRW")}
    assert Q.xbrl_controlling(data, "CFS", END, START) == {}         # 차원 없는 값을 연결로 보지는 않는다


def test_fiscal_year_start_follows_fiscal_month():
    assert Q.fiscal_year_start(2026, 2, 12) == date(2026, 1, 1)
    assert Q.fiscal_year_start(2024, 1, 3) == date(2024, 4, 1)       # 3월 결산 「2024 1분기」 = 2024.06 말
    assert Q.fiscal_year_start(2026, 3, 6) == date(2025, 7, 1)       # 6월 결산 「2026 3분기」 = 2026.03 말


# ── 수집 통합(Postgres 17) ───────────────────────────────────────────────────

psycopg = pytest.importorskip("psycopg")

FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""
FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""
TODAY = date(2026, 10, 18)


class _Today(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


class _FakeDart:
    def __init__(self, avail: dict, xbrl: dict):
        self.avail, self.xbrl = avail, xbrl
        self.calls: list[tuple] = []

    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        from open_proxy_mcp.dart.client import DartClientError
        self.calls.append(("acnt", corp_code, bsns_year, reprt_code, fs_div))
        rows = self.avail.get((corp_code, bsns_year, reprt_code, fs_div))
        if rows is None:
            raise DartClientError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": rows}

    async def get_fnltt_xbrl(self, rcept_no, reprt_code="11011"):
        from open_proxy_mcp.dart.client import DartClientError
        self.calls.append(("xbrl", rcept_no, reprt_code))
        if rcept_no not in self.xbrl:
            raise DartClientError("014", "파일이 존재하지 않습니다.")
        return self.xbrl[rcept_no]


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


def _bs_less(rcept):
    """365330 2026 반기 응답 모양 — 손익·현금흐름만, 재무상태표 없음."""
    return [{**row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 33,
                   thstrm_amount=-3883117291, thstrm_add_amount=-3883117291), "rcept_no": rcept},
            {**row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 1, thstrm_amount=1), "rcept_no": rcept}]


@pytest.fixture
def gdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        for t in "ABC":
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched) VALUES (%s, %s, 'ok')", (t, f"0000000{t}"))
            for qq in (1, 2, 3):
                c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case) "
                          "VALUES (%s, 2026, %s, %s, 'CFS', 1, 1, 'ok', 'IS_ID', 'BS_ID')", (t, qq, Q.Q_TO_RC[qq]))
        # 지난 실행이 비워 둔 칸: A 반기 자본(재무상태표 없는 응답) · B 반기 순이익(표준 코드 없는 총계 행)
        c.execute("UPDATE dart_finstat_q SET eq=NULL, ni_cum=-3883117291, ni_case='CIS_ID', eq_case='NODATA' "
                  "WHERE ticker='A' AND fy=2026 AND quarter=2")
        c.execute("UPDATE dart_finstat_q SET ni_cum=NULL, eq=34491416728, fs='OFS', ni_case='NO_INCOME_ROW', eq_case='TOTAL' "
                  "WHERE ticker='B' AND fy=2026 AND quarter=2")
    fake = _FakeDart(
        {("0000000A", "2026", "11012", "CFS"): _bs_less("20260814003676"),
         ("0000000B", "2026", "11012", "OFS"): _ofs_036480_h1(),
         ("0000000C", "2026", "11012", "CFS"): _bs_less("20260814009999")},
        {"20260814003676": _xbrl_zip([("Equity", "C_I", 90832017423, "KRW"),
                                      ("EquityAttributableToOwnersOfParent", "C_I", 90467706469, "KRW")], _ctx_365330())})
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    monkeypatch.setattr(Q, "date", _Today)
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    return pg17, fake


def test_refetch_only_named_cells_and_fill_from_name_and_xbrl(gdb, capsys):
    url, fake = gdb
    asyncio.run(Q.fetch([2026], conc=1, only={("A", 2026, 2), ("B", 2026, 2), ("C", 2026, 2)}))
    # 이름 붙인 세 칸만 — ok 인 다른 칸(1·3분기)은 다시 받지 않는다
    assert {(k[1][-1], k[3]) for k in fake.calls if k[0] == "acnt"} == {("A", "11012"), ("B", "11012"), ("C", "11012")}
    got = {r[0]: r[1:] for r in _q(url, "SELECT ticker, ni_cum, eq, fetched, ni_case, eq_case FROM dart_finstat_q "
                                        "WHERE fy=2026 AND quarter=2 ORDER BY ticker")}
    assert got["A"] == (-3883117291.0, 90467706469.0, "ok", "CIS_ID", "XBRL_ID")     # XBRL 원본의 지배자본
    assert got["B"] == (-645045944.0, 34491416728.0, "ok", "TOTAL_NM", "TOTAL")      # 이름으로 찾은 총계 — XBRL 콜 없음
    assert got["C"] == (-3883117291.0, None, "ok", "CIS_ID", "NODATA")               # 원본도 못 받으면 종전처럼 빈 채로
    assert [k for k in fake.calls if k[0] == "xbrl"] == [("xbrl", "20260814003676", "11012"), ("xbrl", "20260814009999", "11012")]
    out = capsys.readouterr().out
    assert "XBRL 원본 2건 → 채운 칸 1" in out
    assert _q(url, "SELECT count(*) FROM dart_finstat_q WHERE quarter<>2 AND ni_cum=1 AND eq=1") == [(6,)]   # 다른 칸 그대로


def test_regular_fetch_does_not_revisit_ok_cells(gdb):
    url, fake = gdb
    asyncio.run(Q.fetch([2026], conc=1))
    assert fake.calls == []                                     # ok 는 완료 — 정기 실행의 재개 규칙은 종전 그대로
