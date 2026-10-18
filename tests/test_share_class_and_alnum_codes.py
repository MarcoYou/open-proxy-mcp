"""종목코드 모양 두 가지 — 영문이 섞인 신형 코드와 우선주·종류주 코드 (261018).

① 신형 코드(0126Z0 삼성에피스홀딩스 · 0001A0 …). 해석기가 숫자 6자리만 코드로 읽어 원장에 있는 상장사 64곳이 코드로 안 찾혔다 —
   `company` 는 「후보 0개」 오류를, 그것도 영어로(코드의 영문 글자를 영어 질의로 읽었다) 돌려줬다. 같은 코드를 유니버스 나열로 주면
   찾았다(그쪽은 코드를 따로 읽는다). 이제 모든 도구가 같은 해석기로 받고, 코드만 넣으면 응답은 한국어(기본)다.
② 종류주 코드(005935 삼성전자우 · 00088K 한화3우B). 회사 원장에는 보통주 코드만 있어 「못 찾음」이었는데, `trading_data` 설명은
   「우선주는 그 우선주 코드로 직접」을 약속했다. 이제 발행 회사(보통주와 같은 corp_code)로 식별하되 —
     · 시세 도구(`trading_data` firm·quote)는 **그 종류주 자체**의 종가·시총·상장주식수를,
     · 회사 단위 도구(공시·재무·배수)는 발행 회사의 값을 준다. 배수는 정의대로 보통주 시총 기준이다(price_multiple_data 위키).
   어느 코드를 어느 회사로 읽었는지는 응답 맨 앞 경고로 밝힌다(추정 고지와 같은 장부·같은 경로).
   관측된 적 없는 코드(오타 005931)는 규칙만으로 회사에 붙이지 않는다.
③ 덤 — `trading_data` 만 이름 추정 고지를 버려 「삼성전자우」가 보통주 시세로 경고 없이 바뀌었다. 이제 다른 도구와 같은 문장을 싣는다.
④ 응답을 직접 만드는 도구(`forward_estimates_data`·`dividend_data`·`price_multiple_data` 의 firm_history 등)는 종류주 고지를 싣지 않아
   005935 를 삼성전자 값으로 고지 없이 줬다 — 모든 도구가 지나는 경계(`tools._wrap_tool_errors`)가 빠진 고지를 채운다.

앞부분은 network 0 · DB 0. 마지막 시험만 로컬 Docker `postgres:17`(`tests/conftest.py` 의 `pg17`)이 필요하다 — 없으면 건너뛴다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp import company_resolver as CR
from open_proxy_mcp.company_resolver import (
    CompanyResolver,
    MarketContext,
    issuer_ticker,
    krx_code,
    share_class_ticker,
)
from open_proxy_mcp.dart.client import (
    _ctx_ledger,
    new_request_ledger,
    note_share_class_resolution,
    share_class_resolutions,
)
from open_proxy_mcp.services import company as C
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope, declare_weak_resolution
from open_proxy_mcp.tools.company import _english_response, _render_exact

CORPS = [
    {"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG ELECTRONICS CO,.LTD",
     "stock_code": "005930", "modify_date": "20251201"},
    {"corp_code": "01965324", "corp_name": "삼성에피스홀딩스", "corp_eng_name": "SAMSUNG EPIS HOLDINGS Co., Ltd.",
     "stock_code": "0126Z0", "modify_date": "20260108"},
    {"corp_code": "00160588", "corp_name": "한화", "corp_eng_name": "Hanwha Corporation",
     "stock_code": "000880", "modify_date": "20250101"},
    {"corp_code": "00148993", "corp_name": "삼양홀딩스", "corp_eng_name": "SAMYANG HOLDINGS",
     "stock_code": "000070", "modify_date": "20250101"},
    {"corp_code": "00999999", "corp_name": "에이비1234", "corp_eng_name": "AB1234 Co",
     "stock_code": "777770", "modify_date": "20250101"},
]

#: 최신 거래일 단면 — 보통주와 종류주(005935·00088K)가 함께 있다. 000075(삼양홀딩스우)는 2025-08 상장폐지라 단면에 없고 관측표에만 있다.
MARKET = MarketContext(
    market_caps={"005930": 1_259_873, "005935": 134_156, "0126Z0": 8_248, "000880": 4_000, "00088K": 483,
                 "000070": 900, "777770": 10},
    active_tickers=frozenset({"005930", "005935", "0126Z0", "000880", "00088K", "000070", "777770"}),
    as_of_date="20261016",
    source="krx_daily",
    share_class_tickers=frozenset({"000075", "005935", "00088K"}),
)


def _resolver(market: MarketContext = MARKET) -> CompanyResolver:
    return CompanyResolver(CORPS, {}, market)


@pytest.fixture(autouse=True)
def _ledger():
    new_request_ledger()
    yield
    _ctx_ledger.set(None)


# ── 규칙 ──────────────────────────────────────────────────────────────────────
def test_code_shape_and_issuer_rule():
    assert krx_code("0126Z0") == krx_code(" 0126z0 ") == "0126Z0"            # 소문자·공백도 코드
    assert krx_code("005930") == "005930"
    assert krx_code("삼성전자") == krx_code("SAMSNG") == krx_code("0126Z") == ""   # 이름·짧은 것은 코드가 아니다
    assert issuer_ticker("005935") == "005930" and issuer_ticker("005387") == "005380"
    assert issuer_ticker("00680K") == "006800" and issuer_ticker("33626L") == "336260"
    assert issuer_ticker("005930") == issuer_ticker("0126Z0") == ""          # 끝자리 0 = 보통주


# ── ① 신형 영숫자 코드 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("query", ["0126Z0", "0126z0"])
def test_alphanumeric_ticker_resolves_like_a_numeric_one(query):
    hit = _resolver().search(query)
    assert [h["stock_code"] for h in hit] == ["0126Z0"]
    meta = hit[0]["_resolution"]
    assert meta["match_kind"] == "ticker" and meta["inferred"] is False and "share_class" not in meta


def test_code_shaped_text_that_is_no_code_still_goes_through_name_search():
    """코드 모양인데 원장·관측 종목에 없으면 종전처럼 이름 검색으로 내려간다(숫자 6자리는 종전대로 코드로만)."""
    hit = _resolver().search("AB1234")                        # 영문명 「AB1234 Co」의 토큰
    assert hit and hit[0]["stock_code"] == "777770" and hit[0]["_resolution"]["match_kind"] != "ticker"
    assert _resolver().search("999999") == []                 # 없는 숫자 코드는 종전대로 빈 결과


def test_numeric_ticker_and_names_are_unchanged():
    r = _resolver()
    assert r.search("005930")[0]["_resolution"]["match_kind"] == "ticker"
    assert "share_class" not in r.search("005930")[0]["_resolution"]
    assert r.search("삼성전자")[0]["stock_code"] == "005930"


def test_code_only_query_answers_in_korean():
    assert C._prefers_english("0126Z0") is False and C._prefers_english("0126z0") is False
    assert C._prefers_english("Samsung Electronics") is True               # 영문 이름은 종전대로 영어
    assert C._prefers_english("0126Z0", "en") is True                      # 명시 언어가 이긴다
    assert _english_response({"data": {"query": "0126Z0"}}) is False


# ── ② 종류주 코드 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code, issuer, name", [
    ("005935", "005930", "삼성전자"),     # 숫자 우선주
    ("00088K", "000880", "한화"),         # 영숫자 종류주
    ("000075", "000070", "삼양홀딩스"),   # 상장폐지 종류주 — 최신 단면엔 없고 관측표(krx_listing)에만
])
def test_share_class_code_resolves_to_the_issuer(code, issuer, name):
    hit = _resolver().search(code)
    assert [h["corp_name"] for h in hit] == [name] and hit[0]["stock_code"] == issuer
    meta = hit[0]["_resolution"]
    assert meta["match_kind"] == "share_class" and meta["inferred"] is False
    assert meta["share_class"] == {"ticker": code, "issuer_ticker": issuer}
    assert share_class_ticker(hit[0]) == code


def test_unobserved_code_is_not_attached_by_rule_alone():
    """005931 은 규칙상 삼성전자 자리지만 시세 저장분에서 관측된 적이 없다 — 회사에 붙이지 않는다(오타일 수 있다)."""
    assert _resolver().search("005931") == []
    bare = MarketContext({"005930": 1}, None, "20261016", "krx_daily")      # 관측표·단면 모두 005935 없음
    assert _resolver(bare).search("005935") == []


def test_share_class_is_an_exact_identifier_match_and_is_declared():
    hit = _resolver().search("005935")
    status, selected, _ = C._resolve_match("005935", hit)
    assert status == AnalysisStatus.EXACT and selected["stock_code"] == "005930"
    assert share_class_resolutions() == [{"ticker": "005935", "issuer_ticker": "005930", "corp_name": "삼성전자"}]
    warnings = ToolEnvelope(tool="t", status=AnalysisStatus.EXACT, warnings=["기존"]).to_dict()["warnings"]
    assert warnings[0].startswith("「005935」는 **삼성전자**(보통주 005930)의 종류주")
    assert "보통주 시총" in warnings[0] and 'trading_data(company="005935")' in warnings[0]
    assert warnings[-1] == "기존"
    # dict 를 직접 만드는 서비스(price_multiple_data firm 등)도 같은 문장을 받는다
    assert declare_weak_resolution({"warnings": []})["warnings"] == warnings[:1]


def test_plain_ticker_declares_nothing_and_the_ledger_is_bounded():
    C._resolve_match("005930", _resolver().search("005930"))
    assert share_class_resolutions() == []
    for _ in range(3):
        note_share_class_resolution(_resolver().search("005935")[0])
    assert len(share_class_resolutions()) == 1                             # 같은 코드는 한 번만
    _ctx_ledger.set(None)
    note_share_class_resolution(_resolver().search("005935")[0])            # 장부 없는 스크립트·테스트 — 조용히 통과
    assert share_class_resolutions() == []


class _FakeClient:
    """`build_company_payload` 가 부르는 것만 — 해석은 진짜 `CompanyResolver`."""

    def __init__(self, resolver: CompanyResolver):
        self.r = resolver

    def api_call_snapshot(self):
        return 0

    async def lookup_corp_code_all(self, q):
        return self.r.search(q)

    async def periodic_filers(self):
        return frozenset({"00126380"})          # 0126Z0·한화는 명부에 없다 — 코드 조회는 명부 필터를 타지 않아야 한다

    async def suggest_corp_candidates(self, q, limit=5):
        return self.r.suggest(q, limit)

    async def get_company_info(self, corp_code):
        corp = next(c for c in CORPS if c["corp_code"] == corp_code)
        return {"corp_name": corp["corp_name"], "corp_name_eng": corp["corp_eng_name"], "corp_cls": "Y",
                "jurir_no": "1", "induty_code": "264", "acc_mt": "12"}

    async def search_filings(self, **_kw):
        return {"list": []}


def _company(monkeypatch, query: str, **kw) -> dict:
    monkeypatch.setattr(C, "get_dart_client", lambda: _FakeClient(_resolver()))
    return asyncio.run(C.build_company_payload(query, **kw))


def test_company_tool_finds_the_alphanumeric_ticker_in_korean(monkeypatch):
    p = _company(monkeypatch, "0126Z0")
    assert p["status"] == "exact" and p["data"]["identifiers"]["ticker"] == "0126Z0"
    assert p["data"]["company_resolution"]["response_language"] == "ko"
    assert "share_class_ticker" not in p["data"]["identifiers"]


def test_company_tool_names_the_issuer_for_a_share_class_code(monkeypatch):
    p = _company(monkeypatch, "00088K")
    d = p["data"]
    assert p["status"] == "exact" and d["canonical_name"] == "한화"
    assert d["identifiers"]["ticker"] == "000880" and d["identifiers"]["share_class_ticker"] == "00088K"
    assert d["company_resolution"]["matched_on"] == "share_class"
    assert d["company_resolution"]["share_class"] == {"ticker": "00088K", "issuer_ticker": "000880"}
    assert p["warnings"][0].startswith("「00088K」는 **한화**(보통주 000880)")
    md = _render_exact({**p, "data": {**d, "display_language": "ko"}})
    assert "| 종류주 코드(요청) | `00088K` |" in md and "| ticker | `000880` |" in md


# ── 시세 도구: 종류주 자체의 값 ──────────────────────────────────────────────
SH = 802_371_203


def _w(dd, close):
    return (dd, "KS", close, close * SH, SH)


def _firm(monkeypatch, corp: dict):
    calls = []

    async def fake_resolve(_q):
        return corp, None

    async def fake_bounds():
        return "20261009", "20231009"

    def fake_rows(sql, params=()):
        s = " ".join(sql.split())
        calls.append((s, params))
        if "FROM krx_weekly" in s:
            return [_w("20261002", 160_000), _w("20261009", 165_000)]
        if "FROM price_latest" in s:
            return [_w("20261016", 167_200)]
        if "FROM krx_adj_events" in s:
            return []
        raise AssertionError(s)

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    return asyncio.run(T.build_firm_series_payload("005935", format="json")), calls


def test_trading_firm_reads_the_share_class_own_curve(monkeypatch):
    corp = _resolver().search("005935")[0]
    p, calls = _firm(monkeypatch, corp)
    assert p["status"] == "ok" and p["subject"] == "삼성전자 종류주(005935)"
    assert {params[0] for _s, params in calls} == {"005935"}                # 주간·끝점·조정 이벤트 모두 그 종류주 코드로 읽는다
    d = p["data"]
    assert d["ticker"] == "005935" and d["issuer_ticker"] == "005930" and d["latest"]["close_krw"] == 167_200
    assert p["warnings"][0].startswith("005935 는 삼성전자(보통주 005930)의 종류주")


def test_trading_firm_for_a_common_ticker_is_unchanged(monkeypatch):
    corp = _resolver().search("005930")[0]
    p, calls = _firm(monkeypatch, corp)
    assert p["subject"] == "삼성전자(005930)" and "issuer_ticker" not in p["data"]
    assert {params[0] for _s, params in calls} == {"005930"}
    assert p["warnings"][0].startswith("**수정주가 아님**")


def test_trading_quote_reads_the_share_class_row(monkeypatch):
    corp = _resolver().search("00088K")[0]
    seen = []

    async def fake_resolve(_q):
        return corp, None

    def fake_read(ticker, dd):
        seen.append(ticker)
        row = {"trd_dd": "20261016", "ticker": ticker, "market": "KS", "open": 32_000, "high": 33_000, "low": 31_900,
               "close": 32_950, "volume": 10, "value": 329_500, "list_shrs": 14_676_613, "mktcap": 483_594_398_350,
               "prev_dd": "20261015", "prev_close": 32_800, "chg": 150, "chg_pct": 0.46}
        return type("L", (), {"ok": True, "day_present": True, "day": "20261016", "row": row})()

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "read_quote", fake_read)
    monkeypatch.setattr(T, "_change_is_reliable", lambda dd, row: True)
    p = asyncio.run(T.build_quote_payload("00088K", as_of="20261016"))
    assert seen == ["00088K"]
    assert p["status"] == "ok" and p["subject"] == "한화 종류주(00088K) 20261016 시세"
    assert p["data"]["ticker"] == "00088K" and p["data"]["issuer_ticker"] == "000880"
    assert p["data"]["quote"]["close_krw"] == 32_950
    assert p["warnings"][0].startswith("00088K 는 한화(보통주 000880)의 종류주")


# ── ③ trading_data 의 이름 추정 고지 ─────────────────────────────────────────
def test_trading_declares_an_inferred_name_like_the_other_tools():
    hit = [{"corp_name": "삼성전자", "corp_code": "00126380", "stock_code": "005930",
            "_resolution": {"match_kind": "fuzzy", "inferred": True, "auto_selected": True, "candidate_count": 1}}]
    C._resolve_match("삼성전자우", hit)
    p = T.declare_inferred({"status": "ok", "warnings": ["**수정주가 아님** …"]})
    assert p["warnings"][0].startswith("「삼성전자우」를 **삼성전자**(으)로 추정했습니다")
    assert p["warnings"][-1] == "**수정주가 아님** …"
    # 종류주 코드 고지는 시세 도구에 싣지 않는다 — 그 종류주 자체의 값을 주며 따로 적는다
    C._resolve_match("005935", _resolver().search("005935"))
    assert not any("종류주(우선주 등) 종목코드라" in w for w in T.declare_inferred({"warnings": []})["warnings"])


def test_trading_declares_nothing_for_exact_lookups():
    C._resolve_match("005930", _resolver().search("005930"))
    assert T.declare_inferred({"warnings": ["x"]})["warnings"] == ["x"]


class _Registry:
    """FastMCP 대역 — `@mcp.tool()` 계약만 흉내내 tool 함수를 붙잡는다(도구 본문의 배선까지 프로덕션 경로로 탄다)."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.mark.parametrize("scope, builder", [("firm", "build_firm_series_payload"), ("quote", "build_quote_payload")])
