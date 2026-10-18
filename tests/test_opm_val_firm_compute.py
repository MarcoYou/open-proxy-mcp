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
    def __init__(self, scheme: str, *, induty="26110", fetched="ok", currency="KRW", cap=CAP, fin_q=None, fin_y=None, fund_fy=None):
        self.scheme, self.induty, self.fetched, self.currency, self.cap = scheme, induty, fetched, currency, cap
        self.fin_q = FIN_Q if fin_q is None else fin_q
        self.fin_y = FIN_Y if fin_y is None else fin_y
        self.fund_fy = FUND_FY if fund_fy is None else fund_fy
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
        if s.startswith("SELECT market, induty, currency, fetched, acc_mt FROM dart_fundamentals"):
            return [("KS", self.induty, self.currency, self.fetched, None)]   # 261018: 결산월 칸(비면 12월 = 종전 응답)
        if "FROM krx_weekly WHERE ticker=%s AND price_dd=%s UNION ALL" in s:
            return [(self.cap, "KS", 1)] if self.cap else []
        if "FROM dart_finstat_y" in s:
            return list(self.fin_y)
        if s == "SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s":
            return [self.fund_fy]
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
    "fundamentals_not_ok": dict(scheme="ksic", fetched="nocorp"),
    "no_price_that_day": dict(scheme="중분류", cap=None),
    # 상장 첫해 — 직전 사업연도 연간 행이 없고(연간 재무 표 nodata · 재무 요약 연간 값 null) 분기 표에 올해 1·2분기만 있다.
    # 스냅샷 표는 재무 요약의 최근 분기 자본으로 PBR(MRQ)를 냈고 PER(TTM)은 비었다(10-18 운영 최신 주 4종목 — 153890·439960·477850·487580).
    "new_listing_no_annual_row": dict(scheme="ksic", fin_y=[], fund_fy=(None, None),
                                      fin_q=[(2026, 1, -0.3e12, 10.0e12), (2026, 2, 0.9e12, 31.0e12)]),
    "new_listing_no_annual_row_mid": dict(scheme="중분류", fin_y=[], fund_fy=(None, None),
                                          fin_q=[(2026, 1, -0.3e12, 10.0e12), (2026, 2, 0.9e12, 31.0e12)]),
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
    assert imp["data"]["company"] is None and any("완전자본잠식" in w for w in imp["warnings"])


def test_impaired_firm_still_has_no_row_but_the_warning_says_why(monkeypatch):
    """완전자본잠식(260829 마스터 지시 — 모든 연산·출력에서 뺀다): 종전처럼 기업 행이 없다. 종전 경고는 「종목 스냅샷 없음(비상장·미수집)」이라
    상장·수집된 회사를 두고 틀린 말을 했다 — 그 한 문장만 바뀌고 나머지는 옛 코드와 글자까지 같다."""
    kw = dict(fin_q=[(2025, 2, 9.0e12, 58.0e12), (2026, 2, 12.0e12, -1.0)])
    old = _old_module()
    with monkeypatch.context() as mp:
        p_old = _run(old, mp, FakeDb("중분류", **kw), "중분류")
    with monkeypatch.context() as mp:
        p_new = _run(S, mp, FakeDb("중분류", **kw), "중분류")
    assert p_old["data"]["company"] is None and p_new["data"]["company"] is None
    w_old = [w for w in p_old["warnings"] if "종목 스냅샷 없음" in w]
    w_new = [w for w in p_new["warnings"] if "완전자본잠식" in w]
    assert len(w_old) == 1 and len(w_new) == 1 and "비상장" not in w_new[0]
    p_new["warnings"][p_new["warnings"].index(w_new[0])] = w_old[0]
    assert json.dumps(p_new, ensure_ascii=False, default=str) == json.dumps(p_old, ensure_ascii=False, default=str)


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ②-2 그 시점 칸이 비면 앞선 칸으로(감사 4 — 설계 v03 §6-0 P-1) · 「기준 분기 늦음」 표시
# ═══════════════════════════════════════════════════════════════════════════════════════════
FUND25 = (20.0e12, 60.0e12)                      # 최신 확정 FY 2025 (ni, eq)


def _sector(monkeypatch, scheme="ksic", **kw):
    with monkeypatch.context() as mp:
        return _run(S, mp, FakeDb(scheme, **kw), scheme)


