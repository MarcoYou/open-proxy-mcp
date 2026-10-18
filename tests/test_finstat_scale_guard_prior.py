"""분기·연간 재무 원천 결측 두 갈래 (261019, 재설계 P1).

① 스케일 가드의 항등식(자산 = 부채 + 자본) 오탐. 종전엔 0.01% 만 어긋나도 hard(값 무효화)였다. 운영 분기 표에서 이 검사 **하나만**
   켜져 SCALE_GUARD 로 비운 22행을 DART 원문으로 다시 받아 보니 어긋남이 0.01%~32%(한 행만 5.1배 — 모두 10배 미만)였고, 지배 순이익·자본은 회사 제 규모·
   전기 칸과 맞았다 — 원문의 자산·부채 총계 칸이 틀렸다(삼성생명 2025 반기·3분기: 자산총계·부채총계가 1분기 값 그대로).
   같은 표의 진짜 단위 오류 8행은 보고서가 통째로 부풀려져 항등식이 0.0000% 로 맞았고 다른 검사가 잡았다.
   → 10배 이상 벌어질 때만 hard, 그보다 작으면 soft(경고만).
② DART 재무제표 API 에 보고서가 아예 없는 칸(금융업의 2023 반기까지 — KB금융 2023 1분기·반기 등 [013]). 다음 해 같은 분기 보고서의
   전년 동기 칸(손익 전기 누적·자본변동표 전기 말)으로 **정정 칸**을 채운다. 연간은 다음 해 사업보고서의 전기 칸(`--backfill-restated`).

DART 0콜 — 가짜 클라이언트. 수집 통합 시험은 Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
수치는 공시 원문(DART 전체 재무제표 API) 그대로다.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import scale_guard as G
from scripts import market_fund_quarterly as Q


# ── ① 항등식: 단위 오류 꼴만 hard ─────────────────────────────────────────────

# (칸, 자산총계, 부채총계, 자본총계) — 운영 분기 표에서 항등식 하나로만 비었던 행의 원문 값(10-19 DART 재조회)
NON_SCALE_MISMATCH = [
    ("032830 2025 반기", 318858553000000, 287368034000000, 33657838000000),     # 0.68% — 자산·부채 총계가 1분기 값 그대로
    ("032830 2025 3분기", 318858553000000, 287368034000000, 40923920000000),    # 2.96%
    ("053620 2025 3분기", 243933221724, 26057915868, 192788501286),             # 10.28%
    ("352770 2025 1분기", 45180436369, 46622782802, -15991270428),              # 32.2% — 자본잠식
    ("062970 2023 3분기", 20878707108, 20639451191, 1695727237),                # 6.98%
    ("900250 2025 반기(CNY)", 491751994, 49457292, 2470361914),                 # 412% 이지만 5.1배 — 자산 칸만 틀림(자본은 회사 규모와 맞음)
    ("015590 2025 3분기", 195418528797, 64898311686, 130498694437),             # 0.011%
]


@pytest.mark.parametrize("name,a,l,e", NON_SCALE_MISMATCH, ids=[x[0] for x in NON_SCALE_MISMATCH])
def test_balance_mismatch_below_10x_is_soft_not_hard(name, a, l, e):
    v = G.assess(thstrm=1.0, assets=a, liabilities=l, equity=e)
    assert "balance_identity" not in v["hard_hit"]
    assert v["soft_hit"] == ["balance_identity_mismatch"]
    assert v["tier"] == "soft"
    assert v["diagnostics"]["balance_identity_mismatch"]["diff_pct"] > 0.01
    # 재무 지표 도구가 쓰는 원래 검사(경고만)는 그대로 켜진다
    assert G.check_balance_identity(a, l, e)["triggered"]


def test_samsung_life_half_year_values_are_kept_with_its_own_scale():
    """삼성생명 2025 반기 — 지배 순이익·자본은 그 회사 제 규모(연간 자본 중앙값·최근값 중 큰 쪽) 안이다. 종전엔 SCALE_GUARD 로 비었다."""
    v = G.assess(thstrm=1394149000000, assets=318858553000000, liabilities=287368034000000, equity=33657838000000,
                 self_ref=62724296000000, frmtrm_equity=32737931000000, known_prev_equity=30732687000000 + 2005244000000)
    assert v["hard_hit"] == []
    assert v["tier"] == "soft"


@pytest.mark.parametrize("a,l,e", [
    (318858553000000 * 1000, 287368034000000, 31490519000000),     # 자산만 천 배
    (318858553000000, 287368034000000 * 1000, 31490519000000),     # 부채만 천 배
    (100_000, 94_000, 6_000 * 1000),                                # 자본(자산의 6%)만 천 배 — 은행형
    (100_000, 99_000 * 1000, 1_000),                                # 부채만 천 배, 자본 1%
    (100_000, -50_000, -60_000),                                    # 부채+자본 ≤ 0 인데 자산 양수
])
def test_balance_mismatch_of_10x_or_more_is_still_hard(a, l, e):
    v = G.assess(thstrm=1.0, assets=a, liabilities=l, equity=e)
    assert "balance_identity" in v["hard_hit"]
    assert v["tier"] == "hard"
    assert "balance_identity_mismatch" not in v["soft_hit"]


def test_identity_holding_is_clean_and_whole_report_scale_error_still_caught_elsewhere():
    assert G.assess(thstrm=1.0, assets=100, liabilities=60, equity=40)["tier"] == "clean"
    # 060310 2022 3분기 — 보고서가 통째로 ×10⁶(항등식은 0.0000%). 회사 자기 규모·자본 자릿수가 잡는다(종전과 같다).
    v = G.assess(thstrm=1987597002000000, assets=72650807169000000, liabilities=25851813709000000,
                 equity=46798993460000000, self_ref=44628358147, frmtrm_equity=46.0e15, known_prev_equity=46.0e9)
    assert "balance_identity" not in v["hard_hit"]
    assert {"digit_cap_equity", "self_relative_ni", "self_relative_eq", "prior_period_mismatch"} <= set(v["hard_hit"])


def _pmd_read_path():
    """실시간 firm 의 가짜 입력 실행기(`tests/test_pmd_read_path.py`)를 그대로 빌려 쓴다."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("_pmd_rp", pathlib.Path(__file__).with_name("test_pmd_read_path.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("liab,tier,expect", [
    ("140,000,000,000,000", "soft", "자산총계 ≠ 부채총계 + 자본총계(차이 1.17%, 단위 오류 꼴 아님)"),     # 6조 어긋남
    ("134,000,000,000,000,000", "hard", "🚨 DART 재무 단위(스케일) 오류 강하게 의심(['balance_identity'])"),  # 부채만 천 배
])
def test_live_firm_warning_follows_the_new_balance_rule(monkeypatch, liab, tier, expect):
    """실시간 firm — 소폭 어긋남은 「원문 총계 칸 불일치」 경고(값·배수는 그대로), 단위 오류 꼴은 종전처럼 🚨 경고."""
    from open_proxy_mcp.services import price_multiple_data as S
    T = _pmd_read_path()
    base, _ = T.run_firm(S, monkeypatch.setattr, "fm_normal")
    orig = T._fy_rows
    monkeypatch.setattr(T, "_fy_rows", lambda with_eps=True: [
        {**r, "thstrm_amount": liab} if r.get("account_id") == "ifrs-full_Liabilities" else r for r in orig(with_eps)])
    got, _ = T.run_firm(S, monkeypatch.setattr, "fm_normal")
    d = got["data"]
    assert d["data_quality"]["scale_tier"] == tier
    assert any(expect in w for w in d["warnings"]), d["warnings"]
    assert d["multiples"] == base["data"]["multiples"]                 # 개별 조회는 값 그대로(종전과 같다)
    assert not any("재무 비율 이상치" in w for w in d["warnings"])       # 이익 급변 문구와 섞지 않는다


