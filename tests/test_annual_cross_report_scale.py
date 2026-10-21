# -*- coding: utf-8 -*-
"""연간 수집(`scripts/market_val_series.py` 의 `--fetch`·`--amended`·`--refetch`)의 **보고서 사이 단위 가드** (DATA-SC4).

종전: 연간 수집은 한 보고서 안의 검사(`scale_guard.assess` — 항등식·자릿수·순이익 대 전기)만 했다. 회사가 단위를 잘못 적어 사업보고서가 **통째로**
10ⁿ 배면 그 검사는 다 맞고(항등식 0.0000%) 값이 원본 칸(`ni`·`eq`)에 그대로 들어간다. 정정 사업보고서 다시 받기(`--amended`)도 같은 보고서를 같은 단위로
다시 받아 올 수 있어, 한 번 바로잡은 칸에 10ⁿ 배 값이 도로 들어갈 수 있다. 서버 실시간 firm 과 `financial_metrics` 는 이미 같은 날짜의 같은 숫자를 다른 보고서와
대 가린다(`scale_guard.reconcile_report_scales`) — 연간 수집도 같은 함수로 새 보고서의 당기·전기·전전기 자본을 **저장돼 있는 이웃 해 값**(원본 칸·정정 칸)과 댄다.

운영에서 실제로 10ⁿ 배가 저장됐던 네 행의 꼴로 시험한다(수치는 공시 원문·운영 값): 065150 FY2023(×1,000) · 032680 FY2022(×10⁶) · 311390 FY2022(×1,000) ·
0091W0 FY2025(×1,000, 이웃 보고서가 하나뿐이라 시총이 가른다).

DART 0콜 — 가짜 클라이언트. Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from open_proxy_mcp.services import scale_guard as G
from scripts import market_val_series as S

psycopg = pytest.importorskip("psycopg")

B = 100_000_000      # 1억

FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""


def _r(rc: str, *, ni, eq, ni_frm=None, eq_frm=None, ni_bfe=None, eq_bfe=None, cur="KRW") -> list[dict]:
    """사업보고서 응답 꼴(지배 귀속 계정 줄) — 당기·전기·전전기 칸."""
    def amt(v):
        return "" if v is None else str(v)
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": amt(eq), "frmtrm_amount": amt(eq_frm), "bfefrmtrm_amount": amt(eq_bfe), "currency": cur, "rcept_no": rc},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
         "thstrm_amount": amt(eq), "frmtrm_amount": amt(eq_frm), "bfefrmtrm_amount": amt(eq_bfe), "currency": cur, "rcept_no": rc},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
         "thstrm_amount": amt(ni), "frmtrm_amount": amt(ni_frm), "bfefrmtrm_amount": amt(ni_bfe), "currency": cur, "rcept_no": rc},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": amt(ni), "frmtrm_amount": amt(ni_frm), "bfefrmtrm_amount": amt(ni_bfe), "currency": cur, "rcept_no": rc},
    ]


def _times(rows: list[dict], k: int) -> list[dict]:
    """보고서 통째로 k 배 — 회사가 단위를 잘못 적은 보고서(금액 칸만, 정수 문자열)."""
    out = []
    for r in rows:
        n = dict(r)
        for f in ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount"):
            if n.get(f) not in (None, ""):
                n[f] = str(int(float(n[f]) * k))
        out.append(n)
    return out


class _FakeDart:
    def __init__(self):
        self.avail: dict = {}
        self.calls: list[tuple] = []

    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        from open_proxy_mcp.dart.client import DartClientError
        self.calls.append((corp_code, str(bsns_year), reprt_code, fs_div))
        rows = self.avail.get((corp_code, str(bsns_year), reprt_code, fs_div))
        if rows is None:
            raise DartClientError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": rows}


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def adb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, corp_code text, fetched text, acc_mt int)")
    fake = _FakeDart()
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setenv("SERIES_SLEEP", "0")
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    monkeypatch.setattr(S, "_RCEPT_COL", False)
    monkeypatch.setattr(S, "_RESTATED_COL", False)
    monkeypatch.setattr(S, "_mktcap_latest", lambda t: None, raising=False)     # 시총은 시험이 필요할 때만 준다
    S._pg().close()      # 접수번호·정정 칸 출처 칸 만들기
    return pg17, fake


def _seed(url, t, rows):
    """rows = (fy, ni, eq, 접수번호[, 정정 순이익, 정정 자본, 정정 출처]) — 연결(CFS) ok 행."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES (%s,%s,'ok',12) ON CONFLICT DO NOTHING", (t, "00" + t))
        for fy, ni, eq, rc, *rest in rows:
            nir, eqr, rrc = (list(rest) + [None, None, None])[:3]
            c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, rcept_no, ni_restated, eq_restated, restated_rcept_no) "
                      "VALUES (%s,%s,'CFS',%s,%s,'ok',%s,%s,%s,%s)", (t, fy, ni, eq, rc, nir, eqr, rrc))