def test_half_year_report_missing_falls_back_to_q1_with_a_flag(monkeypatch):
    """반기보고서 미제출(그 시점 칸 2026Q2 가 빔) — 종전 스냅샷 표는 1분기 자본으로 PBR 을 냈다. 이제 같은 칸으로 PBR(MRQ)·PER(TTM)을 세고 표시한다."""
    fq = [(2025, 1, 4.0e12, 56.0e12), (2025, 2, 9.0e12, 58.0e12), (2026, 1, 5.0e12, 63.0e12)]
    p = _sector(monkeypatch, fin_q=fq)
    c = p["data"]["company"]
    assert c["firm_pbr_mrq"] == round(CAP / 63.0e12, 2)
    assert c["firm_per_ttm"] == round(CAP / (20.0e12 + 5.0e12 - 4.0e12), 2)
    assert c["firm_basis"] == {"pit_q": "2026Q2", "per_ttm": "2026Q1", "pbr_mrq": "2026Q1"}
    assert any(w.startswith("기준 분기 늦음 — SK하이닉스 의 그 시점 칸(2026Q2)이 비어 PER(TTM) 2026Q1 · PBR(MRQ) 2026Q1 칸으로") for w in p["warnings"])


def test_semiannual_reit_uses_the_annual_cell(monkeypatch):
    """반기마다 결산하는 리츠 — 분기 표에 반기 칸만 있고 1·3분기 칸이 없다. 그 시점이 1분기(5/15~8/13)면 직전 사업보고서(연간 표) 칸을 쓴다."""
    fq = [(2025, 2, 3.4e12, 211e12), (2026, 2, 3.43e12, 207e12)]
    fy = [(2025, 6.95e12, 214.9e12, None, None)]
    p = _sector(monkeypatch, fin_q=fq, fin_y=fy, fund_fy=(6.95e12, 214.9e12))
    c = p["data"]["company"]
    # 20261016 의 그 시점 칸은 2026Q2(반기) — 값이 있으니 대체도 표시도 없다(응답 모양이 종전 그대로)
    assert "firm_basis" not in c and not any(w.startswith("기준 분기 늦음") for w in p["warnings"])
    assert c["firm_pbr_mrq"] == round(CAP / 207e12, 2)
    # 1분기 창(5/15~8/13)에는 1분기 칸이 없다 → 직전 사업보고서(연간 표) 칸
    fin = {2025: (6.95e12, 214.9e12)}
    finq = {(2025, 2): (3.4e12, 211e12), (2026, 2): (3.43e12, 207e12)}
    fb = S._pit_fallback("20260807", CAP, fin, finq)
    assert fb["pbr_mrq"] == (CAP / 214.9e12, (2025, 4)) and fb["per_ttm"] == (CAP / 6.95e12, (2025, 4))


def test_fallback_only_when_the_cell_is_empty_not_when_it_is_a_loss():
    """그 칸 값이 있는데 TTM 이 적자면 N/M 그대로다(앞선 칸의 흑자로 바꾸지 않는다)."""
    fin = {2025: (2.0e12, 60.0e12)}
    finq = {(2025, 2): (3.0e12, 58.0e12), (2026, 1): (1.0e12, 61.0e12), (2026, 2): (-2.0e12, 62.0e12)}
    assert S._pit_raw(AS_OF, CAP, fin, finq)["per_ttm"] is None
    assert S._pit_fallback(AS_OF, CAP, fin, finq) == {}


def test_fallback_never_looks_ahead_and_stops_after_a_year():
    fin = {2025: (20.0e12, 60.0e12)}
    # 2026Q3(11/14 마감) 칸이 이미 있어도 20261016 에는 쓰지 않는다 — 앞선 칸(2026Q1)으로
    finq = {(2025, 1): (4.0e12, 56.0e12), (2026, 1): (5.0e12, 63.0e12), (2026, 3): (15.0e12, 70.0e12)}
    fb = S._pit_fallback(AS_OF, CAP, fin, finq)
    assert fb["pbr_mrq"][1] == (2026, 1) and fb["per_ttm"][1] == (2026, 1)
    # 1년 넘게 앞선 칸뿐이면(2025Q1 = 2025-03-31 < 2026Q2 기간 말 1년 전 2025-06-30) 대체하지 않는다
    old_only = {(2024, 1): (1.0e12, 50.0e12), (2025, 1): (4.0e12, 56.0e12)}
    assert S._pit_fallback(AS_OF, CAP, {}, old_only) == {}
    # 연간 표 칸(2025 사업보고서 = 2025-12-31)은 1년 안이다
    assert S._pit_fallback(AS_OF, CAP, fin, old_only)["pbr_mrq"] == (CAP / 60.0e12, (2025, 4))