# ── ② 다음 해 같은 분기 보고서의 전년 동기 칸 ─────────────────────────────────

def row(sj, aid, nm, ordv, cur="KRW", detail=None, rcept="20240516002265", **amt):
    r = {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": cur, "rcept_no": rcept,
         **{k: ("" if v is None else str(v)) for k, v in amt.items()}}
    if detail is not None:
        r["account_detail"] = detail
    return r


CTRL = "자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]"


def kb_2024_q1():
    """KB금융 2024 1분기 보고서(연결) 모양 — 전년 동기(2023 1분기) 칸을 가진다. 값은 원문 그대로."""
    return [
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업주주지분", 27, thstrm_amount=57136088000000,
            frmtrm_amount=56692684000000),
        row("BS", "ifrs-full_Equity", "자본총계", 26, thstrm_amount=59207331000000, frmtrm_amount=58873310000000),
        row("CIS", "ifrs-full_ProfitLoss", "당기순이익", 20, thstrm_amount=1063214000000, thstrm_add_amount=1063214000000,
            frmtrm_q_amount=1510291000000, frmtrm_add_amount=1510291000000),
        row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업주주지분순이익", 21,
            thstrm_amount=1049133000000, thstrm_add_amount=1049133000000,
            frmtrm_q_amount=1508708000000, frmtrm_add_amount=1508708000000),
        row("SCE", "ifrs-full_Equity", "자본총계", 30, detail="연결재무제표 [member]",
            thstrm_amount=59207331000000, frmtrm_q_amount=56213373000000, frmtrm_amount=58873310000000),
        row("SCE", "ifrs-full_Equity", "자본총계", 30, detail="자본 [구성요소]|비지배지분 [구성요소]",
            thstrm_amount=2071243000000, frmtrm_q_amount=1682200000000),
        row("SCE", "ifrs-full_Equity", "자본총계", 30, detail=CTRL, thstrm_amount=57136088000000, frmtrm_q_amount=54531173000000),
        row("SCE", "ifrs-full_Equity", "자본총계", 30, detail=CTRL + "|이익잉여금 [구성요소]",
            thstrm_amount=32439145000000, frmtrm_q_amount=29850902000000),
    ]