def test_trading_tool_wires_the_inferred_notice(monkeypatch, scope, builder):
    """도우미(`declare_inferred`)만이 아니라 도구가 firm·quote 결과에 실제로 거는지 — 배선이 빠지면 여기서 깨진다."""
    import json

    from open_proxy_mcp.tools import trading as trading_tool

    async def fake_build(company, **_kw):
        C._resolve_match(company, [{"corp_name": "삼성전자", "corp_code": "00126380", "stock_code": "005930",
                                    "_resolution": {"match_kind": "fuzzy", "inferred": True, "auto_selected": True,
                                                    "candidate_count": 1}}])
        return {"tool": "trading_data", "status": "ok", "subject": "삼성전자(005930)", "warnings": ["**수정주가 아님** …"]}

    monkeypatch.setattr(trading_tool, builder, fake_build)
    reg = _Registry()
    trading_tool.register_tools(reg)
    out = json.loads(asyncio.run(reg.tools["trading_data"](company="삼성전자우", scope=scope, format="json")))
    assert out["warnings"][0].startswith("「삼성전자우」를 **삼성전자**(으)로 추정했습니다")
    assert out["warnings"][-1] == "**수정주가 아님** …"


def test_company_tool_answers_a_code_shaped_miss_in_korean(monkeypatch):
    """도구 쪽 언어 판정도 서비스와 같다 — 못 찾은 코드 모양 질의(「0126Z1」)의 표시 언어가 영어로 돌지 않는다."""
    import json

    from open_proxy_mcp.tools import company as company_tool

    async def fake_payload(query, **_kw):
        return {"tool": "company", "status": "error", "subject": query, "warnings": [], "data": {"candidates": []}}

    monkeypatch.setattr(company_tool, "build_company_payload", fake_payload)
    reg = _Registry()
    company_tool.register_tools(reg)
    out = json.loads(asyncio.run(reg.tools["company"](query="0126Z1", format="json")))
    assert out["data"]["display_language"] == "ko"


