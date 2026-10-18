"""정정 비교표 공용 도우미 — 회사가 붙인 「정정항목 · 정정전 · 정정후」 표를 칸 글 그대로 옮긴다.

실제 원문 둘(잠정실적 정정, `tests/dart_responses/dart/`)과 서식별 최소 표로 본다. 네트워크 0.
"""
import asyncio
from pathlib import Path

import pytest

from open_proxy_mcp.services import amendment_table as A
from open_proxy_mcp.services import screener as S
from open_proxy_mcp.tools import screener as T

_FIX = Path(__file__).parent / "dart_responses" / "dart"


def _table(*rows: str) -> str:
    return "<TABLE>" + "".join(rows) + "</TABLE>"


def _tr(*cells: str, tag: str = "TD") -> str:
    return "<TR>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</TR>"


# ── 칸이 비었는가 ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "-", "- -", "－", "해당사항 없음", "해당없음"])
def test_blank_forms_are_blank(value):
    assert A._is_blank(value)


@pytest.mark.parametrize("value", ["0", "0원", "호주", "2026-07-03", "13.54", "- 매출액"])
def test_values_are_not_blank(value):
    assert not A._is_blank(value)


# ── 실제 원문 ────────────────────────────────────────────────────────

def test_real_blob_cells_keep_multiline_values_whole():
    """한 칸에 당해·누계를 `<br>` 로 몰아 쓴 서식 — 항목이 하나라 칸을 통째로 둔다."""
    p = A.parse_amendment_table((_FIX / "provisional_correction_blob_cells_20260303801347.html").read_text("utf-8"))
    assert p["status"] == "parsed"
    assert p["target_document"] == "연결재무제표 기준 영업(잠정)실적(공정공시)"
    assert p["original_filed_date"] == "2026-01-28"
    assert p["reason"] == "당기실적(2025.10.01~12.31) 정정"
    first = p["rows"][0]
    assert first["item"] == "매출액(당기실적)"
    assert first["before"].startswith("당해실적: 478,413\n")
    assert first["after"].startswith("당해실적: 473,133\n")
    assert first["change"] == "changed"


def test_real_section_rows_disambiguate_repeated_item_names():
    """`- 매출액(당해실적)` 이 금액·전기대비·전년동기대비 세 묶음에 되풀이된다 — 소제목이 가른다."""
    p = A.parse_amendment_table((_FIX / "provisional_correction_dash_period_tag_20260727800078.html").read_text("utf-8"))
    assert p["status"] == "parsed"
    sales = [(r["section"], r["before"], r["after"]) for r in p["rows"] if r["item"] == "- 매출액(당해실적)"]
    assert sales == [
        ("1. 연결실적내용 > 당기실적(26.2Q)", "13,000", "13,937"),
        ("1. 연결실적내용 > 전기대비 증감율(%)", "13.54", "21.72"),
        ("1. 연결실적내용 > 전년동기대비 증감율(%)", "35.21", "44.95"),
    ]
    # 소제목 줄 자체는 바뀐 칸이 아니다
    assert not any(r["item"] in ("1. 연결실적내용", "당기실적(26.2Q)") for r in p["rows"])
    # 번호 붙은 값 줄은 앞 묶음을 닫는다
    last = p["rows"][-1]
    assert last["item"] == "4. 기타 투자판단과 관련한 중요사항" and "section" not in last
    assert p["row_count"] == 12


# ── 서식 ────────────────────────────────────────────────────────────

def test_three_column_exchange_form():
    p = A.parse_amendment_table(_table(
        _tr("정정사유", "내용 추가"),
        _tr("공시서류제출일", "2025-03-12"),
        _tr("정정항목", "정정전", "정정후"),
        _tr("3) 실시국가", "호주", "호주, 뉴질랜드"),
        _tr("4) 대상자 수", "120명", "120 명"),
    ))
    assert p["reason"] == "내용 추가"
    assert p["original_filed_date"] == "2025-03-12"
    assert [(r["item"], r["before"], r["after"]) for r in p["rows"]] == [("3) 실시국가", "호주", "호주, 뉴질랜드")]
    assert p["skipped"]["unchanged"] == 1       # 공백만 다른 값은 같은 값


def test_four_column_major_report_form_with_spaced_th_headers():
    p = A.parse_amendment_table(_table(
        _tr("2. 정정대상 공시서류의 최초제출일 :", "2026년 8월 7일"),
        _tr("항 목", "정정사유", "정 정 전", "정 정 후", tag="TH"),
        _tr("1. 처분예정주식(주)", "기재정정(인원 변경)", "51,517", "59,917"),
    ))
    assert p["original_filed"] == "2026년 8월 7일"
    assert p["original_filed_date"] == "2026-08-07"
    assert p["reason"] == "기재정정(인원 변경)"   # 표 위 사유가 없으면 줄별 사유의 첫 값
    row, = p["rows"]
    assert (row["before"], row["after"], row["reason"]) == ("51,517", "59,917", "기재정정(인원 변경)")


