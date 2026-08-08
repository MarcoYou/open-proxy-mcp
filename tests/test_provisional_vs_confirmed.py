"""정기주총은 승인 대상 연도를 **잠정(P)과 확정(A) 둘 다** 보여준다.

표를 던지는 시점은 소집공고가 아니라 주총이다. 시장 전수(12월 결산 2,731사) 기준 사업보고서는
소집공고 +7일(중앙값)에 나오고, 상법 §363(주총 2주 전 통지)과 겹치면 **최소 81.7%가 주총 전에
확정**된다. 그래서 주총 시점에는 잠정과 확정이 둘 다 존재한다. 공고 시점에서 멈추면 안 된다.

표기 — FY2025P = 잠정(회사가 소집공고에 제시), FY2025A = 확정(회사가 사업보고서로 공시).
"""

from __future__ import annotations

from open_proxy_mcp.services.proxy_advise import _extract_facts as _facts


def _conf(**kw):
    return {"data": {"summary": kw}}


def _prov(**kw):
    return {"extraction_status": "success", **kw}


def test_the_confirmed_year_survives_a_notice_we_could_not_parse() -> None:
    """공고를 못 읽었을 때야말로 확정치가 승인 대상 연도의 **유일한** 숫자다.

    한때 확정치를 잠정 블록 안에 넣었다가, 공고 파싱이 안 되는 회사에서 통째로 사라졌다.
    """
    facts = _facts(
        category="financial_statements", title="재무제표 승인의 건",
        eval_match=None, fin_payload=None, comp_payload=None,
        fy_raw_from_agenda={"extraction_status": "no_data"},
        confirmed_payload=_conf(revenue_krw=333_605_938_000_000),
        confirmed_year=2025,
    )
    assert facts["fy_current_confirmed_year"] == 2025
    assert facts["fy_current_revenue_krw_confirmed"] == 333_605_938_000_000


def test_net_income_is_left_out_of_the_comparison_and_says_so() -> None:
    """순이익은 공고가 연결 총액, 확정치가 지배주주 귀속이라 **같은 것이 아니다**.

    실측 영풍 FY2025: 공고 「Ⅶ.당기순손실」 +366억(총액) vs 확정 -83억(지배주주 귀속).
    449억 차이가 전부 비지배지분 몫인데, 이 둘을 빼면 「감사 과정에서 흑자가 적자로 뒤집혔다」는
    없는 사건이 만들어진다. 빼되 뺐다는 사실을 밝힌다 — 말없이 「일치」라고만 해도 오해다.
    """
    facts = _facts(
        category="financial_statements", title="재무제표 승인의 건",
        eval_match=None, fin_payload=None, comp_payload=None,
        fy_raw_from_agenda=_prov(fy_current_revenue_krw=100, fy_current_net_income_krw=36_600_000_000),
        confirmed_payload=_conf(revenue_krw=100, net_income_krw=-8_300_000_000),
        confirmed_year=2025,
    )
    text = facts["fy_provisional_vs_confirmed"]
    assert "일치" in text
    assert "순이익" in text and "제외" in text
    # 순이익 차이를 「조정」으로 보고하면 안 된다
    assert "순이익 " not in text.split("·")[0]


def test_a_real_adjustment_is_reported_as_the_amount_that_moved() -> None:
    """두 값을 나란히 쓰면 반올림에 먹혀 「4.02조원 → 4.02조원」이 된다 — 움직인 크기를 쓴다."""
    facts = _facts(
        category="financial_statements", title="재무제표 승인의 건",
        eval_match=None, fin_payload=None, comp_payload=None,
        fy_raw_from_agenda=_prov(fy_current_total_equity_krw=4_024_572_786_265),
        confirmed_payload=_conf(total_equity_krw=4_018_786_224_502),
        confirmed_year=2025,
    )
    text = facts["fy_provisional_vs_confirmed"]
    assert "FY2025P" in text and "FY2025A" in text
    assert "조정" in text and "자본총계" in text
    assert "4.02조원 → 4.02조원" not in text


def test_nothing_is_claimed_when_the_report_is_not_out_yet() -> None:
    """사업보고서가 주총 뒤에 나오는 18%에서는 확정치가 없다 — 없는 것을 말하지 않는다."""
    facts = _facts(
        category="financial_statements", title="재무제표 승인의 건",
        eval_match=None, fin_payload=None, comp_payload=None,
        fy_raw_from_agenda=_prov(fy_current_revenue_krw=100),
        confirmed_payload=None, confirmed_year=None,
    )
    assert "fy_current_confirmed_year" not in facts
    assert "fy_provisional_vs_confirmed" not in facts