def test_fallback_to_a_non_positive_equity_is_impaired(monkeypatch):
    fq = [(2025, 1, 4.0e12, 56.0e12), (2025, 2, 9.0e12, 58.0e12), (2026, 1, -5.0e12, -1.0)]
    p = _sector(monkeypatch, "중분류", fin_q=fq)
    assert p["data"]["company"] is None and any("완전자본잠식" in w for w in p["warnings"])


def test_twelve_month_fiscal_year_end_cells_are_ordered_by_period_end():
    """3월 결산 — 칸 표기 순서와 기간 말 순서가 다르다(2026Q4 = 2026.03 말 < 2026Q1 = 2026.06 말). 앞선 칸은 기간 말로 고른다."""
    fin = {2026: (10.0e12, 100.0e12)}
    finq = {(2025, 2): (3.0e12, 90.0e12), (2025, 3): (5.0e12, 95.0e12)}   # 2025.09·2025.12 말
    fb = S._pit_fallback(AS_OF, CAP, fin, finq, 3)                     # 그 시점 칸(3월 결산 20261016) = 2026Q1(6월 말)? 비었다
    assert S._pit_quarter(AS_OF, 3) == (2026, 1)
    assert fb["pbr_mrq"] == (CAP / 100.0e12, (2026, 4)), "2026.03 말 사업보고서가 2025.12 말 3분기보다 늦다"


def test_firm_value_uses_the_daily_store_when_the_snapshot_date_is_not_a_weekly_row():
    """`_firm_cap_at` 은 그 날 주간 행이 없으면 일별 저장분을 보고(닫힌 주만 담는 krx_weekly 에 없는 이번 주 스냅샷 날짜), 그 날 행이 정확히 없으면
    같은 주 안 그 날 이하 가장 늦은 행, 그것도 없으면 같은 주 안 그 날 뒤 가장 이른 행으로 대신한다(각 단계 주간 → 일별).
    실제 SQL 실행은 pg17 시험(`test_disclosure_flow_join_pg.py::test_firm_cap_at_*`)."""
    body = " ".join(S._FIRM_CAP_SQL.split())
    assert "FROM krx_weekly WHERE ticker=%s AND price_dd=%s" in body and "FROM krx_daily_v WHERE ticker=%s AND trd_dd=%s" in body
    assert "price_dd >= %s AND price_dd < %s ORDER BY price_dd DESC LIMIT 1" in body, "같은 주·그 날 이하 가장 늦은 행"
    assert "price_dd > %s AND price_dd <= %s ORDER BY price_dd LIMIT 1" in body, "같은 주·그 날 뒤 가장 이른 행"
    assert "trd_dd > %s AND trd_dd <= %s ORDER BY trd_dd LIMIT 1" in body
    srcs = [int(x) for x in re.findall(r"(\d) AS src", body)]
    assert srcs == [1, 2, 3, 4, 5, 6], "그 날(주간·일별) → 그 날 이하(주간·일별) → 그 날 뒤(주간·일별)"
    assert "ORDER BY src LIMIT 1" in body, "앞 단계가 이긴다 — 같은 날 두 표에 다 있으면 주간 행(곡선과 같은 원천)"


def test_firm_cap_at_binds_the_same_week_bounds(monkeypatch):
    """자리표시자 순서 — 그 날 이하 쪽은 (주 첫날, 그 날), 그 날 뒤 쪽은 (그 날, 주 끝날). 주 = 달력 연도 안으로 자른 ISO 주."""
    seen = []

    def rows(sql, params=()):
        seen.append(params)
        return [(1.0e12, "KS", 5)]
    monkeypatch.setattr(S, "_pg_rows", rows)
    assert asyncio.run(S._firm_cap_at(ISU, "20261014")) == (1.0e12, "KS")
    assert seen[0] == (ISU, "20261014", ISU, "20261014", ISU, "20261012", "20261014", ISU, "20261012", "20261014",
                       ISU, "20261014", "20261018", ISU, "20261014", "20261018")
    seen.clear()
    asyncio.run(S._firm_cap_at(ISU, "20251231"))      # 연도 경계 주 — 2026-01-01 이후로 넘어가지 않는다
    assert seen[0][11:13] == ("20251231", "20251231") and seen[0][5:7] == ("20251229", "20251231")
    assert S._FIRM_CAP_SQL.count("%s") == len(seen[0])


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
    assert S._pit_point(AS_OF, None, fin, finq) is None
    assert S._pit_impaired(AS_OF, fin, {(2026, 2): (1.0, -5.0)})
    imp = S._pit_point(AS_OF, CAP, fin, {(2025, 2): (9.0e12, 58.0e12), (2026, 2): (12.0e12, -5.0)})
    assert imp["per_fy0"] is None and imp["per_ttm"] is None and imp["pbr_mrq"] is None and imp["pbr"] is not None


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ④ 그 시점 확정 FY 의 연간 행이 없는 종목(상장 첫해) — 분기 자본이 있으면 PBR(MRQ)만 낸다(곡선·업종 비교 같은 함수)
# ═══════════════════════════════════════════════════════════════════════════════════════════
NEW_Q = {(2026, 1): (-0.3e12, 10.0e12), (2026, 2): (0.9e12, 31.0e12)}