def test_dart_xml_te_cells_are_read():
    """주요사항보고서 XML 은 값 칸이 `TE` 로 온다 — `TD` 만 보면 표가 통째로 안 읽힌다."""
    p = A.parse_amendment_table(
        "<TABLE><TR><TH>항 목</TH><TH>정정사유</TH><TH>정 정 전</TH><TH>정 정 후</TH></TR>"
        "<TR><TE ACODE='X'>발행가액(원)</TE><TE>확정</TE><TE AALIGN='RIGHT'>5,000</TE><TE>4,650</TE></TR>"
        "</TABLE>")
    assert [(r["item"], r["after"]) for r in p["rows"]] == [("발행가액(원)", "4,650")]


def test_rowspan_reason_is_carried_to_rows_below():
    """사유 칸 하나가 여러 항목에 걸치면 아랫줄은 칸이 모자라 온다 — 표가 끊긴 것이 아니다."""
    p = A.parse_amendment_table(
        "<TABLE><TR><TH>항 목</TH><TH>정정사유</TH><TH>정 정 전</TH><TH>정 정 후</TH></TR>"
        "<TR><TD>신주수(주)</TD><TD ROWSPAN='2'>발행조건 확정</TD><TD>1,000,000</TD><TD>950,000</TD></TR>"
        "<TR><TD>발행가액(원)</TD><TD>5,000</TD><TD>4,650</TD></TR>"
        "</TABLE>")
    assert [(r["item"], r["reason"]) for r in p["rows"]] == [
        ("신주수(주)", "발행조건 확정"), ("발행가액(원)", "발행조건 확정")]


def test_br_packed_items_are_unmerged_only_when_counts_match():
    """한 줄에 세 항목을 눌러 담은 칸(특수건설 `20260909900207`)은 항목마다 편다."""
    p = A.parse_amendment_table(_table(
        _tr("정정항목", "정정전", "정정후"),
        _tr("확정 계약금액<br/>계약금액 총액<br/>매출액 대비(%)",
            "27,631,500,000<br/>27,631,500,000<br/>13.90",
            "34,028,600,000<br/>34,028,600,000<br/>17.11"),
    ))
    assert [(r["item"], r["after"]) for r in p["rows"]] == [
        ("확정 계약금액", "34,028,600,000"), ("계약금액 총액", "34,028,600,000"), ("매출액 대비(%)", "17.11")]


def test_added_and_deleted_are_kept_and_named():
    """지운 칸도 소식이다(안건 철회·항목 삭제) — 버리지 않고 이름을 붙인다."""
    p = A.parse_amendment_table(_table(
        _tr("정정항목", "정정전", "정정후"),
        _tr("제3호 의안", "이사 선임의 건", "-"),
        _tr("제4호 의안", "해당사항 없음", "정관 변경의 건"),
    ))
    assert [(r["item"], r["change"]) for r in p["rows"]] == [("제3호 의안", "deleted"), ("제4호 의안", "added")]


def test_pointer_cells_are_skipped_and_reported():
    p = A.parse_amendment_table(_table(
        _tr("항 목", "정정사유", "정 정 전", "정 정 후", tag="TH"),
        _tr("23. 기타 참고사항", "주식수 변경", "주1) 정정 전", "주2) 정정 후"),
    ))
    assert p["status"] == "no_changes"
    assert p["skipped"]["pointer"] == 1
    assert "각주" in p["note"]


def test_table_ends_where_column_count_breaks():
    """본문 표(칸 수가 다르다)가 비교표에 새어 들지 않는다 — 정정된 항목이 `2. 주요내용` 이어도."""
    p = A.parse_amendment_table(_table(
        _tr("3. 정정사유", "계약상대방 변경"),
        _tr("정정항목", "정정전", "정정후"),
        _tr("2. 주요내용", "1) 계약상대방 : 스펙트럼", "1) 계약상대방 : 자이더스"),
        _tr("-"),
        _tr("1. 제목", "기술이전 계약"),
    ))
    assert [r["item"] for r in p["rows"]] == ["2. 주요내용"]
    assert p["reason"] == "계약상대방 변경"


def test_reason_only_and_absent():
    only = A.parse_amendment_table(_table(_tr("정정사유", "첨부서류 교체")))
    assert only["status"] == "reason_only" and "첨부" in only["note"]
    none = A.parse_amendment_table("<html><body>본문만</body></html>")
    assert none["status"] == "table_absent" and none["rows"] == []


def test_card_summary_counts_what_it_cut():
    rows = [{"item": f"항목{i}", "before": "1", "after": "2", "change": "changed"} for i in range(10)]
    out = A.card_summary({"status": "parsed", "rows": rows, "row_count": 10, "skipped": {}, "note": ""})
    assert len(out["rows"]) == A.CARD_ROWS_MAX
    assert out["rows_truncated"] == 10 - A.CARD_ROWS_MAX
    assert "skipped" not in out and "note" not in out


