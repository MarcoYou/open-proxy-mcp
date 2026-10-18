"""PR #89(최신 주가·시총 읽기를 일별 시세 저장분으로) 검토에서 미뤄 둔 읽는 쪽 정리 세 가지 — 261018. network 0 · DB 0(전부 monkeypatch).

① 사용자 문장에 저장소 표 이름이 샌다. 260907 정리(`test_tool_timing_and_render_leaks`)가 보는 파일 밖에 남은 자리가 있었다 —
   `price_multiple_data(scope="firm_history")` 의 「시계열 없음 — 과거 시세(krx_weekly)·재무(dart_finstat_y)…」(실측 20261018:
   보관된 최신 거래일 상장 2,762종목 중 연간 재무·주간 스냅샷이 모두 없는 191종목이 이 문장을 받는다) · 「주간 곡선 미산출 — krx_weekly 시세…」 ·
   method 의 「연간 dart_finstat_y」, 그리고 같은 두 도구(price_multiple_data·trading_data)의 시장·산업 빈 스냅샷 안내
   (「opm_val_market 비어있음 — market_val_weekly 배치 미실행」·「krx_cap_agg 비어있음 — krx_cap_agg.py 배치 미실행」) ·
   확정 배당수익률 조회 실패 안내 · explain 의 수정계수 출처(`krx_adj_events`). 이 시험이 PR #89 가 건드린 8개 도구의 모듈 전체를 훑어 재발을 막는다.
② `scope="explain"` 의 주가 인풋 줄이 값을 어디서 받았든 「일별 시세 저장분 서빙」이라고 적었다 — `_market_for` 가 저장분에서 받지 못해 KRX 를
   직접 불러 받은 값도. 이제 `_market_for` 반환의 `source`("db"/"krx_live") → firm `data.price_source` → 인풋 줄 문구.
   라이브 폴백이 성공하는 경우는 셋이다 — 최신 기준일 저장분에 그 종목 행이 아직 없음(신규상장 등) · 저장분을 읽지 못함(DB 장애로 기준일 None) ·
   DB 를 붙이지 않은 서버(공개 저장소 자체 호스팅). 그래서 krx_live 문구는 「저장분에서 받지 못해 KRX 에서 직접 조회」로 셋을 다 덮는다(2차 검토).
   작업 중 같은 블록에서 더: 주가를 못 받으면(저장분·KRX 직접 조회 모두 실패) `None:,` 서식에서 TypeError 가 나 explain 응답 전체가 죽었고,
   없는 값이 「DPS…: None원」·「보통주 None」·「= **None**」으로 나갔다. 실측(20261018 운영 DB 읽기 전용): 한창(005110)은 일별 저장분 마지막 행이
   10/14(종가 11원 — 정리매매 뒤 상장폐지로 보임)이고 10/15·10/16 행이 없다 → 저장분도 KRX 직접 조회도 비어 이 크래시 경로를 탄다
   (로컬 파일럿·KRX 키 없음에서 재현). 같은 날 price_latest 날짜가 기준일보다 이른 5종목이 모두 이런 경우다.
   2차 검토에서 더: DPS 칸이 DART 배당 사항 조회 **실패**도 「없음(무배당 또는 미공시)」이라 적었다 — 실패 사유를 버렸기 때문이다.
   이제 firm `inputs.dps_fetch_failed`(013 = 데이터 없음은 실패가 아님) + 경고 한 줄, explain 은 「미확인 — DART 배당 사항 조회 실패」.
③ 서빙 기준일(`krx_daily` 의 가장 늦은 거래일)을 `screener._krx_latest_dd` 와 `price_multiple_data._krx_daily_latest_dd` 가 같은 SQL 로
   따로 정의했다 → `krx_daily.latest_day` 한 곳. 두 이름은 남아 위임하고(여러 시험이 그 이름을 바꿔 끼운다), 각 모듈의 DB 읽기로 읽는다.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest

from open_proxy_mcp import krx_daily
from open_proxy_mcp.server import mcp
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import screener
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.tools import price_multiple_data as TP

from test_pmd_read_path import run_firm, run_firm_history  # noqa: E402 — 같은 가짜 DB·DART 로 firm/firm_history 를 끝까지 돌린다

ROOT = Path(__file__).resolve().parent.parent / "open_proxy_mcp"


def _tool_text(name: str, args: dict) -> str:
    async def go():
        r = await mcp.call_tool(name, args)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    return asyncio.run(go())


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ① 사용자 문장에 저장소 표 이름이 없다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
#: PR #89 가 건드린 8개 도구(price_multiple_data·trading_data·screener·company·dividend_disclosure·asset_holdings·
#: forward_estimates_data·value_up)의 서비스·렌더 모듈.
EIGHT_TOOL_MODULES = (
    "services/price_multiple_data.py", "services/trading.py", "services/screener.py", "services/universe.py",
    "services/company.py", "services/dividend.py", "services/asset_holdings.py", "services/asset_valuation.py",
    "services/forward_estimates.py", "services/value_up.py",
    "tools/price_multiple_data.py", "tools/trading.py", "tools/screener.py", "tools/company.py",
    "tools/dividend_disclosure.py", "tools/asset_holdings.py", "tools/forward_estimates_data.py", "tools/value_up.py",
)
#: 시세 저장분 표 이름 — 8개 도구 어디서도 사용자 문장에 나가지 않는다(「주간 시세 저장분」·「일별 시세 저장분」으로 쓴다).
PRICE_STORE = ("krx_weekly", "krx_daily", "krx_daily_v", "price_latest")
#: 시세·배수 두 도구에서 이번에 걷어낸 표·배치 이름 — 다시 들어오지 않게 같이 본다.
PRICE_TOOLS = ("services/price_multiple_data.py", "services/trading.py",
               "tools/price_multiple_data.py", "tools/trading.py")
OTHER_STORE = ("dart_finstat_y", "dart_finstat_q", "opm_val_market", "opm_val_firm", "market_val_weekly",
               "krx_cap_agg", "krx_adj_events", "div_yield_hist")
#: 로그·예외는 사용자에게 안 나간다(`scripts/output_vocab_lint.py` 와 같은 기준 — 문자열 앞 4줄 안에 이 표시가 있으면 뺀다).
NOT_USER_FACING = ("logger.", "log.debug", "log.info", "log.warning", "log.error", "print(", "raise ", "logging.")
SQL = re.compile(r"\b(SELECT|INSERT|DELETE|UPDATE)\b")
HANGUL = re.compile(r"[가-힣]")


def _user_sentences(rel: str) -> list[tuple[int, str]]:
    """docstring(도구 설명 = 호출측 AI 채널)·SQL·로그를 뺀, 한글이 든 문자열 리터럴. 백틱 안(인용된 식별자)은 지운 뒤 돌려준다."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree, lines = ast.parse(src), src.split("\n")
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)}
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)) or id(n) in docs:
            continue
        if not HANGUL.search(n.value) or SQL.search(n.value):
            continue
        if any(m in ln for ln in lines[max(0, n.lineno - 4):n.lineno] for m in NOT_USER_FACING):
            continue
        out.append((n.lineno, re.sub(r"`[^`]*`", " ", n.value)))
    return out


