"""배당 결정 **정정 공시** — 값은 정정 후 본문에서, 앞 「정정사항」 표는 원 공시를 찾는 열쇠·정정 항목 메타로만. (261019)

실제 공시 원문 8건(`tests/dart_responses/dart/dividend_decision_amendments.json`, 공시 뷰어 평문)으로 본다. 네트워크 0.

고치기 전 파서는 칸마다 **첫 일치**를 잡아 표의 정정전 값을 읽었다(캐시 정정 원문 250건 중 112건에서 핵심 칸이 갈렸다 —
배당금총액 74 · 결의일 15 · 기준일 8 …). 동화약품은 기준일 2025-03-25 → 2026-03-25 정정이 FY2024 결산배당으로 남았고,
하나금융지주는 회사가 결산배당 → 분기배당으로 고친 결의가 결산배당으로 남았다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_proxy_mcp.services import dividend as dv
from open_proxy_mcp.services.dividend_parser import parse_dividend_decision
from open_proxy_mcp.tools import dividend_disclosure as tool

_DOCS = json.loads(
    (Path(__file__).parent / "dart_responses" / "dart" / "dividend_decision_amendments.json").read_text(encoding="utf-8")
)


def _text(rcept_no: str) -> str:
    return _DOCS[rcept_no]["text"]


def _detail(rcept_no: str, text: str | None = None) -> dict:
    """`dividend._decision_details._fetch` 와 같은 모양 — 파싱 결과에 접수번호·접수일·제목을 붙인다."""
    doc = _DOCS.get(rcept_no, {})
    parsed = parse_dividend_decision(text if text is not None else doc["text"])
    assert parsed is not None
    parsed["rcept_no"] = rcept_no
    parsed["rcept_dt"] = doc.get("rcept_dt") or rcept_no[:8]
    parsed["report_name"] = doc.get("report_nm") or "현금ㆍ현물배당결정"
    return parsed


# ── 파서: 값은 정정 후 본문에서 ─────────────────────────────────────────

def test_record_date_correction_is_read_from_the_body():
    """동화약품 20260224801063 — 기준일 2025-03-25 → 2026-03-25(「단순 착오」), 시가배당율 1.01 → 0.99."""
    p = parse_dividend_decision(_text("20260224801063"))
    assert p["record_date"] == "2026-03-25"
    assert p["yield_common"] == 0.99
    meta = p["amendment"]
    assert meta["record_date_before"] == "2025-03-25"
    assert meta["board_date_before"] is None          # 결의일은 안 고쳤다
    assert meta["original_filed"] == "2026-02-24"
    assert meta["reason"] == "단순 착오"
    assert meta["values_from"] == "정정후 본문"
    assert {"record_date", "yield", "remarks"} <= set(meta["items"])
    # 비고도 본문 것 — 표의 11번 행(정정전·정정후 비고를 이어 붙인 글)이 아니다.
    assert p["remarks"].startswith("-시가배당율'은 이사회결의일")
    assert "현금ㆍ현물배당 결정 1. 배당구분" not in p["remarks"]


def test_total_amount_and_dps_correction_is_read_from_the_body():
    """유수홀딩스 20210330802309 — 주총 수정동의로 1주당 250 → 500, 총액 6,244,556,000 → 12,489,112,000.

    고치기 전에는 주당 500(본문 — 표 행이 「- 보통주식」 이라 안 걸렸다)과 총액 6,244,556,000(표의 정정전)이 섞여 나갔다.
    """
    p = parse_dividend_decision(_text("20210330802309"))
    assert p["dps_common"] == 500
    assert p["total_amount"] == 12_489_112_000
    assert p["agm_date"] == "2021-03-30"
    assert p["amendment"]["original_filed"] == "2021-02-15"
    assert {"dps", "total_amount", "agm_date"} <= set(p["amendment"]["items"])


def test_dividend_type_correction_is_read_from_the_body():
    """하나금융지주 20230427800550 — 배당구분 결산배당 → 분기배당. 고치기 전 이 결의가 FY2022 「결산배당 중복」을 만들었다."""
    p = parse_dividend_decision(_text("20230427800550"))
    assert p["dividend_type"] == "분기배당"
    assert p["amendment"]["dividend_type_before"] == "결산배당"
    assert p["amendment"]["items"] == ["dividend_type"]


def test_board_date_correction_keeps_the_before_value_as_a_key():
    """카카오 20220211800530 — 결의일 2021-02-10 → 2022-02-10. 값은 정정후, 원 공시를 찾는 열쇠는 정정전."""
    p = parse_dividend_decision(_text("20220211800530"))
    assert p["board_date"] == "2022-02-10"
    assert p["amendment"]["board_date_before"] == "2021-02-10"


def test_dotted_dates_in_the_amendment_table():
    """엠앤씨솔루션 20260204801233 — 표가 점 날짜(「2025.02.04 2026.02.04」). 하이픈만 보던 정규식은 정정전 결의일을 잃었다."""
    p = parse_dividend_decision(_text("20260204801233"))
    assert p["board_date"] == "2026-02-04"
    assert p["agm_date"] == "2026-03-26"
    meta = p["amendment"]
    assert meta["board_date_before"] == "2025-02-04"
    assert meta["original_filed"] == "2026-02-04"          # 「제출일 2026.02.04」
    assert meta["items"] == ["agm_date", "board_date"]


def test_dotted_and_hyphenated_dates_in_the_body():
    """본문 날짜 칸도 점·하이픈·빗금을 모두 받아 YYYY-MM-DD 로 낸다. 말이 안 되는 날짜는 None."""
    body = (
        "현금ㆍ현물배당 결정 1. 배당구분 결산배당 2. 배당종류 현금배당 "
        "3. 1주당 배당금(원) 보통주식 1,000 종류주식 - 5. 배당금총액(원) 9,000,000 "
        "6. 배당기준일 2025.12.31 7. 배당금지급 예정일자 2026/4/20 8. 주주총회 개최여부 개최 "
        "9. 주주총회 예정일자 2026-03-20 10. 이사회결의일(결정일) 2026. 2. 10 "
        "11. 기타 투자판단과 관련한 중요사항 -"
    )
    p = parse_dividend_decision(body)
    assert (p["record_date"], p["payment_date"], p["agm_date"], p["board_date"]) == (
        "2025-12-31", "2026-04-20", "2026-03-20", "2026-02-10")
    assert "amendment" not in p
    bad = parse_dividend_decision(body.replace("2025.12.31", "2025.13.31"))
    assert bad["record_date"] is None


def test_an_original_filing_is_parsed_as_before():
    """원 공시(정정사항 표 없음)는 종전 그대로 — 메타도 안 붙는다. 캐시 원 공시 4,377건 전 칸 같음(261019 전수)."""
    p = parse_dividend_decision(_text("20260224800779"))
    assert p["record_date"] == "2025-03-25"          # 회사가 잘못 적은 그대로 — 고치는 것은 정정 공시다
    assert "amendment" not in p


def test_a_cancelling_amendment_is_kept_and_flagged():
    """웅진씽크빅 20200508800742 — 「배당기준일 명부확정 절차를 사전 진행하지 못해 … 배당금 지급 결정 취소」.

    표가 1주당 20 → -, 총액 → - 로 고쳤다. 고치기 전에는 표의 정정전 값을 읽어 취소된 배당이 지급으로 남았다.
    빈 결과(None)로 버리면 원 공시를 대체할 수 없으므로 취소 표시를 단 행으로 낸다.
    """
    p = parse_dividend_decision(_text("20200508800742"))
    assert p is not None
    assert (p["dps_common"], p["total_amount"], p["record_date"]) == (0, 0, None)
    meta = p["amendment"]
    assert meta["cancelled"] is True
    assert (meta["board_date_before"], meta["record_date_before"]) == ("2020-05-07", "2020-03-31")


def test_an_amendment_without_a_cleared_amount_and_without_amounts_stays_empty():
    """금액이 비었어도 표가 금액을 지운 모양이 아니면(서식이 다른 정정) 종전처럼 None — 취소로 단정하지 않는다."""
    text = _text("20220211800530").replace("보통주식 53", "보통주식 -").replace("22,989,645,700", "-")
    assert parse_dividend_decision(text) is None


# ── 서비스: 정정이 고친 앞 공시를 대체 ─────────────────────────────────

def test_record_date_amendment_supersedes_the_original_across_fiscal_years():
    """동화약품 — 정정이 기준일을 고치면 (사업연도·분기·기준일) 키가 갈린다. 원 공시는 FY2024, 정정은 FY2025.

    종전 중복 제거는 키가 같아야 원 공시를 지웠다. 그게 맞아 보였던 건 파서가 정정전 기준일을 읽어 키가 우연히 같았기 때문이다.
    """
    orig, amend = _detail("20260224800779"), _detail("20260224801063")
    details = dv._mark_superseded([orig, amend])
    assert orig["superseded_by"] == "20260224801063"
    assert "superseded_by" not in amend
    assert dv._bucket_fiscal_year(amend) == 2025
    assert dv._decisions_summary_for_year(details, 2024) == {}        # FY2024 가짜 결산배당 없음
    fy2025 = dv._decisions_summary_for_year(details, 2025)
    assert (fy2025["cash_dps"], fy2025["decision_count"]) == (65, 1)
    assert [d["rcept_no"] for d in dv._effective_decisions(details)] == ["20260224801063"]
    rows = dv._quarterly_breakdown(details, [2024, 2025])
    flags = {r["rcept_no"]: r["is_superseded"] for r in rows}
    assert flags == {"20260224800779": True, "20260224801063": False}


def test_board_date_amendment_finds_the_original_by_the_before_value():
    """엠앤씨솔루션 — 원 공시 결의일 2025-02-04(오기), 정정 2026-02-04. 정정전 결의일(점 날짜 표)로 원 공시를 찾는다."""
    orig, amend = _detail("20260204800355"), _detail("20260204801233")
    dv._mark_superseded([orig, amend])
    assert orig["superseded_by"] == "20260204801233"


def test_dividend_type_amendment_supersedes_the_mislabelled_original():
    """하나금융지주 — 원 공시는 「결산배당」(정정전), 정정은 분기배당. 원 공시 원문이 캐시에 없어 정정 본문에 구분만 되돌려 만든다."""
    amend = _detail("20230427800550")
    body = _text("20230427800550")
    body = body[body.index("현금ㆍ현물배당 결정 1. 배당구분"):].replace("1. 배당구분 분기배당", "1. 배당구분 결산배당", 1)
    orig = _detail("20230427800387", body)
    orig["rcept_dt"] = "20230427"
    final_2022 = _detail("20230209800624", body.replace("보통주식 600", "보통주식 2,550").replace("2023-03-31", "2022-12-31"))
    final_2022["rcept_dt"] = "20230209"
    final_2022["board_date"] = "2023-02-09"
    details = dv._mark_superseded([final_2022, orig, amend])
    assert orig["superseded_by"] == "20230427800550"
    assert "superseded_by" not in final_2022
    live = dv._effective_decisions([d for d in details if dv._bucket_fiscal_year(d) == 2022])
    assert [d["rcept_no"] for d in live] == ["20230209800624"]        # FY2022 결산배당은 하나뿐


def test_cancelling_amendment_removes_the_payment_but_stays_visible():
    """웅진씽크빅 — 원 공시(20원 분기배당)는 대체되고, 취소 정정은 합산·건수에서 빠지되 목록에는 남는다."""
    amend = _detail("20200508800742")
    body = _text("20200508800742")
    body = body[body.index("현금ㆍ현물배당 결정 1. 배당구분"):]
    orig_text = (body.replace("보통주식 - 종류주식 - - 차등배당", "보통주식 20 종류주식 - - 차등배당", 1)
                 .replace("5. 배당금총액(원) -", "5. 배당금총액(원) 2,651,438,880", 1)
                 .replace("6. 배당기준일 -", "6. 배당기준일 2020-03-31", 1)
                 .replace("10. 이사회결의일(결정일) -", "10. 이사회결의일(결정일) 2020-05-07", 1))
    orig = _detail("20200507800510", orig_text)
    orig["rcept_dt"] = "20200507"
    details = dv._mark_superseded([orig, amend])
    assert orig["superseded_by"] == "20200508800742"
    assert dv._decisions_summary_for_year(details, 2020) == {}
    assert dv._effective_decisions(details) == []
    shown = dv._effective_decisions(details, keep_cancelled=True)
    assert [d["rcept_no"] for d in shown] == ["20200508800742"]
    assert tool._decision_kind(shown[0]) == "분기배당 [정정·취소]"
    rows = {r["rcept_no"]: r for r in dv._quarterly_breakdown(details, [2020])}
    assert rows["20200507800510"]["is_superseded"] is True
    assert rows["20200508800742"]["is_cancelled"] is True


def test_same_day_decisions_only_the_corrected_one_is_superseded():
    """같은 날 결의가 둘(KC그린홀딩스 FY2022 꼴 — 정기 50원 · 별도 2,949원, 기준일 같음). 총액만 고친 정정은 그 하나만 대체한다.

    결의일·구분·기준일이 모두 같아 열쇠로는 못 가르므로, 표에 오르지 않은(안 고친) 1주당 배당금이 같은 쪽으로 좁힌다.
    """
    base = {"dividend_type": "결산배당", "board_date": "2022-02-24", "record_date": "2022-12-31", "rcept_dt": "20220224"}
    regular = {**base, "rcept_no": "20220224000001", "dps_common": 50, "total_amount": 1_000_000}
    special = {**base, "rcept_no": "20220224000002", "dps_common": 2_949, "total_amount": 59_000_000}
    amend = {**base, "rcept_no": "20220310000003", "rcept_dt": "20220310", "dps_common": 2_949, "total_amount": 58_980_000,
             "amendment": {"board_date_before": None, "dividend_type_before": None, "record_date_before": None,
                           "original_filed": "2022-02-24", "items": ["total_amount"]}}
    dv._mark_superseded([regular, special, amend])
    assert special["superseded_by"] == "20220310000003"
    assert "superseded_by" not in regular


def test_same_day_reissue_keeps_the_later_filing_in_the_breakdown():
    """같은 날 같은 키(연도·분기·기준일)로 두 번 낸 결의 — 분기별 표는 접수번호가 늦은 쪽을 남긴다.

    고치기 전에는 접수일로만 정렬해 입력 순서(공시 검색은 최신 먼저)가 남았다 — 운영 응답에서 동화약품·하나금융지주의
    같은 날 정정본이 「(대체됨)」, 원 공시가 남는 쪽으로 나갔다. 합산 쪽(`_effective_decisions`)은 이미 접수번호까지 봤다.
    """
    base = {"dividend_type": "결산배당", "board_date": "2026-02-10", "record_date": "2025-12-31", "rcept_dt": "20260210",
            "dps_common": 100, "report_name": "현금ㆍ현물배당결정"}
    later = {**base, "rcept_no": "20260210000009", "dps_common": 110}
    earlier = {**base, "rcept_no": "20260210000001"}
    rows = {r["rcept_no"]: r for r in dv._quarterly_breakdown([later, earlier], [2025])}
    assert rows["20260210000001"]["is_superseded"] is True
    assert rows["20260210000009"]["is_superseded"] is False
    assert [d["rcept_no"] for d in dv._effective_decisions([later, earlier])] == ["20260210000009"]


def test_latest_decisions_table_marks_amendments():
    assert tool._decision_kind({"dividend_type": "결산배당"}) == "결산배당"
    assert tool._decision_kind({"dividend_type": "결산배당", "amendment": {"items": []}}) == "결산배당 [정정]"


@pytest.mark.parametrize("rcept_no", sorted(r for r, d in _DOCS.items() if "정정" in d["report_nm"]))
def test_every_amendment_sample_reads_the_body_not_the_table(rcept_no):
    """표본 정정 전부 — 고친 칸이 정정전 값으로 나가지 않는다(정정전 값이 표에만 있고 본문에는 없다)."""
    p = parse_dividend_decision(_text(rcept_no))
    meta = p["amendment"]
    if meta.get("record_date_before"):
        assert p["record_date"] != meta["record_date_before"] or meta.get("cancelled")
    if meta.get("board_date_before"):
        assert p["board_date"] != meta["board_date_before"] or meta.get("cancelled")
    if meta.get("dividend_type_before"):
        assert p["dividend_type"] != meta["dividend_type_before"]