def _row(url, t, fy):
    return _q(url, "SELECT ni, eq, fetched, rcept_no, ni_restated, eq_restated, restated_rcept_no FROM dart_finstat_y WHERE ticker=%s AND fy=%s", (t, fy))[0]


# ── 순수 규칙 ──────────────────────────────────────────────────────────────────────────────────────────

def test_plan_majority_of_stored_neighbours_rescales_the_new_report():
    new = {2023: 255.48 * B * 1000, 2022: 240 * B * 1000, 2021: 200 * B * 1000}
    stored = [("R2022", 2022, 240 * B), ("R2021", 2021, 200 * B), ("R2025", 2023, 252.36 * B)]
    p = S.cross_report_plan(new, stored)
    assert p["status"] == "fixed" and p["divisor"] == 1000 and p["method"] == "majority"


def test_plan_stored_value_off_leaves_the_new_report_alone():
    """새 보고서가 맞고 저장된 이웃 하나가 틀렸다 — 새 보고서는 건드리지 않고 틀린 저장 칸만 알린다."""
    new = {2024: 300 * B, 2023: 200 * B, 2022: 100 * B}
    stored = [("R2023", 2023, 200 * B * 1000), ("R2022", 2022, 100 * B), ("R2022b", 2022, 100.4 * B)]
    p = S.cross_report_plan(new, stored)
    assert p["status"] == "stored_off" and p["divisor"] == 1 and p["off"] == ["R2023"]


def test_plan_ordinary_restatement_is_clean():
    new = {2024: 300 * B, 2023: 214 * B}
    assert S.cross_report_plan(new, [("R2023", 2023, 200 * B)])["status"] == "clean"
    assert S.cross_report_plan(new, [])["status"] == "clean", "대 볼 저장 값이 없다(상장 첫해) — 못 잡는 것이 아니라 대조 대상이 없다"


def test_plan_one_neighbour_needs_the_market_cap_signal():
    new = {2025: 119.88 * B * 1000}
    stored = [("restated:2025", 2025, 119.88 * B)]
    assert S.cross_report_plan(new, stored)["status"] == "undecided"
    calls = []
    p = S.cross_report_plan(new, stored, mktcap_fn=lambda: calls.append(1) or 124.7 * B)
    assert p["status"] == "fixed" and p["divisor"] == 1000 and p["method"] == "mktcap" and calls == [1]
    # 시총이 새 보고서 쪽 단위를 가리키면(PBR 이 새 보고서 단위에서만 그럴듯) 저장 쪽이 틀린 것 — 새 보고서는 그대로
    p2 = S.cross_report_plan({2025: 119.88 * B}, [("restated:2025", 2025, 119.88 * B / 1000)], mktcap_fn=lambda: 124.7 * B)
    assert p2["status"] == "stored_off" and p2["divisor"] == 1
    assert S.cross_report_plan(new, stored, mktcap_fn=lambda: None)["status"] == "undecided"


