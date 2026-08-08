# -*- coding: utf-8 -*-
"""소집공고 본문 잠정 재무제표의 매출 행 매칭. network 0콜.

260729 사용자 지적: LG화학 1호 안건 재무 사실란이 「당기 매출액 1조 6,468억」인데
연결 매출은 48.9조였다. 원문을 보니 파서가 「Ⅳ. 기타영업수익 1,646,811」을 잡고 있었다.

원인 둘:
  ① 계정 매칭이 **부분 포함**이라 「기타영업수익」이 「영업수익」에 걸렸다
  ② 「Ⅰ. 매출」(액 없음)이 키워드에 없어 진짜 매출 행을 못 잡았다
"""
from __future__ import annotations

from open_proxy_mcp.services.provisional_financial_statement import (
    _METRIC_KEYWORDS,
    _account_matches,
    _strip_item_marker,
)

_REV = tuple(k.replace(" ", "") for k in _METRIC_KEYWORDS["revenue_krw"])


def _matches(account: str) -> bool:
    """**production 함수를 그대로 부른다** — 재구현하면 테스트가 다른 것을 검증한다."""
    a = _strip_item_marker(account.replace(" ", ""))
    return _account_matches(a, _METRIC_KEYWORDS["revenue_krw"], "revenue_krw")


def test_item_marker_is_stripped():
    assert _strip_item_marker("Ⅰ.매출") == "매출"
    assert _strip_item_marker("1.매출액") == "매출액"
    assert _strip_item_marker("(1)매출") == "매출"
    assert _strip_item_marker("자산총계") == "자산총계"


def test_real_revenue_rows_match():
    # 캐시 소집공고 479건 실측 표기: 매출액 1001 · 매출 474 · 영업수익 135
    for a in ("Ⅰ. 매출", "매출액", "1. 매출액", "수익(매출액)", "영업수익",
              "Ⅰ. 영업수익", "1. 보험영업수익"):
        assert _matches(a), a


def test_other_income_rows_do_not_match():
    """「기타~」는 매출이 아니다 — 캐시에 기타수익 541·기타매출 97·기타영업수익 10건."""
    for a in ("Ⅳ. 기타영업수익", "기타수익", "기타매출", "기타매출액", "12. 기타영업수익"):
        assert not _matches(a), a


def test_insurance_revenue_is_kept():
    """보험사는 「보험영업수익」이 매출이다 — 접두 매칭이라 명시하지 않으면 소실된다
    (260729 회귀에서 흥국화재·코리안리가 사라졌다)."""
    assert _matches("1. 보험영업수익")
    assert "보험영업수익" in _REV


def test_non_revenue_rows_never_match():
    for a in ("매출원가", "매출총이익", "판매비와 관리비", "자산총계", "부채총계"):
        assert not _matches(a), a


def test_cross_check_catches_the_mis_parsed_revenue():
    """소집공고는 사업보고서보다 먼저 나오므로 **본문 전기 = API 당기**다 — 서로 검산이 된다.

    260729 실측: 파서가 「Ⅳ. 기타영업수익」을 매출로 잡았을 때 이 비율이 0.03 이었다.
    정상 20사에서는 14곳이 비율 1.00, 오탐 0건.
    """
    from open_proxy_mcp.services.proxy_advise import _cross_check_provisional_revenue as chk
    api = {"revenue_krw": 48_916_104_000_000}          # LG화학 FY2024 확정치
    ok = chk({"fy_prior_revenue_krw": 48_699_754_000_000}, api)
    assert ok and "일치" in ok, ok
    bug = chk({"fy_prior_revenue_krw": 1_480_020_000_000}, api)   # 기타영업수익을 잡았을 때
    assert bug and "확인하세요" in bug and "0.03배" in bug, bug


def test_cross_check_covers_operating_profit_too():
    """실측 18사에서 영업이익도 17곳이 1.00±5% — 매출과 같이 본다."""
    from open_proxy_mcp.services.proxy_advise import _cross_check_provisional_revenue as chk
    api = {"revenue_krw": 48_916_104_000_000, "operating_profit_krw": 916_798_000_000}
    ok = chk({"fy_prior_revenue_krw": 48_699_754_000_000,
              "fy_prior_operating_profit_krw": 874_927_000_000}, api)
    assert "매출·영업이익" in ok and "일치" in ok, ok
    # 영업이익만 어긋나도 잡는다
    bad = chk({"fy_prior_revenue_krw": 48_699_754_000_000,
               "fy_prior_operating_profit_krw": 90_000_000_000}, api)
    assert "영업이익" in bad and "확인하세요" in bad, bad


