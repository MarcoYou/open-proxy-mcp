"""이름 해석 — 활성 회사엔 맞는 이름이 없고 비활성(상장폐지·합병·코넥스 등) 회사 **하나만** 맞으면 그 회사로 확정한다 (261018). network 0.

활성 명부가 켜지기 전 코드(7a6ccfc3)와 main 을 같은 명부·시세로 돌린 비교(재설계 감사 13번): 활성 상장 명부가 실제로 켜진 뒤(#90)
「현대미포」「인프라코어」「동양생명」「라피」「아퓨」가 그 회사(에이치디현대미포·에이치디현대인프라코어·동양생명보험·라피치·아퓨어스)에서
오류(근접 후보만)로 바뀌었다 — 폐지·합병 직후 회사의 배당·주총 이력을 줄임말로 묻는 경우다. 「세이」「비엔」「루트」는 활성 회사가 맞아
이 규칙과 무관하다. 고르되 「상장폐지·코넥스 등」을 응답 맨 앞에 밝히고, company 의 신뢰도는 low 다.
이름 조각 9,000개 스윕에서 이 규칙으로 바뀐 1,052개(전부 「못 찾음」→회사)는 하나도 빠짐없이 7a6ccfc3 이 고르던 그 회사다 — 새로 고르는 회사는 없다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.company_resolver import CompanyResolver, MarketContext, is_inactive_fallback
from open_proxy_mcp.dart.client import _ctx_ledger, corp_registry_miss_kind, new_request_ledger, weak_resolutions
from open_proxy_mcp.services import company as C
from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope, declare_weak_resolution

ACTIVE = [
    {"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG ELECTRONICS CO,.LTD",
     "stock_code": "005930", "modify_date": "20251201"},
    {"corp_code": "s2", "corp_name": "세이브존I&C", "corp_eng_name": "SAVEZONE I&C", "stock_code": "067830",
     "modify_date": "20250101"},
]
#: 최신 거래일 코스피·코스닥 시세에 없는 법인 — 합병 상장폐지·코넥스.
GONE = [
    {"corp_code": "g1", "corp_name": "에이치디현대미포", "corp_eng_name": "HD HYUNDAI MIPO", "stock_code": "010620",
     "modify_date": "20251201"},
    {"corp_code": "g2", "corp_name": "동양생명보험", "corp_eng_name": "TONGYANG LIFE INSURANCE", "stock_code": "082640",
     "modify_date": "20260101"},
    {"corp_code": "g3", "corp_name": "더존비즈온", "corp_eng_name": "DOUZONE BIZON", "stock_code": "012510",
     "modify_date": "20260101"},
    {"corp_code": "g4", "corp_name": "더존디지털웨어", "corp_eng_name": "DUZON DIGITALWARE", "stock_code": "045380",
     "modify_date": "20100101"},
    {"corp_code": "g5", "corp_name": "세이프아시아", "corp_eng_name": "SAFE ASIA", "stock_code": "066330",
     "modify_date": "20260101"},
]
MARKET = MarketContext({"005930": 1_000, "067830": 40}, frozenset({"005930", "067830"}), "20261016", "krx_daily")


def _resolver(market: MarketContext = MARKET) -> CompanyResolver:
    return CompanyResolver([*ACTIVE, *GONE], {}, market)


@pytest.fixture(autouse=True)
def _ledger():
    new_request_ledger()
    yield
    _ctx_ledger.set(None)


@pytest.mark.parametrize("query, name, kind", [
    ("현대미포", "에이치디현대미포", "substring"),     # 이름 가운데 — 부분일치
    ("동양생명", "동양생명보험", "token"),             # 이름 앞부분 — 토큰
    ("Hyundai Mipo", "에이치디현대미포", "token"),     # 영문 토큰
])
def test_one_inactive_match_is_selected_and_flagged(query, name, kind):
    hit = _resolver().search(query)
    assert [h["corp_name"] for h in hit] == [name]
    meta = hit[0]["_resolution"]
    assert meta["match_kind"] == kind and meta["inferred"] and meta["auto_selected"]
    assert meta["inactive_listing"] is True and meta["dominant"] is False
    assert is_inactive_fallback(hit)
    status, selected, _ = C._resolve_match(query, hit)
    assert status == AnalysisStatus.EXACT and selected["corp_name"] == name


def test_the_warning_says_delisted_or_konex_with_the_code_and_the_day():
    hit = _resolver().search("현대미포")
    C._resolve_match("현대미포", hit)
    assert weak_resolutions()[0]["inactive"] == {"ticker": "010620", "as_of": "20261016"}
    warnings = ToolEnvelope(tool="t", status=AnalysisStatus.EXACT, warnings=["기존"]).to_dict()["warnings"]
    line = warnings[0]
    assert line.startswith("「현대미포」를 **에이치디현대미포**(으)로 추정했습니다")
    assert "이 회사(종목코드 010620)는 최신 거래일(20261016) 코스피·코스닥 시세에 없는 **상장폐지·코넥스 등** 비활성 회사" in line
    assert line.endswith("다른 회사를 뜻했다면 종목코드나 정식명으로 다시 물어보세요.")
    assert warnings[-1] == "기존"
    assert declare_weak_resolution({"warnings": []})["warnings"] == [line]     # dict 를 직접 만드는 서비스도 같은 문장


def test_an_ordinary_inferred_pick_keeps_its_old_sentence():
    hit = _resolver().search("세이")                     # 활성 세이브존I&C 가 맞는다 — 이 규칙과 무관
    assert [h["corp_name"] for h in hit] == ["세이브존I&C"] and not is_inactive_fallback(hit)
    C._resolve_match("세이", hit)
    assert "inactive" not in weak_resolutions()[0]
    line = ToolEnvelope(tool="t", status=AnalysisStatus.EXACT).to_dict()["warnings"][0]
    assert line == ("「세이」를 **세이브존I&C**(으)로 추정했습니다 — 이름이 정확히 일치하지 않습니다. "
                    "다른 회사를 뜻했다면 종목코드나 정식명으로 다시 물어보세요.")


def test_two_inactive_matches_stay_unresolved():
    assert _resolver().search("더존") == []               # 더존비즈온·더존디지털웨어 — 고르지 않는다(근접 후보만)


def test_a_typo_is_not_rescued_by_the_inactive_rule():
    assert _resolver().search("에이치디현대미포오") == []  # 오타 교정(fuzzy)으로만 닿는 비활성 회사는 고르지 않는다


def test_exact_names_and_identifiers_are_untouched():
    for q, kind in (("에이치디현대미포", "official"), ("010620", "ticker")):
        hit = _resolver().search(q)
        assert hit and hit[0]["corp_name"] == "에이치디현대미포"
        assert hit[0]["_resolution"]["match_kind"] == kind and not hit[0]["_resolution"]["inferred"]
        assert "inactive_listing" not in hit[0]["_resolution"]


def test_without_an_active_registry_the_old_path_already_answers_and_no_flag_is_set():
    market = MarketContext({"005930": 1_000}, None, "", "local_popularity_prior")   # 시세 단면 없음 — 활성 명부 꺼짐
    hit = _resolver(market).search("현대미포")
    assert hit and hit[0]["corp_name"] == "에이치디현대미포"
    assert "inactive_listing" not in hit[0]["_resolution"]


def test_an_inactive_pick_still_triggers_the_stale_registry_refresh():
    """명부가 낡아 신규상장사를 모르면 이름이 겹치는 옛 법인이 그 자리를 차지한다 — 못 찾음과 같이 보고 새로 받아 본다."""
    hit = _resolver().search("현대미포")
    assert corp_registry_miss_kind("현대미포", hit, [*ACTIVE, *GONE]) == "name"
    assert corp_registry_miss_kind("세이", _resolver().search("세이"), [*ACTIVE, *GONE]) is None


class _FakeClient:
    def __init__(self, resolver: CompanyResolver):
        self.r = resolver

    def api_call_snapshot(self):
        return 0

    async def lookup_corp_code_all(self, q):
        return self.r.search(q)

    async def periodic_filers(self):
        return frozenset({"00126380", "s2"})     # 폐지·합병 법인은 정기보고서 명부에 없다 — 종목코드가 남아 있어 통과해야 한다

    async def suggest_corp_candidates(self, q, limit=5):
        return self.r.suggest(q, limit)

    async def get_company_info(self, corp_code):
        corp = next(c for c in [*ACTIVE, *GONE] if c["corp_code"] == corp_code)
        return {"corp_name": corp["corp_name"], "corp_name_eng": corp["corp_eng_name"], "corp_cls": "E",
                "jurir_no": "1", "induty_code": "301", "acc_mt": "12"}

    async def search_filings(self, **_kw):
        return {"list": []}


def test_company_tool_reports_the_inactive_pick_with_low_confidence(monkeypatch):
    monkeypatch.setattr(C, "get_dart_client", lambda: _FakeClient(_resolver()))
    p = asyncio.run(C.build_company_payload("현대미포"))
    assert p["status"] == "exact" and p["data"]["canonical_name"] == "에이치디현대미포"
    res = p["data"]["company_resolution"]
    assert res["listing_status"] == "inactive" and res["confidence"] == "low" and res["match_type"] == "inferred"
    assert res["reason"].startswith("활성 상장사에는 후보가 없고, 부분 회사명과 일치하는 비활성(상장폐지·합병·코넥스 등) 회사가 하나뿐")
    assert "상장폐지·코넥스 등" in p["warnings"][0]
    # 다른 도구가 타는 관문도 같은 회사로 확정한다
    monkeypatch.setattr(C, "get_dart_client", lambda: _FakeClient(_resolver()))
    r = asyncio.run(C.resolve_company_query("동양생명"))
    assert r.status == AnalysisStatus.EXACT and r.selected["corp_name"] == "동양생명보험"


def test_company_tool_still_errors_with_candidates_when_two_inactive_match(monkeypatch):
    monkeypatch.setattr(C, "get_dart_client", lambda: _FakeClient(_resolver()))
    p = asyncio.run(C.build_company_payload("더존"))
    assert p["status"] == "error"
    assert [c["corp_name"] for c in p["data"]["candidates"]][:2] == ["더존비즈온", "더존디지털웨어"]


# ── 이름 목록(유니버스)·보유지분 — 비활성 회사로 확정된 이름이 조용히 사라지거나 엉뚱한 후보로 달리지 않는다 ──

class _Res:
    def __init__(self, hit):
        self.selected = hit[0] if hit else None
        self.status = AnalysisStatus.EXACT if hit else AnalysisStatus.ERROR
        self.candidates = []


def test_a_name_list_keeps_the_inactive_pick_but_says_so(monkeypatch):
    """261018 회귀(main 대비): 「삼성전자, 현대미포」 유니버스에서 현대미포가 확정되면 종목코드는 들어가지만 최신 거래일 시세가 없어
    시세·시총 표(trading_data·forward_estimates_data)에서 행 없이 사라졌다 — main 은 「미해결」로 밝혔는데 아무 말이 없었다."""
    from open_proxy_mcp.services import screener as S

    async def _q(tok, *a, **k):
        return _Res(_resolver().search(tok))
    monkeypatch.setattr(S, "resolve_company_query", _q)
    uf = asyncio.run(S._resolve_custom_universe("삼성전자, 현대미포", "20261016"))
    assert uf.resolved and uf.allowed == {"005930", "010620"}
    assert uf.notice == ("비활성 회사로 식별: '현대미포'→에이치디현대미포(010620)는 최신 거래일(20261016) 코스피·코스닥 시세에 없는 "
                         "상장폐지·코넥스 등 비활성 회사 — 시세·시총을 쓰는 표에는 행이 없다")
    # 못 찾은 이름이 함께 있으면 종전 「미해결」 안내 뒤에 붙인다
    uf = asyncio.run(S._resolve_custom_universe("삼성전자, 현대미포, 더존", "20261016"))
    assert uf.notice.startswith("해결된 2종목으로 진행 — 미해결: '더존'") and " / 비활성 회사로 식별: '현대미포'→" in uf.notice
    # 활성 회사만이면 안내는 종전 그대로(빈 문자열)
    assert asyncio.run(S._resolve_custom_universe("삼성전자, 세이", "20261016")).notice == ""


def test_a_held_stake_name_does_not_borrow_an_inactive_lookalike():
    """261018 회귀(main 대비, 현대차 asset_holdings): 해외 상장 보유지분 「HMI」에 이름 조각이 겹치는 비활성 법인이 「추정 후보」로 달렸다 —
    비활성 회사는 최신 시세가 없어 평가할 수 없으니 못 찾은 것과 같다(main 의 사유 그대로)."""
    from open_proxy_mcp.services import asset_holdings as A

    class _Client:
        async def lookup_corp_code_all(self, name):
            return _resolver().search(name)
    codes, why = asyncio.run(A._stake_codes(_Client(), "현대미포"))
    assert codes == [] and why == "국내 상장 종목코드를 찾지 못함(해외 상장·상장폐지이거나 공식 상호와 표기가 다름)"
    codes, why = asyncio.run(A._stake_codes(_Client(), "삼성전자"))
    assert codes == ["005930"] and why is None


# ── 도구 경계: 응답을 직접 만드는 도구도 「추정·비활성 회사」를 맨 앞에 밝힌다 ──────────────────────────────

def _boundary(name: str, result: str) -> str:
    from open_proxy_mcp.tools import _wrap_tool_errors

    async def tool_fn(**_kw):
        return result
    tool_fn.__name__ = name
    return asyncio.run(_wrap_tool_errors(tool_fn)())


def _inactive_line() -> str:
    C._resolve_match("현대미포", _resolver().search("현대미포"))
    return ToolEnvelope(tool="t", status=AnalysisStatus.EXACT).to_dict()["warnings"][0]


def test_tool_boundary_adds_the_guess_notice_to_replies_built_without_the_envelope():
    """261018 검토: `ToolEnvelope`·`declare_weak_resolution` 을 거치지 않고 dict·md 를 직접 만드는 경로(`dividend_data` ·
    `forward_estimates_data` · `price_multiple_data` 의 firm_history·sector · `trading_data` universe)는 「현대미포」를 상장폐지된
    에이치디현대미포로 고르고도 「추정했다」·「비활성 회사」를 한 줄도 싣지 않았다. 경계가 빠진 고지를 채운다 — JSON 은 warnings 맨 앞, md 는 제목 아래."""
    import json

    line = _inactive_line()
    assert "상장폐지·코넥스 등" in line
    md = _boundary("dividend_data", "## 에이치디현대미포 (010620) — 확정 배당\n\n| 연도 | DPS |")
    assert md.startswith("## 에이치디현대미포 (010620) — 확정 배당\n\n> ⚠ " + line) and md.count(line) == 1
    js = json.loads(_boundary("dividend_data", json.dumps({"status": "ok", "subject": "에이치디현대미포", "data": {}},
                                                          ensure_ascii=False)))
    assert js["warnings"] == [line]                                               # warnings 칸이 없던 응답
    fwd = json.loads(_boundary("forward_estimates_data", json.dumps({"warnings": ["기존"]}, ensure_ascii=False)))
    assert fwd["warnings"] == [line, "기존"]
    # 종류주 고지를 스스로 싣는 trading_data 도 추정 고지는 경계가 채운다(universe 경로엔 없었다)
    trd = json.loads(_boundary("trading_data", json.dumps({"tool": "trading_data", "warnings": []}, ensure_ascii=False)))
    assert trd["warnings"] == [line]


def test_tool_boundary_leaves_replies_that_already_carry_the_notice_byte_identical():
    import json

    from open_proxy_mcp.services.contracts import as_pretty_json

    line = _inactive_line()
    declared = as_pretty_json(ToolEnvelope(tool="t", status=AnalysisStatus.EXACT, warnings=["기존"]).to_dict())
    assert _boundary("dividend_disclosure", declared) == declared                  # 봉투가 이미 실었다(따옴표 이스케이프 포함)
    md = "# trading_data: 에이치디현대미포\n\n- " + line + "\n- 20261016 에 010620 시세가 없습니다"
    assert _boundary("trading_data", md) == md                                     # declare_inferred 가 이미 실었다
    prefixed = json.dumps({"warnings": ["[notice] " + line]}, ensure_ascii=False)
    assert _boundary("director_board", prefixed) == prefixed                       # 감싸는 도구가 접두어를 붙여 옮긴 사본
    _ctx_ledger.set(None)
    new_request_ledger()
    C._resolve_match("에이치디현대미포", _resolver().search("에이치디현대미포"))     # 정식명 — 추정 아님, 장부 빔
    assert _boundary("dividend_data", "## 에이치디현대미포") == "## 에이치디현대미포"


def test_dividend_data_firm_tells_the_guess_and_the_inactive_listing(monkeypatch):
    """실제 `dividend_data` 도구를 경계째로 — 「현대미포」 배당 이력이 「추정·비활성」 고지와 함께 나간다(md·json)."""
    import json

    from open_proxy_mcp.services import dividend_data as dd
    from open_proxy_mcp.services import price_multiple_data as P
    from open_proxy_mcp.tools import _wrap_tool_errors
    from open_proxy_mcp.tools import dividend_data as dd_tool

    async def _resolve_listed(q):
        _status, selected, _ = C._resolve_match(q, _resolver().search(q))
        return selected, None
    monkeypatch.setattr(P, "_resolve_listed", _resolve_listed)
    monkeypatch.setattr(dd, "year_range", lambda: (2020, 2025))
    monkeypatch.setattr(dd, "firm_history", lambda *_a: {"status": "ok", "annual_failed": False, "quarterly_failed": False,
                                                          "empty_kind_rows_folded": 0, "annual": [], "quarterly": []})
    monkeypatch.setattr(dd, "payment_counts", lambda *_a: {"status": "ok", "complete_years": [2020, 2021, 2022, 2023, 2024],
                                                           "rows": [], "decisions": []})

    class _Reg:
        tools: dict = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.tools[fn.__name__] = _wrap_tool_errors(fn)
                return fn
            return deco

    reg = _Reg()
    dd_tool.register_tools(reg)
    fn = reg.tools["dividend_data"]
    md = asyncio.run(fn(company="현대미포", scope="firm"))
    head, _, rest = md.partition("\n")
    assert head.startswith("## 에이치디현대미포 (010620)")
    assert rest.lstrip().startswith("> ⚠ 「현대미포」를 **에이치디현대미포**(으)로 추정했습니다")
    assert "**상장폐지·코넥스 등** 비활성 회사" in rest
    _ctx_ledger.set(None)
    new_request_ledger()
    js = json.loads(asyncio.run(fn(company="현대미포", scope="firm", format="json")))
    assert js["subject"] == "에이치디현대미포" and js["warnings"][0].startswith("「현대미포」를 **에이치디현대미포**(으)로 추정했습니다")
