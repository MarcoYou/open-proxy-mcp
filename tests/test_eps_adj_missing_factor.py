# -*- coding: utf-8 -*-
"""수정계수가 없을 때 **조용히 틀린 PER 을 내지 않는다**. network 0콜.

260823: 수정계수 파이프라인은 cron 없이 수동이라(private wiki 「이벤트/수동」) 7주 밀려 있었고,
그 사이 상장주식수가 크게 바뀐 종목 146개(2배 이상 62개)의 계수가 통째로 비었다.

계수가 없으면 `_eps_adj_factor` 가 1.0 을 돌려주고, 공시 EPS 조각
(FY0 · 당해 분기누적 · 전년동기누적)이 **옛 분모와 새 분모로 섞인 채** 조립된다.
메이슨캐피탈(021880, 10:1 액면병합) 실측 — TTM 지배순이익 **-70억**인데
EPS(TTM) **+39원**, PER(TTM) **32.31** 이 live 로 나갔다. 부호부터 뒤집혔다.

기존 sanity 가드는 이 경우 「DART 파싱오류 의심」이라고 **원인을 오진**하면서
PBR 만 무효화하고 PER 은 통과시켰다.

이제 불변식으로 잡는다 — 조정성 이벤트는 주가·EPS 와 주식수가 상쇄하므로 **계수 × 주식수배율 ≈ 1**.
"""
from __future__ import annotations

import inspect

import open_proxy_mcp.services.valuation as V


def test_invariant_band_admits_rights_issue_but_catches_merge():
    """유상증자(계수 대상 아님)는 통과, 액면병합은 잡힌다 — 밴드가 그 사이에 있어야 한다."""
    lo, hi = V._ADJ_INVARIANT_LO, V._ADJ_INVARIANT_HI
    assert lo <= 1.0 <= hi, "이벤트 없는 정상 종목(f=1, r=1)이 걸리면 안 된다"
    assert lo <= 1.0 * 1.3 <= hi, "30% 유상증자(r=1.3)는 계수 대상이 아니라 통과해야 한다"
    assert not (lo <= 1.0 * 0.1 <= hi), "10:1 병합(r=0.1)에 계수가 없으면 반드시 걸려야 한다"
    assert lo <= 10.0 * 0.1 <= hi, "계수(10)가 있으면 같은 병합도 통과해야 한다"


def test_per_is_invalidated_when_factor_missing():
    """탐지되면 PER 이 N/M 이 된다 — 값이 나가면 안 된다."""
    src = inspect.getsource(V._build_valuation_payload_impl)
    assert "eps_ok = not shares_unadjusted" in src
    assert "eps_fy > 0 and eps_ok" in src, "PER(FY0)이 계수 누락을 무시한다"
    assert "eps_ttm > 0 and eps_ok" in src, "PER(TTM)이 계수 누락을 무시한다"


def test_warning_no_longer_misdiagnoses_as_parsing_error():
    """계수 누락을 「DART 파싱오류」라고 안내하던 오진을 고쳤다."""
    src = inspect.getsource(V._build_valuation_payload_impl)
    i_unadj = src.index("if shares_unadjusted:")
    i_bad = src.index("elif shares_bad:")
    assert i_unadj < i_bad, "계수 누락 분기가 먼저 와야 파싱오류로 오진하지 않는다"
    assert "수정계수가 없습니다" in src


def test_shares_ratio_reads_krx_weekly_not_dart():
    """배율은 KRX 상장주식수(실측)에서 온다 — DART 유통주식수는 자기주식 제외라 개념이 다르다."""
    src = inspect.getsource(V._shares_ratio)
    assert "krx_weekly" in src and "list_shrs" in src
    assert "stockTotqySttus" not in src
