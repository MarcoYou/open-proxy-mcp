"""price_multiple_data 읽기 경로(재설계 4단계 T5) — `firm_history`(DB 읽기 9번)·`firm`(5번)을 겹쳐 돌려도 결과가 같다.

종전: 서로 입력이 필요 없는 읽기를 **차례로** 기다렸다. 읽기 하나가 Fly 도쿄 ↔ Supabase 서울 왕복(≈30ms 이상)이라 대기의 대부분이 왕복이었다.
이제: 입력이 겹치지 않는 것끼리 묶어 한꺼번에 보내되, **호출당 동시에 도는 읽기는 `_PG_FANOUT`(기본 3)개까지**다(풀 5커넥션·기본 스레드 5칸 보호).

여기서 지키는 것
  ① 같은 입력 → 같은 응답. `tests/pmd_read_path_golden.json` 은 **겹치기 전 코드(origin/main 43893f5e)가 낸 응답**이다 — 새 코드가 글자까지 같아야 한다.
     읽기 하나씩 실패시킨 변형, 통화가 KRW 가 아닌 종목(환율 조회 순서), 읽기 전부 실패한 경우를 포함한다.
     261018 의도한 변경 두 가지만 골든에 반영했다(그 밖의 값·키 순서는 옛 골든과 글자까지 같음을 대조한 뒤 썼다):
     firm_history 경고 2문장·method 의 저장소 표 이름 → 중립 표현, firm 의 `price_source` 칸(이 시험의 가짜 `_market_for` 는 출처를 안 주므로 null).
     같은 날 2차: firm `inputs.dps_fetch_failed` 칸(가짜 배당 조회는 성공하므로 false) — 이것도 그 한 칸 말고는 글자까지 같음을 대조한 뒤 썼다.
  ② 순차(`OPM_PG_FANOUT=1`)와 겹침(기본 3)이 같은 응답·같은 읽기 묶음을 낸다.
  ③ 상한: 한 호출이 동시에 3개를 넘게 DB 를 물지 않는다(중첩 gather 포함).
  ④ 겹쳐서 실제로 빨라진다(가짜 지연으로 잰다) · 오류는 넣은 순서상 첫 예외가 올라온다 · 환경변수 오타가 서버를 못 뜨게 하지 않는다.

network 0 · DB 0 (전부 monkeypatch). 골든을 다시 만드는 법은 파일 맨 아래 `golden_from` 에 있다.
"""
from __future__ import annotations

import asyncio
import copy
import json
import pathlib
import threading
import time
from datetime import date

import pytest

from open_proxy_mcp.services import price_multiple_data as S