def test_cross_check_skips_net_income():
    """순이익은 본문=총·API=지배주주 귀속이라 개념이 다르다 — 실측 -0.75~22.69배.
    하이브는 매출·영업이익이 1.00 인데 순이익만 22.69배였다. 검산에 쓰면 오탐이다."""
    from open_proxy_mcp.services.proxy_advise import _CROSS_CHECK_ITEMS
    assert not any("net_income" in k for k, _, _ in _CROSS_CHECK_ITEMS), _CROSS_CHECK_ITEMS


def test_cross_check_ignores_small_amounts():
    """절대액이 작으면 비율이 흔들린다 — 남광토건 영업이익 43억 vs 73억(0.60배)은
    오파싱이 아니라 감사 전/후 조정이다."""
    from open_proxy_mcp.services.proxy_advise import _cross_check_provisional_revenue as chk
    out = chk({"fy_prior_operating_profit_krw": 4_300_000_000},
              {"operating_profit_krw": 7_300_000_000})
    assert out is None, out


def test_cross_check_stays_silent_without_both_sides():
    from open_proxy_mcp.services.proxy_advise import _cross_check_provisional_revenue as chk
    assert chk(None, {"revenue_krw": 1}) is None
    assert chk({"fy_prior_revenue_krw": 1}, None) is None
    assert chk({}, {"revenue_krw": 1}) is None
    assert chk({"fy_prior_revenue_krw": 1}, {}) is None


def test_cross_check_tolerates_audit_adjustment():
    """감사 전/후 조정으로 몇 % 어긋나는 것은 정상이다(남광토건 실측 1.09배)."""
    from open_proxy_mcp.services.proxy_advise import _cross_check_provisional_revenue as chk
    for r in (0.75, 0.9, 1.0, 1.09, 1.35):
        out = chk({"fy_prior_revenue_krw": int(1e12 * r)}, {"revenue_krw": int(1e12)})
        assert "일치" in out, (r, out)


def test_cross_check_assumes_fin_year_is_two_years_back():
    """검산의 성립 조건 — API 쪽 회계연도가 `target_year - 2` 여야 「본문 전기 = API 당기」다.

    260729 사용자 지적: 「주총공고 시점에도 두 데이터가 다 있느냐」.
    확인 결과 소집공고 시점에 API 는 FY(N-2) 를 갖고 있다(1년 전 제출분). 그리고 도구가
    의도적으로 FY(N-2) 를 고르므로 사업보고서가 나온 뒤에 돌려도 값이 안 바뀐다.
    이 선택이 「최신 사업보고서」로 바뀌면 검산이 조용히 무너지므로 소스로 계약을 잡는다.
    """
    import inspect
    from open_proxy_mcp.services import proxy_advise as pa
    src = inspect.getsource(pa.build_proxy_advise_payload)
    assert "fin_year = target_year - 2" in src, (
        "fin_year 선택이 바뀌었다 — _cross_check_provisional_revenue 의 "
        "「본문 전기 = API 당기」 전제가 깨졌는지 확인하라"
    )


def test_a_loss_making_company_is_not_skipped() -> None:
    """적자 회사는 본문이 「당기순손실」·「영업손실」이라 쓴다.

    이익형 키워드만 두면 손익계산서 행을 통째로 놓치고, 재무상태표의 「지배기업 소유주지분」
    (자본)이 대신 걸린다 — 영풍 2026 실측: 순이익이 366억 대신 3조 6,027억으로 들어갔다.
    """
    from open_proxy_mcp.services.provisional_financial_statement import (
        _METRIC_KEYWORDS, _account_matches,
    )
    ni = _METRIC_KEYWORDS["net_income_krw"]
    op = _METRIC_KEYWORDS["operating_profit_krw"]
    assert _account_matches("당기순손실", ni, "net_income_krw", "income_statement")
    assert _account_matches("영업손실", op, "operating_profit_krw", "income_statement")


def test_the_same_account_name_means_different_things_in_two_statements() -> None:
    """「지배기업 소유주지분」 — 손익계산서면 순이익 귀속, 재무상태표면 지배주주 자본이다."""
    from open_proxy_mcp.services.provisional_financial_statement import (
        _METRIC_KEYWORDS, _account_matches,
    )
    ni = _METRIC_KEYWORDS["net_income_krw"]
    assert _account_matches("지배기업소유주지분", ni, "net_income_krw", "income_statement")
    assert not _account_matches("지배기업소유주지분", ni, "net_income_krw", "balance_sheet")