def _bare(names: tuple[str, ...], text: str) -> list[str]:
    return [t for t in names if re.search(rf"(?<![A-Za-z_]){t}(?![A-Za-z_])", text)]


@pytest.mark.parametrize("rel", EIGHT_TOOL_MODULES)
def test_user_sentences_do_not_name_storage_tables(rel):
    names = PRICE_STORE + (OTHER_STORE if rel in PRICE_TOOLS else ())
    leaks = [(ln, _bare(names, t), " ".join(t.split())[:100]) for ln, t in _user_sentences(rel) if _bare(names, t)]
    assert not leaks, f"{rel}: 사용자 문장에 저장소 표 이름 — {leaks}"


def test_the_guard_actually_sees_the_strings_it_guards():
    """감시가 헛돌지 않는다 — 고친 문장이 실제로 이 스캐너에 잡히는 자리(사용자 문장)에 있다."""
    pmd = " ".join(t for _, t in _user_sentences("services/price_multiple_data.py"))
    assert "주간 곡선 미산출 — 주간 시세 저장분" in pmd and "시계열 없음 — 과거 시세(주간 시세 저장분)" in pmd
    assert "주간 밸류 스냅샷이 비어 있음" in pmd
    assert "시총 집계 저장분이 비어 있음" in " ".join(t for _, t in _user_sentences("services/trading.py"))
    # 로그 문장은 대상이 아니다(쓰는 쪽 로그는 표 이름을 그대로 쓴다)
    assert not any("KRX 스냅샷 기록" in t for _, t in _user_sentences("services/price_multiple_data.py"))


