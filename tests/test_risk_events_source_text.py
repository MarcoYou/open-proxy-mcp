"""관리종목·매매거래정지 상세가 비던 것 — 원문을 실어 주는지 본다.

실사용 시험자가 두 세션 연속 지적한 자리다. `include_details=True` 인데
「### 상세」 머리만 찍히고 사유·유예기간·해소요건이 한 줄도 안 나갔다.
원인은 파싱이 아니라 **렌더러** 였다 — 거래소 계열 6종에 해당 분기가 없어
파싱 결과가 통째로 버려졌다.

본문은 2026-08 I003 실제 공시에서 그대로 따왔다.
"""
from __future__ import annotations

import re

from open_proxy_mcp.services.risk_events import _parse_document
from open_proxy_mcp.tools.risk_events import _render

# 에스제이그룹 20260827900829 — 시험자가 막힌 바로 그 공시.
SJ_TEXT = """에스제이그룹/기타시장안내(관리종목지정우려종목)/(2026.08.27)기타시장안내(관리종목지정우려종목)(시가총액 200억원 미달)
기타시장안내(관리종목지정우려종목)
제목 : 주식회사 에스제이그룹 관리종목 지정우려 관련 안내(시가총액 200억원 미달)
코스닥시장 상장규정 제53조 및 동규정 부칙 제2440호 제2조에 따라 '보통주식의 시가총액이 200억원 미만인 상태가 연속하여 30일(매매거래일 기준)동안 계속'되는 경우 관리종목으로 지정하고 있으며,
이와 관련하여 동사는 '26.08.27 현재 시가총액 200억원 미만인 상태가 연속하여 25매매거래일동안 지속되었습니다.
따라서 시가총액 200억원 미만인 상태가 '26.08.28부터 5매매거래일 지속될 경우 관리종목으로 지정될 수 있음을 알려드립니다."""

# 코이즈 20260825900709 — 해소요건이 숫자로 적힌 판.
KOIZ_TEXT = """코이즈/기타시장안내/(2026.08.25)기타시장안내(시가총액 미달에 따른 상장폐지 우려 관련 안내)
제목 : (주)코이즈 보통주 시가총액 미달에 따른 상장폐지 우려 관련 안내
상기 사유로 관리종목으로 지정된 후 90일(매매거래일 기준) 이내에 해당 보통주식의 시가총액이 다음의 요건 중 하나라도 충족하지 못할 경우 상장폐지 사유에 해당됩니다.
- 시가총액이 기준금액 이상인 상태가 10일 이상 계속될 것
- 시가총액이 기준금액 이상인 일수가 30일 이상일 것
- 관리종목 지정 후 경과일수 : 56일
- 200억원 이상 계속충족일수 : 0일(해제요건 : 10일 이상)
- 200억원 이상 누적충족일수 : 0일(해제요건 : 30일 이상)"""

# 듀오백 20260819900653 — 정지 서식(칸 다섯).
HALT_TEXT = """주권매매거래정지
1.대상종목
(주)듀오백
보통주
2.정지사유
상장적격성 실질심사 대상 (사유발생)
3.정지기간
가.정지일시
2026-08-20
-
나.만료일시
상장적격성 실질심사 대상여부에 관한 결정일까지
4.근거규정
코스닥시장상장규정 제18조 및 동규정시행세칙 제19조
5.기타
- 상장적격성 실질심사 사유
 : 관리종목 또는 투자주의환기종목의 경영권 변동"""


def _html(text: str) -> str:
    return "<html><body>" + "".join(f"<p>{ln}</p>" for ln in text.split("\n")) + "</body></html>"


def _payload(rows: list[dict], guide: dict | None = None) -> dict:
    return {
        "status": "ok",
        "subject": "테스트",
        "warnings": [],
        "data": {
            "mode": "company",
            "canonical_name": "테스트",
            "category": "all",
            "window": {"start_date": "20260101", "end_date": "20260828"},
            "event_count": {"total": len(rows)},
            "events": rows,
            "usage": {},
            "details_guide": guide or {},
            "source_window": {"source_chars": 4000, "max": 20000},
        },
    }