def kb_2024_h1():
    """KB금융 2024 반기 — 반기는 전기 3개월 칸(frmtrm_q)이 누적이 아니다. 전기 누적(frmtrm_add)만 쓴다."""
    return [
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업주주지분", 27, rcept="20260324000813",
            thstrm_amount=57152838000000),
        row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업주주지분순이익", 21, rcept="20260324000813",
            thstrm_amount=1732210000000, thstrm_add_amount=2781343000000,
            frmtrm_q_amount=1498916000000, frmtrm_add_amount=3007624000000),
        row("SCE", "ifrs-full_Equity", "자본총계", 30, detail=CTRL, rcept="20260324000813",
            thstrm_amount=57152838000000, frmtrm_q_amount=55638084000000),
    ]


def test_prior_ni_uses_cumulative_comparative_column():
    assert Q.extract_controlling_ni_cum(Q._as_prior_rows(kb_2024_q1(), 1)) == (1508708000000.0, "CIS_ID")
    assert Q.extract_controlling_ni_cum(Q._as_prior_rows(kb_2024_h1(), 2)) == (3007624000000.0, "CIS_ID")   # 3개월 칸 아님
    # 반기에 전기 누적 칸이 없으면 3개월 칸으로 대신하지 않는다
    rows = [{**r, "frmtrm_add_amount": ""} for r in kb_2024_h1()]
    assert Q.extract_controlling_ni_cum(Q._as_prior_rows(rows, 2))[0] is None


def test_prior_equity_from_statement_of_changes_in_equity():
    assert Q.prior_equity_from_sce(kb_2024_q1()) == (54531173000000.0, "SCE_PRIOR")
    assert Q.prior_equity_from_sce(kb_2024_h1()) == (55638084000000.0, "SCE_PRIOR")
    # 이번 분기 말 값이 재무상태표 지배자본과 같은 행이 없으면 쓰지 않는다(구성요소·총계 행으로 잘못 고르지 않게)
    rows = [r for r in kb_2024_q1() if r.get("account_detail") != CTRL]
    assert Q.prior_equity_from_sce(rows) == (None, "NO_SCE_ROW")
    # 같은 이번 값의 행이 서로 다른 전기 값을 주면 쓰지 않는다
    dup = kb_2024_q1() + [row("SCE", "ifrs-full_Equity", "자본총계", 31, detail="자본 [구성요소]",
                              thstrm_amount=57136088000000, frmtrm_q_amount=1)]
    assert Q.prior_equity_from_sce(dup) == (None, "SCE_AMBIGUOUS")
    assert Q.prior_equity_from_sce([r for r in kb_2024_q1() if r["sj_div"] != "BS"]) == (None, "NO_CURRENT_EQ")


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
TODAY = date(2026, 10, 19)


