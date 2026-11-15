"""발견 전용 유형과 제목 분류 가드 — 2026-10-12~11-13 코스피·코스닥 공시 11,477건 표본에서 나온 실제 제목."""
import asyncio

import pytest

from open_proxy_mcp.services import risk_events as R
from open_proxy_mcp.services import screener as S


@pytest.mark.parametrize("title,code,subtype", [
    ("주식소각결정", "treasury", "소각"),                       # 제목에 「자기주식」이 없다 — 한 달 112건
    ("[기재정정]주식소각결정", "treasury", "소각"),
    ("주식병합결정", "stock_split", "병합"),
    ("주식분할결정", "stock_split", "분할"),
    ("매출액또는손익구조30%(대규모법인은15%)이상변동", "earnings_confirmed", "손익구조변동"),
    ("신탁계약해지결과보고서", "treasury_result", "신탁해지결과"),
    ("자기주식취득결과보고서", "treasury_result", "취득결과"),      # 결정(`treasury` 취득)으로 분류되던 것 — 결과보고는 E001 에 산다
    ("자기주식처분결과보고서", "treasury_result", "처분결과"),
    ("최대주주등소유주식변동신고서", "major_holder_change", "변동신고"),
    ("특수관계인의유상증자참여", "related_party", "유상증자참여"),
    ("동일인등출자계열회사와의상품ㆍ용역거래변경", "related_party", "내부거래"),
    ("타인에대한채무보증결정", "guarantee_lending", "채무보증"),
    ("신규시설투자등", "asset_deal", "시설투자"),
    ("주요사항보고서(유형자산양수결정)", "asset_deal", "유형자산"),
    ("수시공시의무관련사항(공정공시)", "fair_disclosure", "수시공시"),
    ("연결재무제표기준영업실적등에대한전망(공정공시)", "fair_disclosure", "실적전망"),
    ("결산실적공시예고", "earnings_schedule", "예고"),
    ("전환가액의조정", "capital_followup", "가액조정"),
    ("주요사항보고서(자기전환사채매도결정)", "capital_followup", "사채매도"),
    ("감자완료", "capital_followup", "감자완료"),
    ("합병등종료보고서(합병)", "restructuring_followup", "종료보고"),
    ("독립이사의선임ㆍ해임또는중도퇴임에관한신고", "officer_change", "독립이사"),
    ("주식매수선택권부여에관한신고", "stock_option", "부여"),
    ("기업가치제고계획(자율공시)", "value_up_plan", "계획"),
    ("기타주요경영사항(해외증권시장 주권등 상장 결정 철회)", "withdrawal_notice", "철회"),
    ("철회신고서", "withdrawal_notice", "신고서철회"),                          # 증권신고서 철회 — C001
    ("[정정제출요구]증권신고서(합병)", "merger_registration", "합병"),           # C004 — 머리말 대괄호가 겹쳐도
    ("[정정제출요구][기재정정]증권신고서(합병)", "merger_registration", "합병"),
    ("[기재정정]증권신고서(주식의포괄적교환ㆍ이전)", "merger_registration", "주식교환"),   # 결정 유형(주식교환)에 먼저 걸리면 안 된다
    ("경영권변경등에관한계약체결", "control_contract", "경영권계약"),            # 코스닥 서식 — 한 달 10건이 미분류였다
    ("권리락              (무상증자)", "capital_followup", "권리락"),
    ("지주회사의자회사편입", "holding_subsidiary", "편입"),                      # 261115 다섯 유형 — 한 달 표본의 실제 제목
    ("지주회사의자회사탈퇴", "holding_subsidiary", "탈퇴"),
    ("외국지주회사의자회사편입ㆍ탈퇴", "holding_subsidiary", "편입"),
    ("종속회사등상장에관한찬성여부", "subsidiary_listing", "찬성여부"),
    ("종속회사등상장에대한주주영향평가등", "subsidiary_listing", "주주영향평가"),
    ("[기재정정]해외증권시장주권등상장결정(종속회사의주요경영사항)", "subsidiary_listing", "해외상장"),
    ("부동산투자회사금전배당결정", "reit_dividend", "금전배당"),
    ("[기재정정]감사보고서제출(자회사의 주요경영사항)", "audit_report", "제출"),
    ("감사보고서제출(2024사업연도)", "audit_report", "제출"),
    ("증권신고서(지분증권)", "equity_registration", "신고서"),
    ("[발행조건확정]증권신고서(지분증권)", "equity_registration", "발행조건확정"),
    ("[기재정정]증권신고서(지분증권)", "equity_registration", "신고서"),
    ("기타경영사항(자율공시)", "voluntary_disclosure", "자율공시"),
    ("투자판단 관련 주요경영사항(임상시험계획 변경승인신청)", "investment_judgment", "투자판단"),
    ("주요사항보고서(주식교환ㆍ이전결정)", "restructuring", "주식교환"),
    ("임원ㆍ주요주주특정증권등거래계획보고서", "insider10", "거래계획"),
])
def test_titles_that_were_unclassified_now_have_a_type(title, code, subtype):
    assert S.classify(title)[:2] == (code, subtype)


