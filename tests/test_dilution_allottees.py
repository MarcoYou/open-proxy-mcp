"""제3자배정 대상자 원문 싣기 + C001 발행공시 채널.

원문 표본은 2026-08-28 DART 실제 공시에서 잘라 왔다 (고려아연 20260219002746 등).
"""

from __future__ import annotations

import pytest

from open_proxy_mcp.services.dilution_allottees import (
    SECTION_CHARS_DEFAULT,
    SECTION_CHARS_MAX,
    classify_c_filing,
    clamp_section_chars,
    extract_allotment_sections,
    extract_issuance_result_sections,
    parse_allottee_rows,
)


# 고려아연 20260219002746 원문에서 잘라온 조각 (표 칸이 줄마다 평평해져 오는 실제 모양).
KOREA_ZINC = """20. 기타 투자판단에 참고할 사항

 (1) 본 유상증자는 Crucible JV LLC와 당사 간 체결한 신주인수계약서에 따른 제3자배정 유상증자 건입니다.

 (9) 신주인수계약에 따르면 Crucible JV LLC는 본 유상증자에 따라 발행되는 신주에 대한 의결권을
 독립적으로 행사하며, 당사 또는 다른 주주와 의결권 행사 관련 어떠한 합의도 존재하지 않습니다.

 【제3자배정 근거, 목적 등】

 제3자배정 근거가 되는 정관규정

 제17조 제2항 제4호 및 제17조의2 제2항

 【제3자배정 조달자금의 구체적 사용목적】

 【타법인 증권 취득자금ㆍ영업양수자금의 경우】

 자금용도

 타법인 증권 취득자금
 Crucible Metals Holdings, LLC

 【제3자배정 대상자별 선정경위, 거래내역, 배정내역 등】

 제3자배정 대상자

 회사 또는최대주주와의 관계

 선정경위

 증자결정 전후 6월이내 거래내역 및 계획

 배정주식수 (주)

 비 고

 Crucible JV LLC
 주요주주
 사업 및 경영상 목적 달성 및 투자자의 납입 능력 등을 고려하여 선정
 주)
 2,209,716
 1년간보호예수

 주) 증자결정 전후 6월이내 거래내역 및 계획

 【제3자배정 대상자 중 법인 또는 단체가 포함된 경우】

 명 칭

 Crucible JV LLC
 4
 미국 정부
 40
"""


