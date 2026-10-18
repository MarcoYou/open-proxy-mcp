# -*- coding: utf-8 -*-
"""DART 키 한도(020) 계열 오류에서 배치 수집이 **멈추고, 그 응답을 행에 남기지 않는다** (261019).

10-19 운영: 연간 재무 다시 받기(`market_val_series --fetch`)가 키의 하루 한도에 닿은 뒤에도 남은 2,074건을 쉬지 않고 불러
모두 `err:DART API 에러 [020]…` 으로 적었다(몇 분 동안 분당 수백 콜, 전부 거절). 같은 때 분기 표 전기 칸 보충(`--prior-from-next`)은
020 예외로 죽으면서 그때까지 받은 칸까지 버렸다(끝에 한 번에 쓰는 구조 — 쓴 칸 0). 이 파일이 지키는 것:
  ① 키 한도·키 문제·점검(`DART_BATCH_HALT_STATUSES`)이면 그 자리에서 멈춘다 — 다음 항목을 부르지 않는다.
  ② 멈춘 항목과 남은 항목의 행은 그대로다(err 로 적지 않는다) — 다음 실행의 재개 규칙이 그대로 받는다.
     분기 수집의 XBRL 원본 대체 경로도 같다 — 한도로 못 받은 원본을 「원본 없음」으로 보고 칸을 빈 채로 적지 않는다.
  ③ 멈추기 전에 받은 결과는 쓴다.
  ④ 클라이언트는 키의 사정(한도·키 문제)이면 남은 키를 모두 본 뒤에야 오류를 올린다 — 「멈춤 = 쓸 키가 없다」가 되게.
     종전엔 보조 키로 한 번만 돌았다(연달아 두 키가 막혔으면 셋째 키가 놀아도 오류). 배치가 다음 항목에서 다음 키로 넘어가던
     길이 이제 멈춤으로 바뀌므로 그 몫을 클라이언트가 맡는다.

DART 0콜 — 가짜 클라이언트·가짜 HTTP. 수집 통합 시험은 Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.dart.client import DartClientError, dart_halts_batch


# ── 어떤 오류에서 멈추나 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("status,halts", [
    ("020", True),    # 요청 제한 초과(키의 하루 한도)
    ("010", True),    # 등록되지 않은 키
    ("011", True),    # 사용할 수 없는 키
    ("012", True),    # 접근할 수 없는 IP
    ("800", True),    # 시스템 점검
    ("901", True),    # 개인정보 보유기간 만료로 쓸 수 없는 키
    ("013", False),   # 조회된 데이터 없음 — 그 항목의 답
    ("014", False),   # 파일 없음 — 그 항목의 답
    ("021", False),   # 조회 가능한 회사 개수 초과(최대 100건) — 그 요청의 사정
    ("100", False),   # 필드의 부적절한 값 — 그 요청의 사정
    ("900", False),   # 정의되지 않은 오류
    ("TRANSPORT_ERROR", False),
])
def test_halt_statuses(status, halts):
    assert dart_halts_batch(DartClientError(status, "x")) is halts


def test_only_dart_client_errors_halt():
    assert dart_halts_batch(RuntimeError("DART API 에러 [020]: 문구만 같은 다른 예외")) is False


# ── 클라이언트: 키의 사정이면 남은 키를 모두 본다 ──────────────────────────────

K1, K2, K3 = "1" * 40, "2" * 40, "3" * 40


class _Resp:
    def __init__(self, data=None, content=b""):
        self._data, self.content = data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Http:
    """키마다 정해 둔 상태 코드로 답한다 — JSON 경로는 {"status": …}, 바이너리 경로는 ZIP 또는 XML 오류."""

    def __init__(self, answers: dict):
        self.answers, self.seen = answers, []

    async def get(self, url, params=None, timeout=None):
        key = params["crtfc_key"]
        self.seen.append(key)
        st = self.answers[key]
        if url.endswith(".json"):
            return _Resp({"status": st, "message": "m", "list": [{"k": key}]} if st == "000" else {"status": st, "message": "m"})
        if st == "000":
            return _Resp(content=b"PK\x03\x04zip")
        return _Resp(content=f"<?xml version='1.0'?><result><status>{st}</status><message>m</message></result>".encode())


def _client(monkeypatch, answers):
    from open_proxy_mcp.dart.client import DartClient
    monkeypatch.setenv("OPENDART_API_KEY", K1)
    monkeypatch.setenv("OPENDART_API_KEY_2", K2)
    monkeypatch.setenv("OPENDART_API_KEY_3", K3)
    for i in range(4, 10):
        monkeypatch.delenv(f"OPENDART_API_KEY_{i}", raising=False)
    c = DartClient()
    c._http = _Http(answers)

    async def _no_throttle():
        return None
    c._throttle_api = _no_throttle
    return c


def test_json_quota_tries_every_key_before_raising(monkeypatch):
    c = _client(monkeypatch, {K1: "020", K2: "020", K3: "000"})
    out = asyncio.run(c._request("fnlttSinglAcntAll.json", {"corp_code": "00126380"}))
    assert out["list"] == [{"k": K3}]
    assert c._http.seen == [K1, K2, K3]


def test_json_quota_on_every_key_raises_after_one_pass(monkeypatch):
    c = _client(monkeypatch, {K1: "020", K2: "020", K3: "020"})
    with pytest.raises(DartClientError) as ei:
        asyncio.run(c._request("fnlttSinglAcntAll.json", {"corp_code": "00126380"}))
    assert ei.value.status == "020" and dart_halts_batch(ei.value)
    assert c._http.seen == [K1, K2, K3]          # 키마다 한 번 — 돌고 돌지 않는다


def test_json_other_status_still_retries_once(monkeypatch):
    """키와 무관한 답(013 자료 없음 등)은 종전처럼 보조 키로 한 번만."""
    c = _client(monkeypatch, {K1: "013", K2: "013", K3: "000"})
    with pytest.raises(DartClientError) as ei:
        asyncio.run(c._request("fnlttSinglAcntAll.json", {"corp_code": "00126380"}))
    assert ei.value.status == "013"
    assert c._http.seen == [K1, K2]


def test_json_quota_then_other_answer_stops_rotating(monkeypatch):
    """다음 키가 키와 무관한 답(013)을 주면 그게 답이다 — 셋째 키까지 가지 않는다."""
    c = _client(monkeypatch, {K1: "020", K2: "013", K3: "000"})
    with pytest.raises(DartClientError) as ei:
        asyncio.run(c._request("fnlttSinglAcntAll.json", {"corp_code": "00126380"}))
    assert ei.value.status == "013"
    assert c._http.seen == [K1, K2]


def test_binary_quota_tries_every_key_before_raising(monkeypatch):
    c = _client(monkeypatch, {K1: "020", K2: "020", K3: "000"})
    assert asyncio.run(c._request_binary("fnlttXbrl.xml", {"rcept_no": "20260814003676"})).startswith(b"PK")
    assert c._http.seen == [K1, K2, K3]
    c = _client(monkeypatch, {K1: "020", K2: "020", K3: "020"})
    with pytest.raises(DartClientError) as ei:
        asyncio.run(c._request_binary("fnlttXbrl.xml", {"rcept_no": "20260814003676"}))
    assert ei.value.status == "020" and c._http.seen == [K1, K2, K3]


# ── 수집 통합(Postgres 17) ───────────────────────────────────────────────────

psycopg = pytest.importorskip("psycopg")

from scripts import market_fund_quarterly as Q  # noqa: E402
from scripts import market_val_series as S  # noqa: E402

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
QUOTA = DartClientError("020", "사용한도를 초과하였습니다.")


class _Today(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


class _QuotaDart:
    """`ok_calls` 번째 콜까지는 정해 둔 응답(없으면 013), 그 뒤로는 모든 콜이 020 — 키가 그 자리에서 한도에 닿은 모양.
    공시 목록은 늘 013(한도와 무관 — 결산월 시험에서 회사 개황 콜만 세려고)."""

    def __init__(self, ok_calls: int, acnt=None, xbrl=None, company=None):
        self.left = ok_calls
        self.acnt, self.xbrl, self.company = acnt or {}, xbrl or {}, company or {}
        self.calls: list[tuple] = []

    def _spend(self):
        if self.left <= 0:
            raise QUOTA
        self.left -= 1

    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        self.calls.append(("acnt", corp_code, str(bsns_year), reprt_code, fs_div))
        self._spend()
        rows = self.acnt.get((corp_code, str(bsns_year), reprt_code, fs_div))
        if rows is None:
            raise DartClientError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": rows}

    async def get_fnltt_xbrl(self, rcept_no, reprt_code="11011"):
        self.calls.append(("xbrl", rcept_no, reprt_code))
        self._spend()
        if rcept_no not in self.xbrl:
            raise DartClientError("014", "파일이 존재하지 않습니다.")
        return self.xbrl[rcept_no]

    async def get_company_info(self, corp_code):
        self.calls.append(("company", corp_code))
        self._spend()
        return self.company.get(corp_code, {})

    async def search_filings(self, **kw):
        raise DartClientError("013", "조회된 데이타가 없습니다.")


def r(sj, aid, rcept="20260814000001", **amt):
    return {"sj_div": sj, "account_id": aid, "account_nm": aid, "ord": "1", "currency": "KRW", "rcept_no": rcept,
            **{k: str(v) for k, v in amt.items()}}


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def db(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        for t in "ABC":
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched) VALUES (%s, %s, 'ok')", (t, f"0000000{t}"))
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setenv("SERIES_SLEEP", "0")
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    monkeypatch.setattr(Q, "date", _Today)
    monkeypatch.setattr(S, "date", _Today)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return pg17


def _use(monkeypatch, fake):
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    return fake


# ── 연간(market_val_series) ─────────────────────────────────────────────────

ANNUAL_OK = [r("BS", "ifrs-full_EquityAttributableToOwnersOfParent", thstrm_amount=5000),
             r("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", thstrm_amount=500, frmtrm_amount=450)]


@pytest.fixture
def ydb(db, monkeypatch):
    with psycopg.connect(db, autocommit=True) as c:
        for t in "ABC":
            for fy in range(2019, 2026):
                c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES (%s, %s, 'CFS', 1, 1, 'ok')", (t, fy))
        # 남은 칸은 2018 뿐: A 행 없음 · B nodata(다시 받는 해) · C 행 없음
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES ('B', 2018, NULL, NULL, NULL, 'nodata')")
    monkeypatch.setattr(S, "REFETCH_NODATA_FY", frozenset({2018}))
    return db


def test_annual_fetch_stops_at_quota_and_leaves_rows_as_they_were(ydb, monkeypatch, capsys):
    fake = _use(monkeypatch, _QuotaDart(1, acnt={("0000000A", "2018", "11011", "CFS"): ANNUAL_OK}))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    asyncio.run(S.fetch())
    assert fake.calls == [("acnt", "0000000A", "2018", "11011", "CFS"),
                          ("acnt", "0000000B", "2018", "11011", "CFS")]          # C 는 부르지 않는다
    got = _q(ydb, "SELECT ticker, fs, ni, eq, fetched FROM dart_finstat_y WHERE fy=2018 ORDER BY ticker")
    assert got == [("A", "CFS", 500.0, 5000.0, "ok"),                           # 멈추기 전 결과는 쓴다
                   ("B", None, None, None, "nodata")]                           # 멈춘 항목은 그대로 · C 는 행이 생기지 않는다
    assert _q(ydb, "SELECT count(*) FROM dart_finstat_y WHERE fetched LIKE 'err:%%'") == [(0,)]
    out = capsys.readouterr().out
    assert "::warning::" in out and "[020]" in out and "1/3" in out
    # 다음 실행(한도가 풀린 뒤)은 남은 두 칸을 그대로 받는다
    fake2 = _use(monkeypatch, _QuotaDart(9))
    asyncio.run(S.fetch())
    assert [k[1] for k in fake2.calls if k[4] == "CFS"] == ["0000000B", "0000000C"]


def test_annual_fetch_other_errors_are_still_recorded_and_paced(ydb, monkeypatch):
    """키와 무관한 오류(예: 900)는 종전처럼 그 항목만 err 로 적고 다음 항목으로 간다(다음 실행이 다시 받는다)."""
    class _Odd(_QuotaDart):
        async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
            if corp_code == "0000000B":
                self.calls.append(("acnt", corp_code, str(bsns_year), reprt_code, fs_div))
                raise DartClientError("900", "정의되지 않은 오류")
            return await super().get_fnltt_singl_acnt_all(corp_code, bsns_year, reprt_code, fs_div)
    fake = _use(monkeypatch, _Odd(9, acnt={("0000000A", "2018", "11011", "CFS"): ANNUAL_OK,
                                           ("0000000C", "2018", "11011", "CFS"): ANNUAL_OK}))
    asyncio.run(S.fetch())
    got = dict(_q(ydb, "SELECT ticker, fetched FROM dart_finstat_y WHERE fy=2018"))
    assert got["A"] == "ok" and got["C"] == "ok" and got["B"].startswith("err:DART API 에러 [900]")
    assert len(fake.calls) == 3


def test_backfill_restated_stops_at_quota(ydb, monkeypatch, capsys):
    rows = [r("BS", "ifrs-full_EquityAttributableToOwnersOfParent", thstrm_amount=5000, frmtrm_amount=4000, bfefrmtrm_amount=3000),
            r("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", thstrm_amount=500, frmtrm_amount=400, bfefrmtrm_amount=300)]
    fake = _use(monkeypatch, _QuotaDart(1, acnt={("0000000A", "2024", "11011", "CFS"): rows}))
    asyncio.run(S.backfill_restated(None, 2024))
    assert [k[1] for k in fake.calls] == ["0000000A", "0000000B"]               # C 는 부르지 않는다
    assert _q(ydb, "SELECT ticker, fy, ni_restated, eq_restated FROM dart_finstat_y WHERE ni_restated IS NOT NULL "
                   "ORDER BY ticker, fy") == [("A", 2022, 300.0, 3000.0), ("A", 2023, 400.0, 4000.0)]
    assert "[020]" in capsys.readouterr().out


# ── 분기(market_fund_quarterly) ─────────────────────────────────────────────

@pytest.fixture
def qdb(db):
    with psycopg.connect(db, autocommit=True) as c:
        for t in "ABC":
            for qq in (1, 2, 3):
                c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case) "
                          "VALUES (%s, 2026, %s, %s, 'CFS', 1, 1, 'ok', 'IS_ID', 'BS_ID')", (t, qq, Q.Q_TO_RC[qq]))
            c.execute("UPDATE dart_finstat_q SET ni_cum=NULL, eq=NULL, fetched='nodata', ni_case='NODATA', eq_case='NODATA' "
                      "WHERE ticker=%s AND fy=2026 AND quarter=2", (t,))
    return db


ONLY = {("A", 2026, 2), ("B", 2026, 2), ("C", 2026, 2)}
H1_OK = [r("BS", "ifrs-full_EquityAttributableToOwnersOfParent", thstrm_amount=5000),
         r("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", thstrm_amount=100, thstrm_add_amount=200)]
H1_NO_BS = [r("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", thstrm_amount=100, thstrm_add_amount=200)]


def _h1_cells(url):
    return _q(url, "SELECT ticker, ni_cum, eq, fetched, ni_case, eq_case FROM dart_finstat_q WHERE fy=2026 AND quarter=2 ORDER BY ticker")


def test_quarterly_fetch_stops_at_quota_without_err_rows(qdb, monkeypatch, capsys):
    fake = _use(monkeypatch, _QuotaDart(1, acnt={(f"0000000{t}", "2026", "11012", "CFS"): H1_OK for t in "ABC"}))
    asyncio.run(Q.fetch([2026], conc=1, only=ONLY))
    assert len(fake.calls) == 2                                                  # 받은 1콜 + 거절 1콜, 셋째 칸은 부르지 않는다
    cells = _h1_cells(qdb)
    assert sum(1 for c in cells if c[1:] == (200.0, 5000.0, "ok", "CIS_ID", "BS_ID")) == 1   # 멈추기 전 결과는 쓴다
    assert sum(1 for c in cells if c[1:] == (None, None, "nodata", "NODATA", "NODATA")) == 2   # 나머지는 그대로
    assert _q(qdb, "SELECT count(*) FROM dart_finstat_q WHERE fetched LIKE 'err:%%'") == [(0,)]
    assert "err 0" in capsys.readouterr().out


def test_quarterly_xbrl_quota_does_not_write_the_cell_as_empty(qdb, monkeypatch):
    """응답에 재무상태표가 없어 XBRL 원본을 보러 갔는데 그 콜이 한도에 걸렸다 — 「원본 없음」이 아니다. 칸을 적지 않고 멈춘다."""
    fake = _use(monkeypatch, _QuotaDart(1, acnt={(f"0000000{t}", "2026", "11012", "CFS"): H1_NO_BS for t in "ABC"}))
    before = _h1_cells(qdb)
    asyncio.run(Q.fetch([2026], conc=1, only=ONLY))
    assert [k[0] for k in fake.calls] == ["acnt", "xbrl"]                       # 다음 칸을 부르지 않는다
    assert _h1_cells(qdb) == before                                              # 순이익만 든 칸으로 적지 않는다
    assert _q(qdb, "SELECT count(*) FROM dart_finstat_q WHERE fetched LIKE 'err:%%'") == [(0,)]


def test_quarterly_xbrl_other_errors_still_leave_the_cell_empty(qdb, monkeypatch):
    """원본이 정말 없으면(014) 종전처럼 그 칸은 빈 채로 적고 다음 칸으로 간다."""
    fake = _use(monkeypatch, _QuotaDart(9, acnt={(f"0000000{t}", "2026", "11012", "CFS"): H1_NO_BS for t in "ABC"}))
    asyncio.run(Q.fetch([2026], conc=1, only=ONLY))
    assert [k[0] for k in fake.calls] == ["acnt", "xbrl"] * 3
    assert {c[1:] for c in _h1_cells(qdb)} == {(200.0, None, "ok", "CIS_ID", "NODATA")}


def test_quarterly_refetch_failure_does_not_wipe_an_ok_cell(qdb, monkeypatch):
    """`--refetch` 는 ok 칸도 다시 받는다. 그때 그 칸만의 오류(900)·환율 실패가 나도 받아 둔 값을 빈 값·err 로 덮지 않는다
    (서버는 이 표의 값을 상태와 무관하게 읽는다). nodata 칸 위에는 종전처럼 err 를 적어 다음 실행이 다시 받는다."""
    class _Odd(_QuotaDart):
        async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
            if corp_code in ("0000000A", "0000000B"):
                self.calls.append(("acnt", corp_code, str(bsns_year), reprt_code, fs_div))
                raise DartClientError("900", "정의되지 않은 오류")
            return await super().get_fnltt_singl_acnt_all(corp_code, bsns_year, reprt_code, fs_div)
    usd = [{**x, "currency": "USD"} for x in H1_OK]
    _use(monkeypatch, _Odd(9, acnt={("0000000C", "2026", "11013", "CFS"): usd}))

    async def _no_rate(*a, **k):
        return None
    monkeypatch.setattr(Q, "fx_to_krw", _no_rate)
    asyncio.run(Q.fetch([2026], conc=1, only={("A", 2026, 1), ("B", 2026, 2), ("C", 2026, 1)}))
    got = {(t, qq): rest for t, qq, *rest in _q(qdb, "SELECT ticker, quarter, ni_cum, eq, fetched FROM dart_finstat_q "
                                                     "WHERE (ticker, quarter) IN (('A', 1), ('B', 2), ('C', 1))")}
    assert got[("A", 1)] == [1.0, 1.0, "ok"]                                     # DART 오류 — 받아 둔 값 그대로
    assert got[("C", 1)] == [1.0, 1.0, "ok"]                                     # 환율 실패 — 받아 둔 값 그대로
    assert got[("B", 2)][:2] == [None, None] and got[("B", 2)][2].startswith("err:DART API 에러 [900]")   # nodata 위엔 종전처럼


# ── 분기 전기 칸 보충(--prior-from-next) ────────────────────────────────────

def _next_year_q1(ni_prior, eq_prior, eq_now=10000):
    """다음 해 1분기 보고서 — 손익 전기 누적 · 재무상태표 지배자본 · 자본변동표 기말 행(당기 = 지배자본, 전기 = 전년 같은 분기 말)."""
    return [r("BS", "ifrs-full_EquityAttributableToOwnersOfParent", thstrm_amount=eq_now),
            r("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", thstrm_amount=1, thstrm_add_amount=1,
              frmtrm_q_amount=ni_prior, frmtrm_add_amount=ni_prior),
            r("SCE", "ifrs-full_Equity", thstrm_amount=eq_now, frmtrm_q_amount=eq_prior)]


@pytest.fixture
def pdb(db):
    with psycopg.connect(db, autocommit=True) as c:
        for t in "ABC":
            c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case) "
                      "VALUES (%s, 2023, 1, '11013', 'OFS', NULL, NULL, 'nodata', 'NODATA', 'NODATA')", (t,))
    return db


PKEYS = {("A", 2023, 1), ("B", 2023, 1), ("C", 2023, 1)}


def _restated(url):
    return _q(url, "SELECT ticker, ni_cum_restated, eq_restated, fetched FROM dart_finstat_q WHERE fy=2023 ORDER BY ticker")


def test_prior_from_next_keeps_what_it_got_before_the_quota(pdb, monkeypatch):
    fake = _use(monkeypatch, _QuotaDart(1, acnt={("0000000A", "2024", "11013", "CFS"): _next_year_q1(700, 9000)}))
    res = asyncio.run(Q.fill_prior_from_next(PKEYS))
    assert [k[1] for k in fake.calls] == ["0000000A", "0000000B"]               # C 는 부르지 않는다
    assert _restated(pdb) == [("A", 700.0, 9000.0, "nodata"),                    # 받은 칸은 쓴다(종전엔 0칸)
                              ("B", None, None, "nodata"), ("C", None, None, "nodata")]
    assert res[("A", 2023, 1)].startswith("채움") and "중단" in res[("B", 2023, 1)] and "중단" in res[("C", 2023, 1)]


def test_prior_from_next_other_errors_skip_only_that_cell(pdb, monkeypatch):
    class _Odd(_QuotaDart):
        async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
            if corp_code == "0000000A":
                self.calls.append(("acnt", corp_code, str(bsns_year), reprt_code, fs_div))
                raise DartClientError("900", "정의되지 않은 오류")
            return await super().get_fnltt_singl_acnt_all(corp_code, bsns_year, reprt_code, fs_div)
    _use(monkeypatch, _Odd(9, acnt={("0000000B", "2024", "11013", "CFS"): _next_year_q1(700, 9000),
                                    ("0000000C", "2024", "11013", "CFS"): _next_year_q1(800, 9500)}))
    res = asyncio.run(Q.fill_prior_from_next(PKEYS))
    assert _restated(pdb) == [("A", None, None, "nodata"), ("B", 700.0, 9000.0, "nodata"), ("C", 800.0, 9500.0, "nodata")]
    assert "[900]" in res[("A", 2023, 1)]


# ── 결산월(--acc-mt)의 회사 개황 콜 ────────────────────────────────────────

def test_acc_mt_company_info_stops_at_quota(db, monkeypatch):
    fake = _use(monkeypatch, _QuotaDart(0))
    out = asyncio.run(Q.refresh_acc_mt(today=TODAY))
    assert [k[0] for k in fake.calls] == ["company"]                             # 셋 중 한 번 거절된 뒤로 부르지 않는다
    assert out == {}
    assert _q(db, "SELECT count(*) FROM dart_fundamentals WHERE acc_mt IS NOT NULL") == [(0,)]