def test_new_listing_without_an_annual_row_gets_pbr_mrq_only():
    pt = S._pit_point(AS_OF, CAP, {}, NEW_Q)
    assert pt == {"asof": AS_OF, "pit_fy": 2025, "pit_q": "2026Q2", "cap_krw": round(CAP),
                  "per_fy0": None, "pbr": None, "per_ttm": None, "pbr_mrq": round(CAP / 31.0e12, 3)}
    assert S._pit_raw(AS_OF, CAP, {}, NEW_Q)["pbr_mrq"] == CAP / 31.0e12, "업종 비교는 반올림 전 값을 받는다"
    # 그 시점 분기(1분기 마감 5/15 뒤 · 반기 마감 8/14 전)는 1분기 자본
    assert S._pit_point("20260731", CAP, {}, NEW_Q)["pbr_mrq"] == round(CAP / 10.0e12, 3)


def test_the_annual_row_still_wins_when_it_exists():
    """연간 행이 있으면 종전 그대로(FY0 배수 포함) — 바뀌는 것은 연간 행이 **없을** 때뿐이다."""
    fin = {2025: (20.0e12, 60.0e12)}
    pt = S._pit_point(AS_OF, CAP, fin, NEW_Q)
    assert pt["per_fy0"] == round(CAP / 20.0e12, 3) and pt["pbr"] == round(CAP / 60.0e12, 3) and pt["pbr_mrq"] == round(CAP / 31.0e12, 3)


@pytest.mark.parametrize("dd,finq", [
    (AS_OF, {(2026, 1): (-0.3e12, 10.0e12)}),                 # 그 시점 분기(2026 2분기) 칸이 비었다 — 앞 분기로 대신하지 않는다
    (AS_OF, {}),                                               # 분기 표에도 없다
    ("20260430", NEW_Q),                                       # 4/1~5/14 의 기준은 직전 사업보고서(연간) — 연간 행이 없으면 낼 것이 없다
    (AS_OF, {(2026, 2): (0.9e12, -1.0)}),                      # 완전자본잠식(260829 — 배수를 내지 않는다)
])
def test_no_point_when_nothing_can_be_computed_without_the_annual_row(dd, finq):
    """연간 행도 분기 배수도 없으면 종전처럼 점을 만들지 않는다 — 시총만 있는 점으로 곡선·응답 상태를 바꾸지 않는다."""
    assert S._pit_raw(dd, CAP, {}, finq) is None and S._pit_point(dd, CAP, {}, finq) is None


def test_new_listing_curve_and_sector_compare_agree(monkeypatch):
    """firm_history 곡선의 그 주 점 = 업종 비교의 기업 PBR(MRQ) — 한 함수(`_pit_raw`)."""
    with monkeypatch.context() as mp:
        p = _run(S, mp, FakeDb("ksic", **{k: v for k, v in CASES["new_listing_no_annual_row"].items() if k != "scheme"}), "ksic")
    c = p["data"]["company"]
    assert c["firm_per_ttm"] is None and c["firm_pbr_mrq"] == round(CAP / 31.0e12, 2)
    assert round(S._pit_raw(AS_OF, CAP, {}, NEW_Q)["pbr_mrq"], 2) == c["firm_pbr_mrq"]


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ⑤ firm_history `history` 의 「주간」 항목 — 최근 16주를 곡선 점으로(감사 5 — 종전 주간 스냅샷 표 모양)
# ═══════════════════════════════════════════════════════════════════════════════════════════
def _weeks(n, start="20260102"):
    from datetime import date, timedelta
    d0 = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    return [((d0 + timedelta(days=7 * i)).strftime("%Y%m%d"), 100.0e12 + i * 1e12) for i in range(n)]


