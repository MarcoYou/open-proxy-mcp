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
    assert set(all_types) == set(S._BY_CODE)
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
        return rows.get(code, []), len(rows.get(code, [])), False, None

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


def test_unknown_filers_do_not_establish_supersession(monkeypatch):
    result = run_stubbed(monkeypatch, {"D004": [
        filing("20260909000001", "공개매수신고서", ""),
        filing("20260909000002", "공개매수신고서", ""),
    ]})
    assert len(result["hits"]) == 2
    assert all("supersedes_rcept_no" not in hit for hit in result["hits"])
