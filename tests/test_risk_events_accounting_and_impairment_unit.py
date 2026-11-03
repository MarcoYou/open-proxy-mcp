"""risk_events — 회계처리기준 위반 조치 분류·파싱 · 자본잠식 금액 단위(천원 → 원). network 0.

① 회계처리기준 위반 조치(검찰고발·해임권고·감사인지정) 공시는 제목이 소송 계열 낱말
   (`소송등의`·`벌금등의부과`·`과징금`) 어디에도 안 걸려 **분류조차 안 됐다.**
   서식이 셋이고 칸 이름만 다르다. 셋째(증권선물위원회 조치) 판은 실제 공시 원문으로 고정한다.
② 자본잠식 50%·매출액 50억 미만 공시는 금액 절 머리에 `(단위:천원)` 이 붙는데, 숫자를
   그대로 `*_won` 에 넣어 **1,000분의 1** 로 나갔다(신한글로벌액티브리츠 2026-10-13 실측).
"""
from __future__ import annotations

from open_proxy_mcp.services.exchange_actions import (
    parse_accounting_sanction,
    parse_capital_impairment,
    parse_litigation,
)
from open_proxy_mcp.services.risk_events import _ACTIVE_CATEGORIES, _classify, _parse_document

# 피델릭스 20261028900777 — DART 원문(본문 텍스트) 그대로.
FIDELIX_20261028900777 = (
    "피델릭스/회계처리기준위반행위로인한증권선물위원회의검찰고발등/(2026.10.28)회계처리기준위반행위로인한증권선물위원회의검찰고발등 "
    "회계처리기준 위반행위로 인한 증권선물위원회의 검찰고발 등 1. 조치대상자 (주)피델릭스 회사와의 관계 본인 "
    "2. 감리(조사) 지적사항 ①매출등 허위계상 (개별: ’18년3,052백만원, ’19년3,161백만원, ’20년2,970백만원, "
    "’21년2,943백만원, ’22년2,943백만원) - 원활한 외부자금 조달을 위해 매출액등 영업실적을 보다 양호한것처럼 "
    "보이게할 목적으로 거래처와 공모를 통해 허위매출등을 계상함 ②외부감사방해 - 회사는 채권조회서에 거짓으로 "
    "회신할것을 거래처에 요청하는 등 감사인의 정상적인 외부감사를 방해함 3. 증권선물위원회 조치내용 -과징금 "
    "※회사 및 회사관계자에 대한 과징금 부과는 향후 금융위원회에서 최종 결정될 예정 -감사인 지정 3년 "
    "-前대표이사외 1인 해임(면직)권고상당 -수사기관 고발(회사,前대표이사외 1인) -수사기관 통보(前담당임원) "
    "4. 조치결정일 2026-10-28 5. 향후대책 당사는 회계투명성 제고와 내부통제 및 내부감시체계를 더욱 강화하고, "
    "회계처리기준을 철저히 준수하여 동일한 사례가 재발하지 않도록 최선을 다하겠습니다. 6. 조치사실 확인일 "
    "2026-10-28 7. 기타 투자판단에 참고할 사항 - 상기4. 조치결정일 및 6. 조치사실 확인일은 금융감독원 배포 "
    "자료를 통해 당사가 증권선물위원회의 감리결과 제재등을 확인한 날짜입니다. - 조회공시 요구(풍문 또는 보도)에 "
    "대한 답변은 해당 공시로 갈음합니다. ※관련공시 -"
)

# 첫째·둘째 서식은 칸 이름만 시험한다(값은 지어낸 것 — 칸 순서와 이름은 서식 그대로).
FORM1_SYNTHETIC = (
    "회계처리기준 위반에 따른 검찰고발 등 조치 1. 조치내용 - 검찰고발 - 대표이사 해임권고 - 감사인지정 3년 "
    "2. 주요 위반혐의내용 매출 허위계상 3. 조치기관 증권선물위원회 4. 향후대책 내부통제 강화 "
    "5. 조치일자 2026-09-02 6. 확인일자 2026-09-02 7. 기타 투자판단에 참고할 사항 -"
)
FORM2_SYNTHETIC = (
    "회계처리기준 위반에 따른 검찰고발 등 조치결과 1. 조치결과내용 벌금 확정 2. 주요 위반혐의내용 "
    "자산 과대계상 3. 관할법원 또는 검찰 서울중앙지방법원 4. 향후대책 재발 방지 "
    "5. 판결ㆍ결정일자 2026-09-10 6. 확인일자 2026-09-10"
)

