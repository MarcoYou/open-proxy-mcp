# -*- coding: utf-8 -*-
"""집계 표(시총 집계 `krx_cap_agg` · 배수 `opm_val_market`)의 3년 밖을 콜드 서빙본에서 읽는 이음매(261021).

지키는 것 — 골든: **응답 글자 동일**
  ① 꺼짐(`OPM_COLD`≠1) — 서빙 목록도 DB 의 추가 질의도 없고 응답이 종전 그대로다
  ② 켬 · DB 에서 옛 행을 지우기 전(지금) — 같은 행을 콜드에서 읽을 뿐 응답 JSON 이 꺼짐과 글자까지 같다
  ③ 켬 · 3년 밖 행을 DB 에서 지운 뒤 — 응답이 ①(옛 행이 DB 에 있던 때)과 글자까지 같다
  ④ 이음매 S = min(B, coverage 다음 날) — 콜드 날짜 < S, DB 날짜 >= S, 겹치는 날 0. as_of 가 S 직전·직후여도 같은 답
  ⑤ 강등(목록 못 읽음 · 이음매 틈) — 지우기 전이면 응답 그대로, 지운 뒤면 DB 3년 + 안내 한 줄(그 줄을 빼면 꺼짐·지운 뒤 응답과 같다)
대상: trading_data market·sector(bucket 시계열) · price_multiple_data market(전 이력 · as_of) · sector(as_of 단면 · KSIC · 기업 sector_history).
network 0 · 운영 DB 0 — 가짜 DB 는 메모리 sqlite(같은 SQL 을 `%s`→`?` 로), 서빙본은 로컬 file:// 목록(`cold_hist` 고정물)."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3

import pytest

from open_proxy_mcp import cold_store as CS
from open_proxy_mcp import krx_retention as R
from open_proxy_mcp.services import price_multiple_data as P
from open_proxy_mcp.services import trading as T

LATEST = "20261016"                      # 저장된 최신 주 → B = 20231016
CUT = R.krx_weekly_cutoff(LATEST)
assert CUT == "20231016"
UPTO = "20231016"                        # 서빙본 coverage 끝 다음 날 → S = 20231016


# ═════════════════════════════════════════════════════════════════════════════
# 합성 원천 — 운영과 같은 모양(주간 시총 집계 2015-12~, 월말 배수 2020-01~ → 2026-07 부터 주간)
# ═════════════════════════════════════════════════════════════════════════════
def _fridays(a: str, b: str) -> list[str]:
    from datetime import date, timedelta
    d = date(int(a[:4]), int(a[4:6]), int(a[6:]))
    end = date(int(b[:4]), int(b[4:6]), int(b[6:]))
    out = []
    while d <= end:
        if d.weekday() == 4:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _month_ends(a: str, b: str) -> list[str]:
    days = _fridays(a, b)
    last: dict[str, str] = {}
    for d in days:
        last[d[:6]] = d
    return sorted(last.values())


CAP_DATES = ["20151230", *_fridays("20160101", LATEST)]
SEC = {"wics_sector": [("G10", "에너지"), ("G45", "IT"), ("G40", "금융")],
       "wics_industry": [("G1010", "에너지"), ("G4530", "반도체와반도체장비")]}


def _cap_rows() -> list[dict]:
    rows = []
    for i, d in enumerate(CAP_DATES):
        for m, base in (("KS", 1_500_000_000_000_000), ("KQ", 300_000_000_000_000)):
            parts = [base // 7 + i * 1_000_003, base // 3 + i * 7_919, base // 5 + i * 104_729]
            ns = [40 + i % 3, 120 + i % 5, 70]
            if m == "KQ" and "20200101" <= d <= "20210101":            # 미분류가 있다 없다 하는 구간
                parts.append(9_999_991 + i)
                ns.append(3)
            total, ntot = sum(parts), sum(ns)
            rows.append({"price_dd": d, "market": m, "scheme": "market", "bucket": "_ALL", "label": "전체", "n": ntot,
                         "cap": total, "sector_asof": None})
            for scheme, codes in SEC.items():
                codes = list(codes) + ([("_UNCLASSIFIED", "미분류")] if len(parts) == 4 else [])
                # 대분류 3개 · 중분류 2개 — 중분류는 마지막 두 몫을 합친다(합 = 시장 합)
                ps = parts if scheme == "wics_sector" else [parts[0], sum(parts[1:3])] + parts[3:]
                nn = ns if scheme == "wics_sector" else [ns[0], ns[1] + ns[2]] + ns[3:]
                for (code, label), p, n in zip(codes, ps, nn):
                    rows.append({"price_dd": d, "market": m, "scheme": scheme, "bucket": code, "label": label, "n": n,
                                 "cap": p, "sector_asof": "20260821"})
    return rows


VAL_DATES = _month_ends("20200101", "20260630") + _fridays("20260701", LATEST)


def _val_rows() -> list[dict]:
    rows = []
    for i, d in enumerate(VAL_DATES):
        weekly = d >= "20260701"
        for m in ("KS", "KQ"):
            f = 1 + i / 97.0 + (0.31 if m == "KQ" else 0)
            rows.append({"snap_dd": d, "market": m, "scheme": "market", "sector": "_ALL", "label": None, "n": None,
                         "per_fy0": 11.123456789 * f, "per_ttm": None if (m == "KQ" and i < 3) else 12.987654321 * f,
                         "pbr_fy0": 1.0123456789 * f, "pbr_mrq": 0.987654321 * f, "cap": 2.1e15 * f, "ni_ttm": 1.7e14 / f,
                         "eq": 2.2e15 / f if weekly else None, "cap_pref": 3.3e13 * f if weekly else None, "ni_fy0": 1.6e14 / f})
            for scheme, codes in (("wics_sector", SEC["wics_sector"] + [("_fold", "기타(소규모)")]),
                                  ("wics_industry", SEC["wics_industry"]),
                                  # KSIC 은 코드 순서를 거꾸로 넣고 몇 달은 cap 이 같다(운영: 시총 0 업종 동률) — DB 힙 순서와 서빙본 기본키 순서가 달라도
                                  # 단면 순서가 같아야 한다(`ORDER BY market, cap DESC, sector`, 10-21 리허설 24/26 발견)
                                  ("ksic", [("64", "금융"), ("26", "전자부품"), ("10", "식료품")])):
                if scheme == "ksic" and d >= "20260823":
                    continue
                for j, (code, label) in enumerate(codes):
                    rows.append({"snap_dd": d, "market": m, "scheme": scheme, "sector": code, "label": label, "n": 10 + j,
                                 "per_fy0": None if j == 2 and i % 4 == 0 else 9.87654321 * f + j,
                                 "per_ttm": 10.1111111 * f + j, "pbr_fy0": 1.1 * f + j / 10, "pbr_mrq": 1.05 * f + j / 10,
                                 "cap": (0.0 if scheme == "ksic" and i % 3 == 2 else
                                         (3e14 + j * 1e13) * f if not (j == 1 and i == 5) else None),
                                 "ni_ttm": 2.9e13 / f, "eq": None, "cap_pref": None, "ni_fy0": 3.1e13 / f})
    return rows


CAP_ROWS = _cap_rows()
VAL_ROWS = _val_rows()
CAP_COLS = ["price_dd", "market", "scheme", "bucket", "label", "n", "cap", "sector_asof"]
VAL_COLS = ["snap_dd", "market", "per_fy0", "per_ttm", "pbr_fy0", "pbr_mrq", "cap", "ni_ttm", "eq", "cap_pref", "sector", "label", "n",
            "scheme", "ni_fy0"]


class FakeDB:
    """운영 질의를 그대로 받는 메모리 sqlite — `%s`→`?`, `::bigint` 는 sqlite 산술 변환으로."""

    def __init__(self, *, moved: bool):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.execute(f"CREATE TABLE krx_cap_agg ({', '.join(CAP_COLS)})")
        self.con.execute(f"CREATE TABLE opm_val_market ({', '.join(VAL_COLS)})")
        self.con.execute("CREATE TABLE wise_sector (snap_dd, ticker, sector_code, sector, industry_code, industry)")
        cap = [r for r in CAP_ROWS if not moved or r["price_dd"] >= CUT]
        val = [r for r in VAL_ROWS if not moved or r["snap_dd"] >= CUT]
        self.con.executemany(f"INSERT INTO krx_cap_agg VALUES ({','.join('?' * len(CAP_COLS))})",
                             [tuple(r[c] for c in CAP_COLS) for r in cap])
        self.con.executemany(f"INSERT INTO opm_val_market VALUES ({','.join('?' * len(VAL_COLS))})",
                             [tuple(r[c] for c in VAL_COLS) for r in val])
        self.con.execute("INSERT INTO wise_sector VALUES ('20260821', '005930', 'G45', 'IT', 'G4530', '반도체와반도체장비')")
        self.sql: list[str] = []

    def __call__(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append(s)
        s = re.sub(r"::bigint", "", s).replace("%s", "?")
        return [tuple(r) for r in self.con.execute(s, tuple(params)).fetchall()]


def _cold_payloads(cut_dd: str = CUT):
    return {"krx_cap_agg_hist": [r for r in CAP_ROWS if r["price_dd"] < cut_dd],
            "opm_val_market_hist": [r for r in VAL_ROWS if r["snap_dd"] < cut_dd]}


@pytest.fixture
def env(monkeypatch):
    """DB 상태(moved)를 고르는 함수 — 가짜 DB 와 도구가 부르는 DB 밖 읽기를 끼운다."""
    def make(*, moved: bool) -> FakeDB:
        db = FakeDB(moved=moved)
        for mod in (P, T):
            monkeypatch.setattr(mod, "_pg_rows", db)
        monkeypatch.setattr(P, "_krx_db_latest_dd", lambda: LATEST)

        async def no_div(*a, **k):
            return {}, {}

        async def no_fwd(*a, **k):
            return {}, {}, []

        async def resolve(q):
            return {"corp_name": "삼성전자", "stock_code": "005930"}, None

        async def firm_val(isu, dd):
            return "26", "KS", 12.3456, 1.2345, {}
        monkeypatch.setattr(P, "_div_yield_map", no_div)
        monkeypatch.setattr(P, "_fwd_val_map", no_fwd)
        monkeypatch.setattr(P, "_resolve_listed", resolve)
        monkeypatch.setattr(P, "_firm_val_at", firm_val)
        monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
        P._AGG_FIRST_CACHE.clear()
        return db
    return make


def _j(p) -> str:
    return json.dumps(p, ensure_ascii=False)


#: 골든 질의 — (이름, 코루틴 만들기)
CALLS = {
    "trading_market_all_weekly": lambda: T.build_cap_agg_payload("market", format="json", freq="weekly"),
    "trading_market_since_20151230": lambda: T.build_cap_agg_payload("market", format="json", since="20151230", freq="weekly"),
    "trading_market_since_monthly": lambda: T.build_cap_agg_payload("market", format="json", since="20180105"),
    "trading_sector_bucket_IT": lambda: T.build_cap_agg_payload("대분류", format="json", bucket="IT", freq="weekly"),
    "trading_sector_bucket_unclassified": lambda: T.build_cap_agg_payload("대분류", format="json", bucket="미분류", since="20151230",
                                                                           freq="weekly"),
    "trading_sector_mid_code": lambda: T.build_cap_agg_payload("중분류", format="json", bucket="G4530"),
    "pmd_market_history": lambda: P.build_market_val_payload(format="json"),
    "pmd_market_asof_20210630": lambda: P.build_market_val_payload(format="json", as_of="20210630"),
    "pmd_sector_asof_20210630": lambda: P.build_sector_val_payload(format="json", scheme="중분류", as_of="20210630"),
    "pmd_sector_major_asof_20210630": lambda: P.build_sector_val_payload(format="json", scheme="대분류", as_of="20210630"),
    "pmd_sector_ksic_asof_20210630": lambda: P.build_sector_val_payload(format="json", scheme="ksic", as_of="20210630"),
    "pmd_sector_asof_seam_minus1": lambda: P.build_sector_val_payload(format="json", scheme="중분류", as_of="20231015"),
    "pmd_sector_asof_seam": lambda: P.build_sector_val_payload(format="json", scheme="중분류", as_of="20231016"),
    "pmd_sector_asof_seam_plus": lambda: P.build_sector_val_payload(format="json", scheme="중분류", as_of="20231030"),
    "pmd_sector_asof_after_month_end": lambda: P.build_sector_val_payload(format="json", scheme="대분류", as_of="20231031"),
    "pmd_sector_asof_before_first": lambda: P.build_sector_val_payload(format="json", scheme="중분류", as_of="20191231"),
    "pmd_sector_latest": lambda: P.build_sector_val_payload(format="json", scheme="중분류"),
    "pmd_firm_sector_history": lambda: P.build_sector_val_payload("삼성전자", format="json", scheme="중분류"),
    "pmd_firm_sector_history_ksic": lambda: P.build_sector_val_payload("삼성전자", format="json", scheme="ksic", as_of="20210630"),
}


def _all(names=CALLS) -> dict[str, str]:
    async def go():
        return {k: _j(await CALLS[k]()) for k in names}
    return asyncio.run(go())


@pytest.fixture
def golden(env, monkeypatch):
    """꺼짐 · 옛 행이 DB 에 다 있는(지금) 응답 — 기준."""
    monkeypatch.setenv("OPM_COLD", "0")
    env(moved=False)
    out = _all()
    for k, v in out.items():                     # 기준 자체가 헛돌지 않게 — 3년 밖이 실제로 담겨 있다
        if k.startswith(("trading_market_all", "pmd_market_history")):
            assert "20151230" in v or "20200131" in v, k
    assert '"status": "ok"' in out["pmd_sector_asof_20210630"] and '"as_of": "20210625"' in out["pmd_sector_asof_20210630"]
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 0. 순수 규칙
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("seam,first,step,gap", [
    ("20231016", None, 14, False), ("20231016", "20151230", 14, False),           # DB 가 S 이전부터 갖는다(지우기 전)
    ("20231016", "20231020", 14, False), ("20231016", "20231031", 35, False),      # S 뒤 첫 표본(주·월말)
    ("20231016", "20231103", 14, True), ("20230602", "20231020", 35, True),        # 표본 간격보다 긴 빈칸 = 틈
    ("20231016", "20231030", 14, False), ("20231016", "20231031", 14, True),       # 경계: 정확히 간격만큼은 틈이 아니다, 하루 넘으면 틈
])
def test_agg_seam_gap(seam, first, step, gap):
    assert R.agg_seam_gap(seam, first, step) is gap


def test_desc_key_orders_like_snap_dd_desc_market_asc():
    rows = [("20200131", "KS"), ("20200228", "KQ"), ("20200131", "KQ"), ("20200228", "KS")]
    assert sorted(rows, key=lambda x: (P._desc_key(x[0]), x[1])) == [
        ("20200228", "KQ"), ("20200228", "KS"), ("20200131", "KQ"), ("20200131", "KS")]


def test_default_datasets_include_both_aggregates():
    assert set(CS.DEFAULT_DATASETS) >= {"krx_weekly_hist", "krx_cap_agg_hist", "opm_val_market_hist"}


# ═════════════════════════════════════════════════════════════════════════════
# 1. 골든 — 응답 글자 동일
# ═════════════════════════════════════════════════════════════════════════════
def test_off_reads_nothing_new(env, monkeypatch):
    monkeypatch.setenv("OPM_COLD", "0")
    seen = []

    async def spy(*a, **k):
        seen.append(a)
        return CS.ColdError("off")
    monkeypatch.setattr(CS, "ready", spy)
    db = env(moved=False)
    _all()
    assert seen == []
    assert not any("SELECT min(" in s for s in db.sql)                     # 틈 판정용 질의도 없다


def test_cold_before_the_move_is_byte_identical(golden, env, cold_hist):
    cold_hist(_cold_payloads(), upto=UPTO)
    env(moved=False)
    got = _all()
    assert {k: v for k, v in got.items() if v != golden[k]} == {}
    st = CS.stats()
    assert st["counters"]["reads"] > 0 and not st["degraded"]             # 콜드를 실제로 읽었다


def test_cold_after_the_move_is_byte_identical(golden, env, cold_hist):
    cold_hist(_cold_payloads(), upto=UPTO)
    db = env(moved=True)
    assert db("SELECT min(price_dd) FROM krx_cap_agg")[0][0] >= CUT          # 정말 지웠다
    got = _all()
    assert {k: v for k, v in got.items() if v != golden[k]} == {}
    assert CS.stats()["counters"]["reads"] > 0


def test_seam_takes_cold_before_and_db_from_s_without_duplicates(env, cold_hist, monkeypatch):
    """S 앞뒤 값이 서로 다르게 꾸민다 — 콜드는 S 이전만, DB 는 S 이후만 쓰였는지 값으로 본다. 같은 날짜는 한 번만."""
    def tag(rows, k):
        return [{**r, "cap": r["cap"] + 1} for r in rows] if k == "krx_cap_agg_hist" else rows
    payload = {k: tag(v, k) for k, v in _cold_payloads().items()}
    payload["krx_cap_agg_hist"] += [{**r, "cap": -1} for r in CAP_ROWS if CUT <= r["price_dd"] < "20231103"]   # 서빙본에 S 이후 행이 있어도 쓰지 않는다
    cold_hist(payload, upto=UPTO)
    env(moved=False)
    p = asyncio.run(T.build_cap_agg_payload("market", format="json", freq="weekly"))
    s = p["data"]["series"]
    keys = [(x["asof"], x["market"]) for x in s]
    assert len(keys) == len(set(keys)) and keys == sorted(keys)
    real = {(r["price_dd"], r["market"]): r["cap"] for r in CAP_ROWS if r["scheme"] == "market"}
    assert all(x["cap_krw"] == real[(x["asof"], x["market"])] + 1 for x in s if x["asof"] < CUT)     # 콜드 값
    assert all(x["cap_krw"] == real[(x["asof"], x["market"])] for x in s if x["asof"] >= CUT)        # DB 값
    assert s[0]["asof"] == "20151230" and s[-1]["asof"] == LATEST


def test_coverage_before_b_moves_the_seam_to_the_day_after_coverage(env, cold_hist):
    """coverage 끝이 B 보다 이르면 S = coverage 다음 날 — 그 사이(S~B)는 DB(지우기 전 행)에서 읽는다(S7: 롤링은 coverage 까지만 지운다)."""
    pl = _cold_payloads("20230901")
    cold_hist(pl, upto="20230901")
    db = env(moved=False)
    db.con.execute("DELETE FROM krx_cap_agg WHERE price_dd < '20230901'")    # 롤링이 coverage 까지만 지운 상태
    p = asyncio.run(T.build_cap_agg_payload("market", format="json", freq="weekly"))
    dates = [x["asof"] for x in p["data"]["series"]]
    assert dates[0] == "20151230" and "20230825" in dates and "20230901" in dates and "20231013" in dates
    assert len([d for d in dates if d == "20230901"]) == 2                  # KS·KQ 한 번씩(중복 0)
    assert not any("보관본을 지금 읽지 못해" in w for w in p["warnings"])


# ═════════════════════════════════════════════════════════════════════════════
# 2. 강등
# ═════════════════════════════════════════════════════════════════════════════
def _broken(monkeypatch, tmp_path):
    monkeypatch.setenv("OPM_COLD", "1")
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", "file://" + str(tmp_path / "nope" / "manifest.json"))
    monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))


def test_degraded_before_the_move_is_byte_identical(golden, env, monkeypatch, tmp_path):
    _broken(monkeypatch, tmp_path)
    env(moved=False)
    got = _all()
    assert {k: v for k, v in got.items() if v != golden[k]} == {}
    assert CS.stats()["degraded"]                                         # 강등은 센다(/health)


def _strip_note(s: str) -> str:
    p = json.loads(s)
    if isinstance(p.get("warnings"), list):
        p["warnings"] = [w for w in p["warnings"] if w != R.COLD_AGG_DEGRADED_NOTE]
    return _j(p)


def test_degraded_after_the_move_is_db_window_plus_one_line(env, monkeypatch, tmp_path):
    monkeypatch.setenv("OPM_COLD", "0")
    env(moved=True)
    off = _all()                                                          # 꺼짐 · 지운 뒤 = DB 3년만, 안내 없음
    assert R.COLD_AGG_DEGRADED_NOTE not in "".join(off.values())
    _broken(monkeypatch, tmp_path)
    env(moved=True)
    got = _all()
    for k in ("trading_market_all_weekly", "trading_market_since_20151230", "trading_sector_bucket_IT", "pmd_market_history",
              "pmd_sector_asof_20210630", "pmd_firm_sector_history"):
        assert R.COLD_AGG_DEGRADED_NOTE in got[k], k
    for k in ("pmd_sector_latest", "pmd_sector_asof_seam_plus"):          # 3년 안 질의 — 안내 없음
        assert R.COLD_AGG_DEGRADED_NOTE not in got[k], k
    assert {k: v for k, v in got.items() if _strip_note(v) != off[k]} == {}
    p = json.loads(got["trading_market_all_weekly"])
    assert p["data"]["series"][0]["asof"] >= CUT


def test_seam_gap_degrades(env, cold_hist):
    """서빙본 coverage 는 2023-06 까지인데 DB 는 B 이후만 남았다(S7 이 깨진 롤링) — 빈 넉 달을 잇지 않고 강등한다."""
    cold_hist(_cold_payloads("20230602"), upto="20230602")
    env(moved=True)
    p = asyncio.run(T.build_cap_agg_payload("market", format="json", freq="weekly"))
    assert R.COLD_AGG_DEGRADED_NOTE in p["warnings"]
    assert p["data"]["series"][0]["asof"] >= CUT
    assert CS.stats()["degraded"].get("seam_gap")


def test_missing_dataset_in_manifest_degrades_quietly_before_the_move(golden, env, cold_hist):
    """발행기가 집계 데이터셋을 올리기 전에 서버가 먼저 배포돼도(목록에 없음) 지우기 전이면 응답이 같다."""
    cold_hist({"opm_val_market_hist": _cold_payloads()["opm_val_market_hist"]}, upto=UPTO)
    env(moved=False)
    got = _all(["trading_market_all_weekly", "trading_sector_bucket_IT", "pmd_market_history"])
    assert {k: v for k, v in got.items() if v != golden[k]} == {}


def test_since_inside_the_window_never_opens_the_cold_store(env, cold_hist, monkeypatch):
    cold_hist(_cold_payloads(), upto=UPTO)
    seen = []
    real = CS.ready

    async def spy(*a, **k):
        seen.append(a)
        return await real(*a, **k)
    monkeypatch.setattr(CS, "ready", spy)
    env(moved=True)
    p = asyncio.run(T.build_cap_agg_payload("market", format="json", since="20240105", freq="weekly"))
    assert seen == [] and p["data"]["series"][0]["asof"] == "20240105"


# ═════════════════════════════════════════════════════════════════════════════
# 3. 리허설 측정 — 읽기 시간·RSS(rss_guard 안)
# ═════════════════════════════════════════════════════════════════════════════
def test_cold_reads_are_fast_and_inside_the_rss_guard(env, cold_hist):
    import time
    cold_hist(_cold_payloads(), upto=UPTO)
    env(moved=True)
    _all(["trading_sector_bucket_IT"])                                     # 첫 읽기(받기·검증)
    t0 = time.perf_counter()
    _all()
    dt = time.perf_counter() - t0
    st = CS.stats()
    assert st["read_p95_ms"] < 200 and dt < 10
    rss = CS._rss_mb()
    assert rss is None or rss < CS._rss_guard_mb()
