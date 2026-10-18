"""종목별 밸류에이션 스냅샷 표(`opm_val_firm`) 없이 읽을 때 계산 — 업종 비교의 기업 배수·KSIC 섹터 (261018, Supabase 재설계 6단계).

종전: 주간 배치(`scripts/market_val_weekly.py`)가 종목마다 시총 ÷ 재무 요약을 저장했고, 서버는 업종 비교(`scope=sector` + company)에서
그 날 행을 읽었다. 이제 서버가 주간 곡선과 같은 정의(`_pit_raw` — 그 날 시총 ÷ 그 시점까지 공시된 재무)로 그 날 한 점을 계산한다.

여기서 지키는 것
  ① KSIC 하이브리드 버킷 — 서버(`ksic_bucket`)와 배치(`bucket`)가 KSIC 코드 전부·오버라이드 종목에서 같은 값을 낸다(정의가 두 곳이라 어긋나면 안 된다).
  ② 재무 요약과 시점별 재무가 같은 세계에서는 업종 비교 응답이 **바꾸기 전 코드(origin/main)와 글자까지 같다** — 옛 코드는 가짜 스냅샷 표를,
     새 코드는 가짜 재무 표를 읽는다. 행이 없던 경우(완전자본잠식·재무 요약 수집 실패·그 날 시세 없음)도 같은 응답이다.
  ③ 서버 코드 어디도 그 표를 읽지 않는다.
network 0 · DB 0 (전부 monkeypatch). 운영 DB 대조(10-18, 읽기 전용)는 PR 본문에 있다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import re
import subprocess
import types

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services.ksic_sector import ksic_bucket

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ① KSIC 버킷 — 서버와 배치가 같다
# ═══════════════════════════════════════════════════════════════════════════════════════════
def _batch_bucket():
    spec = importlib.util.spec_from_file_location("mvw_for_test", ROOT / "scripts" / "market_val_weekly.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.bucket


def test_ksic_bucket_matches_the_batch_rule_for_every_ksic_code_and_override():
    batch = _batch_bucket()
    m = json.loads((ROOT / "open_proxy_mcp/data/ksic/opm_sector_map.json").read_text(encoding="utf-8"))
    codes = sorted(json.loads((ROOT / "open_proxy_mcp/data/ksic/ksic10_ko.json").read_text(encoding="utf-8")))
    codes += list(m.get("prefix_reassign", {})) + ["29271", "292711", "26410", "64992", "0"]
    tickers = [None, "000000"] + list(m.get("code_overrides", {}))
    n = 0
    for ind in codes:
        for t in tickers:
            assert ksic_bucket(ind, t) == batch(ind, t), (ind, t)
            n += 1
    assert n > 1000, "대조가 헛돌았다"


@pytest.mark.parametrize("ind", [None, "", "none", "err"])
def test_ksic_bucket_is_none_without_a_usable_industry_code(ind):
    """배치도 업종코드가 비었거나 수집 실패 표지면 섹터를 비운다(`sec = bucket(...) if ind and ind not in ("none","err") else None`)."""
    assert ksic_bucket(ind, "005930") is None


def test_samsung_override_and_semiconductor_equipment_reassign():
    assert ksic_bucket("264", "005930") == "261"          # 종목 오버라이드
    assert ksic_bucket("29271", "123456") == "261"        # 세세분류 재배정
    assert ksic_bucket("26410", "123456") == "264"        # 3자리 중분류
    assert ksic_bucket("10100", "123456") == "10"         # 2자리


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ② 업종 비교 — 옛 코드(스냅샷 표) ↔ 새 코드(읽을 때 계산) 응답이 같다
# ═══════════════════════════════════════════════════════════════════════════════════════════
AS_OF = "20261016"
ISU = "000660"
# 재무 — 시점별(연간·분기)과 재무 요약이 같은 값인 세계(운영 최신 주 2,561종목 중 2,494종목이 이렇다 — 10-18 실측).
FIN_Y = [(2024, 19.8e12, 54.0e12, None, None)]
FUND_FY = (20.0e12, 60.0e12)                                   # ni_fy, eq_fy (최신 확정 FY = 2025)
FIN_Q = [(2025, 2, 9.0e12, 58.0e12), (2026, 2, 12.0e12, 66.0e12)]   # 누적 지배순이익, 지배자본
TTM = 20.0e12 + 12.0e12 - 9.0e12                               # FY2025 + 누적(2026,2) − 누적(2025,2) = 23조
MRQ = 66.0e12
CAP = 150.0e12


def _sectors(scheme: str) -> list[tuple]:
    """opm_val_market 이 scheme·날짜로 돌려주는 섹터 행 (snap_dd, market, sector, label, n, cap, per_ttm, pbr_mrq, per_fy0, pbr_fy0, ni_fy0, ni_ttm)."""
    if scheme == "ksic":
        return [(AS_OF, "KS", "261", "반도체·반도체장비(261+29271)", 30, 900e12, 14.123, 1.987, 15.0, 2.0, 1e12, 1e12),
                (AS_OF, "KS", "_fold", "기타(소규모)", 12, 10e12, 9.0, 0.9, 9.5, 0.95, 1e11, 1e11)]
    return [(AS_OF, "KS", "G2530", "반도체와반도체장비", 30, 900e12, 13.456, 1.876, 14.0, 1.9, 1e12, 1e12)]


class FakeDb:
    def __init__(self, scheme: str, *, induty="26110", fetched="ok", currency="KRW", cap=CAP, fin_q=None):
        self.scheme, self.induty, self.fetched, self.currency, self.cap = scheme, induty, fetched, currency, cap
        self.fin_q = FIN_Q if fin_q is None else fin_q
        self.log: list[str] = []

    def snapshot_row(self):
        """옛 코드가 읽던 스냅샷 표 행 — 배치 규칙 그대로(재무 요약으로 계산, 완전자본잠식·수집 실패·시세 없음은 행 없음)."""
        if self.fetched != "ok" or not self.cap:
            return []
        eq_mrq = self.fin_q[-1][3] if self.fin_q else None
        if eq_mrq is not None and eq_mrq <= 0:
            return []
        ttm = TTM if self.fin_q == FIN_Q else None
        sec = ksic_bucket(self.induty, ISU)
        lab = {r[2]: r for r in _sectors("ksic")}.get(sec)
        return [(sec, "KS", self.cap / ttm if ttm and ttm > 0 else None, self.cap / eq_mrq if eq_mrq and eq_mrq > 0 else None,
                 lab[3] if lab else None, lab[6] if lab else None, lab[7] if lab else None)]

    def __call__(self, sql, params=()):
        s = " ".join(sql.split())
        self.log.append(s)
        if "FROM opm_val_firm" in s:
            return self.snapshot_row()
        if s.startswith("SELECT snap_dd, market, sector, label, n, cap, per_ttm"):
            return _sectors(params[0])
        if "FROM opm_val_market WHERE market=%s AND sector=%s AND scheme=%s" in s:
            return [("20251226", 12.0, 11.0, 1.5, 1.4, 800e12, 1e12, 1e12), (AS_OF, 14.0, 13.0, 1.9, 1.8, 900e12, 1e12, 1e12)]
        if "FROM wise_sector WHERE ticker=%s" in s:
            return [("G2530", "반도체와반도체장비", "20261016")]
        if s.startswith("SELECT market, induty, currency, fetched FROM dart_fundamentals"):
            return [("KS", self.induty, self.currency, self.fetched)]
        if "FROM krx_weekly WHERE ticker=%s AND price_dd=%s UNION ALL" in s:
            return [(self.cap, "KS", 1)] if self.cap else []
        if "FROM dart_finstat_y" in s:
            return FIN_Y
        if s == "SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s":
            return [FUND_FY]
        if "FROM dart_finstat_q" in s:
            return list(self.fin_q)
        raise AssertionError("가짜 DB 가 모르는 SQL: " + s)


def _old_module():
    try:
        src = subprocess.check_output(["git", "show", "origin/main:open_proxy_mcp/services/price_multiple_data.py"], cwd=ROOT,
                                      stderr=subprocess.DEVNULL)
    except Exception:
        pytest.skip("origin/main 을 읽을 수 없다(얕은 클론 등) — 옛 코드 대조는 건너뛴다")
    if b"FROM opm_val_firm" not in src:
        pytest.skip("origin/main 이 이미 스냅샷 표를 읽지 않는다 — 이 대조는 병합 전 한 번만 뜻이 있다")
    mod = types.ModuleType("old_pmd_for_test")
    mod.__file__ = "old_pmd_for_test.py"
    exec(compile(src, "old_pmd_for_test.py", "exec"), mod.__dict__)
    return mod


def _run(mod, mp, db, scheme):
    async def resolve(q):
        return {"corp_name": "SK하이닉스", "stock_code": ISU, "corp_code": "00164779"}, None

    async def no_fwd(*a, **k):
        return {}, {}, None

    async def no_div(*a, **k):
        return {}, {}
    mp.setattr(mod, "_pg_rows", db)
    mp.setattr(mod, "_resolve_listed", resolve)
    mp.setattr(mod, "_fwd_val_map", no_fwd)
    mp.setattr(mod, "_div_yield_map", no_div)
    mp.setattr(mod, "today_kst", lambda: __import__("datetime").date(2026, 10, 18))
    return asyncio.run(mod.build_sector_val_payload(company="SK하이닉스", scheme=scheme, format="json"))


CASES = {
    "ksic_normal": dict(scheme="ksic"),
    "mid_normal": dict(scheme="중분류"),
    "major_normal": dict(scheme="대분류"),
    "ksic_folded_small_sector": dict(scheme="ksic", induty="10100"),            # 섹터 표에 없는 버킷 → 기타(소규모) 비교
    "impaired_no_ctx": dict(scheme="중분류", fin_q=[(2025, 2, 9.0e12, 58.0e12), (2026, 2, 12.0e12, -1.0)]),
    "fundamentals_not_ok": dict(scheme="ksic", fetched="nocorp"),
    "no_price_that_day": dict(scheme="중분류", cap=None),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_sector_compare_matches_the_snapshot_table_answer(monkeypatch, name):
    kw = dict(CASES[name])
    scheme = kw.pop("scheme")
    old = _old_module()
    with monkeypatch.context() as mp:
        p_old = _run(old, mp, FakeDb(scheme, **kw), scheme)
    with monkeypatch.context() as mp:
        db = FakeDb(scheme, **kw)
        p_new = _run(S, mp, db, scheme)
    assert not any("opm_val_firm" in s for s in db.log), "새 코드가 스냅샷 표를 읽었다"
    assert json.dumps(p_new, ensure_ascii=False, default=str) == json.dumps(p_old, ensure_ascii=False, default=str)


def test_the_cases_exercise_both_branches(monkeypatch):
    with monkeypatch.context() as mp:
        ok = _run(S, mp, FakeDb("ksic"), "ksic")
    c = ok["data"]["company"]
    assert c["sector"] == "261" and c["firm_per_ttm"] == round(CAP / TTM, 2) and c["firm_pbr_mrq"] == round(CAP / MRQ, 2)
    assert c["sector_per_ttm"] == 14.12 and c["sector_label"].startswith("반도체")
    with monkeypatch.context() as mp:
        imp = _run(S, mp, FakeDb("중분류", fin_q=[(2025, 2, 9.0e12, 58.0e12), (2026, 2, 12.0e12, -1.0)]), "중분류")
    assert imp["data"]["company"] is None and any("종목 스냅샷 없음" in w for w in imp["warnings"])


def test_firm_value_uses_the_daily_store_when_the_snapshot_date_is_not_a_weekly_row():
    """`_firm_cap_at` 은 그 날 주간 행이 없으면 일별 저장분을 보고(닫힌 주만 담는 krx_weekly 에 없는 이번 주 스냅샷 날짜), 그 날 행이 정확히 없으면
    같은 주 안 그 날 이하 가장 늦은 행으로 대신한다. 실제 SQL 실행은 pg17 시험(`test_disclosure_flow_join_pg.py::test_firm_cap_at_*`)."""
    body = " ".join(S._FIRM_CAP_SQL.split())
    assert "FROM krx_weekly WHERE ticker=%s AND price_dd=%s" in body and "FROM krx_daily_v WHERE ticker=%s AND trd_dd=%s" in body
    assert "price_dd >= %s AND price_dd < %s ORDER BY price_dd DESC LIMIT 1" in body, "같은 주·그 날 이하(뒤 날짜는 쓰지 않는다)"
    assert "ORDER BY src LIMIT 1" in body, "같은 날 두 표에 다 있으면 주간 행(곡선과 같은 원천)을 먼저 쓴다"


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ③ 서버 코드는 스냅샷 표를 읽지 않는다
# ═══════════════════════════════════════════════════════════════════════════════════════════
def test_no_server_code_reads_the_firm_snapshot_table():
    hits = []
    for p in (ROOT / "open_proxy_mcp").rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(FROM|JOIN|INTO|UPDATE)\s+opm_val_firm\b", line):
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert not hits, f"서버가 opm_val_firm 을 읽거나 쓴다: {hits}"


def test_pit_point_is_the_rounded_raw_point():
    fin = {2025: (20.0e12, 60.0e12)}
    finq = {(2025, 2): (9.0e12, 58.0e12), (2026, 2): (12.0e12, 66.0e12)}
    raw = S._pit_raw(AS_OF, CAP, fin, finq)
    pt = S._pit_point(AS_OF, CAP, fin, finq)
    assert pt == {"asof": AS_OF, "pit_fy": 2025, "pit_q": "2026Q2", "cap_krw": round(CAP),
                  "per_fy0": round(CAP / 20.0e12, 3), "pbr": round(CAP / 60.0e12, 3),
                  "per_ttm": round(CAP / TTM, 3), "pbr_mrq": round(CAP / MRQ, 3)}
    assert raw["per_ttm"] == CAP / TTM
    assert S._pit_point(AS_OF, None, fin, finq) is None and S._pit_point(AS_OF, CAP, {}, finq) is None
    assert S._pit_impaired(AS_OF, fin, {(2026, 2): (1.0, -5.0)})
    imp = S._pit_point(AS_OF, CAP, fin, {(2025, 2): (9.0e12, 58.0e12), (2026, 2): (12.0e12, -5.0)})
    assert imp["per_fy0"] is None and imp["per_ttm"] is None and imp["pbr_mrq"] is None and imp["pbr"] is not None