def test_firm_history_no_data_and_missing_curve_warnings_are_neutral(monkeypatch):
    p, _, _ = run_firm_history(S, monkeypatch.setattr, "fh_no_data")
    assert p["status"] == "no_data"
    assert p["warnings"] == ["시계열 없음 — 과거 시세(주간 시세 저장분)·연간 재무·주간 스냅샷 모두 미수집."]
    p, _, _ = run_firm_history(S, monkeypatch.setattr, "fh_every_other_read_fails")
    assert "주간 곡선 미산출 — 주간 시세 저장분 또는 재무(연간·분기) 미수집." in p["warnings"]
    assert "FY0=직전 확정 FY(연간 재무·PIT)" in p["data"]["method"] and "(분기 재무)" in p["data"]["method"]
    # MCP 로 부른 사용자 화면(md)에도 표 이름이 없다 — 렌더러가 method·warnings 를 그대로 싣는다
    out = _tool_text("price_multiple_data", {"company": "삼성전자", "scope": "firm_history"})
    assert "주간 곡선 미산출 — 주간 시세 저장분" in out and "연간 재무·PIT" in out
    assert not _bare(PRICE_STORE + OTHER_STORE, out), out


def test_market_sector_and_cap_agg_empty_notices_are_neutral(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): [])
    assert asyncio.run(S.build_market_val_payload())["warnings"] == ["주간 밸류 스냅샷이 비어 있음 — 집계 배치 미실행."]
    assert asyncio.run(S.build_sector_val_payload(scheme="ksic"))["warnings"] == ["주간 밸류 스냅샷이 비어 있음 — 집계 배치 미실행."]
    monkeypatch.setattr(T, "_pg_rows", lambda sql, params=(): [])
    for scope in ("market", "sector"):
        out = _tool_text("trading_data", {"scope": scope})
        assert "시총 집계 저장분이 비어 있음 — 집계 배치 미실행." in out and "krx_cap_agg" not in out, out


def test_actual_dividend_yield_read_failure_notice_is_neutral(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): None)          # DB 장애
    _, ruler = asyncio.run(S._div_yield_map("market"))
    assert ruler["actual_error"] == "확정 배당수익률 조회 실패 — 일시 장애 가능, PER·PBR 값은 그대로다."


def test_explain_adjustment_factor_line_and_methodology_name_no_table():
    assert "krx_adj_events" not in TP._METHODOLOGY and "수정계수(거래소 기준가 조정 이력)" in TP._METHODOLOGY
    md = TP._render_explain_firm(_explain_payload(price=70_000, source="db", adj={"current": 0.2, "prior_q": 0.1}))
    line = next(ln for ln in md.splitlines() if "수정계수 보정 적용" in ln)
    assert "krx_adj_events" not in line and "거래소 기준가 리셋 실측 수정계수" in line


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ② explain 의 주가 출처는 실제 출처대로
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def _explain_payload(*, price, source, adj=None) -> dict:
    return {"tool": "price_multiple_data", "status": "ok", "subject": "삼성전자", "warnings": [],
            "data": {"price_krw": price, "price_date": "20261016" if price is not None else None, "price_source": source,
                     "inputs": {"net_income_fy0_krw": 1, "net_income_ttm_krw": 1, "controlling_equity_krw": 1,
                                "eps_ttm_basis": "disclosed_assembled" if adj else None, "eps_adj_factors": adj},
                     "multiples": {}, "data_quality": {}}}


