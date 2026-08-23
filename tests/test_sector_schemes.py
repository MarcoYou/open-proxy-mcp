# -*- coding: utf-8 -*-
"""섹터 분류 축이 둘 이상 — **섞이면 안 된다**. network 0콜.

260823: KSIC 하이브리드 하나뿐이던 집계에 WICS(WiseIndex)를 더했다. 같은 표
(`opm_val_market`)에 `scheme` 으로 나눠 담는데, `sector != '_ALL'` 만으로 거르던 기존
질의가 **WICS 행까지 집어와** 섹터 표가 중복될 뻔했다(같은 종목이 두 버킷에 잡힌다).

당시엔 우연히 안 섞였다 — WICS 백필이 20260731 까지고 최신 snap_dd 는 20260821 이라
`MAX(snap_dd)` 가 KSIC 만 골랐다. **다음 배치가 8월분을 채우면 바로 섞이는** 상태였다.
오늘 하루 반복해서 본 형태 그대로다: 지금 안 터진다고 안전한 게 아니다.
"""
from __future__ import annotations

import inspect

import open_proxy_mcp.services.valuation as V


def test_every_market_aggregate_query_pins_a_scheme():
    """`opm_val_market` 을 읽는 곳은 **전부** scheme 을 건다 — 하나라도 빠지면 축이 섞인다."""
    src = inspect.getsource(V)
    lines = src.split("\n")
    bad = []
    for i, line in enumerate(lines):
        # 실제 SQL 만 — 주석·경고 문구의 표 이름 언급은 오탐이다
        if "opm_val_market" not in line or line.lstrip().startswith("#"):
            continue
        if not any(k in line for k in ("FROM ", "INTO ", "JOIN ")):
            continue
        if "scheme" not in "\n".join(lines[i:i + 4]):
            bad.append(line.strip()[:90])
    assert not bad, f"scheme 을 안 건 질의: {bad}"


def test_scheme_whitelist_and_descriptions():
    assert set(V._SECTOR_SCHEMES) == {"ksic", "wics_sector", "wics_industry"}
    for k, desc in V._SECTOR_SCHEMES.items():
        assert desc, f"{k} 설명 없음"


def test_unknown_scheme_is_rejected_not_silently_defaulted():
    """모르는 값을 조용히 ksic 으로 떨어뜨리면 사용자는 다른 축을 봤다고 믿는다."""
    src = inspect.getsource(V.build_sector_val_payload)
    assert "if scheme not in _SECTOR_SCHEMES" in src
    assert '"status": "invalid"' in src


def test_wics_company_sector_comes_from_wise_sector_with_fallback():
    """WICS 는 종목 섹터가 opm_val_firm 이 아니라 wise_sector 에서 온다.
    과거 날짜는 관측이 없어 **가장 가까운 스냅샷으로 폴백**한다(소급) — 그 사실을 남긴다."""
    src = inspect.getsource(V.build_sector_val_payload)
    assert "FROM wise_sector WHERE ticker=%s" in src
    assert "snap_dd <= %s" in src, "폴백 순서(과거 우선)가 없다"
    assert "sector_asof" in src, "소급 여부를 payload 에 안 남긴다"
