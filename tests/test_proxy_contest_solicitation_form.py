import asyncio

from open_proxy_mcp.services import proxy_contest as pc


FORM = ('<TABLE><TR><TE ACODE="REC_NM">(주)대표홀딩스</TE><TE ACODE="REC_COP_RL">본 인</TE></TR>'
        '<TR><TE ACODE="GMS_OBJ">□ 이사의선임</TE></TR>'
        '<TR><TE ACODE="REC_NM">(주)대표홀딩스</TE><TE ACODE="REC_COP_RL">본인</TE><TE ACODE="REC_STK">1</TE>'
        '<TE ACODE="REC_STK_RT">3.10</TE><TE ACODE="REC_ETC">자기주식</TE></TR>'
        '<TR><TE ACODE="AFF_NM">갑</TE><TE ACODE="REC_COP_RL">특수관계인</TE></TR></TABLE>')


def test_parse_solicitation_form_splits_head_and_stake_rows():
    got = pc.parse_solicitation_form(FORM)
    assert got == {"solicitor": "(주)대표홀딩스", "relation_to_company": "본인", "agenda_objects": ["이사의선임"],
                   "solicitor_stake_pct": "3.10", "solicitor_stake_note": "자기주식"}
    assert pc.parse_solicitation_form("<p>본문만</p>") == {}


def test_form_relation_overrides_name_based_side(monkeypatch):
    # 제출인 이름(대표홀딩스)이 회사명(대표)과 달라 주주 쪽으로 잡혔지만 서식은 「본인」이다.
    class Fake:
        async def get_document_cached(self, rcept_no):
            return {"html": FORM} if rcept_no == "R1" else {"html": FORM.replace("본 인", "최대주주")}
    monkeypatch.setattr(pc, "get_dart_client", lambda: Fake())
    rows = [{"rcept_no": "R1", "report_name": "의결권대리행사권유참고서류", "side": "shareholder"},
            {"rcept_no": "R2", "report_name": "의결권대리행사권유참고서류", "side": "shareholder"},
            {"rcept_no": "R3", "report_name": "주식등의대량보유상황보고서", "side": "shareholder"}]
    asyncio.run(pc._apply_solicitation_forms(rows))
    assert rows[0]["side"] == "company" and rows[0]["side_basis"] == "form_relation"
    assert rows[1]["side"] == "shareholder" and rows[1]["stated_relation"] == "최대주주"
    assert "solicitation_form" not in rows[2]