def test_fvpl_financial_instruments_are_not_net_income() -> None:
    """「당기손익-공정가치측정 금융자산」은 K-IFRS 1109호의 **금융상품 분류 명칭**이다.

    접두 매칭이라 「당기손익」을 키워드에 두면 이것들이 전부 순이익으로 잡힌다. 금융사는 이 계정이
    당기순이익 행보다 위에 와서 먼저 매칭되고, 값의 자릿수가 그럴듯해 사람도 못 잡는다.
    캐시 소집공고 197건 실측: 482행이 이렇게 걸리고 있었다.
    """
    from open_proxy_mcp.services.provisional_financial_statement import (
        _METRIC_KEYWORDS, _NET_INCOME_ACCOUNT_OK, _account_matches,
    )
    ni = _METRIC_KEYWORDS["net_income_krw"]
    for bad in ("당기손익-공정가치측정금융자산", "당기손익-공정가치측정금융부채",
                "당기손익공정가치측정금융상품관련이익", "당기손익으로재분류되지않는항목"):
        assert not _account_matches(bad, ni, "net_income_krw", "income_statement"), bad
        assert not _NET_INCOME_ACCOUNT_OK.match(bad), bad


def test_the_source_gate_is_the_backstop_not_the_keyword_list() -> None:
    """사전은 새 계정명이 나올 때마다 구멍이 생긴다 — 출처로 한 번 더 거른다.

    **틀린 값이 빈 값보다 나쁘다.** 순이익 계열이 아니면 버리고 「확인 못 했다」로 간다.
    """
    from open_proxy_mcp.services.provisional_financial_statement import _NET_INCOME_ACCOUNT_OK as ok
    for good in ("당기순이익", "당기순손실", "당기순이익(손실)", "연결당기순이익",
                 "지배기업소유주지분", "지배지분순이익"):
        assert ok.match(good), good
    for bad in ("기타포괄손익", "당기손익", "매출총이익", "영업이익"):
        assert not ok.match(bad), bad


def test_the_source_gate_reads_the_same_form_the_matcher_did() -> None:
    """게이트가 항목번호를 떼지 않으면 매칭을 통과한 정당한 값을 도로 지운다.

    실측 48사 중 24건(영풍·POSCO홀딩스·HD현대·삼성물산·롯데케미칼 등)의 순이익이 「Ⅶ.당기순손실」·
    「XI. 당기순이익」처럼 항목번호가 붙었다는 이유로 사라졌다.
    """
    from open_proxy_mcp.services.provisional_financial_statement import (
        _NET_INCOME_ACCOUNT_OK as ok, _strip_item_marker as strip,
    )
    for numbered in ("Ⅶ.당기순손실", "XI. 당기순이익", "Ⅵ. 당기순이익(손실)", "VIII. 당기순이익"):
        assert ok.match(strip(numbered.replace(" ", ""))), numbered
    # 마커를 떼도 금융상품 계정은 여전히 걸러진다
    assert not ok.match(strip("2. 당기손익-공정가치측정금융상품관련이익".replace(" ", "")))


def test_foreign_currency_is_refused_not_silently_read_as_won() -> None:
    """외화 표시 재무제표를 원으로 읽으면 자릿수가 통째로 틀린다.

    실측 두산밥캣(「USD천」) — 매출이 618만원으로 나갔다(실제 6,181,806 USD천 ≈ 9조원).
    조용히 계수 1을 쓰면 검산도 통과한다(외화끼리는 자산=부채+자본이 맞고 순이익<매출도 성립).
    **환산 근거가 본문에 없으면 값을 내지 않는다.**
    """
    from open_proxy_mcp.services.provisional_financial_statement import _scale_factor
    for foreign in ("USD", "USD천", "단위: USD", "달러", "JPY백만", "EUR", "(단위:$)"):
        assert _scale_factor(foreign) is None, foreign


def test_billion_won_is_not_read_as_hundred_million() -> None:
    """「십억원」이 「억원」에 걸리면 1/10 로 환산된다 — 더 긴 단위부터 본다."""
    from open_proxy_mcp.services.provisional_financial_statement import _scale_factor
    assert _scale_factor("십억원") == 1_000_000_000
    assert _scale_factor("억원") == 100_000_000
    assert _scale_factor("백만원") == 1_000_000
    assert _scale_factor("천원") == 1_000
    assert _scale_factor("단위 : 원") == 1