# ── 도구 경계의 안전망: 고지가 빠진 응답 ───────────────────────────────────────
_NOTICE_HEAD = "「005935」는 **삼성전자**(보통주 005930)의 종류주"


def _boundary(name: str, result: str) -> str:
    from open_proxy_mcp.tools import _wrap_tool_errors

    async def tool_fn(**_kw):
        return result
    tool_fn.__name__ = name
    return asyncio.run(_wrap_tool_errors(tool_fn)())


def test_tool_boundary_adds_a_missing_share_class_notice_once():
    """`ToolEnvelope`·`declare_weak_resolution` 을 거치지 않는 응답(forward_estimates_data·dividend_data·
    price_multiple_data 의 firm_history 등)도 종류주 고지를 싣는다 — JSON 은 warnings 맨 앞, md 는 제목 바로 아래."""
    import json

    C._resolve_match("005935", _resolver().search("005935"))
    raw = json.dumps({"tool": "forward_estimates_data", "status": "ok", "warnings": ["기존"]}, ensure_ascii=False, indent=2)
    out = json.loads(_boundary("forward_estimates_data", raw))
    assert out["warnings"][0].startswith(_NOTICE_HEAD) and out["warnings"][1:] == ["기존"]
    no_key = json.loads(_boundary("dividend_data", json.dumps({"status": "ok", "data": {}})))
    assert no_key["warnings"][0].startswith(_NOTICE_HEAD)                      # warnings 칸이 없던 응답
    md = _boundary("price_multiple_data", "# 삼성전자 밸류에이션 히스토리\n\n| 월 | PER |")
    assert md.startswith("# 삼성전자 밸류에이션 히스토리\n\n> ⚠ " + _NOTICE_HEAD)
    assert md.endswith("\n\n| 월 | PER |") and md.count(_NOTICE_HEAD) == 1
    odd = '{"warnings": {"k": "v"}}'                                               # 붙일 자리가 없는 JSON 은 깨뜨리지 않는다
    assert _boundary("dividend_data", odd) == odd