def test_plan_clean_report_never_asks_for_the_market_cap():
    calls = []
    S.cross_report_plan({2024: 1.0 * B}, [("R", 2024, 1.05 * B)], mktcap_fn=lambda: calls.append(1) or 1.0)
    assert calls == [], "어긋남이 없으면 시총 조회(DB 읽기)를 하지 않는다"


def test_plan_ignores_an_unrelated_inconsistent_pair_far_from_the_new_report():
    """저장 칸끼리 서로 10ⁿ 어긋난 옛 해가 있어도(새 보고서와 상관없는 덩어리) 새 보고서 판정을 막지 않는다."""
    new = {2024: 300 * B, 2023: 200 * B}
    stored = [("R2023", 2023, 200 * B), ("R2019", 2019, 50 * B), ("restated:2019", 2019, 50 * B * 1000)]
    assert S.cross_report_plan(new, stored)["status"] == "clean"


def test_plan_prior_column_only_mismatch_is_not_a_whole_report_error():
    """당기 자본이 앞 해 저장 값과 이어지는데(321억 대 300억) 전기 칸만 1,000배 — 보고서가 통째로 틀린 꼴이 아니다. 나누지도 막지도 않는다.
    (당기 값을 같이 나누면 맞는 값이 1/1,000 이 된다. 이 꼴의 전기 칸은 정정 칸 쓰기의 단위 꼴 검사가 막는다.)"""
    new = {2024: 321 * B, 2023: 300 * B * 1000}
    stored = [("R2023", 2023, 300 * B)]
    assert S.cross_report_plan(new, stored, current_fy=2024)["status"] == "prior_only"
    assert S.cross_report_plan(new, stored)["status"] == "undecided", "당기 연도를 모르면 이어짐을 못 본다"
    # 당기도 앞 해와 이어지지 않으면(보고서 통째 1,000배) 해당 없다
    whole = {2024: 321 * B * 1000, 2023: 300 * B * 1000}
    assert S.cross_report_plan(whole, stored, current_fy=2024)["status"] == "undecided"
    # 당기 날짜를 직접 대 본 저장 값(정정 칸)과도 어긋나면 이어짐 검사로 덮지 않는다(양쪽에서 어긋나 못 가림)
    both = [("R2023", 2023, 300 * B), ("restated:2024", 2024, 321 * B * 1000)]
    assert S.cross_report_plan(new, both, current_fy=2024)["status"] == "undecided"


def test_plan_decides_the_new_report_even_if_an_unrelated_pair_is_undecided():
    """새 보고서가 틀린 판정은 새 보고서와 이어진 보고서들만으로 한다 — 멀리 떨어진 옛 해의 저장 칸 둘이 서로 어긋나(어느 쪽인지 못 가림) 있어도 막히지 않는다."""
    new = {2023: 200 * B * 1000, 2022: 100 * B * 1000}
    stored = [("R2023", 2023, 200 * B), ("R2022", 2022, 100 * B), ("R2022b", 2022, 100.2 * B),
              ("R2019", 2019, 50 * B), ("restated:2019", 2019, 50 * B * 1000)]
    p = S.cross_report_plan(new, stored)
    assert p["status"] == "fixed" and p["divisor"] == 1000


# ── 운영에서 10ⁿ 배가 저장됐던 네 행의 꼴 ───────────────────────────────────────────────────────────────

