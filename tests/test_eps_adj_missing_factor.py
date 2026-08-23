# -*- coding: utf-8 -*-
"""배수는 **시총 기반**이라 액면분할·병합에 흔들리지 않는다. network 0콜.

260823 오전: `주가 ÷ EPS` 였다. 공시 EPS 조각(FY0 · 당해 분기누적 · 전년동기누적)을 수정계수로
현재 주식수 기준에 정렬해 조립하는데, **계수가 없으면 옛 분모와 새 분모가 섞였다**
(메이슨캐피탈 021880, 10:1 액면병합 — TTM 지배순이익 -70억인데 EPS(TTM) +39원, PER 32.31 이
live 로 나갔다). 계수 파이프라인은 cron 없이 수동이라 7주 밀려 있었고, 실측 4.1%(116/2,797)가
그 영향권이었다.

260823 오후: 계수를 지키는 대신 **분모에서 주식수를 뺐다.** PER = 보통주 시총 ÷ 지배순이익.
주식수가 분자·분모에서 상쇄돼 조정성 이벤트에 불변이고, scope=market/sector/firm_history
스냅샷과 정의가 같아졌다(종전에는 같은 `per_ttm` 이름으로 서로 다른 지표가 나갔다).

EPS·BPS 는 회사 공시 공식값이라 인풋으로 계속 노출하되 배수 산출에는 쓰지 않는다.
"""
from __future__ import annotations

import inspect

import open_proxy_mcp.services.valuation as V
import open_proxy_mcp.tools.valuation as T


def test_multiples_are_cap_based_not_per_share():
    """배수 분모에 주식수가 들어가면 안 된다 — 그게 분할·병합에 깨지던 원인이었다."""
    src = inspect.getsource(V._build_valuation_payload_impl)
    assert "per_ttm = nm(_div(cap, ni_ttm)" in src, "PER(TTM)이 시총 기반이 아니다"
    assert "per_fy = nm(_div(cap, ni_fy)" in src, "PER(FY0)이 시총 기반이 아니다"
    assert "pbr = nm(_div(cap, ctrl_equity)" in src, "PBR 이 시총 기반이 아니다"
    assert "_div(price, eps_ttm)" not in src, "주가÷EPS 로 되돌아갔다"
    assert "_div(price, bps)" not in src, "주가÷BPS 로 되돌아갔다"


def test_basis_is_declared_in_payload():
    """기계 소비자가 정의를 알 수 있어야 한다 — 이름만으로는 두 정의가 구분되지 않는다."""
    src = inspect.getsource(V._build_valuation_payload_impl)
    assert '"multiples_basis": "common_mktcap_over_controlling_income"' in src


def test_basis_is_visible_to_the_reader():
    """사람이 보는 계산식에도 기준이 그대로 나와야 한다(사용자 요구)."""
    src = inspect.getsource(T._render_explain_firm)
    assert "보통주 시총 ÷ 지배순이익(TTM)" in src
    assert "보통주 시총 ÷ 지배자본(MRQ)" in src
    assert "우선주 편향" in src, "대가(우선주 편향)를 밝히지 않는다"
    assert "가중평균이 아닙니다" in src, "대가(가중평균 아님)를 밝히지 않는다"


def test_share_change_still_warns_about_eps_input():
    """배수는 안전해졌지만 EPS 인풋은 여전히 섞일 수 있다 — 그 사실은 계속 알린다."""
    src = inspect.getsource(V._build_valuation_payload_impl)
    assert "shares_unadjusted" in src
    i_unadj = src.index("if shares_unadjusted:")
    i_bad = src.index("elif shares_bad:")
    assert i_unadj < i_bad, "계수 누락을 「DART 파싱오류」로 오진하던 순서로 되돌아갔다"
    assert "주당 비교에는 쓰지 마세요" in src


def test_shares_ratio_reads_krx_weekly_not_dart():
    """배율은 KRX 상장주식수(실측)에서 온다 — DART 유통주식수는 자기주식 제외라 개념이 다르다."""
    src = inspect.getsource(V._shares_ratio)
    assert "krx_weekly" in src and "list_shrs" in src
    assert "stockTotqySttus" not in src
