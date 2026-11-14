"""Neutral market discovery before source-based governance assessment."""
import asyncio

import pytest

from open_proxy_mcp.services import screener as S


@pytest.mark.parametrize("title,code,subtype", [
    ("공개매수신고서", "tender_offer", "신고서"),
    ("[기재정정]공개매수신고서", "tender_offer", "신고서"),
    ("공개매수설명서", "tender_offer", "설명서"),
    ("공개매수결과보고서", "tender_offer", "결과"),
    ("공개매수철회신고서", "tender_offer", "철회"),
    ("공개매수에관한의견표명서", "tender_offer", "의견표명"),
    ("의결권대리행사권유참고서류", "proxy_solicitation", "권유서류"),
    ("의결권 대리행사 권유에 관한 의견표명서", "proxy_solicitation", "의견표명"),
    ("위임장권유참고서류", "proxy_solicitation", "권유서류"),
])
def test_new_discovery_types_and_distinct_filing_roles(title, code, subtype):
    classified = S.classify(title)
    assert classified[:2] == (code, subtype)
    assert classified[2] == title.startswith("[기재정정]")


@pytest.mark.parametrize("phrase,codes", [
    ("공개매수", ["tender_offer"]),
    ("위임장", ["proxy_solicitation"]),
    ("공개매수, 위임장", ["tender_offer", "proxy_solicitation"]),
    ("의결권대리행사권유", ["proxy_solicitation"]),
    ("거버넌스", S.GOVERNANCE_PRESET),
    ("지배구조", S.GOVERNANCE_PRESET),
    ("governance", S.GOVERNANCE_PRESET),
])
def test_aliases_route_to_explicit_governance_discovery(phrase, codes):
    selected, warnings = S._resolve_types(S._nl_types(phrase))
    assert selected == codes
    assert warnings == []


def test_default_core_is_unchanged_and_all_contains_new_explicit_types():
    core, _ = S._resolve_types("core")
    assert core == ["order", "treasury", "dividend", "dilutive", "agm_notice", "ownership5", "earnings"]
    assert {S._BY_CODE[code]["scan_code"] for code in core} == {"I001", "B001", "D001", "I002"}
    all_types, _ = S._resolve_types("all")
    # 261114: 발견 전용 유형은 `all` 밖(`discovery` 프리셋)이다 — 스캔 코드가 늘어 코드당 페이지 몫이 줄지 않게.
    assert set(all_types) | set(S.DISCOVERY_PRESET) == set(S._BY_CODE)
    assert not set(all_types) & set(S.DISCOVERY_PRESET)
    assert {"tender_offer", "proxy_solicitation"} <= set(all_types)
    assert len(S.GOVERNANCE_PRESET) == 8


class Client:
    def api_call_snapshot(self):
        return 0


def filing(receipt, title, filer):
    return {"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001",
            "corp_cls": "Y", "rcept_no": receipt, "rcept_dt": "20260909",
            "report_nm": title, "flr_nm": filer}


def run_stubbed(monkeypatch, rows):
    async def scan(client, code, bgn, end, pages):
        assert code in {"D003", "D004", "B001", "I001", "D001"}
        items = rows.get(code, [])
        return S._scan_result(items, len(items), 1, 1)

    async def universe(value):
        return S.UniverseFilter(label="전체시장", resolved=True)

    monkeypatch.setattr(S, "_scan_code", scan)
    monkeypatch.setattr(S, "get_dart_client", lambda: Client())
    monkeypatch.setattr(S, "resolve_universe", universe)
    return asyncio.run(S.build_screener_payload(
        types="governance", period="20260909", universe="all", details=False))