def test_065150_amended_report_with_the_same_unit_error_does_not_put_1000x_back(adb, capsys):
    """065150 FY2023 — 저장 원본은 바로잡혀 있다(255.48억·정정 칸 252.36억). 정정 사업보고서가 와도 금액이 통째로 1,000배면 원본 칸에 도로 들어가지 않는다.
    새 보고서 값은 ÷1,000 해서 쓴다(앞뒤 해 보고서·정정 칸 셋과 다수결)."""
    url, fake = adb
    _seed(url, "000011", [(2021, 10 * B, 200 * B, "20220321000011"),
                          (2022, 12 * B, 240 * B, "20230321000011"),
                          (2023, -58.43 * B, 255.48 * B, "20240408003770", -61.56 * B, 252.36 * B, "20260323000011")])
    good = _r("20260401000011", ni=-58.5 * B, eq=256 * B, ni_frm=12 * B, eq_frm=240 * B, ni_bfe=10 * B, eq_bfe=200 * B)
    fake.avail[("00000011", "2023", "11011", "CFS")] = _times(good, 1000)
    asyncio.run(S.fetch(only={("000011", 2023)}, keep_fs=True, amended=True))
    ni, eq, fetched, rc, *_ = _row(url, "000011", 2023)
    assert (ni, eq, fetched, rc) == (-58.5 * B, 256.0 * B, "ok", "20260401000011"), "1,000배 값(2.56e13)이 아니라 ÷1,000 한 값"
    out = capsys.readouterr().out
    assert "단위" in out and "1000" in out.replace(",", "")


def test_065150_first_collection_stores_the_divided_value(adb):
    """같은 꼴, 정규 수집(그 해 행이 아직 없을 때) — 앞 두 해 원본 칸과 대 ÷1,000."""
    url, fake = adb
    _seed(url, "000012", [(2021, 10 * B, 200 * B, "20220321000012"), (2022, 12 * B, 240 * B, "20230321000012")])
    good = _r("20240408003770", ni=-58.43 * B, eq=255.48 * B, ni_frm=12 * B, eq_frm=240 * B, ni_bfe=10 * B, eq_bfe=200 * B)
    fake.avail[("00000012", "2023", "11011", "CFS")] = _times(good, 1000)
    asyncio.run(S.fetch(["000012"]))
    ni, eq, fetched, rc, *_ = _row(url, "000012", 2023)
    assert (ni, eq, fetched, rc) == (-58.43 * B, 255.48 * B, "ok", "20240408003770")


def test_032680_million_fold_report_is_divided_not_dropped(adb):
    """032680 FY2022 — XBRL 원시값에 배율을 안 곱해 100만 배. 한 보고서 안의 자릿수 검사는 값을 비웠다(자료 없음) — 이제 앞 해 보고서와 대 ÷10⁶ 한 값을 쓴다."""
    url, fake = adb
    _seed(url, "000013", [(2020, 3 * B, 180 * B, "20210317000013"), (2021, 5 * B, 190 * B, "20220322000013")])
    good = _r("20230323001157", ni=-7.2 * B, eq=185.6 * B, ni_frm=5 * B, eq_frm=190 * B, ni_bfe=3 * B, eq_bfe=180 * B)
    fake.avail[("00000013", "2022", "11011", "CFS")] = _times(good, 1_000_000)
    asyncio.run(S.fetch(["000013"]))
    ni, eq, fetched, *_ = _row(url, "000013", 2022)
    assert fetched == "ok" and ni == pytest.approx(-7.2 * B) and eq == pytest.approx(185.6 * B)


def test_311390_amended_report_checked_against_the_restated_cell(adb):
    """311390 FY2022 — 원본은 바로잡혀 있고 정정 칸(다음 해 보고서의 전기 칸)은 바른 단위. 정정 보고서가 1,000배면 정정 칸·앞 해와 대 ÷1,000."""
    url, fake = adb
    _seed(url, "000014", [(2021, 2 * B, 80 * B, "20220325000014"),
                          (2022, 3 * B, 90 * B, "20231226000346", 3 * B, 90 * B, "20240326000014")])
    good = _r("20260402000014", ni=3.1 * B, eq=90.5 * B, ni_frm=2 * B, eq_frm=80 * B)
    fake.avail[("00000014", "2022", "11011", "CFS")] = _times(good, 1000)
    asyncio.run(S.fetch(only={("000014", 2022)}, keep_fs=True, amended=True))
    ni, eq, fetched, rc, *_ = _row(url, "000014", 2022)
    assert (ni, eq, fetched, rc) == (3.1 * B, 90.5 * B, "ok", "20260402000014")