def test_market_for_marks_where_the_price_came_from(monkeypatch):
    S._serving_dd_invalidate()
    monkeypatch.setattr(S, "_krx_daily_latest_dd", lambda: "20261016")
    monkeypatch.setattr(S, "_krx_db_get", lambda dd, t: {"price": 71_000, "date": dd, "common_mktcap": 1, "list_shrs": 2})
    assert asyncio.run(S._market_for("005930")) == {"price": 71_000, "date": "20261016", "common_mktcap": 1, "list_shrs": 2,
                                                   "source": "db"}
    # 저장분에 그 종목 행이 없음 → KRX 직접 조회
    monkeypatch.setattr(S, "_krx_db_get", lambda dd, t: {})

    async def live(dd):
        return {"005930": {"TDD_CLSPRC": "72,000", "MKTCAP": "3", "LIST_SHRS": "4"}}
    monkeypatch.setattr(S, "_krx_market_live", live)
    monkeypatch.setattr(S, "_price_dates", lambda today=None: ["20261016"])
    assert asyncio.run(S._market_for("005930"))["source"] == "krx_live"
    # 저장분을 읽지 못함(DB 장애·DB 없는 서버 → 기준일 None) → 저장분 행 조회 없이 KRX 직접 조회 — 같은 krx_live
    S._serving_dd_invalidate()
    monkeypatch.setattr(S, "_krx_daily_latest_dd", lambda: None)
    monkeypatch.setattr(S, "_krx_db_get", lambda dd, t: pytest.fail("기준일이 없으면 저장분 행을 읽지 않는다"))
    assert asyncio.run(S._market_for("005930"))["source"] == "krx_live"
    # 둘 다 못 받으면 종전처럼 빈 dict(출처도 없다)

    async def nothing(dd):
        return {}
    monkeypatch.setattr(S, "_krx_market_live", nothing)
    assert asyncio.run(S._market_for("005930")) == {}
    S._serving_dd_invalidate()


@pytest.mark.parametrize("source,expected,absent", [
    ("db", "(20261016 종가 — KRX 일별시세, 일별 시세 저장분 서빙)", "직접 조회"),
    ("krx_live", "(20261016 종가 — KRX 일별시세, 저장분에서 받지 못해 KRX 에서 직접 조회)", "저장분 서빙"),
    (None, "(20261016 종가 — KRX 일별시세)", "저장분 서빙"),                     # 출처를 모르면 갈래를 적지 않는다
], ids=["db", "krx_live", "unknown"])
def test_explain_price_line_follows_the_actual_source_through_the_mcp_tool(monkeypatch, source, expected, absent):
    run_firm(S, monkeypatch.setattr, "fm_normal")                                 # 가짜 DB·DART 를 깔아 둔다(같은 가짜를 MCP 경로가 쓴다)

    async def market_for(code):
        mk = {"price": 70000, "date": "20261016", "common_mktcap": 417_000_000_000_000, "list_shrs": 5_969_782_550}
        return {**mk, "source": source} if source else mk
    monkeypatch.setattr(S, "_market_for", market_for)
    js = json.loads(_tool_text("price_multiple_data", {"company": "삼성전자", "format": "json"}))
    assert js["data"]["price_source"] == source and js["data"]["price_date"] == "20261016"
    out = _tool_text("price_multiple_data", {"company": "삼성전자", "scope": "explain"})
    line = next(ln for ln in out.splitlines() if ln.startswith("- 주가:"))
    assert line == f"- 주가: **70,000원** {expected}" and absent not in line


