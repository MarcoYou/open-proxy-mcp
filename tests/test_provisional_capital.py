"""소집공고 잠정 재무제표에서 자본금·지배주주지분·비지배지분을 읽는 규칙.

자본잠식률 = (자본금 − 지배주주지분) / 자본금 이라 셋이 다 필요하다. 소집공고에는 XBRL 계정
코드가 **없어서**(실측 소집공고 62건 중 0건) 한글 라벨로만 찾아야 하고, 그만큼 함정이 많다.
아래 케이스는 전부 캐시 실측에서 나온 실제 표 모양이다.

`extract_metrics` 에 파싱된 표를 직접 넣는다 — HTML 파싱이 아니라 **추출 규칙**을 재는 자리다.
"""

from __future__ import annotations

from open_proxy_mcp.services.provisional_financial_statement import extract_metrics

COLS = ["account", "current", "prior"]


def _bs(rows, unit="원", scope="consolidated"):
    """재무상태표 하나짜리 파싱 결과."""
    other = "separate" if scope == "consolidated" else "consolidated"
    table = {"columns": COLS, "rows": rows, "unit": unit}
    return {scope: {"balance_sheet": table, "income_statement": None},
            other: {"balance_sheet": None, "income_statement": None}}


#: 표가 재무상태표임을 스스로 말하게 하는 최소 행 — 없으면 자본 계정을 인정하지 않는다
ANCHORS = [["유동자산", "100", "90"], ["비유동자산", "200", "180"],
           ["자산총계", "300", "270"], ["부채총계", "100", "100"]]


def _m(rows, **kw):
    return extract_metrics(_bs(ANCHORS + rows, **kw))


def test_the_subtotal_wins_over_share_classes() -> None:
    """삼성전자형 — 소계가 먼저 오고 우선주·보통주가 뒤따른다. 다 더하면 자본금이 정확히 2배."""
    m = _m([["Ⅰ. 자본금", "897,514", "897,514"],
            ["1. 우선주자본금", "119,467", "119,467"],
            ["2. 보통주자본금", "778,047", "778,047"],
            ["자본총계", "436,320", "402,192"]], unit="백만원")
    assert m["fy_current_capital_stock_krw"] == 897_514_000_000


def test_share_classes_are_summed_when_there_is_no_subtotal() -> None:
    """소계 행이 없는 표도 있다 — 하나만 집으면 분모가 반쪽이 되어 잠식률이 두 배가 된다."""
    m = _m([["보통주자본금", "7,142,858,500", "7,142,858,500"],
            ["우선주자본금", "1,000,000,000", "1,000,000,000"],
            ["자본총계", "50,000,000,000", "40,000,000,000"]])
    assert m["fy_current_capital_stock_krw"] == 8_142_858_500
    assert (m["source_accounts"]["capital_stock_krw"]).get("method") == "summed"


def test_paid_in_capital_is_not_capital_stock() -> None:
    """납입자본 = 자본금 + 자본잉여금. 분모로 쓰면 잠식률이 과소 산정된다(상법 §451① 액면총액).

    실측 헝셩그룹·오가닉티코스메틱은 자본금 행 없이 납입자본만 싣는다.
    """
    m = _m([["납입자본", "610,375,708", "610,375,708"],
            ["기타불입자본", "1,000", "1,000"],
            ["자본총계", "700,000,000", "700,000,000"]])
    assert m.get("fy_current_capital_stock_krw") is None


