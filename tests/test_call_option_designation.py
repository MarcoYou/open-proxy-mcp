"""CB 매수선택권(콜옵션) 행사자 지정·양도 — 희석 후속 공시로 분류하고 대상자 부표를 읽는다. network 0.

회사가 CB 를 되살 권리를 남에게 넘기는 공시다. 받는 사람이 최대주주·특수관계인이면 **사채 발행 없이
지분을 늘리는 길**이 된다. 위쪽 칸에는 이름이 없고, 이름은 원문 끝 부표 둘에 있다. 둘째 부표
「대상자 중 회사의 최대주주 또는 그의 특수관계인이 포함된 경우」는 해당될 때만 붙는다 — 표가 있다는 것
자체가 신호다.

고정 원문: 대성하이텍 129920 `20260617000491` 주요사항보고서(전환사채매수선택권행사자지정)의 표 칸을
그대로 옮긴 것(매도청구권 조건 줄글은 최대주주 한도 문단만 남겼다 — 부표가 없어도 본문에 「최대주주」
낱말이 나오는 함정을 같이 시험한다).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from open_proxy_mcp.services import dilution_followup as F
from open_proxy_mcp.services.dilutive_issuance import _followup_headline
from open_proxy_mcp.tools.dilutive_issuance import _render_followup_card

HTML = (Path(__file__).parent / "dart_responses" / "dart"
        / "call_option_designation_20260617000491.html").read_text(encoding="utf-8")

#: 같은 공시에서 둘째 부표만 뺀 판 — 대상자에 최대주주·특수관계인이 없을 때의 서식 모양.
_CUT = HTML.index("<P>【대상자 중 회사의 최대주주")
HTML_WITHOUT_LARGEST = HTML[:_CUT] + "</body></html>"


def test_title_is_classified_as_followup() -> None:
    assert F.classify_followup("주요사항보고서(전환사채매수선택권행사자지정)") == "call_option_designation"
    assert F.classify_followup("[기재정정]주요사항보고서(전환사채매수선택권행사자지정)") == "call_option_designation"
    assert "B001" in F._CHANNELS
    assert any("매수선택권" in kw for kw in F._ALL_KEYWORDS)


def test_top_fields_are_read() -> None:
    d = F.parse_call_option_designation(HTML)
    assert d["series"] == "6" and d["bond_kind"] == "무기명식 무보증 사모 전환사채"
    assert d["issued_on"] == "2025-04-18" and d["maturity_on"] == "2030-04-18"
    assert d["designation_kind"] == "매수선택권 양도"
    assert d["designated_face_won"] == 1_150_000_000
    assert d["face_total_won"] == 15_000_000_000
    assert d["designated_ratio_pct"] == 7.67
    assert d["decided_on"] == "2026-06-17"
    assert d["consideration_won"] == 345_000_000
    assert d["consideration_due_on"] == "2027-03-17"
    assert d["conversion_price_won"] == 3_789
    assert d["conversion_shares"] == 303_509 and d["conversion_shares_ratio_pct"] == 1.69
    # 산정근거 안의 `47.2%` 점을 칸 번호로 읽어 근거가 잘리지 않는다.
    assert "이항옵션" in d["consideration_basis"] and "67.4%" in d["consideration_basis"]


def test_designee_table_names_who_receives_the_option() -> None:
    d = F.parse_call_option_designation(HTML)
    names = [x["name"] for x in d["designees"]]
    assert len(names) == 4 and "최호형" in names and "민정기" in names
    choi = next(x for x in d["designees"] if x["name"] == "최호형")
    assert choi == {"name": "최호형", "relation": "최대주주 본인",
                    "face_won": 650_000_000, "consideration_won": 195_000_000}
    min_ = next(x for x in d["designees"] if x["name"] == "민정기")
    assert "relation" not in min_                       # 원문 `-` 는 관계 없음으로


def test_largest_holder_subtable_presence_is_the_signal() -> None:
    d = F.parse_call_option_designation(HTML)
    assert d["largest_holder_included"] is True
    assert d["largest_holder_designees"] == [{
        "name": "최호형", "relation": "최대주주 본인",
        "held_shares_at_issue": 2_540_205, "held_pct_at_issue": "18.52",
        "conversion_shares": 171_549, "conversion_pct": "4.33",
    }]
    assert "【대상자 중 회사의 최대주주" in d["designee_tables_text"]   # 원문 대목도 함께


def test_without_the_subtable_it_says_so_even_if_body_mentions_largest_holder() -> None:
    d = F.parse_call_option_designation(HTML_WITHOUT_LARGEST)
    assert d["largest_holder_included"] is False        # 본문 「최대주주등」 문단에 끌려가지 않는다
    assert "largest_holder_designees" not in d
    assert "부표가 원문에 없다" in d["largest_holder_note"]
    assert d["designated_face_won"] == 1_150_000_000


def test_headline_and_card_surface_the_largest_holder() -> None:
    row = {"type": "call_option_designation", "label": "매수선택권 행사자 지정·양도",
           "direction": "잠재 지분 이전", "report_nm": "주요사항보고서(전환사채매수선택권행사자지정)",
           "rcept_no": "20260617000491", "rcept_dt": "20260617",
           "details": F.parse_call_option_designation(HTML)}
    head = _followup_headline(row)
    assert "권면 1,150,000,000원 (발행총액의 7.67%)" in head
    assert "최호형(최대주주 본인) 보유 18.52% · 전환 시 +4.33%" in head
    card = "\n".join(_render_followup_card(row))
    assert "CB 매수선택권" in card and "잠재 지분 이전" in card
    assert "최대주주·특수관계인이 포함된 경우」 부표가 있다" in card and "2,540,205주(18.52%)" in card
    row["details"] = F.parse_call_option_designation(HTML_WITHOUT_LARGEST)
    assert "최대주주·특수관계인 부표 없음" in _followup_headline(row)


def test_fetch_hands_raw_html_to_the_call_option_parser(monkeypatch) -> None:
    async def search(**kw):
        return ([{"report_nm": "주요사항보고서(전환사채매수선택권행사자지정)",
                  "rcept_no": "20260617000491", "rcept_dt": "20260617"}], [], None)

    class _Client:
        async def get_document(self, rcept_no):
            return {"html": HTML}

    monkeypatch.setattr(F, "search_filings_by_report_name", search)
    monkeypatch.setattr(F, "get_dart_client", lambda: _Client())
    rows, warnings, _ = asyncio.run(F.fetch_dilution_followup("00577335", "20260101", "20260630"))
    assert rows[0]["type"] == "call_option_designation" and rows[0]["direction"] == "잠재 지분 이전"
    assert rows[0]["details"]["largest_holder_designees"][0]["name"] == "최호형"