def test_0091W0_single_neighbour_is_decided_by_the_market_cap(adb, monkeypatch):
    """0091W0 FY2025 — 이웃 보고서가 하나뿐(정정 칸 119.88억). 다수결이 안 서므로 시총(124.7억 → PBR 1.04 vs 0.001)으로 가린다."""
    url, fake = adb
    _seed(url, "000015", [(2025, -17_858_891, 11_988_390_000, "20260310002858", -17_858_891, 11_988_390_000, None)])
    monkeypatch.setattr(S, "_mktcap_latest", lambda t: 12_470_000_000.0)
    good = _r("20261015000015", ni=-17_900_000, eq=11_990_000_000)
    fake.avail[("00000015", "2025", "11011", "CFS")] = _times(good, 1000)
    asyncio.run(S.fetch(only={("000015", 2025)}, keep_fs=True, amended=True))
    ni, eq, fetched, rc, *_ = _row(url, "000015", 2025)
    assert (ni, eq, fetched, rc) == (-17_900_000, 11_990_000_000, "ok", "20261015000015")


def test_undecided_amended_report_keeps_the_existing_row(adb, capsys):
    """이웃 하나·시총 없음 → 어느 쪽이 맞는지 못 가린다. 정정 보고서 값으로 바꾸지 않고(있던 ok 행 그대로) 이유를 로그에 남긴다."""
    url, fake = adb
    _seed(url, "000016", [(2025, -17_858_891, 11_988_390_000, "20260310002858", -17_858_891, 11_988_390_000, None)])
    fake.avail[("00000016", "2025", "11011", "CFS")] = _times(_r("20261015000016", ni=-17_900_000, eq=11_990_000_000), 1000)
    asyncio.run(S.fetch(only={("000016", 2025)}, keep_fs=True, amended=True))
    assert _row(url, "000016", 2025)[:4] == (-17_858_891, 11_988_390_000, "ok", "20260310002858")
    out = capsys.readouterr().out
    assert "못 가려" in out


def test_undecided_first_collection_is_stored_as_nodata_not_as_the_wrong_unit(adb):
    url, fake = adb
    _seed(url, "000017", [(2024, 5 * B, 100 * B, "20250320000017")])
    fake.avail[("00000017", "2025", "11011", "CFS")] = _times(_r("20260320000017", ni=6 * B, eq=110 * B, ni_frm=5 * B, eq_frm=100 * B), 1000)
    asyncio.run(S.fetch(["000017"]))
    ni, eq, fetched, *_ = _row(url, "000017", 2025)
    assert fetched == "nodata" and ni is None and eq is None, "1,000배 값이 ok 로 들어가지 않는다(다음 보고서가 나오면 다시 본다)"


# ── 건드리지 말아야 할 것 ─────────────────────────────────────────────────────────────────────────────────

def test_correct_new_report_with_a_wrong_stored_neighbour_is_written_as_is(adb, capsys):
    """새 보고서가 맞고 저장된 이웃 한 해가 1,000배 — 새 보고서를 나누면 안 된다. 틀린 저장 칸은 알리기만(고치는 건 데이터 갈래의 몫)."""
    url, fake = adb
    _seed(url, "000018", [(2022, 10 * B, 100 * B, "20230320000018"),
                          (2023, 11 * B * 1000, 110 * B * 1000, "20240320000018")])
    fake.avail[("00000018", "2024", "11011", "CFS")] = _r("20250320000018", ni=12 * B, eq=120 * B, ni_frm=11 * B, eq_frm=110 * B,
                                                          ni_bfe=10 * B, eq_bfe=100 * B)
    asyncio.run(S.fetch(["000018"]))
    ni, eq, fetched, rc, *_ = _row(url, "000018", 2024)
    assert (ni, eq, fetched) == (12 * B, 120 * B, "ok")
    assert "저장" in capsys.readouterr().out
    assert _row(url, "000018", 2023)[:2] == (11 * B * 1000, 110 * B * 1000), "저장된 이웃 칸은 이 수집이 고치지 않는다"


