# -*- coding: utf-8 -*-
"""asset_holdings 금융업 판정 — 일반 지주회사(KSIC 64992)를 금융업으로 가르던 결함의 회귀. network 0.

운영 실측(261018): SK·LG(업종코드 64992) 가 `is_financial` true → 「금융업 — 배수 미제공」, 지분 NAV 배수 null.
KSIC 는 금융지주와 일반지주를 같은 64992 에 넣는다. `financial_metrics` 는 이미 260713 에 이 둘을 연결 BS 계정으로
가르는 규칙을 검증해 두었다 — 이 tool 도 그 규칙을 그대로 쓴다(공유 파생지표 재사용).
호출부(빌드 수준 — `is_financial`·금융업 경고·일반 지주회사 배수)는 `test_asset_holdings_listed_mark.py` 의
LG(64992, 금융 계정 없음)·신한지주형(64992 + 예수부채) 사례가 막는다 — 여기 헬퍼만 시험하면 호출부를 옛 업종코드
앞 두 자리 판정으로 되돌려도 아무것도 실패하지 않았다(261018 검토).
"""
from __future__ import annotations

from open_proxy_mcp.services import asset_holdings as A


def _bs(*names):
    return [{"sj_div": "BS", "account_nm": n, "thstrm_amount": "1"} for n in names]


def test_general_holding_company_is_not_financial():
    # SK·LG — 연결 BS 에 금융업 고유 계정이 없다
    assert A._is_financial("64992", _bs("현금및현금성자산", "관계기업및공동기업투자", "차입금")) is False


def test_financial_holding_company_is_caught_by_balance_sheet():
    # 신한지주·KB금융 — 같은 64992 지만 은행 자회사 연결로 예수부채가 뜬다(띄어쓰기 변형도)
    assert A._is_financial("64992", _bs("현금및현금성자산", "예수 부채")) is True
    assert A._is_financial("64992", _bs("보험계약부채")) is True


def test_ksic_financial_codes_still_financial_without_bs_signal():
    # 카드·캐피탈처럼 예수부채가 없는 금융사는 업종코드로 잡는다(64913 등). 보험 65·증권 66 도 그대로.
    assert A._is_financial("64913", _bs("현금및현금성자산")) is True
    assert A._is_financial("65110", []) is True
    assert A._is_financial("66121", []) is True


def test_non_financial_codes_and_income_statement_rows_do_not_trip():
    assert A._is_financial("467", _bs("현금및현금성자산")) is False
    assert A._is_financial("", []) is False
    # 손익계산서 줄의 같은 낱말은 신호가 아니다(재무상태표만 본다)
    assert A._is_financial("467", [{"sj_div": "IS", "account_nm": "예수부채이자비용"}]) is False


def test_same_rule_as_financial_metrics():
    """규칙의 정본은 financial_metrics 다 — 두 tool 이 같은 회사를 다르게 가르면 안 된다."""
    from open_proxy_mcp.services.financial_metrics import _ksic_is_financial
    for code in ("64992", "64913", "65110", "66121", "467", "6419", ""):
        assert A._is_financial(code, []) is _ksic_is_financial(code)
