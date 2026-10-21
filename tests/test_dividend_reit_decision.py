"""리츠 결산기 배당결정 서식 「부동산투자회사금전배당결정」을 배당 도구가 거르고 읽는다(DATA-DIV1).

종전엔 `DIVIDEND_KEYWORDS` 에 이 서식 이름이 없어 KB스타리츠(2026-10-15, DPS 90원)·신한글로벌액티브리츠(2026-10-13, DPS 34원)의 결정공시가 걸리지 않았다(둘 다 I001 하위).
서식 번호도 일반 배당결정과 달라(1 배당금총액 · 2 1주당 배당금 · 3 시가배당률 · 4 배당기준일 …, 배당구분 행 없음) 일반 파서는 DPS 0 을 냈다.
실제 공시 원문 2건(`tests/dart_responses/dart/dividend_decision_reit.json`, 공시 뷰어 평문)으로 본다. 네트워크 0.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from open_proxy_mcp.services import dividend as dv
from open_proxy_mcp.services.dividend_parser import DIVIDEND_KEYWORDS, parse_dividend_decision
from open_proxy_mcp.services.filing_search import report_name_matches

_DOCS = json.loads((Path(__file__).parent / "dart_responses" / "dart" / "dividend_decision_reit.json").read_text(encoding="utf-8"))
KB, SHINHAN = "20261015800504", "20261013800451"


def _parsed(rn: str) -> dict:
    p = parse_dividend_decision(_DOCS[rn]["text"])
    assert p is not None
    return p


def test_report_name_is_a_dividend_decision_keyword():
    assert report_name_matches({"report_nm": "부동산투자회사금전배당결정"}, DIVIDEND_KEYWORDS)
    assert report_name_matches({"report_nm": "[기재정정]부동산투자회사금전배당결정"}, DIVIDEND_KEYWORDS)
    assert not report_name_matches({"report_nm": "주주총회소집결의"}, DIVIDEND_KEYWORDS)


def test_kb_starreits_dps_90():
    p = _parsed(KB)
    assert p["dps_common"] == 90 and p["total_amount"] == 13_568_785_650
    assert p["yield_common"] == 5.7
    assert (p["record_date"], p["agm_date"], p["board_date"]) == ("2026-11-06", "2026-10-30", "2026-10-15")
    assert p["payment_date"] is None                     # 「정기주주총회일로부터 1개월 이내」 — 날짜가 아니다
    assert p["dividend_method"] == "현금배당" and p["dividend_type"] == "결산배당"
    assert "추정" in p["dividend_type_source"] or "집계" in p["dividend_type_source"]
    assert p["has_special"] is False and p["preferred_stocks"] == [] and p["dps_preferred"] == 0
    assert "13,568,785,650원/150,764,285주" in p["remarks"] and "관련공시" not in p["remarks"]


def test_shinhan_global_active_reit_dps_34():
    p = _parsed(SHINHAN)
    assert p["dps_common"] == 34 and p["total_amount"] == 1_500_000_000
    assert p["yield_common"] == 3.4
    assert (p["record_date"], p["agm_date"], p["board_date"]) == ("2026-08-31", "2026-11-26", "2026-10-13")


@pytest.mark.parametrize("rn,year", [(KB, 2026), (SHINHAN, 2026)])
def test_decisions_bucket_into_the_record_date_year(rn, year):
    d = {**_parsed(rn), "rcept_no": rn, "rcept_dt": _DOCS[rn]["rcept_dt"]}
    assert dv._bucket_fiscal_year(d) == year
    assert dv._quarter_label(d) == "결산"


def test_search_returns_the_reit_decision(monkeypatch):
    items = [{"rcept_no": KB, "report_nm": "부동산투자회사금전배당결정", "rcept_dt": "20261015"},
             {"rcept_no": "20261015800501", "report_nm": "현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정", "rcept_dt": "20261015"},
             {"rcept_no": "20261015800507", "report_nm": "주주총회소집결의", "rcept_dt": "20261015"}]

    async def fake_scan(**kwargs):
        return items, [], None

    monkeypatch.setattr(dv, "fetch_filings_for_title_scan", fake_scan)
    decisions, notices, _n, err = asyncio.run(dv._search_dividend_filings("01622720", 2026, 2026))
    assert err is None
    assert KB in [d["rcept_no"] for d in decisions]                     # 종전: 이 서식 이름이 키워드에 없어 빠졌다
    assert "20261015800507" not in [d["rcept_no"] for d in decisions]
    assert [n["rcept_no"] for n in notices] == ["20261015800501"]


def test_amended_reit_form_reads_values_from_the_corrected_body():
    """[기재정정] 리츠 서식 — 「정정사항」 표(정정전 33원)가 앞서도 값은 정정 후 본문(34원)에서 읽는다."""
    body = _DOCS[SHINHAN]["text"]
    pre = ("정정신고(보고) 정정일자 2026-10-14 1. 정정관련 공시서류 부동산투자회사금전배당결정 2. 정정관련 공시서류제출일 2026-10-13 "
           "3. 정정사유 오기 4. 정정사항 정정항목 정정전 정정후 2. 1주당 배당금(원) 보통주식 33 34 ")
    p = parse_dividend_decision(pre + body)
    assert p is not None and p["dps_common"] == 34
    assert p["amendment"]["values_from"] == "정정후 본문" and p["amendment"]["original_filed"] == "2026-10-13"


def test_ordinary_decision_form_is_unchanged():
    ordinary = ("현금ㆍ현물배당 결정 1. 배당구분 결산배당 2. 배당종류 현금배당 3. 1주당 배당금(원) 보통주식 1,000 종류주식 - "
                "4. 시가배당율(%) 보통주식 2.1 5. 배당금총액(원) 100,000,000 6. 배당기준일 2025-12-31 7. 배당금지급 예정일자 2026-04-10 "
                "8. 주주총회 개최여부 개최 9. 주주총회 예정일자 2026-03-25 10. 이사회결의일(결정일) 2026-02-10")
    p = parse_dividend_decision(ordinary)
    assert p["dps_common"] == 1000 and p["total_amount"] == 100_000_000 and p["dividend_type"] == "결산배당"
    assert "dividend_type_source" not in p