@pytest.mark.parametrize("title", [
    "정정명령부과(2026.10.30.제출주요사항보고서(회사합병결정))",       # 금감원 처분 — 합병 결정으로 분류됐었다
    "정정신고서제출요구",
    "효력발생안내(2026.10.14.제출증권신고서(주식의포괄적교환ㆍ이전))",
    "정정신고서제출요구(2026.10.28.제출증권신고서(지분증권))",       # 금감원 안내가 인용한 지분증권 신고서 — 신고서로 분류하지 않는다
    "주권매매거래정지 (단일판매ㆍ공급계약체결)",                      # 거래소 조치 — 수주 체결로 분류됐었다
    "주권매매거래정지(자기주식취득결정)",
])
def test_forms_that_only_quote_another_filing_are_not_classified_as_that_filing(title):
    assert S.classify(title)[0] is None


@pytest.mark.parametrize("title,code,subtype", [
    ("주요사항보고서(자기주식취득결정)", "treasury", "취득"),
    ("단일판매ㆍ공급계약체결", "order", "체결"),
    ("주요사항보고서(유상증자결정)", "dilutive", "유상증자"),
    ("기타주요경영사항(제3자배정유상증자결정철회)", "dilutive", "유상증자"),   # 종전대로 — 철회 단계 표지가 붙는다
    # 261115: 「권」 없이 적은 사채 발행결정 철회도 같은 사건이다 — 종전엔 발견 전용(withdrawal_notice)으로만 갔다.
    ("기타주요경영사항(제7회차 무기명식 무보증 사모 전환사채 발행결정 철회)", "dilutive", "CB발행"),
    ("기타주요경영사항(제7회차 전환사채권 발행 결정 철회)", "dilutive", "CB발행"),
    ("[발행조건확정]증권신고서(채무증권)", None, ""),                             # 채무증권 신고서는 그대로 미분류
    ("현금ㆍ현물배당결정", "dividend", "현금배당"),                                # 리츠 금전배당 유형이 일반 배당을 가져가지 않는다
    ("부동산투자회사자금차입", None, ""),
    ("최대주주등소유주식변동신고서(최대주주변경시)", "control_change", "변경"),
    ("투자판단관련주요경영사항(최대주주변경을수반하는주식양수도계약체결)", "control_change", "변경"),
    ("연결재무제표기준영업(잠정)실적(공정공시)", "earnings", "잠정실적"),
])
def test_existing_classification_is_unchanged(title, code, subtype):
    assert S.classify(title)[:2] == (code, subtype)


def test_discovery_types_stay_out_of_default_presets_and_the_ledger():
    core, _ = S._resolve_types("core")
    all_types, _ = S._resolve_types("all")
    discovery, _ = S._resolve_types("discovery")
    assert discovery == S.DISCOVERY_PRESET and len(discovery) == 24
    assert not set(discovery) & (set(core) | set(all_types))
    assert all(S._BY_CODE[c].get("opt_in") and S._BY_CODE[c]["detail_kind"] is None for c in discovery)   # 원장·상세 파서 밖
    assert S._resolve_types(S._nl_types("그 밖의 공시"))[0] == discovery
    assert S._resolve_types(S._nl_types("주식분할"))[0] == ["stock_split"]       # 「분할」(회사분할)에 먼저 걸리지 않는다
    assert S._resolve_types(S._nl_types("분할"))[0] == ["restructuring"]
    assert S._SUGGESTED_TOOL["equity_registration"] == "filing_section"       # 상장 전 회사가 대부분 — 이름으로 찾는 도구로 보내지 않는다
    assert S._resolve_types(S._nl_types("합병증권신고서"))[0] == ["merger_registration"]   # 「증권신고서」(지분증권)에 먼저 걸리지 않는다
    assert S._resolve_types(S._nl_types("증권신고서"))[0] == ["equity_registration"]
    assert S._resolve_types(S._nl_types("리츠배당"))[0] == ["reit_dividend"]              # 「배당」에 먼저 걸리지 않는다
    assert S._resolve_types(S._nl_types("배당"))[0] == ["dividend"]
    assert S._resolve_types(S._nl_types("합병"))[0] == ["restructuring"]