def test_explain_does_not_crash_when_no_price_could_be_read(monkeypatch):
    """저장분·KRX 직접 조회 모두 실패(예: DB 미연결·KRX 키 없는 서버) — firm 은 ok 로 배수를 N/M 으로 내는데,
    explain 은 `None:,` 서식에서 TypeError 로 통째로 죽었다(261018 HEAD 렌더러로 재현). 이제 「미확보」라고 적는다."""
    run_firm(S, monkeypatch.setattr, "fm_normal")

    async def no_price(code):
        return {}
    monkeypatch.setattr(S, "_market_for", no_price)
    out = _tool_text("price_multiple_data", {"company": "삼성전자", "scope": "explain"})
    assert "- 주가: **미확보** (일별 시세 저장분·KRX 직접 조회 모두에서 받지 못함 — 시총 기반 배수는 N/M)" in out
    assert "PER(FY0) = 보통주 시총 ÷ 지배순이익(FY0) → **N/M**" in out
    assert TP._price_input_line(None, None, None).startswith("- 주가: **미확보**")


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ③ 서빙 기준일 정의는 한 곳
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_serving_day_has_one_definition_read_through_each_modules_own_db_reader(monkeypatch):
    seen = []
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): seen.append(("pmd", sql)) or [("20261016",)])
    monkeypatch.setattr(screener, "pg_rows", lambda sql, params=(): seen.append(("screener", sql)) or [("20261016",)])
    assert S._krx_daily_latest_dd() == screener._krx_latest_dd() == "20261016"
    assert seen == [("pmd", krx_daily.LATEST_DAY_SQL), ("screener", krx_daily.LATEST_DAY_SQL)]
    assert krx_daily.LATEST_DAY_SQL == "SELECT max(trd_dd) FROM krx_daily"      # 시험들이 이 SQL 모양으로 가짜 DB 를 맞춘다
    # 빈 표·장애는 둘 다 None — krx_weekly 로 대신하지 않는다
    for ans in ([(None,)], None, []):
        monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(), a=ans: a)
        monkeypatch.setattr(screener, "pg_rows", lambda sql, params=(), a=ans: a)
        assert S._krx_daily_latest_dd() is None and screener._krx_latest_dd() is None
    # 두 함수 본문에 SQL 이 따로 적혀 있지 않다(다시 갈라지지 않게)
    for fn in (S._krx_daily_latest_dd, screener._krx_latest_dd):
        body = inspect.getsource(fn).split('"""')[-1]
        assert "krx_daily.latest_day" in body.replace("_krx_daily.", "krx_daily.") and "SELECT" not in body


def test_explain_never_prints_python_none_for_missing_inputs():
    """같은 explain 블록의 다른 칸 — 값이 없으면 「None」·「None원」이 그대로 나갔다(261018 로컬 파일럿 한창 005110:
    「DPS(보통주 현금배당 — DART alotMatter): None원」). 없는 값은 「미확정」·「없음(무배당 또는 미공시)」으로 적는다."""
    p = _explain_payload(price=None, source=None)
    p["data"]["inputs"] = {"net_income_fy0_krw": 61_110_643_142, "net_income_ttm_krw": None, "controlling_equity_krw": None,
                           "eps_fy0_krw": None, "eps_ttm_basis": "disclosed_assembled", "eps_ttm_krw": None,
                           "shares_common": None, "shares_total": None, "dps_krw": None, "dps_fetch_failed": False}
    md = TP._render_explain_firm(p)
    assert "None" not in md, md
    assert "- 지배순이익: FY0=61,110,643,142원 / TTM=미확정 (일부 미확정)" in md
    assert "보통주 미확정 / 합계(보통+우선) 미확정" in md and "DART alotMatter): 없음(무배당 또는 미공시)" in md
    assert "= **미확정** (부재 시 지배순이익÷보통주 폴백)" in md
    # 값이 있으면 종전과 같은 모양(천 단위 쉼표 + 원)
    p["data"]["inputs"].update(eps_fy0_krw=3_212, eps_ttm_krw=-284, shares_common=20_483_425, shares_total=20_483_425, dps_krw=1_668)
    md = TP._render_explain_firm(p)
    assert "= **3,212원** (부재 시" in md and "= **-284원** — FY0과" in md
    assert "보통주 20,483,425 / 합계(보통+우선) 20,483,425" in md and "DART alotMatter): 1,668원" in md