class TestAllotteeTable:
    def test_고려아연_대상자와_배정주식수를_원문에서_읽는다(self):
        out = extract_allotment_sections(KOREA_ZINC, section_chars=SECTION_CHARS_DEFAULT)
        table = next(s for s in out["sections"] if s["key"] == "allottee_table")
        # 원문이 근거다 — 발췌 안에 이름과 주식수가 그대로 있어야 한다.
        assert "Crucible JV LLC" in table["excerpt"]
        assert "2,209,716" in table["excerpt"]

        rows, note = parse_allottee_rows(table["excerpt"])
        assert len(rows) == 1
        assert rows[0]["name"] == "Crucible JV LLC"
        assert rows[0]["relation_to_company_or_controller"] == "주요주주"
        assert rows[0]["allotted_shares_text"] == "2,209,716"
        assert rows[0]["note"] == "1년간보호예수"
        assert "각주" in note  # 표로 나누지 않은 꼬리를 밝힌다

    def test_대상자가_여럿이면_모두_나온다(self):
        block = """【제3자배정 대상자별 선정경위, 거래내역, 배정내역 등】

 제3자배정 대상자
 회사 또는최대주주와의 관계
 선정경위
 증자결정 전후 6월이내 거래내역 및 계획
 배정주식수 (주)
 비 고
 김재욱
 대표이사
 이사회에서 선정
 해당사항 없음
 541,418
 1년간 보호예수
 한영택
 임원
 이사회에서 선정
 해당사항 없음
 270,709
 1년간 보호예수
"""
        rows, _note = parse_allottee_rows(block)
        assert [r["name"] for r in rows] == ["김재욱", "한영택"]
        assert [r["relation_to_company_or_controller"] for r in rows] == ["대표이사", "임원"]

    def test_표_머리가_다르면_행을_지어내지_않는다(self):
        rows, note = parse_allottee_rows("【제3자배정 대상자별 선정경위】\n 3자배정 대상자상호변경\n 주1)\n 주2)\n a\n b\n c\n")
        assert rows == []
        assert "원문을 직접 읽을 것" in note

    def test_전부_대시인_빈_표는_파싱_실패가_아니라고_말한다(self):
        """제3자배정이 아닌 증자에도 서식상 이 블록이 따라온다 — 실측 43건 중 3건."""
        block = ("【제3자배정 대상자별 선정경위, 거래내역, 배정내역 등】\n\n 제3자배정 대상자\n\n"
                 " 회사 또는최대주주와의 관계\n\n 선정경위\n\n 증자결정 전후 6월이내 거래내역 및 계획\n\n"
                 " 배정주식수 (주)\n\n 비 고\n\n - \n - \n - \n - \n - \n -")
        rows, note = parse_allottee_rows(block)
        assert rows == []
        assert "파싱 실패가 아니라" in note

    def test_배정주식수_자리가_숫자가_아니면_거기서_멈춘다(self):
        block = """【제3자배정 대상자별 선정경위, 거래내역, 배정내역 등】
 제3자배정 대상자
 회사 또는최대주주와의 관계
 선정경위
 증자결정 전후 6월이내 거래내역 및 계획
 배정주식수 (주)
 비 고
 A조합
 없음
 선정
 없음
 1,000
 보호예수
 주) 아래는 각주다
 이 줄은 표가 아니다
 그래서
 여섯
 줄이지만
 행이 아니다
"""
        rows, note = parse_allottee_rows(block)
        assert len(rows) == 1 and rows[0]["name"] == "A조합"
        assert "표로 나누지 않은" in note

    def test_칸이_여러_줄로_쪼개진_서식은_짝짓지_않는다(self):
        """한화에어로스페이스 20250418000538 형 — 거래내역·비고 칸이 2줄씩이라 6열 격자가 어긋난다.

        여기서 억지로 6줄씩 끊으면 **다음 대상자의 이름 자리에 앞 행의 비고가 들어간다.**
        잘못 짝지어 내보내느니 원문을 읽게 둔다.
        """
        block = """【제3자배정 대상자별 선정경위, 거래내역, 배정내역 등】
 제3자배정 대상자
 회사 또는최대주주와의 관계
 선정경위
 증자결정 전후 6월이내 거래내역 및 계획
 배정주식수 (주)
 비 고
 Hanwha Impact Partners Inc.
 최대주주의 계열회사
 이사회 결의를 통해 최종 선정
 약 8,881억원
 주1)
 1,171,584
 전매제한조치
 신주 전량 1년간 보호예수
"""
        rows, note = parse_allottee_rows(block)
        assert rows == []
        assert "원문에 그대로 있다" in note