def test_competing_filers_remain_distinct_and_carry_a_neutral_workflow_handoff(monkeypatch):
    result = run_stubbed(monkeypatch, {"D003": [
        filing("20260909000001", "의결권대리행사권유참고서류", "시험회사"),
        filing("20260909000002", "의결권대리행사권유참고서류", "주주제안자"),
        filing("20260909000003", "[기재정정]의결권대리행사권유참고서류", "시험회사"),
    ]})
    assert result["counts"]["matched"] == 2
    assert {h["flr_nm"] for h in result["hits"]} == {"시험회사", "주주제안자"}
    company_hit = next(h for h in result["hits"] if h["flr_nm"] == "시험회사")
    assert company_hit["supersedes_rcept_no"] == "20260909000001"
    for hit in result["hits"]:
        assert hit["interpretation"] == "discovery_only"
        assert hit["signal_basis"] == "filing_title"
        assert hit["suggested_tool"] == "governance_screen"
        assert "decision" not in hit and "risk_score" not in hit
    assert result["types"]["interpretation"] == "discovery_only"
    assert "최대 30개씩" in result["types"]["next_step"]
    assert result["counts"]["deduped_away"] == 1
    assert {entry["code"] for entry in result["coverage"]} == {"D003", "D004", "B001", "I001", "D001"}
    assert all(entry["complete"] for entry in result["coverage"])


def test_unknown_filers_do_not_establish_supersession(monkeypatch):
    result = run_stubbed(monkeypatch, {"D004": [
        filing("20260909000001", "공개매수신고서", ""),
        filing("20260909000002", "공개매수신고서", ""),
    ]})
    assert len(result["hits"]) == 2
    assert all("supersedes_rcept_no" not in hit for hit in result["hits"])


def test_ambiguous_correction_keeps_separate_offers_and_excludes_other_filers(monkeypatch):
    result = run_stubbed(monkeypatch, {"D004": [
        filing("20260909000001", "공개매수신고서", "매수자A"),
        filing("20260909000002", "공개매수신고서", "매수자B"),
        filing("20260909000003", "공개매수신고서", "매수자A"),
        filing("20260909000004", "[기재정정]공개매수신고서", "매수자A"),
    ]})
    assert result["counts"]["matched"] == 4
    assert result["counts"]["deduped_away"] == 0
    correction = next(hit for hit in result["hits"] if hit["is_correction"])
    assert correction["ambiguous_correction"] is True
    assert set(correction["correction_candidates"]) == {"20260909000001", "20260909000003"}
    assert "supersedes_rcept_no" not in correction


def test_tender_offer_cards_point_to_proxy_contest_and_show_who_filed(monkeypatch):
    from open_proxy_mcp.tools import screener as T

    result = run_stubbed(monkeypatch, {"D004": [
        filing("20260909000010", "공개매수신고서", "알파홀딩스"),
        filing("20260909000011", "공개매수에관한의견표명서", "시험회사"),
    ]})
    tender = [h for h in result["hits"] if h["type"]["code"] == "tender_offer"]
    assert len(tender) == 2
    assert {h["suggested_tool"] for h in tender} == {"proxy_contest"}
    text = T._render_digest(result)
    assert "제출인 알파홀딩스" in text and "제출인 시험회사" in text and "`proxy_contest`" in text


def _tender_card(rcept, subtype, filer, corp="00000001", correction=False):
    return {"rcept_no": rcept, "corp_code": corp, "flr_nm": filer, "subtype": subtype, "title": f"공개매수{subtype}",
            "is_correction": correction, "type": {"code": "tender_offer"}}