class _Today(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


class _FakeDart:
    def __init__(self, avail: dict):
        self.avail = avail
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


def samsung_life_h1():
    """삼성생명 2025 반기(연결) 응답의 총계·지배 행 — 자산총계·부채총계는 1분기 값 그대로(원문)."""
    r = "20250813000585"
    return [
        row("BS", "ifrs-full_Assets", "자산총계", 7, rcept=r, thstrm_amount=318858553000000, frmtrm_amount=312218844000000),
        row("BS", "ifrs-full_Equity", "자본총계", 26, rcept=r, thstrm_amount=33657838000000, frmtrm_amount=32737931000000),
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업지분", 27, rcept=r, thstrm_amount=31641518000000,
            frmtrm_amount=30732687000000),
        row("BS", "ifrs-full_EquityAndLiabilities", "자본과부채총계", 34, rcept=r, thstrm_amount=319124603000000),
        row("BS", "ifrs-full_Liabilities", "부채총계", 45, rcept=r, thstrm_amount=287368034000000, frmtrm_amount=279480913000000),
        row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업지분순이익", 30, rcept=r,
            thstrm_amount=758890000000, thstrm_add_amount=1394149000000),
    ]


@pytest.fixture
def gdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        for t, cc in (("105560", "00688996"), ("032830", "00126256"), ("X", "0000000X")):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched) VALUES (%s, %s, 'ok')", (t, cc))
        # 연간 자본(회사 자기 규모의 자)
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES "
                  "('105560', 2023, 'CFS', 4631932000000, 56929804000000, 'ok'), "
                  "('105560', 2024, 'CFS', 5078221000000, 57888952000000, 'ok'), "
                  "('032830', 2024, 'CFS', 2106839000000, 30732687000000, 'ok'), "
                  "('032830', 2025, 'CFS', 2302837000000, 62724296000000, 'ok')")
        ins = ("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
        for qq in (1, 2):   # API 에 보고서가 없던 칸
            c.execute(ins, ("105560", 2023, qq, Q.Q_TO_RC[qq], "OFS", None, None, "nodata", "NODATA", "NODATA"))
        c.execute(ins, ("105560", 2023, 3, "11014", "CFS", 4370442000000, 56884307000000, "ok", "CIS_ID", "BS_ID"))
        c.execute(ins, ("X", 2023, 1, "11013", "OFS", None, None, "nodata", "NODATA", "NODATA"))          # 다음 해도 없음
        c.execute(ins, ("X", 2026, 1, "11013", "OFS", None, None, "nodata", "NODATA", "NODATA"))          # 다음 해 마감 전
        c.execute(ins, ("032830", 2025, 2, "11012", "CFS", None, None, "nodata", "SCALE_GUARD", "SCALE_GUARD"))
        # 이미 정정 값이 있는 칸은 건드리지 않는다
        c.execute(ins, ("032830", 2023, 1, "11013", "OFS", None, None, "nodata", "NODATA", "NODATA"))
        c.execute("UPDATE dart_finstat_q SET eq_restated=1, restate_why='다른 정정' WHERE ticker='032830' AND fy=2023 AND quarter=1")
    fake = _FakeDart({("00688996", "2024", "11013", "CFS"): kb_2024_q1(),
                      ("00688996", "2024", "11012", "CFS"): kb_2024_h1(),
                      ("00126256", "2025", "11012", "CFS"): samsung_life_h1(),
                      ("00126256", "2024", "11013", "CFS"): kb_2024_q1()})
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    monkeypatch.setattr(Q, "date", _Today)
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    return pg17, fake


def test_refetch_keeps_samsung_life_half_year_values(gdb, capsys):
    url, fake = gdb
    asyncio.run(Q.fetch([2025], conc=1, only={("032830", 2025, 2)}))
    assert _q(url, "SELECT ni_cum, eq, fetched, ni_case, eq_case FROM dart_finstat_q WHERE ticker='032830' AND fy=2025 AND quarter=2") \
        == [(1394149000000.0, 31641518000000.0, "ok", "CIS_ID", "BS_ID")]
    assert "[가드]" not in capsys.readouterr().out


KEYS = {("105560", 2023, 1), ("105560", 2023, 2), ("105560", 2023, 3), ("X", 2023, 1), ("X", 2026, 1), ("032830", 2023, 1),
        ("NOPE", 2023, 1)}


def test_prior_from_next_dry_writes_nothing(gdb):
    url, fake = gdb
    before = _q(url, "SELECT * FROM dart_finstat_q ORDER BY 1, 2, 3")
    res = asyncio.run(Q.fill_prior_from_next(KEYS, dry=True))
    assert res[("105560", 2023, 1)].startswith("채움 순이익 1508708000000.0 (CIS_ID) · 자본 54531173000000.0 (SCE_PRIOR)")
    assert _q(url, "SELECT * FROM dart_finstat_q ORDER BY 1, 2, 3") == before


def test_prior_from_next_fills_only_restated_cells_of_empty_nodata_rows(gdb):
    url, fake = gdb
    res = asyncio.run(Q.fill_prior_from_next(KEYS))
    got = {(r[0], r[1], r[2]): r[3:] for r in _q(url, "SELECT ticker, fy, quarter, ni_cum, eq, fetched, ni_cum_restated, eq_restated, "
                                                      "restate_why FROM dart_finstat_q ORDER BY 1, 2, 3")}
    ni, eq, fetched, ni_r, eq_r, why = got[("105560", 2023, 1)]
    assert (ni, eq, fetched, ni_r, eq_r) == (None, None, "nodata", 1508708000000.0, 54531173000000.0)   # 원본 칸·상태 그대로
    assert "20240516002265" in why and "전년 동기" in why
    assert got[("105560", 2023, 2)][3:5] == (3007624000000.0, 55638084000000.0)
    assert got[("105560", 2023, 3)] == (4370442000000.0, 56884307000000.0, "ok", None, None, None)      # ok 칸은 손대지 않는다
    assert got[("032830", 2023, 1)][3:] == (None, 1.0, "다른 정정")                                       # 이미 정정 값
    assert res[("X", 2023, 1)] == "다음 해 같은 분기 보고서도 API 에 없음"
    assert res[("X", 2026, 1)].startswith("건너뜀(다음 해 같은 분기 보고서가 아직 공시 마감 전")
    assert res[("NOPE", 2023, 1)] == "행 없음"
    # 콜: KB 두 칸(연결 1콜씩) + X 2023 1분기(연결·별도) — 마감 전·ok·정정 칸은 부르지 않는다
    assert sorted(fake.calls) == sorted([("00688996", "2024", "11013", "CFS"), ("00688996", "2024", "11012", "CFS"),
                                         ("0000000X", "2024", "11013", "CFS"), ("0000000X", "2024", "11013", "OFS")])


def test_derive_reads_prior_filled_cells(gdb):
    """파생(재무 요약 4열)은 정정 칸을 원본보다 먼저 읽는다 — 채운 2023 1분기로 2024 1분기 TTM 이 생긴다."""
    url, fake = gdb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) VALUES "
                  "('105560', 2023, 4, '11011', 'CFS', 4594835000000, 56692684000000, 'ok'), "
                  "('105560', 2024, 1, '11013', 'CFS', 1049133000000, 57136088000000, 'ok')")
    Q.derive_fundamentals(["105560"])
    assert _q(url, "SELECT ni_ttm FROM dart_fundamentals WHERE ticker='105560'") == [(None,)]
    asyncio.run(Q.fill_prior_from_next({("105560", 2023, 1)}))
    Q.derive_fundamentals(["105560"])
    assert _q(url, "SELECT ni_ttm FROM dart_fundamentals WHERE ticker='105560'") == [(4594835000000.0 + 1049133000000 - 1508708000000,)]