def test_scan_codes_cover_where_the_title_actually_lives():
    assert S.scan_codes_of("treasury") == ("B001", "I001")              # 소각 결정은 I001
    assert set(S.scan_codes_of("treasury_result")) == {"E001", "E002"}
    assert S.scan_codes_of("related_party") == ("J001",)
    # core 디제스트의 스캔 코드는 그대로다 — 소각이 사는 I001 은 이미 훑고 있었다
    assert {c for t in S.CORE_PRESET for c in S.scan_codes_of(t)} == {"I001", "B001", "D001", "I002"}


class _Client:
    def api_call_snapshot(self):
        return 0


def test_single_type_scan_fetches_every_code_of_that_type(monkeypatch):
    seen = []

    async def scan(client, code, bgn, end, pages):
        seen.append(code)
        items = [{"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001", "corp_cls": "Y",
                  "rcept_no": "20260918800133", "rcept_dt": "20260918", "report_nm": "주식소각결정", "flr_nm": "시험회사"}] if code == "I001" else []
        return S._scan_result(items, len(items), 1, 1)

    async def universe(value):
        return S.UniverseFilter(label="전체시장", resolved=True)

    monkeypatch.setattr(S, "_scan_code", scan)
    monkeypatch.setattr(S, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(S, "resolve_universe", universe)
    result = asyncio.run(S.build_screener_payload(types="treasury", period="20260918", universe="all", details=False))
    assert sorted(seen) == ["B001", "I001"]
    assert [(h["type"]["code"], h["subtype"]) for h in result["hits"]] == [("treasury", "소각")]


@pytest.mark.parametrize("title,category", [
    ("불성실공시법인지정예고(공시불이행)", "unfaithful_disclosure"),
    ("불성실공시법인미지정(지정유예)", "unfaithful_disclosure"),
    ("파산신청", "insolvency"),
    ("파산신청기각", "insolvency"),
    ("대출원리금연체사실발생(자율공시)", "insolvency"),
    ("회생계획인가", "rehabilitation"),
    ("기타시장안내(실질심사 대상여부 결정을 위한 조사기간 연장 안내)", "listing_review"),
    ("기타시장안내(기업심사위원회 개최결과 안내)", "listing_review"),
    ("기타시장안내(개선계획서 제출)", "listing_review"),
    ("반기또는분기매출액미달사실발생", "listing_review"),
    ("기타경영사항(자율공시)(2026년 반기보고서 제출 지연)", "listing_review"),
    ("거래처와의거래중단", "production_halt"),
])
def test_risk_titles_that_were_unclassified(title, category):
    assert R._classify(title)[0] == category


@pytest.mark.parametrize("title,category", [
    ("주권매매거래정지해제(파산신청 사유 해소)", "trading_halt"),          # 파산 낱말이 거래정지 분류를 가로채지 않는다
    ("주권매매거래정지(횡령ㆍ배임혐의발생)", "embezzlement"),             # 종전 우선순위 그대로
    ("기업가치제고계획(자율공시)(PBR 개선계획 제출기업)", ""),            # 「개선계획」 낱말에 걸리지 않는다
])
def test_new_risk_keywords_do_not_steal_existing_titles(title, category):
    assert R._classify(title)[0] == category


def test_new_risk_categories_are_on_by_default_and_have_a_reading_guide():
    for cat in ("unfaithful_disclosure", "insolvency"):
        assert cat in R._ACTIVE_CATEGORIES and cat in R._DETAIL_GUIDE
        assert set(R._CATEGORY_CHANNELS[cat]) >= {"I001"}


def test_discovery_card_shows_the_filing_title_because_it_is_the_content():
    from open_proxy_mcp.tools import screener as T

    result = {"mode": "digest", "period": {"label": "x", "bgn_de": "20261112", "end_de": "20261112"},
              "universe": {"label": "전체시장"}, "counts": {}, "paging": {}, "warnings": [], "types": {"selected": ["investment_judgment"]},
              "hits": [{"corp_name": "시험바이오", "stock_code": "000001", "mktcap_won": 1e11, "stage": "-",
                        "type": {"code": "investment_judgment", "label": "투자판단 주요경영사항(임상·허가·기술이전·계약)"},
                        "title": "투자판단 관련 주요경영사항(임상시험계획  변경승인신청)", "rcept_no": "20261112900958", "dart_url": "u",
                        "suggested_tool": "risk_events", "interpretation": "discovery_only", "detail_status": "scan_only"}]}
    assert "공시명: 투자판단 관련 주요경영사항(임상시험계획 변경승인신청)" in T._render_digest(result)
    assert S._resolve_types(S._nl_types("임상"))[0] == ["investment_judgment"]


def test_withdrawals_are_scanned_with_their_decision_type_and_not_counted_as_new():
    from datetime import date
    from open_proxy_mcp.services import disclosure_flow as F
    for code in ("dilutive", "restructuring", "stake_deal"):                    # 철회는 I001 「기타주요경영사항」으로 온다
        assert "I001" in S.scan_codes_of(code)
    assert S._stage_tag("dilutive", "기타주요경영사항(유상증자결정(제3자배정) 철회)", "유상증자") == "철회/취소"
    base = {"kind": "dilutive", "rcept_dt": date(2026, 11, 10), "corp_name": "가", "sector": "IT", "subtype": "유상증자"}
    rows = [{**base, "stage": "결정(예정)"}, {**base, "stage": "철회/취소"}]
    assert [F.is_new(r) for r in rows] == [True, False]
    out = F.aggregate_flow(rows, "dilutive", "sector", (date(2026, 11, 8), date(2026, 11, 14)), (date(2026, 8, 9), date(2026, 11, 7)), 7, 91)
    assert out["total"]["new"] == 1 and out["total"]["withdrawals"] == 1
    # 소송 제목의 「취소」는 다투는 대상이다 — 새 소송으로 센다(원장에 옛 단계로 적힌 행까지)
    suit = "소송등의제기ㆍ신청(경영권분쟁소송)              (주주총회결의 취소의 소)"
    assert S._stage_tag("litigation", suit, "소송") != "철회/취소"
    assert F.is_new({"kind": "litigation", "stage": "철회/취소", "rcept_dt": date(2026, 11, 10)})


def test_withdrawal_notices_keep_only_titles_about_the_topic(monkeypatch):
    from open_proxy_mcp.services import filing_search as FS
    async def fake(**kw):
        assert kw["pblntf_detail_ty"] == "I001" and kw["keywords"] == ("철회",)
        return ([{"rcept_no": "20261110900357", "rcept_dt": "20261110", "report_nm": "기타주요경영사항              (유상증자결정(제3자배정) 철회)"},
                 {"rcept_no": "20261006900379", "rcept_dt": "20261006", "report_nm": "[기재정정]주주총회소집결의 (임시주주총회-철회)"}], [], None)
    monkeypatch.setattr(FS, "search_filings_by_report_name", fake)
    got, warns = asyncio.run(FS.find_withdrawal_notices("00000000", "20260501", "20261114", ("증자", "사채", "감자")))
    assert [g["rcept_no"] for g in got] == ["20261110900357"] and not warns
    assert got[0]["report_nm"] == "기타주요경영사항 (유상증자결정(제3자배정) 철회)"


def test_restructuring_withdrawal_words_do_not_catch_other_withdrawals(monkeypatch):
    from open_proxy_mcp.services import filing_search as FS
    titles = ["기타주요경영사항 (회사 합병결정 철회)", "기타주요경영사항 (회사분할결정 철회)",
              "기타주요경영사항 (제2회차 교환사채 발행결정 철회)", "[기재정정]주식분할결정 (철회)", "기타주요경영사항 (이전상장 추진 철회)"]
    async def fake(**kw):
        return ([{"rcept_no": str(i), "rcept_dt": "20261001", "report_nm": t} for i, t in enumerate(titles)], [], None)
    monkeypatch.setattr(FS, "search_filings_by_report_name", fake)
    words = ("회사합병", "합병결정", "회사분할", "분할합병", "주식교환", "주식의포괄적", "영업양수", "영업양도")
    got, _ = asyncio.run(FS.find_withdrawal_notices("00000000", "20260501", "20261114", words))
    assert [g["rcept_no"] for g in got] == ["0", "1"]


def test_withdrawal_lookup_failure_becomes_a_warning_not_a_crash(monkeypatch):
    from open_proxy_mcp.dart.client import DartClientError
    from open_proxy_mcp.services import filing_search as FS
    async def boom(**kw):
        raise DartClientError("020", "한도")
    monkeypatch.setattr(FS, "search_filings_by_report_name", boom)
    got, warns = asyncio.run(FS.find_withdrawal_notices("00000000", "20260501", "20261114", ("증자",)))
    assert got == [] and "020" in warns[0]