# 신한글로벌액티브리츠 20261013800446 — 원문 본문(줄 단위). 절 머리가 `(단위:천원)` 이다.
SHINHAN_REIT_20261013800446 = "\n".join([
    "자본잠식 50% 이상 또는 매출액 50억원 미만 사실 발생",
    "1. 재무제표의 종류", "연결",
    "2. 결산기간", "당해사업연도", "직전사업연도",
    "- 시작일", "2026-03-01", "2025-09-01", "- 종료일", "2026-08-31", "2026-02-28",
    "3. 매출액 또는 손익구조 변동내용(단위:천원)",
    "당해사업연도", "직전사업연도", "증감금액", "증감비율(%)", "흑자적자전환여부",
    "- 매출액", "2,573,138", "2,254,979", "318,159", "14.1", "-",
    "- 영업이익", "2,282,562", "1,437,204", "845,358", "58.8", "-",
    "- 법인세비용차감전계속사업이익", "2,084,750", "-1,471,465", "3,556,215", "241.7", "흑자전환",
    "- 당기순이익", "2,084,750", "-1,471,465", "3,556,215", "241.7", "흑자전환",
    "- 대규모법인여부", "미해당",
    "4. 재무현황(단위:천원)", "당해사업연도", "직전사업연도",
    "- 자산총계", "137,893,482", "109,414,524",
    "- 부채총계", "20,568,178", "18,509,089",
    "- 자본총계", "117,325,304", "90,905,435",
    "- 자본금", "43,477,664", "43,477,664",
    "- 자본잠식률(%) =〔(자본금-자본총계)/자본금〕×100", "-", "-",
    "5. 매출액 또는 손익구조 변동 주요원인",
    "당사의 매출액은 자리츠(신한글로벌제1호위탁관리부동산투자회사)가 투자한 기초자산 펀드로부터 발생한 "
    "2026년 1분기 및 2분기 배당수익으로 구성되어 있음.",
    "6. 이사회 결의일(결정일)", "2026-10-13",
])


def _lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.strip()]


# ── ① 분류 ────────────────────────────────────────────────────


def test_accounting_sanction_titles_are_classified_as_litigation() -> None:
    for title in ("회계처리기준위반행위로인한증권선물위원회의검찰고발등",
                  "회계처리기준위반에따른검찰고발등조치",
                  "회계처리기준위반에따른검찰고발등조치결과",
                  "[기재정정]회계처리기준위반에따른검찰고발등조치"):
        cat, _ = _classify(title)
        assert cat == "litigation", title
    assert "litigation" in _ACTIVE_CATEGORIES          # 기본 조회에 뜬다


def test_exchange_notices_mentioning_the_violation_keep_their_category() -> None:
    """같은 낱말이 괄호 사유로 붙는 거래소 안내는 원래 카테고리를 지킨다."""
    assert _classify("조회공시요구(풍문또는보도)              (회계처리기준 위반)")[0] == "inquiry_disclosure"
    assert _classify("기타시장안내              (회계처리기준 위반행위 관련 상장적격성 실질심사 절차 미진행 안내)")[0] \
        == "listing_review"


# ── ① 파싱 ────────────────────────────────────────────────────


def test_third_form_fss_committee_sanction_is_read() -> None:
    d = parse_litigation(FIDELIX_20261028900777, [], "기타")
    assert d["sanction_kind"] == "회계처리기준 위반 조치"
    assert d["form"] == "증권선물위원회 조치"
    assert d["authority"] == "증권선물위원회"
    assert d["action_date"] == "2026-10-28" and d["confirmed_date"] == "2026-10-28"
    assert "허위" in d["violation"] and "외부감사방해" in d["violation"]
    assert d["target"].startswith("(주)피델릭스")
    assert "재발" in d["response_plan"]
    acts = " / ".join(d["actions"])
    assert "감사인 지정 3년" in acts and "해임(면직)권고" in acts and "수사기관 고발" in acts