def test_explain_shows_nm_not_none_when_a_multiple_is_withheld():
    """분모가 양수여도 배수를 내지 않는 경우(완전자본잠식 → None) 계산 줄이 「= **None**」이 아니라 N/M 이다."""
    p = _explain_payload(price=70_000, source="db")
    p["data"]["inputs"].update(common_market_cap_krw=417_000_000_000_000, net_income_fy0_krw=10, net_income_ttm_krw=10,
                               controlling_equity_krw=10)
    p["data"]["multiples"] = {"per_fy0": None, "per_ttm": None, "pbr_mrq": None}
    md = TP._render_explain_firm(p)
    assert "**None**" not in md and "PER(FY0) = 보통주 시총 ÷ 지배순이익(FY0) → **N/M**" in md


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ② 의 2차 — DPS 가 없을 때 「무배당」과 「모름(조회 실패)」을 가른다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_dps_fetch_failed_reads_the_annual_summary_reason_and_013_is_not_a_failure(monkeypatch):
    """`_annual_summary` 의 실패 사유 모양(「alotMatter 조회 실패: {상태}」)에 기대므로 그 모양을 실제 함수로 고정한다."""
    from open_proxy_mcp.dart.client import DartClientError
    from open_proxy_mcp.services import dividend as D

    def client_raising(status):
        class C:
            async def get_dividend_info(self, cc, year, rc):
                raise DartClientError(status, "x")
        return lambda: C()
    for status, failed in (("013", False), ("020", True), ("TRANSPORT_ERROR", True), ("800", True)):
        monkeypatch.setattr(D, "get_dart_client", client_raising(status))
        summary, err = asyncio.run(D._annual_summary("00126380", 2025))
        # 013 은 실패 사유가 아니라 「배당 사항 없음」이라 사유 없이(None) 돌아온다(261018) — 빈 표와 같은 값
        assert summary == {} and err == (None if status == "013" else f"alotMatter 조회 실패: {status}")
        assert S._dps_fetch_failed(err) is failed, status
    assert S._dps_fetch_failed(None) is False and S._dps_fetch_failed({}) is False   # 성공(골든 시험의 가짜는 {} 를 준다)


@pytest.mark.parametrize("summary,err,failed,line,warned", [
    ({"cash_dps": 1444}, None, False, "DART alotMatter): 1,444원", False),
    ({"cash_dps": 0}, None, False, "DART alotMatter): 없음(무배당 또는 미공시)", False),
    ({}, "alotMatter 조회 실패: 013", False, "DART alotMatter): 없음(무배당 또는 미공시)", False),
    ({}, "alotMatter 조회 실패: 020", True, "DART alotMatter): 미확인 — DART 배당 사항 조회 실패(무배당이라는 뜻이 아님)", True),
], ids=["paid", "zero_dps", "no_rows_013", "fetch_failed"])
def test_firm_and_explain_tell_a_failed_dividend_lookup_from_no_dividend(monkeypatch, summary, err, failed, line, warned):
    run_firm(S, monkeypatch.setattr, "fm_normal")

    async def annual_summary(cc, fy):
        return summary, err
    monkeypatch.setattr(S, "_annual_summary", annual_summary)
    js = json.loads(_tool_text("price_multiple_data", {"company": "삼성전자", "format": "json"}))
    assert js["data"]["inputs"]["dps_fetch_failed"] is failed
    assert js["data"]["inputs"]["dps_krw"] == (summary.get("cash_dps") or None)
    notice = "DART 배당 사항 조회 실패 — DPS·배당수익률 미확인(무배당이라는 뜻이 아니다, 일시 장애 가능). PER·PBR 값은 그대로다."
    assert (notice in js["data"]["warnings"]) is warned
    out = _tool_text("price_multiple_data", {"company": "삼성전자", "scope": "explain"})
    dps_line = next(ln for ln in out.splitlines() if ln.startswith("- DPS("))
    assert dps_line.endswith(line), dps_line
    assert (notice in out) is warned                                              # explain 의 「유의(원문 경고)」에도 실린다
    md = _tool_text("price_multiple_data", {"company": "삼성전자"})
    assert (notice in md) is warned                                               # 기본 md 의 「주의」에도


def test_explain_dps_cell_without_the_flag_does_not_assert_no_dividend():
    """`dps_fetch_failed` 칸이 없는 응답(출처 불명)이면 무배당으로도 실패로도 단정하지 않는다."""
    p = _explain_payload(price=70_000, source="db")
    p["data"]["inputs"]["dps_krw"] = None
    md = TP._render_explain_firm(p)
    assert "DART alotMatter): 미확인(무배당·미공시 또는 조회 실패)" in md and "없음(무배당" not in md