# ── 연간: 다음 해 사업보고서의 전기 칸(--backfill-restated --tickers) ─────────────

def test_backfill_restated_with_tickers_fills_the_missing_year(gdb):
    from scripts import market_val_series as S
    url, fake = gdb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES ('001720', '00136721', 'ok', 3)")
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, ni_restated, eq_restated) VALUES "
                  "('001720', 2022, 'CFS', NULL, NULL, 'nodata', 89182585076, 1429783198380), "
                  "('001720', 2023, 'OFS', NULL, NULL, 'nodata', NULL, NULL), "
                  "('001720', 2024, 'CFS', 142649513102, 1612823517609, 'ok', NULL, NULL)")
    fake.avail[("00136721", "2024", "11011", "CFS")] = [
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업소유지분", 5, thstrm_amount=1612823517609,
            frmtrm_amount=1464375874901, bfefrmtrm_amount=1429783198380),
        row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "1. 지배기업소유지분", 9, thstrm_amount=142649513102,
            frmtrm_amount=96390989623, bfefrmtrm_amount=89182585076),
    ]
    # 종전 판정(두 해 중 하나라도 재작성치가 있으면 처리됨)으로는 건너뛴다
    asyncio.run(S.backfill_restated())
    assert ("00136721", "2024", "11011", "CFS") not in fake.calls
    asyncio.run(S.backfill_restated(["001720"], 2024))
    assert _q(url, "SELECT fy, fetched, ni_restated, eq_restated FROM dart_finstat_y WHERE ticker='001720' ORDER BY fy") == [
        (2022, "nodata", 89182585076.0, 1429783198380.0),
        (2023, "nodata", 96390989623.0, 1464375874901.0),
        (2024, "ok", None, None)]
