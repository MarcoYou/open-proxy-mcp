"""귀속 줄이 하나도 없는 총계(TOTAL) 칸을 자본변동표 지배 몫과 대조한다(DATA-NI3 열린 결함 — 322칸 꼴 가운데 귀속 줄 없는 총계).

공용 규칙(`services/controlling_ni`)은 손익계산서에 지배·비지배 귀속 줄이 없으면 당기순이익 총계 = 지배로 읽는다(무소수지분 회사 001340·NAVER 가 이 길).
그러나 같은 응답의 자본변동표가 지배 몫 + 비지배 몫 = 당기순이익으로 맞고 비지배 몫이 0 이 아니면 총계에는 비지배가 들어 있다 — 지배 몫을 쓴다(케이스 `TOTAL_SCE`).
  · 217500 2026 1분기: 총계 −4.05억(손익계산서 귀속 줄 없음) · 자본변동표 지배 −594.3만 · 비지배 −3.99억 → 지배 −594.3만.
  · 081580 FY2023: 총계 −4.90억 · 자본변동표 지배 −1.62억 · 비지배 −3.28억 → 지배 −1.62억.
DART 0콜 · DB 0.
"""
from __future__ import annotations

from open_proxy_mcp.services import controlling_ni as C

F = "ifrs-full_"
NOSTD = "-표준계정코드 미사용-"


def _is(sj, aid, nm, ordv, cur, cum=""):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": str(cur), "frmtrm_amount": "", "bfefrmtrm_amount": "", "thstrm_add_amount": str(cum)}


def _sce(detail, ordv, cur, reprt="11013"):
    return {"sj_div": "SCE", "account_id": F + "ProfitLoss", "account_nm": "분기순이익(손실)", "account_detail": detail, "ord": str(ordv),
            "currency": "KRW", "thstrm_amount": str(cur), "frmtrm_amount": "", "bfefrmtrm_amount": "", "reprt_code": reprt}


OWNER = "자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]"
NCI = "자본 [member]|비지배지분 [member]"
CONSOL = "연결재무제표 [member]"


def rows_217500_2026q1(owner=-5942742, nci=-399289988, total=-405232730):
    return [
        _is("CIS", F + "IncomeTaxExpenseContinuingOperations", "법인세비용", 9, 1200000),
        _is("CIS", F + "ProfitLoss", "분기순이익(손실)", 10, total),
        _is("CIS", F + "ComprehensiveIncome", "분기총포괄손익", 14, total - 10000),
        _sce(OWNER, 2, owner), _sce(NCI, 2, nci), _sce(CONSOL, 2, total),
    ]


def rows_081580_fy2023():
    rows = [_is("CIS", F + "ProfitLoss", "당기순이익(손실)", 10, -490479253),
            _is("CIS", F + "ComprehensiveIncome", "총포괄손익", 14, -500000000)]
    rows += [{**_sce(d, 2, v, "11011"), "account_nm": "당기순이익(손실)"}
             for d, v in ((OWNER, -162432505), (NCI, -328046748), (CONSOL, -490479253))]
    return rows


def test_217500_2026_q1_total_is_replaced_by_the_sce_controlling_share():
    v, case = C.controlling_ni(rows_217500_2026q1())
    assert (v, case) == (-5942742, "TOTAL_SCE")                           # 종전: (−405,232,730, "TOTAL")


def test_081580_fy2023_total_is_replaced_by_the_sce_controlling_share():
    v, case = C.controlling_ni(rows_081580_fy2023())
    assert (v, case) == (-162432505, "TOTAL_SCE")                         # 종전: (−490,479,253, "TOTAL")


def test_no_sce_keeps_the_total_rule():
    rows = [x for x in rows_217500_2026q1() if x["sj_div"] != "SCE"]
    assert C.controlling_ni(rows) == (-405232730, "TOTAL")


def test_no_minority_share_in_sce_keeps_the_total():
    """무소수지분 회사(001340·NAVER 꼴) — 자본변동표 지배 몫 = 총계(비지배 0) → 종전 그대로 TOTAL."""
    v, case = C.controlling_ni(rows_217500_2026q1(owner=-405232730, nci=0))
    assert (v, case) == (-405232730, "TOTAL")


def test_sce_that_does_not_add_up_to_the_total_is_not_used():
    """자본변동표 지배 + 비지배 ≠ 손익계산서 당기순이익(두 표가 어긋남) — 자본변동표를 쓰지 않는다."""
    v, case = C.controlling_ni(rows_217500_2026q1(owner=-5942742, nci=-300000000))
    assert (v, case) == (-405232730, "TOTAL")


def test_explicit_attribution_lines_keep_their_own_case():
    """손익계산서에 지배·비지배 귀속 줄이 있으면 종전 길(값 구조로 가름) 그대로 — TOTAL_SCE 가 아니다."""
    rows = rows_217500_2026q1() + [
        _is("CIS", F + "ProfitLossAttributable" + "ToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 12, -5942742),
        _is("CIS", F + "ProfitLossAttributable" + "ToNoncontrolling" + "Interests", "비지배지분에 귀속되는 당기순이익(손실)", 13, -399289988)]
    v, case = C.controlling_ni(rows)
    assert v == -5942742 and "TOTAL" not in case
