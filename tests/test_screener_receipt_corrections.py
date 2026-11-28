import asyncio

from open_proxy_mcp.services.governance_patterns import inspect_filing
from open_proxy_mcp.services.screener_allotment import exchange_allotment
from open_proxy_mcp.services.screener_corrections import attachment_amendment, attachment_signals, withdrawal_quote


def table(*rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows) + "</table>"


def test_shareholder_form_does_not_inherit_unused_third_party_heading():
    html = table(["종속회사인", "자회사 A", "의 주요경영사항 신고"],
                 ["5. 증자방식", "주주배정증자"], ["[제3자배정증자의 경우]", "-"],
                 ["22. 기타 투자판단에 참고할 사항", "기존 차입금을 상환하고 후속 청산절차를 추진한다."])
    assert exchange_allotment(html)["issuer"] == "자회사 A"
    signals = inspect_filing("유상증자결정(종속회사의 주요경영사항)", {"text": "증자", "html": html})
    assert [s["pattern"] for s in signals] == ["subsidiary_liquidation"]


def test_subsidiary_recipient_and_shares_are_same_row():
    html = table(["5. 증자방식", "제3자배정증자"]) + table(
        ["제3자배정 대상자", "회사 또는 최대주주와의 관계", "선정경위", "배정주식수(주)"],
        ["A", "최대주주", "경영상 목적", "88"], ["B", "-", "사업제휴", "-"])
    signal, = inspect_filing("유상증자결정", {"text": "증자", "html": html})
    assert signal["subscribers"] == [{"name": "A", "reported_relationship": "최대주주", "shares": "88"},
                                     {"name": "B", "reported_relationship": "-"}]


def test_attachment_identity_cutoff_and_amendment_before_after():
    rc, old, future = "20261104900123", "20261001900111", "20261130900110"
    main = (f'<select id="att"><option value="rcpNo={rc}&amp;dcmNo=111">2026.11.04 [정정] 이사회의사록</option></select>'
            f'<select id="family"><option value="rcpNo={old}">2026.10.01 주총</option>'
            f'<option value="rcpNo={future}">2026.11.30 정정</option></select>')
    class Client:
        node_rc = rc

        async def _fetch_viewer_main_html(self, receipt):
            return main + f'viewDoc("{self.node_rc}", "111", "0", "0", "0", "HTML", "")'

        def _extract_viewer_nodes(self, _):
            return []

        async def _fetch_viewer_section_html(self, node):
            assert node["rcpNo"] == rc and node["dtd"] == "HTML"
            return table(["정정항목", "정정전", "정정후"], ["부여할 주식수", "10주", "9주"])

    client = Client()
    result = asyncio.run(attachment_amendment(client, rc, "20261128"))
    assert result["related_body"]["rcept_no"] == old and "later_body" not in result
    assert result["changes"][0]["after"] == "9주"
    assert attachment_signals(result)[0]["pattern"] == "award_amendment"
    client.node_rc = old
    assert asyncio.run(attachment_amendment(client, rc, "20261128"))["status"] == "unread"


def test_withdrawal_of_issue_in_after_column_only():
    head = ["항 목", "정정사유", "정 정 전", "정 정 후"]
    row = ["전환사채발행결정", "계약철회", "전환사채권 발행결정", "전환사채권 발행결정 철회"]
    assert withdrawal_quote({"html": table(head, row)}) == "전환사채권 발행결정 철회"
    assert not withdrawal_quote({"html": table(head, [row[0], "기재 오류", row[3], row[2]])})


def test_visual_changes_and_unread_attachment_keep_their_source():
    context = {"status": "comparison_read", "reason": "단순 오기", "changes": [{"item":"이사회의사록"}],
               "selected":{"title":"[정정] 이사회의사록", "dart_url":"https://dart.fss.or.kr/"}}
    context["visual_reading"] = {"status":"needs_review"}
    assert "대조 필요" in attachment_signals(context)[0]["label"]
    context["visual_reading"] = {"status":"read","changes":[{"label":"감자 주식 수","before":30,"after":22,"unit":"주"}]}
    signal, = attachment_signals(context)
    assert signal["changes"] == context["visual_reading"]["changes"]
    assert signal["dart_url"] == context["selected"]["dart_url"]


def test_original_review_is_only_requested_for_unresolved_readings(monkeypatch):
    from open_proxy_mcp.services import screener_corrections as corrections

    rc, dcm = "20261104900414", "111"
    source = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}&dcmNo={dcm}"
    args = {"rcept_no":rc, "dcm_no":dcm}
    call = {"tool":"original_reader", "arguments":args}
    for result in ({"status":"read"}, {"status":"version_unverified","next_call":call}):
        async def reader(**kwargs):
            return result
        monkeypatch.setattr(corrections, "_attachment_reader", lambda: reader)
        context = {"selected":{"title":"의사록","dart_url":source}}
        asyncio.run(corrections._attach_reading(None, rc, "", [], {}, "", context, {}))
        if result["status"] == "read":
            assert "original_review" not in context
        else:
            assert context["original_review"] == {"viewer_url":source, "next_call":call}