def test_tender_cards_get_offeror_type_from_one_filing_per_company_and_filer(monkeypatch):
    from pathlib import Path

    html = (Path(__file__).parent / "dart_responses" / "tender_offer" / "offer_filing_20240913000040.html").read_text(encoding="utf-8")
    opened = []

    class Doc:
        async def get_document_cached(self, rcept_no):
            opened.append(rcept_no)
            return {"html": html}

    monkeypatch.setattr(S, "get_dart_client", lambda: Doc())
    page = [
        _tender_card("20260909000003", "설명서", "알파홀딩스"),
        _tender_card("20260909000002", "신고서", "알파홀딩스"),
        _tender_card("20260910000004", "신고서", "알파홀딩스", correction=True),
        _tender_card("20260915000009", "결과", "알파홀딩스"),
        _tender_card("20260912000001", "의견표명", "시험회사"),  # 제출인이 대상회사
    ]
    stats = asyncio.run(S._enrich_tender_offerors(page))
    assert opened == ["20260909000002"]  # 쌍당 원 신고서 한 건만(읽히면 거기서 멈춘다)
    assert stats["parsed"] == 1 and stats["lookups"] == 1
    for card in page:
        assert [o["name"] for o in card["offeror"]["offerors"]] == ["주식회사 한국기업투자홀딩스", "주식회사 영풍"]
    assert page[-1]["offeror"].get("inferred") is True and "inferred" not in page[0]["offeror"]


def test_ambiguous_opinion_card_gets_no_offeror_when_company_has_two_offerors(monkeypatch):
    from pathlib import Path

    html = (Path(__file__).parent / "dart_responses" / "tender_offer" / "offer_filing_20240913000040.html").read_text(encoding="utf-8")

    class Doc:
        async def get_document_cached(self, rcept_no):
            return {"html": html}

    monkeypatch.setattr(S, "get_dart_client", lambda: Doc())
    page = [_tender_card("20260909000002", "신고서", "알파"), _tender_card("20260909000012", "신고서", "베타"),
            _tender_card("20260912000001", "의견표명", "시험회사")]
    asyncio.run(S._enrich_tender_offerors(page))
    assert "offeror" in page[0] and "offeror" in page[1] and "offeror" not in page[2]


def test_offeror_line_is_rendered_on_the_card():
    from open_proxy_mcp.tools import screener as T

    result = {"mode": "digest", "period": {"label": "x", "bgn_de": "20260101", "end_de": "20260131"},
              "universe": {"label": "전체시장"}, "counts": {}, "paging": {}, "warnings": [], "types": {"selected": ["tender_offer"]},
              "hits": [{"corp_name": "시험회사", "stock_code": "000001", "mktcap_won": 1e11, "stage": "신고서(접수)", "type": {"code": "tender_offer", "label": "공개매수"},
                        "flr_nm": "알파", "title": "공개매수신고서", "rcept_no": "20260105000001", "dart_url": "u", "suggested_tool": "proxy_contest",
                        "offeror": {"offerors": [{"name": "알파 주식회사", "type": "회사", "relation_to_target": "대상회사의 최대주주 또는 임원"}], "purpose": ["상장폐지"]}}]}
    text = T._render_digest(result)
    assert "공개매수자: 알파 주식회사(회사) · 대상회사의 최대주주 또는 임원 · 목적 상장폐지" in text


def test_pair_without_original_filing_falls_back_to_next_candidate(monkeypatch):
    from pathlib import Path

    named = (Path(__file__).parent / "dart_responses" / "tender_offer" / "result_20241017000004.html").read_text(encoding="utf-8")
    bare = "<TABLE><TR><TD>항 목</TD><TD>정정사유</TD></TR></TABLE>"
    opened = []

    class Doc:
        async def get_document_cached(self, rcept_no):
            opened.append(rcept_no)
            return {"html": bare if rcept_no == "20260910000004" else named}

    monkeypatch.setattr(S, "get_dart_client", lambda: Doc())
    page = [_tender_card("20260910000004", "신고서", "알파", correction=True),   # 정정본: 이름 칸이 없다
            _tender_card("20260915000009", "결과", "알파")]                      # 결과보고서: 표지에 이름이 있다
    stats = asyncio.run(S._enrich_tender_offerors(page))
    assert opened == ["20260910000004", "20260915000009"]  # 정정본 → 결과보고서 순으로 시도
    assert stats["not_found"] == 1 and stats["parsed"] == 1
    assert [o["name"] for o in page[0]["offeror"]["offerors"]] == ["주식회사 한국기업투자홀딩스", "주식회사 영풍"]
    assert page[1]["offeror"]["from_rcept_no"] == "20260915000009"