def test_first_form_planned_sanction_labels() -> None:
    d = parse_accounting_sanction(FORM1_SYNTHETIC, [], "기타")
    assert d["form"] == "조치 예정" and d["authority"] == "증권선물위원회"
    assert d["violation"] == "매출 허위계상" and d["action_date"] == "2026-09-02"
    assert d["actions"] == ["검찰고발", "대표이사 해임권고", "감사인지정 3년"]


def test_second_form_court_result_labels() -> None:
    d = parse_accounting_sanction(FORM2_SYNTHETIC, [], "기타")
    assert d["form"] == "조치 결과(재판 종료)" and d["court"] == "서울중앙지방법원"
    assert d["action_date"] == "2026-09-10" and "authority" not in d


def test_lawsuit_form_is_not_taken_by_the_accounting_parser() -> None:
    lawsuit = ("소송등의제기ㆍ신청 1. 사건의 명칭 손해배상 청구의 소 사건번호 2026가합12345 "
               "2. 원고ㆍ신청인 홍길동 3. 청구내용 손해배상 4. 관할법원 서울중앙지방법원 5. 향후대책 적극 대응")
    d = parse_litigation(lawsuit, [], "기타")
    assert "sanction_kind" not in d and d.get("case_no") == "2026가합12345"


def test_document_path_carries_fields_and_source() -> None:
    parsed, note = _parse_document(f"<html><body><p>{FIDELIX_20261028900777}</p></body></html>",
                                   "litigation", "기타")
    assert note == "" and parsed["authority"] == "증권선물위원회" and parsed["source_text"]


# ── ② 자본잠식 단위 ───────────────────────────────────────────


def test_capital_impairment_amounts_are_converted_from_thousand_won() -> None:
    d = parse_capital_impairment(SHINHAN_REIT_20261013800446, _lines(SHINHAN_REIT_20261013800446), "발생")
    assert d["amount_unit"] == "천원"
    assert d["revenue_won"] == 2_573_138_000            # 25.7억원 — 종전 2,573,138(원)으로 나갔다
    assert d["operating_income_won"] == 2_282_562_000
    assert d["net_income_won"] == 2_084_750_000
    assert d["equity_won"] == 117_325_304_000
    assert d["paid_in_capital_won"] == 43_477_664_000
    assert d["revenue_change_pct"] == "14.1"            # 비율은 단위와 무관
    assert "unit_conflict" not in d                     # 환산한 자산총계 1,379억 < 2조 — 미해당과 맞는다


def test_mislabelled_unit_is_caught_after_conversion() -> None:
    """머리는 천원인데 값이 원으로 적힌 판 — 환산한 자산총계가 2조를 넘어 `미해당` 과 어긋난다."""
    won_written = SHINHAN_REIT_20261013800446.replace("137,893,482", "137,893,482,000") \
        .replace("2,573,138\n", "2,573,138,000\n")
    d = parse_capital_impairment(won_written, _lines(won_written), "발생")
    assert d.get("unit_conflict") is True and "revenue_won" not in d


def test_unknown_unit_leaves_amounts_empty_and_says_so() -> None:
    no_unit = SHINHAN_REIT_20261013800446.replace("(단위:천원)", "")
    d = parse_capital_impairment(no_unit, _lines(no_unit), "발생")
    assert "revenue_won" not in d and "equity_won" not in d
    assert "단위" in d["unit_unread"]


def test_won_unit_form_is_left_as_is() -> None:
    won = SHINHAN_REIT_20261013800446.replace("(단위:천원)", "(단위:원)")
    d = parse_capital_impairment(won, _lines(won), "발생")
    assert d["revenue_won"] == 2_573_138 and d["amount_unit"] == "원"