def test_tool_boundary_leaves_declared_and_unrelated_responses_byte_identical():
    import json

    C._resolve_match("005935", _resolver().search("005935"))
    declared = ToolEnvelope(tool="t", status=AnalysisStatus.EXACT, warnings=["기존"]).to_dict()
    raw = json.dumps(declared, ensure_ascii=False, indent=2)                    # 이미 실린 JSON(따옴표 이스케이프 포함)
    assert _boundary("financial_metrics", raw) == raw
    md = "# 제목\n\n## 주의\n- " + declared["warnings"][0]
    assert _boundary("price_multiple_data", md) == md                           # 이미 실린 md
    trading = '{"warnings": ["005935 는 삼성전자(보통주 005930)의 종류주 …"]}'
    assert _boundary("trading_data", trading) == trading                       # 그 종류주 자체의 값을 주는 도구는 제외
    _ctx_ledger.set(None)
    new_request_ledger()
    C._resolve_match("005930", _resolver().search("005930"))                    # 종류주로 묻지 않은 요청 — 그대로
    assert _boundary("forward_estimates_data", '{"warnings": []}') == '{"warnings": []}'
    assert _boundary("dividend_data", "# 삼성전자") == "# 삼성전자"


# ── 시장 맥락: 종류주 관측표 읽기 (Postgres 17) ──────────────────────────────
def test_market_context_reads_observed_share_classes_and_survives_a_missing_table(pg17, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from test_krx_daily_pg import _apply, put_daily

    _apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        put_daily(c, [("20261016", "005930", "KS", 215500, 10), ("20261016", "005935", "KS", 167200, 5)])
    ctx = CR._database_market_context()                       # 관측표가 아직 없다(마이그레이션 전) — 맥락은 그대로 읽힌다
    assert ctx is not None and ctx.market_caps == {"005930": 2_155_000, "005935": 836_000}
    assert ctx.share_class_tickers == frozenset()
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE krx_listing (ticker text PRIMARY KEY, market text, first_seen_dd text NOT NULL, "
                  "last_seen_dd text NOT NULL, before_window boolean NOT NULL, window_lo text NOT NULL)")
        c.execute("INSERT INTO krx_listing VALUES ('005930','KS','20151230','20261016',true,'20151230'), "
                  "('005935','KS','20151230','20261016',true,'20151230'), ('000075','KS','20151230','20250814',true,'20151230')")
    ctx = CR._database_market_context()
    assert ctx.share_class_tickers == frozenset({"005935", "000075"})     # 끝자리 0(보통주)은 빼고, 상장폐지 종류주는 든다
    corps = [{"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "modify_date": "1"},
             {"corp_code": "00148993", "corp_name": "삼양홀딩스", "stock_code": "000070", "modify_date": "1"}]
    r = CompanyResolver(corps, {}, ctx)
    assert r.search("000075")[0]["corp_name"] == "삼양홀딩스" and r.search("005935")[0]["corp_name"] == "삼성전자"