def test_the_liability_row_does_not_become_non_controlling_interest() -> None:
    """실측 KT&G — 부채 섹션의 「비지배지분부채」가 자본 섹션의 「비지배지분」보다 **앞에** 온다.

    막지 않으면 56,609 대신 6,469 를 집어 8.7배 틀린다. 그 값으로 지배지분을 차감 산출하면
    자기자본이 부풀어 자본잠식이 과소 판정된다.
    """
    m = _m([["비지배지분부채", "6,469", "6,000"],
            ["자본금", "954,959", "954,959"],
            ["지배기업 소유주지분", "9,279,560", "9,000,000"],
            ["비지배지분", "56,609", "50,000"],
            ["자본총계", "9,336,169", "9,050,000"]], unit="백만원")
    assert m["fy_current_nci_krw"] == 56_609_000_000
    # 검산이 성립해야 한다 — 지배 + 비지배 = 자본총계
    assert (m["fy_current_controlling_equity_krw"] + m["fy_current_nci_krw"]
            == m["fy_current_total_equity_krw"])


def test_a_lowercase_L_item_marker_is_stripped() -> None:
    """실측 네이버 — 로마숫자를 소문자 L 로 타이핑한다(`l.` / `ll.`).

    이걸 못 떼면 지배지분 27.6조도, 차감용 비지배지분도 **둘 다** 사라진다.
    """
    m = _m([["자본금", "16,481,339,500", "16,481,339,500"],
            ["l. 지배기업 소유주지분", "27,587,015,954,183", "25,459,903,574,291"],
            ["ll. 비지배지분", "1,370,135,990,299", "1,541,008,274,046"],
            ["자본총계", "28,957,151,944,482", "27,000,911,848,337"]])
    assert m["fy_current_controlling_equity_krw"] == 27_587_015_954_183
    assert m["fy_current_nci_krw"] == 1_370_135_990_299


def test_capital_rows_are_ignored_outside_a_balance_sheet() -> None:
    """「지배기업 소유주지분」은 손익계산서에서 **당기순이익 귀속**이다.

    실측 국일제지는 `consolidated.balance_sheet` 슬롯에 손익계산서가 들어 있어, 칸 이름을
    믿으면 순손실 -145억을 자기자본으로 읽는다. 표가 스스로 재무상태표라고 말할 때만 읽는다.
    """
    only_is_rows = [["매출액", "1,000", "900"], ["영업이익", "100", "90"],
                    ["1. 지배기업소유주지분", "(14,579,602,370)", "(1,000)"]]
    m = extract_metrics(_bs(only_is_rows))          # 총계·유동 계정이 없다 = 재무상태표가 아니다
    assert m.get("fy_current_controlling_equity_krw") is None


def test_a_foreign_currency_notice_yields_nothing() -> None:
    """환산 근거가 본문에 없다 — 값을 내지 않는 것이 맞다(실측 헝셩그룹 RMB)."""
    m = _m([["자본금", "610,375,708", "610,375,708"],
            ["자본총계", "700,000,000", "700,000,000"]], unit="RMB")
    assert m.get("fy_current_capital_stock_krw") is None


def test_the_controlling_share_can_be_derived_when_the_subtotal_is_missing() -> None:
    """실측 고려아연 — 지배지분 소계 없이 비지배지분만 적는 표.

    자본총계로 물러나면 비지배 몫만큼 자기자본이 부풀어 잠식이 과소 산정된다. 차감할 재료
    (비지배지분·자본총계)가 남아 있어야 호출측이 만들어 쓸 수 있다.
    """
    m = _m([["Ⅰ. 자본금(주26)", "115,591,520,000", "115,591,520,000"],
            ["Ⅵ. 비지배지분(주1)", "245,713,735,969", "200,000,000,000"],
            ["자본총계", "11,185,672,942,042", "10,000,000,000,000"]])
    assert m.get("fy_current_controlling_equity_krw") is None
    assert m["fy_current_nci_krw"] == 245_713_735_969
    assert m["fy_current_total_equity_krw"] == 11_185_672_942_042


def test_total_equity_is_not_taken_from_the_assets_line() -> None:
    """「부채및자본총계」는 자산총계다 — 자기자본으로 읽으면 잠식 판정이 통째로 무의미해진다."""
    m = _m([["자본금", "1,000", "1,000"],
            ["자본총계", "400", "380"],
            ["부채및자본총계", "1,000", "950"]])
    assert m["fy_current_total_equity_krw"] == 400