def _row(cat: str, stage: str, text: str, source_chars: int = 4000) -> dict:
    details, note = _parse_document(_html(text), cat, stage, source_chars)
    row = {
        "category": cat, "category_label": cat, "stage": stage,
        "rcept_no": "20260827900829", "rcept_dt": "20260827",
        "report_nm": "기타시장안내(관리종목지정우려종목)", "filer_name": "코스닥시장본부",
        "details": details,
    }
    if note:
        row["detail_note"] = note
    return row


def test_관리종목_상세에_사유와_유예기간이_실린다():
    row = _row("listing_review", "기타", SJ_TEXT)
    out = _render(_payload([row]))
    assert "### 상세" in out
    # 사유
    assert "시가총액 미달" in out
    # 유예기간 — 이 숫자들이 두 세션 연속 안 나왔다.
    assert "25매매거래일동안 지속" in out
    assert "5매매거래일 지속될 경우" in out
    # 해소요건의 근거
    assert "제53조" in out
    # 원문 블록이 붙는다
    assert "공시 원문" in out


def test_해소요건_숫자가_잘리지_않는다():
    row = _row("listing_review", "폐지", KOIZ_TEXT)
    out = _render(_payload([row]))
    assert "해제요건 : 10일 이상" in out
    assert "해제요건 : 30일 이상" in out
    assert "관리종목 지정 후 경과일수 : 56일" in out


def test_매매거래정지_상세가_비지_않는다():
    row = _row("trading_halt", "거래정지", HALT_TEXT)
    out = _render(_payload([row]))
    assert "상장적격성 실질심사 대상" in out
    assert "만료일시" in out or "결정일까지" in out
    assert "코스닥시장상장규정 제18조" in out
    assert "공시 원문" in out


def test_상세_머리만_찍히고_비는_일이_없다():
    """「### 상세」 아래에 최소 한 줄은 나와야 한다 — 그게 원래 증상이었다."""
    for cat, stage, text in (("listing_review", "기타", SJ_TEXT),
                             ("trading_halt", "거래정지", HALT_TEXT),
                             ("listing_review", "폐지", KOIZ_TEXT)):
        out = _render(_payload([_row(cat, stage, text)]))
        body = out.split("### 상세", 1)[1]
        content = [ln for ln in body.split("\n") if ln.strip() and not ln.startswith("#")]
        assert len(content) >= 3, (cat, content)


def test_원문_창은_source_chars_로_넓힌다():
    narrow = _row("listing_review", "기타", SJ_TEXT, source_chars=200)
    assert narrow["details"]["source_text_truncated"] is True
    assert len(narrow["details"]["source_text"]) == 200
    out = _render(_payload([narrow]))
    assert "source_chars" in out
    wide = _row("listing_review", "기타", SJ_TEXT, source_chars=20000)
    assert wide["details"]["source_text_truncated"] is False
    assert "25매매거래일" in wide["details"]["source_text"]


def test_원문이_없으면_왜_없는지_말한다():
    details, note = _parse_document("<html><body></body></html>", "listing_review", "기타")
    assert details == {}
    assert "원문 없음" in note
    # 「없음」 한 단어로 뭉개지 않는다 — 이유가 구분돼 나온다.
    row = {"category": "listing_review", "stage": "기타", "rcept_no": "20260827900829",
           "rcept_dt": "20260827", "report_nm": "기타시장안내", "details": {}, "detail_note": note}
    out = _render(_payload([row]))
    assert "확인 못 한 이유" in out
    assert "원문 없음" in out


def test_서식이_다르면_원문을_그대로_싣고_그렇다고_말한다():
    unknown = "듣도 보도 못한 서식입니다. " * 5
    details, note = _parse_document(_html(unknown), "dissolution", "기타")
    assert details["source_text"]
    assert "서식이 달라" in note


def test_길잡이가_붙는다():
    from open_proxy_mcp.services.risk_events import _guide_for
    rows = [_row("listing_review", "기타", SJ_TEXT)]
    out = _render(_payload(rows, guide=_guide_for(rows)))
    assert "원문에서 더 볼 곳" in out
    assert "거기 없으면 볼 곳" in out
    assert "그래도 안 나오면" in out


def test_원문이_있으면_발췌를_두_번_쓰지_않는다():
    row = _row("dissolution", "기타", SJ_TEXT)
    out = _render(_payload([row]))
    assert out.count("본문 발췌") == 0