class TestSections:
    def test_자금사용목적은_하위블록을_품고_간다(self):
        """`【…의 경우】` 하위 블록에서 끊으면 부모가 빈 껍데기로 나간다."""
        out = extract_allotment_sections(KOREA_ZINC, section_chars=SECTION_CHARS_DEFAULT)
        fund = next(s for s in out["sections"] if s["key"] == "fund_use")
        assert "타법인 증권 취득자금ㆍ영업양수자금의 경우" in fund["excerpt"]
        assert "Crucible Metals Holdings, LLC" in fund["excerpt"]

    def test_대상자가_법인이면_그_최대출자자가_실린다(self):
        out = extract_allotment_sections(KOREA_ZINC, section_chars=SECTION_CHARS_DEFAULT)
        prof = next(s for s in out["sections"] if s["key"] == "allottee_entity_profile")
        assert "미국 정부" in prof["excerpt"]

    def test_의결권_합의는_기타_투자판단_절에서_온다(self):
        out = extract_allotment_sections(KOREA_ZINC, section_chars=SECTION_CHARS_DEFAULT)
        note = next(s for s in out["sections"] if s["key"] == "investor_judgment_note")
        assert "의결권" in note["excerpt"]

    def test_없는_대목은_만들지_않고_무엇이_없는지_말한다(self):
        out = extract_allotment_sections(KOREA_ZINC, section_chars=SECTION_CHARS_DEFAULT)
        missing = {m["key"] for m in out["sections_not_found"]}
        assert "new_controller_profile" in missing
        assert all(m["what_is_here"] for m in out["sections_not_found"])

    def test_잘리면_잘렸다고_말하고_넓힐_길을_준다(self):
        out = extract_allotment_sections(KOREA_ZINC, section_chars=120)
        cut = [s for s in out["sections"] if s["truncated"]]
        assert cut, "좁은 창이면 적어도 한 대목은 잘려야 한다"
        for s in cut:
            assert "section_chars" in s["truncation_note"]
            assert s["chars"] > len(s["excerpt"])

    def test_띄어쓰기_변종_머리표지도_받는다(self):
        text = "【제3자배정 대상자별 선정경위,거래내역, 배정내역 등】\n 제3자배정 대상자\n"
        out = extract_allotment_sections(text, section_chars=SECTION_CHARS_DEFAULT)
        assert any(s["key"] == "allottee_table" for s in out["sections"])

    def test_하위블록은_다른_후보로_다시_세지_않는다(self):
        out = extract_allotment_sections(KOREA_ZINC, section_chars=SECTION_CHARS_DEFAULT)
        assert "【타법인 증권 취득자금ㆍ영업양수자금의 경우】" not in out["other_headings_in_document"]

    def test_제3자배정이_아닌_원문이면_아무것도_안_준다(self):
        out = extract_allotment_sections("1. 신주의 종류와 수\n 보통주식 (주)\n 1,000\n", section_chars=4000)
        assert out["sections"] == []
        assert len(out["sections_not_found"]) == 7


class TestSectionChars:
    @pytest.mark.parametrize("given,expected", [
        (None, SECTION_CHARS_DEFAULT),
        (True, SECTION_CHARS_DEFAULT),
        ("4000", SECTION_CHARS_DEFAULT),
        (10, 500),
        (999_999, SECTION_CHARS_MAX),
        (12_000, 12_000),
    ])
    def test_범위를_벗어나면_되돌린다(self, given, expected):
        assert clamp_section_chars(given) == expected


class TestCChannel:
    @pytest.mark.parametrize("report_nm,kind", [
        ("증권신고서(지분증권)", "registration"),
        ("[기재정정]증권신고서(지분증권)", "registration"),
        ("[발행조건확정]증권신고서(지분증권)", "registration"),
        ("증권발행실적보고서", "issuance_result"),
        ("투자설명서", "prospectus"),
        ("철회신고서", "withdrawal"),
        ("소액공모공시서류(지분증권)", "small_offering"),
        ("듣도보도못한서류", "other"),
    ])
    def test_보고서_이름을_무엇이_들었는지로_옮긴다(self, report_nm, kind):
        got_kind, what = classify_c_filing(report_nm)
        assert got_kind == kind
        assert what  # 「무엇이 여기 있나」는 언제나 채운다

    def test_실적보고서에서_지분변동_절을_원문으로_뽑는다(self):
        # 한화에어로스페이스 20250709000241 구조.
        text = """Ⅰ. 발행개요

 1. 기업개요

Ⅱ. 청약 및 배정에 관한 사항

 2. 인수기관별 인수금액

Ⅲ. 유상증자 전후의 주요주주 지분변동

 한화에너지㈜
 특수관계인
 163,037
 0.34

Ⅳ. 증권교부일 등
"""
        secs = extract_issuance_result_sections(text, section_chars=4000)
        heads = [s["heading"] for s in secs]
        assert "Ⅲ. 유상증자 전후의 주요주주 지분변동" in heads
        assert "Ⅱ. 청약 및 배정에 관한 사항" in heads
        share = next(s for s in secs if s["heading"].startswith("Ⅲ"))
        assert "한화에너지㈜" in share["excerpt"]
        assert "Ⅳ." not in share["excerpt"]  # 다음 절을 먹지 않는다

    def test_지분변동_절이_없으면_비운다(self):
        assert extract_issuance_result_sections("Ⅰ. 발행개요\n", section_chars=4000) == []
