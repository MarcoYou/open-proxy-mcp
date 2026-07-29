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