GOLDEN = pathlib.Path(__file__).with_name("pmd_read_path_golden.json")
FAIL = object()                       # 이 읽기는 None(DB 장애)을 돌려준다
TODAY = date(2026, 10, 18)
CORP = {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 가짜 DB — SQL 모양으로 읽기 종류를 가른다 (겹치기 전 코드와 같은 SQL 을 쓴다)
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def route(sql: str) -> str:
    s = " ".join(sql.split())
    if "SELECT market, induty, currency FROM dart_fundamentals" in s:
        return "cur_row"
    if "FROM dart_finstat_y" in s:
        return "fin_y"
    if "SELECT ni_fy, eq_fy FROM dart_fundamentals" in s:
        return "fund"
    if "FROM dart_finstat_q" in s:
        return "fin_q"
    if "DISTINCT ON (substring(price_dd,1,4))" in s:
        return "caps"
    if s.startswith("SELECT MAX(price_dd) FROM krx_weekly"):
        return "latest"
    if "SELECT price_dd, mktcap FROM krx_weekly WHERE ticker=%s AND price_dd>=%s" in s:
        return "px"
    if "FROM opm_val_firm" in s:
        return "snap"
    if "FROM krx_stock_flags" in s:
        return "flags"
    if "FROM krx_adj_events" in s:
        return "adj"
    if "SELECT list_shrs FROM krx_weekly" in s and "ASC LIMIT 1" in s:
        return "shares_first"
    if "SELECT list_shrs FROM krx_weekly" in s and "DESC LIMIT 1" in s:
        return "shares_last"
    if s == "SELECT list_shrs FROM price_latest WHERE ticker=%s AND list_shrs>0":
        return "shares_latest"                       # 261018: 최신 주식수는 일별 저장분 먼저(없으면 위 shares_last 로 대신)
    raise AssertionError("가짜 DB 가 모르는 SQL: " + s)


class FakeDb:
    """`_pg_rows` 대역. 어떤 읽기가 몇 번 나갔는지, 동시에 몇 개가 떠 있었는지 센다. `delay` 는 읽기 한 번의 왕복 흉내."""

    def __init__(self, data: dict, delay: float = 0.0, raise_on: str | None = None):
        self.data, self.delay, self.raise_on = data, delay, raise_on
        self.log: list[tuple[str, tuple]] = []
        self._lock = threading.Lock()
        self.inflight = self.max_inflight = 0
        self._per_slot: dict[int, int] = {}            # 상한(세마포어)별 지금 떠 있는 읽기 — to_thread 가 컨텍스트를 스레드로 넘긴다
        self.max_per_slot = 0

    def __call__(self, sql, params=()):
        key = route(sql)
        slot = id(S._PG_SLOTS.get())
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self._per_slot[slot] = self._per_slot.get(slot, 0) + 1
            self.max_per_slot = max(self.max_per_slot, self._per_slot[slot])
            self.log.append((key, tuple(params)))
        try:
            if self.delay:
                time.sleep(self.delay)
            if key == self.raise_on:
                raise RuntimeError(f"읽기 {key} 가 예외를 냈다")
            val = self.data.get(key, [])
            if val is FAIL:
                return None
            if key == "px":
                return [tuple(r) for r in val if r[0] >= params[1]]
            if key == "adj":
                return [tuple(r) for r in val.get(params[1], [])]
            return copy.deepcopy(val)
        finally:
            with self._lock:
                self.inflight -= 1
                self._per_slot[slot] -= 1

    def kinds(self) -> list[tuple[str, tuple]]:
        """나간 읽기의 (종류, 인자) 묶음 — 순서와 무관하게 비교한다."""
        return sorted(self.log, key=repr)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 시나리오 데이터 — firm_history
# ═════════════════════════════════════════════════════════════════════════════════════════════════
_WEEKLY_DD = ["20231020", "20240119", "20240419", "20240719", "20241018", "20250117", "20250418",
              "20250718", "20251017", "20260116", "20260417", "20260717", "20261016"]


def _fh_base() -> dict:
    return {
        "cur_row": [("KS", "264", "KRW")],
        "fin_y": [(2019, 21_500_000_000_000, 240_000_000_000_000, None, None),
                  (2020, 26_400_000_000_000, 260_000_000_000_000, None, None),
                  (2021, 39_900_000_000_000, 300_000_000_000_000, None, None),
                  (2022, 55_700_000_000_000, 350_000_000_000_000, None, None),
                  (2023, 14_500_000_000_000, 360_000_000_000_000, None, None),
                  (2024, 33_600_000_000_000, 370_000_000_000_000, 33_000_000_000_000, None)],
        "fund": [(40_000_000_000_000, 380_000_000_000_000)],
        "fin_q": [(2023, 1, 0.6e12, 355e12), (2023, 2, 1.2e12, 357e12), (2023, 3, 5.5e12, 358e12),
                  (2024, 1, 6.6e12, 363e12), (2024, 2, 15.3e12, 365e12), (2024, 3, 22.0e12, 368e12),
                  (2025, 1, 4.9e12, 372e12), (2025, 2, 10.4e12, 375e12), (2025, 3, 24.0e12, 378e12),
                  (2026, 1, 9.0e12, 382e12), (2026, 2, 18.0e12, 386e12)],
        "caps": [(str(y), f"{y}1229", 3.0e14 + (y - 2020) * 4e13) for y in range(2020, 2026)],
        "latest": [("20261016",)],
        "px": [(d, 3_000_000_000_000_00 + i * 25_000_000_000_000) for i, d in enumerate(_WEEKLY_DD)],
        "snap": [("20261002", "KS", "264", 4.1e14, 15.1, 12.3, 1.2, 1.1),
                 ("20261009", "KS", "264", 4.2e14, 15.4, 12.5, 1.2, 1.1),
                 ("20261016", "KS", "264", 4.3e14, 15.7, 12.7, None, 1.15)],
        "flags": [],
    }


def _fh(**over) -> dict:
    d = _fh_base()
    d.update(over)
    return d


FH_SCENARIOS: dict[str, dict] = {
    "fh_samsung": _fh(),
    "fh_flags_without_snapshots": _fh(flags=[("spinoff_break", "2026-05"), ("unresolved_adjustment", "x"), ("other", "y")], snap=[]),
    "fh_usd_reporter": _fh(cur_row=[("KS", "264", "USD")]),
    "fh_no_data": {"cur_row": [], "fin_y": [], "fund": [], "fin_q": [], "caps": [], "latest": [], "px": [], "snap": [], "flags": []},
    "fh_snapshot_read_fails": _fh(snap=FAIL),
    "fh_every_other_read_fails": _fh(cur_row=FAIL, fin_y=FAIL, fund=FAIL, fin_q=FAIL, caps=FAIL, latest=FAIL, px=FAIL, flags=FAIL),
}
# 읽기 하나씩 실패시킨 변형 — 응답이 종전과 같은 모양으로 무너지는지(종전: `or []`, 스냅샷만 db_error)
for _k in ("cur_row", "fin_y", "fund", "fin_q", "caps", "latest", "px", "flags"):
    FH_SCENARIOS[f"fh_only_{_k}_fails"] = _fh(**{_k: FAIL})


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 시나리오 데이터 — firm (DART 응답은 가짜, DB 읽기 5번만 진짜 대상)
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def _fy_rows(with_eps=True) -> list[dict]:
    rows = [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "514,000,000,000,000", "currency": "KRW"},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "thstrm_amount": "134,000,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "380,000,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": "366,000,000,000,000"},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
         "thstrm_amount": "34,500,000,000,000", "frmtrm_amount": "33,600,000,000,000"},
    ]
    if with_eps:
        rows.append({"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "thstrm_amount": "5,900"})
    return rows


def _q_rows(ni_add: str, eps_add: str | None, eq: str) -> list[dict]:
    rows = [{"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
             "thstrm_amount": "9,000,000,000,000", "thstrm_add_amount": ni_add}]
    if eps_add is not None:
        rows.append({"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare",
                     "thstrm_amount": "1,300", "thstrm_add_amount": eps_add})
    return rows


def _fm(**over) -> dict:
    d = {
        "eps": True,
        "adj": {},                                   # {기준일: [(수정계수,), …]}
        "shares_first": [(5_969_782_550,)], "shares_last": [(5_969_782_550,)],
        "flags": [],
    }
    d.update(over)
    return d
# 261018: 최신 상장주식수는 일별 저장분(`price_latest`, 키 shares_latest)을 먼저 읽는다. 시나리오가 따로 주지 않으면 krx_weekly 의 가장 최근 행(shares_last)과
#   같은 값을 준다 — 지금 운영처럼 두 표의 최신값이 같은 세계다. 그래서 골든(바꾸기 전 코드의 응답)이 그대로 성립해야 한다.


FM_SCENARIOS: dict[str, dict] = {
    "fm_normal": _fm(),
    "fm_split_adjusted": _fm(adj={"20260630": [(0.2,)], "20250630": [(0.2,), (0.5,)]},
                             shares_first=[(1_000_000_000,)], shares_last=[(5_000_000_000,)]),
    "fm_factor_missing_warns": _fm(shares_first=[(5_000_000_000,)], shares_last=[(500_000_000,)]),
    "fm_no_disclosed_eps_still_reads_flags": _fm(eps=False, flags=[("spinoff_break", "2026-05")]),
    "fm_flags": _fm(flags=[("spinoff_break", "a"), ("unresolved_adjustment", "b")]),
    "fm_every_read_fails": _fm(adj=FAIL, shares_first=FAIL, shares_last=FAIL, flags=FAIL),
    "fm_shares_ratio_unavailable": _fm(shares_first=[], shares_last=[]),
}


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 실행기 — `setattr(obj, name, value)` 로 모듈(S 또는 옛 모듈)을 바꿔 끼우고 빌더를 돌린다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def _fx_factory(fx_log):
    async def fx(ccy, dd):
        fx_log.append((ccy, dd))
        return 1300.0 + int(dd[:4]) % 10 * 10 + int(dd[4:6])
    return fx


def run_firm_history(mod, setattr_, name, *, delay=0.0, raise_on=None, data=None):
    """→ (payload, FakeDb, fx 호출 순서)"""
    db, fx_log = FakeDb(data if data is not None else FH_SCENARIOS[name], delay=delay, raise_on=raise_on), []

    async def resolve(q):
        return dict(CORP), None
    setattr_(mod, "_pg_rows", db)
    setattr_(mod, "today_kst", lambda: TODAY)
    setattr_(mod, "_resolve_listed", resolve)
    setattr_(mod, "fx_to_krw", _fx_factory(fx_log))
    payload = asyncio.run(mod.build_firm_history_payload("삼성전자", format="json"))
    return payload, db, fx_log


class _FakeClient:
    async def get_company_info(self, cc):
        return {"induty_code": "264", "acc_mt": "12"}


def run_firm(mod, setattr_, name, *, delay=0.0, fmt="json", data=None, raise_on=None):
    sc = data if data is not None else FM_SCENARIOS[name]
    fdata = {k: sc[k] for k in ("adj", "shares_first", "shares_last", "flags")}
    fdata["shares_latest"] = sc.get("shares_latest", sc["shares_last"])
    db = FakeDb(fdata, delay=delay, raise_on=raise_on)

    async def resolve(q):
        return dict(CORP), None

    async def fin_metrics(code, **kw):
        return {"data": {"summary": {"eps_krw": 5900, "revenue_krw": 300_000_000_000_000, "roe_pct": 10.5,
                                     "capital_impairment_status": None}, "year": 2025}}

    async def market_for(code):
        return {"price": 70000, "date": "20261016", "common_mktcap": 417_000_000_000_000, "list_shrs": 5_969_782_550}

    async def acntall(client, cc, year, rc, fs=None):
        if year == 2025 and rc == "11011":
            return _fy_rows(sc["eps"]), "CFS"
        if rc == "11014":
            return [], None                            # 3분기 누적은 아직 — 반기로 내려간다
        if rc == "11012" and year == 2026:
            return _q_rows("18,000,000,000,000", "3,100" if sc["eps"] else None, "386,000,000,000,000"), "CFS"
        if rc == "11012" and year == 2025:
            return _q_rows("10,400,000,000,000", "2,700" if sc["eps"] else None, "375,000,000,000,000"), "CFS"
        return [], None

    async def shares_out(client, cc, year):
        return {"total": 5_969_782_550, "common": 5_969_782_550}

    async def annual_summary(cc, fy):
        return {"cash_dps": 1444}, {}

    setattr_(mod, "_pg_rows", db)
    setattr_(mod, "today_kst", lambda: TODAY)
    setattr_(mod, "_resolve_listed", resolve)
    setattr_(mod, "get_dart_client", lambda: _FakeClient())
    setattr_(mod, "build_financial_metrics_payload", fin_metrics)
    setattr_(mod, "_market_for", market_for)
    setattr_(mod, "_acntall", acntall)
    setattr_(mod, "_shares_outstanding", shares_out)
    setattr_(mod, "_annual_summary", annual_summary)
    payload = asyncio.run(mod.build_valuation_payload("삼성전자", format=fmt))
    return payload, db


def _canon(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=False, default=str)


@pytest.fixture
def golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def mp(monkeypatch):
    return monkeypatch.setattr


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ① 겹치기 전 코드의 응답과 글자까지 같다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name", sorted(FH_SCENARIOS))
def test_firm_history_payload_equals_the_pre_change_golden(mp, golden, name):
    payload, _, fx_log = run_firm_history(S, mp, name)
    exp = golden[name]
    assert _canon(payload) == _canon(exp["payload"]), f"{name}: 겹치기 전 코드와 응답이 다르다"
    assert [list(x) for x in fx_log] == exp["fx_calls"], f"{name}: 환율 조회 순서가 종전과 다르다"


@pytest.mark.parametrize("name", sorted(FM_SCENARIOS))
def test_firm_payload_equals_the_pre_change_golden(mp, golden, name):
    payload, _ = run_firm(S, mp, name)
    assert _canon(payload) == _canon(golden[name]["payload"]), f"{name}: 겹치기 전 코드와 응답이 다르다"


def test_firm_markdown_equals_the_pre_change_golden(mp, golden):
    payload, _ = run_firm(S, mp, "fm_split_adjusted", fmt="md")
    assert _canon(payload) == _canon(golden["fm_split_adjusted__md"]["payload"])
    assert payload["markdown"].startswith("# 삼성전자 밸류에이션")


def test_the_golden_scenarios_actually_exercise_the_interesting_branches(golden):
    """골든이 전부 같은 분기만 타면 증거가 못 된다 — 갈래마다 한 번씩은 탄다."""
    fh = {k: v["payload"] for k, v in golden.items() if k.startswith("fh_")}
    assert fh["fh_samsung"]["status"] == "ok" and len(fh["fh_samsung"]["data"]["series"]) == 13
    assert fh["fh_no_data"]["status"] == "no_data"
    assert fh["fh_snapshot_read_fails"]["status"] == "db_error"
    assert any("인적분할" in w for w in fh["fh_flags_without_snapshots"]["warnings"])
    assert fh["fh_flags_without_snapshots"]["data"]["market"] == "KS", "스냅샷이 없으면 dart_fundamentals 의 시장을 쓴다"
    assert golden["fh_usd_reporter"]["fx_calls"], "비KRW 시나리오는 환율을 조회해야 한다"
    assert fh["fh_every_other_read_fails"]["status"] == "ok" and fh["fh_every_other_read_fails"]["data"]["series"] == []
    fm = {k: v["payload"]["data"]["inputs"] for k, v in golden.items() if k.startswith("fm_") and "__" not in k}
    assert fm["fm_split_adjusted"]["eps_adj_factors"] == {"current": 0.2, "prior_q": 0.1}
    assert fm["fm_factor_missing_warns"]["shares_unadjusted"] is not None
    assert fm["fm_normal"]["eps_adj_factors"] is None and fm["fm_normal"]["shares_unadjusted"] is None
    warns = {k: v["payload"]["data"]["warnings"] for k, v in golden.items() if k.startswith("fm_") and "__" not in k}
    assert any("인적분할" in w for w in warns["fm_flags"])
    assert any("인적분할" in w for w in warns["fm_no_disclosed_eps_still_reads_flags"]), "EPS 보정을 건너뛰어도 flag 경고는 나간다"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ② 순차(OPM_PG_FANOUT=1)와 겹침(기본 3)은 같은 응답·같은 읽기 묶음
# ═════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name", sorted(FH_SCENARIOS))
def test_firm_history_sequential_and_overlapped_are_identical(mp, name):
    mp(S, "_PG_FANOUT", 1)
    seq_payload, seq_db, seq_fx = run_firm_history(S, mp, name)
    mp(S, "_PG_FANOUT", 3)
    par_payload, par_db, par_fx = run_firm_history(S, mp, name)
    assert _canon(seq_payload) == _canon(par_payload)
    assert seq_db.kinds() == par_db.kinds(), "겹쳐도 같은 읽기가 나가야 한다(더도 덜도 아니게)"
    assert seq_fx == par_fx


@pytest.mark.parametrize("name", sorted(FM_SCENARIOS))
def test_firm_sequential_and_overlapped_are_identical(mp, name):
    mp(S, "_PG_FANOUT", 1)
    seq_payload, seq_db = run_firm(S, mp, name)
    mp(S, "_PG_FANOUT", 3)
    par_payload, par_db = run_firm(S, mp, name)
    assert _canon(seq_payload) == _canon(par_payload)
    assert seq_db.kinds() == par_db.kinds()


def test_firm_history_issues_exactly_the_nine_reads_of_before(mp):
    """종전과 같은 9번 — 겹치기가 읽기를 늘리거나 빼먹지 않는다(스냅샷 실패로 일찍 끝나는 경우만 예외)."""
    _, db, _ = run_firm_history(S, mp, "fh_samsung")
    assert sorted(k for k, _ in db.log) == sorted(
        ["cur_row", "fin_y", "fund", "fin_q", "caps", "latest", "px", "snap", "flags"])


def test_firm_history_stops_early_when_the_snapshot_read_fails(mp):
    """종전엔 재무·곡선을 다 읽은 뒤에야 걸렀다. 같은 db_error 응답을 뒤 단계 없이 낸다 — 장애 중에 읽기 6번을 더 보내지 않는다."""
    payload, db, _ = run_firm_history(S, mp, "fh_snapshot_read_fails")
    assert payload["status"] == "db_error"
    assert sorted(k for k, _ in db.log) == ["cur_row", "latest", "snap"]


def test_firm_reads_exactly_the_five_reads_of_before(mp):
    _, db = run_firm(S, mp, "fm_normal")
    assert sorted(k for k, _ in db.log) == ["adj", "adj", "flags", "shares_first", "shares_latest"]   # 최신 주식수는 일별 저장분(261018)
    _, db = run_firm(S, mp, "fm_no_disclosed_eps_still_reads_flags")
    assert [k for k, _ in db.log] == ["flags"], "공시 EPS 가 없으면 보정 읽기는 건너뛰고 flag 만 읽는다(종전과 같다)"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 비KRW — 환산이 환율을 순서대로 이어 간다. 겹치지 않고 종전 순서 그대로여야 한다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_non_krw_keeps_the_conversion_order_annual_years_then_quarters(mp):
    _, _, fx = run_firm_history(S, mp, "fh_usd_reporter")
    years = [d for _, d in fx if d.endswith("1231")][:8]
    assert fx[0][0] == "USD"
    # 연간 환산(연말 기준일, 연도 오름차순)이 분기 환산보다 전부 앞선다
    n_annual = len(FH_SCENARIOS["fh_usd_reporter"]["fin_y"]) + 1       # 연간 6 + 최신 확정 FY 1
    assert [d for _, d in fx[:n_annual]] == sorted(d for _, d in fx[:n_annual])
    assert all(d[4:] in ("0331", "0630", "0930", "1231") for _, d in fx)
    assert years


def test_krw_reporters_never_call_the_fx_path(mp):
    _, _, fx = run_firm_history(S, mp, "fh_samsung")
    assert fx == []


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ③ 상한 — 한 호출이 풀을 다 쓰지 못한다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_default_cap_is_three(monkeypatch):
    monkeypatch.delenv("OPM_PG_FANOUT", raising=False)
    assert S._env_fanout() == 3


def test_firm_history_never_has_more_than_the_cap_in_flight(mp):
    mp(S, "_PG_FANOUT", 3)
    _, db, _ = run_firm_history(S, mp, "fh_samsung", delay=0.05)
    assert db.max_inflight == 3, f"동시 읽기 최대 {db.max_inflight} — 3 이어야 한다(겹치지 않았거나 상한을 넘었다)"


def test_firm_never_has_more_than_the_cap_in_flight(mp):
    mp(S, "_PG_FANOUT", 3)
    _, db = run_firm(S, mp, "fm_normal", delay=0.05)
    assert db.max_inflight == 3, f"동시 읽기 최대 {db.max_inflight}"


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_the_cap_is_a_real_knob(mp, cap):
    mp(S, "_PG_FANOUT", cap)
    _, db, _ = run_firm_history(S, mp, "fh_samsung", delay=0.05)
    _, db2 = run_firm(S, mp, "fm_normal", delay=0.05)
    assert db.max_inflight == cap and db2.max_inflight == cap


def test_nested_gathers_share_the_callers_cap(mp):
    """안쪽 `_gather_db`(재무 두 읽기)가 새 상한을 만들어 합이 늘어나지 않는다 — 바깥 묶음 셋 + 안쪽 묶음이 있어도 동시에 3."""
    mp(S, "_PG_FANOUT", 3)
    db = FakeDb(FH_SCENARIOS["fh_samsung"], delay=0.05)
    mp(S, "_pg_rows", db)
    mp(S, "today_kst", lambda: TODAY)

    async def go():
        # _firm_fin_by_fy 는 안에서 읽기 둘을 묶는다(2) + _firm_fin_by_q(1) + _annual_pit_band(1) = 4 인데 상한은 3
        return await S._gather_db(S._firm_fin_by_fy("005930"), S._firm_fin_by_q("005930"), S._annual_pit_band("005930", fin={}))
    asyncio.run(go())
    assert db.max_inflight == 3 and db.max_per_slot == 3


def test_two_concurrent_calls_each_respect_their_own_cap(mp):
    """호출 둘이 겹쳐도 **호출마다** 3 이하 — 호출 하나가 풀을 독점하지 않는다(합은 6 까지 갈 수 있고 그건 다른 호출 몫으로 남는 2칸이 아니라 두 호출의 몫이다)."""
    mp(S, "_PG_FANOUT", 3)
    db = FakeDb(FH_SCENARIOS["fh_samsung"], delay=0.05)
    mp(S, "_pg_rows", db)
    mp(S, "today_kst", lambda: TODAY)

    async def resolve(q):
        return dict(CORP), None
    mp(S, "_resolve_listed", resolve)

    async def go():
        await asyncio.gather(S.build_firm_history_payload("a", "json"), S.build_firm_history_payload("b", "json"))
    asyncio.run(go())
    assert db.max_per_slot <= 3, f"한 호출의 한 단계에서 동시 읽기 {db.max_per_slot}"
    assert db.max_inflight > 3, "두 호출이 실제로 겹치지 않았다 — 이 테스트가 아무것도 재지 못한다"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ④ 실제로 빨라진다 · 오류 모양 · 환경변수
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_overlapping_cuts_the_wall_time_with_a_simulated_round_trip(mp):
    """읽기 하나를 60ms 왕복으로 흉내 낸다. 차례로면 9 × 60 = 540ms, 겹치면 3단계 ≈ 180ms."""
    delay = 0.06
    mp(S, "_PG_FANOUT", 1)
    t0 = time.perf_counter()
    run_firm_history(S, mp, "fh_samsung", delay=delay)
    seq = time.perf_counter() - t0
    mp(S, "_PG_FANOUT", 3)
    t0 = time.perf_counter()
    run_firm_history(S, mp, "fh_samsung", delay=delay)
    par = time.perf_counter() - t0
    assert seq >= 9 * delay * 0.95, f"차례 실행이 너무 빨랐다: {seq:.3f}s"
    assert par < seq * 0.6, f"겹쳤는데 {par:.3f}s (차례 {seq:.3f}s)"


def test_firm_overlap_takes_two_round_trips_not_five(mp):
    delay = 0.06
    mp(S, "_PG_FANOUT", 1)
    t0 = time.perf_counter()
    run_firm(S, mp, "fm_normal", delay=delay)
    seq = time.perf_counter() - t0
    mp(S, "_PG_FANOUT", 3)
    t0 = time.perf_counter()
    run_firm(S, mp, "fm_normal", delay=delay)
    par = time.perf_counter() - t0
    assert seq >= 5 * delay * 0.95
    assert par < seq * 0.7, f"겹쳤는데 {par:.3f}s (차례 {seq:.3f}s)"


def test_a_read_that_raises_surfaces_like_before_and_leaves_no_task_behind(mp):
    """DB 계층은 예외를 안 내지만(None) 혹시 터지면: 종전엔 차례로 가다 처음 터진 예외가 올라갔다 — 같은 예외가 올라오고, 남은 읽기는 이 호출 안에서 끝난다."""
    with pytest.raises(RuntimeError, match="fin_y"):
        run_firm_history(S, mp, "fh_samsung", raise_on="fin_y")
    with pytest.raises(RuntimeError, match="flags"):
        run_firm_history(S, mp, "fh_samsung", raise_on="flags")
    with pytest.raises(RuntimeError, match="shares_latest"):
        run_firm(S, mp, "fm_normal", raise_on="shares_latest")


def test_gather_db_returns_in_input_order_and_raises_the_first_exception_in_input_order():
    done = []

    async def ok(tag, d):
        await asyncio.sleep(d)
        done.append(tag)
        return tag

    async def boom(tag, d, exc):
        await asyncio.sleep(d)
        done.append(tag)
        raise exc

    async def go_ok():
        return await S._gather_db(ok("a", 0.03), ok("b", 0.01), ok("c", 0.02))
    assert asyncio.run(go_ok()) == ["a", "b", "c"]

    async def go_err():
        # 빨리 터진 것(두 번째 입력)보다 **넣은 순서상 앞선** 예외가 올라와야 한다. 그리고 느린 정상 읽기도 끝까지 기다린다.
        await S._gather_db(boom("x", 0.03, ValueError("first")), boom("y", 0.005, KeyError("second")), ok("z", 0.05))
    done.clear()
    with pytest.raises(ValueError, match="first"):
        asyncio.run(go_err())
    assert sorted(done) == ["x", "y", "z"], "나머지를 끝까지 기다리지 않고 올렸다"


def test_gather_db_restores_the_context_and_does_not_leak_a_cap():
    async def go():
        assert S._PG_SLOTS.get() is None
        await S._gather_db(asyncio.sleep(0))
        assert S._PG_SLOTS.get() is None
        try:
            await S._gather_db(asyncio.sleep(0), _raises())
        except ZeroDivisionError:
            pass
        assert S._PG_SLOTS.get() is None, "예외 뒤에도 상한이 컨텍스트에 남았다"

    async def _raises():
        1 / 0
    asyncio.run(go())


def test_db_call_outside_a_gather_is_a_plain_to_thread(mp):
    seen = {}

    def rows(sql, params=()):
        seen["tid"] = threading.get_ident()
        return [(1,)]
    mp(S, "_pg_rows", rows)

    async def go():
        main = threading.get_ident()
        out = await S._db_rows("SELECT 1")
        return main, out

    main, out = asyncio.run(go())
    assert out == [(1,)] and seen["tid"] != main


@pytest.mark.parametrize("raw,expected", [("", 3), ("3", 3), ("1", 1), ("2", 2), ("0", 1), ("-4", 1), ("abc", 3), ("2.5", 3)])
def test_env_knob_parses_defensively(monkeypatch, raw, expected):
    monkeypatch.setenv("OPM_PG_FANOUT", raw)
    assert S._env_fanout() == expected


def test_the_pool_is_not_asked_for_more_than_it_has_by_default():
    """상한 기본값 3 은 풀(`OPM_PG_POOL_MAX` 기본 5)과 기본 스레드 풀(1 CPU → 5칸)의 절반을 조금 넘는 값이다 — 다른 호출 몫이 최소 2칸 남는다."""
    from open_proxy_mcp import db
    assert S._PG_FANOUT <= db._POOL_MAX - 2


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 최신 상장주식수 — 일별 저장분 먼저, 없으면 종전 krx_weekly 질의 (261018, 재설계 5-1)
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_latest_shares_prefer_the_daily_store_over_the_weekly_table(mp, golden):
    """두 표가 다르면 일별 저장분을 쓴다 — krx_weekly 에 이번 주 행이 없는 다음 단계 세계(주간 최신 행은 지난주 값)."""
    stale_weekly = _fm(shares_first=[(5_000_000_000,)], shares_latest=[(500_000_000,)], shares_last=[(5_000_000_000,)])
    payload, db = run_firm(S, mp, "", data=stale_weekly)
    assert _canon(payload) == _canon(golden["fm_factor_missing_warns"]["payload"]), "일별 저장분의 최신 주식수(10:1 병합 뒤)를 봐야 한다"
    assert "shares_last" not in [k for k, _ in db.log], "일별 저장분에 행이 있으면 krx_weekly 를 다시 읽지 않는다"


@pytest.mark.parametrize("latest", [[], FAIL], ids=["no_daily_row", "daily_read_fails"])
def test_latest_shares_fall_back_to_the_weekly_query_when_the_daily_store_has_no_row(mp, golden, latest):
    """그 종목 행이 없거나 읽지 못하면 종전 krx_weekly 질의로 대신한다 — 배율이 비면 계수 누락을 못 잡는다. 응답은 종전과 같다."""
    sc = _fm(shares_first=[(5_000_000_000,)], shares_latest=latest, shares_last=[(500_000_000,)])
    payload, db = run_firm(S, mp, "", data=sc)
    assert _canon(payload) == _canon(golden["fm_factor_missing_warns"]["payload"])
    assert sorted(k for k, _ in db.log) == ["adj", "adj", "flags", "shares_first", "shares_last", "shares_latest"]


def test_latest_shares_fallback_is_skipped_when_the_base_week_is_missing(mp, golden):
    """분모(기준일 뒤 첫 주)가 없으면 배율을 낼 수 없다 — 대신 읽기를 보내지 않는다(장애 중 읽기를 늘리지 않는다). 응답은 종전과 같다."""
    payload, db = run_firm(S, mp, "fm_shares_ratio_unavailable")
    assert "shares_last" not in [k for k, _ in db.log]
    assert _canon(payload) == _canon(golden["fm_shares_ratio_unavailable"]["payload"])
    payload2, db2 = run_firm(S, mp, "fm_every_read_fails")
    assert "shares_last" not in [k for k, _ in db2.log]
    assert _canon(payload2) == _canon(golden["fm_every_read_fails"]["payload"])


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 골든 다시 만들기 — 응답을 **일부러** 바꾼 PR 에서만
# ═════════════════════════════════════════════════════════════════════════════════════════════════
class _Patcher:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, value in reversed(self._undo):
            setattr(obj, name, value)
        self._undo.clear()


def golden_from(mod) -> dict:
    """`mod`(= `price_multiple_data` 모듈 객체) 로 모든 시나리오를 돌려 골든 dict 를 돌려준다. pytest 는 부르지 않는다.

    🔴 **바꾸기 전 코드의 모듈**로 돌려야 의미가 있다. 새 코드로 만든 골든을 커밋하면 이 파일의 ① 테스트는 아무것도 증명하지 못한다.
       바꾸기 전 모듈 만들기:  src = subprocess.check_output(["git", "show", "<기준 커밋>:open_proxy_mcp/services/price_multiple_data.py"])
                              mod = types.ModuleType("old_pmd"); exec(compile(src, "old_pmd.py", "exec"), mod.__dict__)
    저장 형식은 시나리오당 한 줄(`{"이름": {"payload": …, "fx_calls": […]}, …}`)이라 골든이 바뀌면 diff 가 시나리오 단위로 보인다.
    `fm_split_adjusted__md` 는 같은 시나리오를 `format="md"` 로 돌린 것이다."""
    out: dict = {}
    for name in sorted(FH_SCENARIOS):
        p = _Patcher()
        try:
            payload, _, fx = run_firm_history(mod, p.setattr, name)
        finally:
            p.restore()
        out[name] = {"payload": payload, "fx_calls": [list(x) for x in fx]}
    for name in sorted(FM_SCENARIOS):
        p = _Patcher()
        try:
            payload, _ = run_firm(mod, p.setattr, name)
        finally:
            p.restore()
        out[name] = {"payload": payload}
    p = _Patcher()
    try:
        payload, _ = run_firm(mod, p.setattr, "fm_split_adjusted", fmt="md")
    finally:
        p.restore()
    out["fm_split_adjusted__md"] = {"payload": payload}
    return out


def dump_golden(golden: dict, path: pathlib.Path = GOLDEN, generated_from: str = "") -> None:
    items = {"_generated_from": generated_from, **golden}
    lines = [f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'), default=str)}"
             for k, v in items.items()]
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