def test_amended_report_that_fixes_the_unit_replaces_the_wrong_stored_value(adb):
    """정정 보고서가 단위를 바로잡은 경우 — 지금 바꾸려는 그 해 옛 원본 칸(1,000배)은 대 볼 상대가 아니다. 접수번호를 몰라도(이행 전 행) 마찬가지.
    그 칸을 상대로 세면 맞는 정정 보고서가 「이웃 하나와 어긋남」으로 막힌다."""
    url, fake = adb
    _seed(url, "000022", [(2023, 11 * B * 1000, 110 * B * 1000, None)])
    fake.avail[("00000022", "2023", "11011", "CFS")] = _r("20260401000022", ni=11 * B, eq=110 * B)
    asyncio.run(S.fetch(only={("000022", 2023)}, keep_fs=True, amended=True))
    assert _row(url, "000022", 2023)[:4] == (11 * B, 110 * B, "ok", "20260401000022")


def test_ordinary_restatement_and_first_listing_year_are_untouched(adb):
    url, fake = adb
    _seed(url, "000019", [(2023, 10 * B, 100 * B, "20240320000019")])
    fake.avail[("00000019", "2024", "11011", "CFS")] = _r("20250320000019", ni=13 * B, eq=130 * B, ni_frm=10.6 * B, eq_frm=107 * B)
    asyncio.run(S.fetch(["000019"]))
    assert _row(url, "000019", 2024)[:3] == (13 * B, 130 * B, "ok")
    _seed(url, "000020", [])      # 상장 첫해 — 저장된 이웃이 없다
    fake.avail[("00000020", "2024", "11011", "CFS")] = _r("20250320000020", ni=13 * B, eq=130 * B, ni_frm=10.6 * B, eq_frm=107 * B)
    asyncio.run(S.fetch(["000020"]))
    assert _row(url, "000020", 2024)[:3] == (13 * B, 130 * B, "ok")


def test_foreign_currency_report_is_compared_in_won(adb, monkeypatch):
    """USD 보고사 — 저장은 원화(환산값)이고 새 보고서는 달러다. 환산 없이 대면 비율이 1/환율(≈10⁻³)이라 단위 오류로 오판한다(분기 수집의 글로벌에스엠 꼴)."""
    url, fake = adb
    monkeypatch.setattr(S, "fx_to_krw", AsyncMock(return_value=1100.0))
    monkeypatch.setattr(S, "fx_source", lambda cur, dd=None: "ecos")
    _seed(url, "000021", [(2022, 110_000 * 1100.0, 1_100_000 * 1100.0, "20230320000021"),
                          (2023, 120_000 * 1100.0, 1_200_000 * 1100.0, "20240320000021")])
    fake.avail[("00000021", "2024", "11011", "CFS")] = _r("20250320000021", ni=130_000, eq=1_300_000, ni_frm=120_000, eq_frm=1_200_000,
                                                          ni_bfe=110_000, eq_bfe=1_100_000, cur="USD")
    asyncio.run(S.fetch(["000021"]))
    ni, eq, fetched, *_ = _row(url, "000021", 2024)
    assert fetched == "ok" and ni == pytest.approx(130_000 * 1100.0) and eq == pytest.approx(1_300_000 * 1100.0)