def test_weekly_history_items_are_the_last_16_curve_points_in_the_snapshot_shape():
    fin = {2025: (20.0e12, 60.0e12)}
    finq = {(2025, 1): (4.0e12, 56.0e12), (2025, 2): (9.0e12, 58.0e12), (2025, 3): (14.0e12, 59.0e12),
            (2026, 1): (5.0e12, 63.0e12), (2026, 2): (12.0e12, 66.0e12)}
    px = _weeks(42)
    items = S._weekly_hist_items(px, fin, finq)
    assert [h["asof"] for h in items] == [d for d, _ in px[-16:]]
    last = items[-1]
    raw = S._pit_raw(px[-1][0], px[-1][1], fin, finq)
    assert last == {"period": f"{px[-1][0][:4]}-{px[-1][0][4:6]}-{px[-1][0][6:]}", "asof": px[-1][0], "cap_krw": px[-1][1],
                    "per_fy0": round(raw["per_fy0"], 2), "per_ttm": round(raw["per_ttm"], 2), "pbr": round(raw["pbr_mrq"], 2),
                    "source": "주간"}
    assert isinstance(last["cap_krw"], float), "종전 스냅샷 표의 시총은 실수(원 단위)였다"


def test_weekly_history_items_cap_only_without_financials_and_skip_impaired_weeks():
    px = _weeks(20)
    only_cap = S._weekly_hist_items(px, {}, {})            # 인프라 펀드 꼴 — 재무가 없다: 종전 스냅샷 표는 시총만 담았다
    assert len(only_cap) == 16 and all(h["per_fy0"] is None and h["per_ttm"] is None and h["pbr"] is None for h in only_cap)
    fin = {2025: (20.0e12, 60.0e12)}
    finq = {(2025, 2): (9.0e12, 58.0e12), (2026, 1): (1.0e12, -5.0)}   # 2026 1분기 자본 ≤ 0 — 5/15~8/13 은 잠식 시점
    items = S._weekly_hist_items(_weeks(30, "20260403"), fin, finq)
    assert items and all(not ("20260515" <= h["asof"] < "20260814") for h in items), "잠식 시점 주는 뺀다(260829)"


def test_firm_history_of_a_fund_without_financials_is_ok_with_cap_only_weeks(monkeypatch):
    """재무가 전혀 없는 상장 인프라 펀드(088980 꼴) — 종전 main 은 스냅샷 표의 「주간」 16항목(시총만)으로 정상 응답했다. 스냅샷 표를 없앤 뒤
    한때 「시계열 없음」(no_data)이 됐던 것을 되돌린다. 수집 정상이 아닌 종목은 종전처럼 「주간」 항목이 없다."""
    px = _weeks(20, "20260605")

    def rows_for(fetched):
        def rows(sql, params=()):
            s = " ".join(sql.split())
            if s.startswith("SELECT market, induty, currency, acc_mt, fetched FROM dart_fundamentals"):
                return [("KS", "64992", "KRW", None, fetched)]
            if s.startswith("SELECT MAX(price_dd) FROM krx_weekly"):
                return [(px[-1][0],)]
            if "SELECT price_dd, mktcap FROM krx_weekly WHERE ticker=%s AND price_dd>=%s" in s:
                return list(px)
            if s == "SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s":
                return [(None, None)]
            if "FROM dart_finstat_y" in s or "FROM dart_finstat_q" in s or "FROM krx_stock_flags" in s or "DISTINCT ON" in s:
                return []
            raise AssertionError("모르는 SQL: " + s)
        return rows

    async def resolve(q):
        return {"corp_name": "맥쿼리인프라", "stock_code": "088980", "corp_code": "00000001"}, None
    monkeypatch.setattr(S, "_resolve_listed", resolve)
    monkeypatch.setattr(S, "today_kst", lambda: __import__("datetime").date(2026, 10, 18))
    monkeypatch.setattr(S, "_pg_rows", rows_for("ok"))
    p = asyncio.run(S.build_firm_history_payload("088980", format="json"))
    assert p["status"] == "ok" and p["data"]["series"] == []
    wk = p["data"]["history"]
    assert len(wk) == 16 and all(h["source"] == "주간" and h["per_ttm"] is None and h["cap_krw"] for h in wk)
    assert p["warnings"][0] == f"최신 {px[-1][0]} 기준. 정밀 배수(보통주 주가·배당·경고 포함)는 scope='firm' 사용."
    assert "주간 곡선 미산출 — 주간 시세 저장분 또는 재무(연간·분기) 미수집." in p["warnings"]
    monkeypatch.setattr(S, "_pg_rows", rows_for("nocorp"))
    assert asyncio.run(S.build_firm_history_payload("088980", format="json"))["status"] == "no_data"