# ── 스크리너 정정 카드 ──────────────────────────────────────────────

_CORR_DOC = _table(
    _tr("3. 정정사유", "계약금액 변경"),
    _tr("2. 정정관련 공시서류제출일", "2026-09-01"),
    _tr("정정항목", "정정전", "정정후"),
    _tr("확정 계약금액", "27,631,500,000", "34,028,600,000"),
)


class _Client:
    def __init__(self):
        self.calls = 0
        self.opened: list[str] = []

    def api_call_snapshot(self):
        return self.calls

    async def get_document_cached(self, rcept_no):
        self.opened.append(rcept_no)
        self.calls += 1
        return {"text": "", "html": _CORR_DOC}


@pytest.fixture
def stubbed(monkeypatch):
    client = _Client()

    async def _scan(client_, code, bgn, end, pages):
        items = [
            {"corp_code": "00000001", "corp_name": "원본회사", "stock_code": "000001",
             "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "20260909000001",
             "rcept_dt": "20260909", "corp_cls": "Y", "flr_nm": "원본회사"},
            {"corp_code": "00000002", "corp_name": "정정회사", "stock_code": "000002",
             "report_nm": "[기재정정]단일판매ㆍ공급계약체결", "rcept_no": "20260909000002",
             "rcept_dt": "20260909", "corp_cls": "Y", "flr_nm": "정정회사"},
        ] if code == "I001" else []
        return S._scan_result(items, len(items), 1, 1)

    async def _detail(h, running):
        return {"detail_status": "parsed", "fields": {"amount_won": 34_028_600_000}}

    monkeypatch.setattr(S, "_scan_code", _scan)
    monkeypatch.setattr(S, "_fetch_detail", _detail)
    monkeypatch.setattr(S, "get_dart_client", lambda: client)
    monkeypatch.setattr(S, "_krx_mktcap_map", lambda codes, dd: {})
    return client


def _run(**kw):
    return asyncio.run(S._build_screener_payload_impl(
        types="order", period="20260909", universe="all", **kw))


def test_only_correction_cards_open_the_amendment_table(stubbed):
    p = _run(details=True)
    by = {h["rcept_no"]: h for h in p["hits"]}
    assert stubbed.opened == ["20260909000002"]
    assert "amendment" not in by["20260909000001"]
    a = by["20260909000002"]["amendment"]
    assert a["status"] == "parsed" and a["reason"] == "계약금액 변경"
    assert a["rows"] == [{"item": "확정 계약금액", "before": "27,631,500,000",
                          "after": "34,028,600,000", "change": "changed"}]


def test_no_amendment_without_details(stubbed):
    p = _run(details=False)
    assert stubbed.opened == []
    assert all("amendment" not in h for h in p["hits"])


def test_amendment_is_rendered_on_the_markdown_card(stubbed):
    md = T._render_digest(_run(details=True))
    assert "정정사유 「계약금액 변경」" in md
    assert "확정 계약금액: 27,631,500,000 → 34,028,600,000" in md


def test_amend_lines_render_status_and_rows():
    card = {"amendment": {"status": "parsed", "reason": "계약금액 변경", "original_filed_date": "2026-09-01",
                          "row_count": 5, "rows": [
                              {"item": "확정 계약금액", "before": "1", "after": "2", "change": "changed"},
                              {"item": "제3호 의안", "before": "이사 선임의 건", "after": "-", "change": "deleted"},
                              {"item": "- 매출액", "section": "전기대비 증감율(%)", "before": "1\n2", "after": "3",
                               "change": "changed"},
                              {"item": "x", "before": "1", "after": "2", "change": "changed"}]}}
    lines = T._amend_lines(card)
    assert lines[0] == "    - 🔁 정정사유 「계약금액 변경」 · 원공시 2026-09-01 · 바뀐 칸 5개"
    assert lines[1] == "        - 확정 계약금액: 1 → 2"
    assert lines[2] == "        - 제3호 의안: 이사 선임의 건 → - (삭제)"
    assert lines[3] == "        - 전기대비 증감율(%) · - 매출액: 1 / 2 → 3"
    assert lines[4].startswith("        - 그 밖에 2칸")


def test_amend_lines_say_why_nothing_is_shown():
    assert T._amend_lines({}) == []
    err = T._amend_lines({"amendment": {"status": "error", "note": "DART 014"}})
    assert err == ["    - 정정 비교표 못 읽음 (DART 014) — 원문 링크로 확인"]
    only = T._amend_lines({"amendment": {"status": "reason_only", "reason": "첨부서류 교체",
                                         "note": A.STATUS_NOTE["reason_only"]}})
    assert only[0] == "    - 🔁 정정사유 「첨부서류 교체」"
    assert "첨부만 바꾼" in only[1]