def test_same_run_earlier_year_is_a_neighbour_for_the_next_year(adb):
    """같은 실행에서 먼저 받은 해(2022)의 값도 다음 해(2023) 보고서의 이웃으로 댄다 — 빠지면 2023 은 이웃이 앞 해 원본 하나뿐이라 못 가려 자료 없음이 된다."""
    url, fake = adb
    _seed(url, "000023", [(2021, 10 * B, 200 * B, "20220321000023")])
    fake.avail[("00000023", "2022", "11011", "CFS")] = _r("20230321000023", ni=12 * B, eq=240 * B, ni_frm=10 * B, eq_frm=200 * B)
    good = _r("20240321000023", ni=-58.43 * B, eq=255.48 * B, ni_frm=12 * B, eq_frm=240 * B, ni_bfe=10 * B, eq_bfe=200 * B)
    fake.avail[("00000023", "2023", "11011", "CFS")] = _times(good, 1000)
    asyncio.run(S.fetch(only={("000023", 2022), ("000023", 2023)}))
    assert _row(url, "000023", 2022)[:3] == (12 * B, 240 * B, "ok")
    assert _row(url, "000023", 2023)[:3] == (-58.43 * B, 255.48 * B, "ok"), "같은 실행의 2022 값 + 2021 원본 다수결로 ÷1,000"


@pytest.mark.parametrize("new_rc,row_rc,echo_rc", [
    ("20240408000024", "20240408000024", "20240408000024"),   # --refetch — 같은 보고서를 다시 받음
    ("20260401000024", "20240408000024", "20240408000024"),   # --amended — 정정 보고서, 저장 정정 칸의 출처는 그 해 옛 판
    ("20240408000024", None, "20240408000024"),               # 그 해 행의 접수번호를 모른다(칸 이행 전) — 지금 보고서의 접수번호로만 가린다
])
def test_restated_cells_echoing_the_report_itself_are_not_votes(adb, new_rc, row_rc, echo_rc):
    """앞 해 정정 칸 가운데 **지금 보고서**(같은 접수번호)나 **그 해 옛 판**에서 온 값은 이 보고서의 메아리라 대 볼 상대가 아니다.
    세면 1,000배 보고서가 자기 메아리와 같은 단위로 표를 얻어 다수결이 비기고(못 가림) 바른 값으로 바뀌지 않는다."""
    url, fake = adb
    t = "000024"
    _seed(url, t, [(2021, 10 * B, 200 * B, "20220321000024", 10 * B * 1000, 200 * B * 1000, echo_rc),
                   (2022, 12 * B, 240 * B, "20230321000024", 12 * B * 1000, 240 * B * 1000, echo_rc),
                   (2023, -58 * B, 255 * B, row_rc)])
    good = _r(new_rc, ni=-58.43 * B, eq=255.48 * B, ni_frm=12 * B, eq_frm=240 * B, ni_bfe=10 * B, eq_bfe=200 * B)
    fake.avail[("00" + t, "2023", "11011", "CFS")] = _times(good, 1000)
    asyncio.run(S.fetch(only={(t, 2023)}, keep_fs=True, amended=(new_rc != row_rc)))
    assert _row(url, t, 2023)[:4] == (-58.43 * B, 255.48 * B, "ok", new_rc)


# ── 분기 수집과 같은 규칙인가 ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,k,new_frm,stored", [
    ("065150 FY2023", 1000, 240 * B * 1000, 240 * B),
    ("032680 FY2022", 1_000_000, 190 * B * 1_000_000, 190 * B),
    ("311390 FY2022", 1000, 80 * B * 1000, 80 * B),
    ("0091W0 FY2025", 1000, 11_988_390_000 * 1000, 11_988_390_000),
])
def test_same_divisor_as_the_quarterly_prior_period_rule(name, k, new_frm, stored):
    """저장 칸이 맞을 때 분기 수집의 전기 칸 대조(`check_prior_period_mismatch` — 저장 연간 값을 정답으로)가 내는 배수와 연간 가드(`reconcile_report_scales`)의 배수가 같다."""
    quarterly = G.check_prior_period_mismatch(new_frm, stored)
    assert quarterly["triggered"] and quarterly["fix_divisor"] == k
    plan = S.cross_report_plan({2022: new_frm, 2021: new_frm}, [("a", 2022, stored), ("b", 2021, stored)])
    assert plan["status"] == "fixed" and plan["divisor"] == k
