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
    assert bug and "어긋납니다" in bug and "0.03배" in bug, bug


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
