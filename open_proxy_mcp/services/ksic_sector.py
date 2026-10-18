"""KSIC 하이브리드 섹터 버킷 — 종목의 DART 업종코드(`induty`)를 OPM 자체 섹터 코드로 접는다 (서버 쪽 정의).

261018(Supabase 재설계 6단계): 종목별 밸류에이션 스냅샷 표를 읽을 때 계산으로 바꾸면서, 그 표의 `sector` 칸(이 버킷 코드)을
서버가 직접 만들어야 하게 됐다. 규칙은 주간 밸류에이션 배치(`scripts/market_val_weekly.py` 의 `bucket`)와 **같아야 한다** —
같은 JSON(`data/ksic/opm_sector_map.json`)을 읽고 같은 순서로 판정한다. 둘이 갈리지 않는지는
`tests/test_opm_val_firm_compute.py` 가 KSIC 코드 전부로 대조한다.

판정 순서(배치와 같다)
  1. 종목 단위 오버라이드(`code_overrides`) — KSIC 와 투자 관점이 크게 어긋나는 최소 사례(예: 삼성전자 264 → 261).
  2. 세세분류 프리픽스 재배정(`prefix_reassign`) — 예: 29271(반도체 장비) → 261.
  3. `level3_prefixes` 에 든 중분류(26·64·66·21·28·20)는 앞 3자리, 나머지는 앞 2자리.
업종코드가 비었거나 수집 실패 표지(`none`·`err`)면 None — 배치도 그 종목의 섹터를 비워 둔다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_MAP = Path(__file__).resolve().parents[1] / "data" / "ksic" / "opm_sector_map.json"


@lru_cache(maxsize=1)
def _rules() -> tuple[frozenset, dict, dict]:
    m = json.loads(_MAP.read_text(encoding="utf-8"))
    return frozenset(m["level3_prefixes"]), dict(m.get("code_overrides", {})), dict(m.get("prefix_reassign", {}))


def ksic_bucket(induty: str | None, ticker: str | None = None) -> str | None:
    """`induty`(DART 업종코드) → KSIC 하이브리드 섹터 코드. 업종코드가 없거나 `none`·`err` 면 None."""
    if not induty or induty in ("none", "err"):
        return None
    l3, overrides, reassign = _rules()
    if ticker and ticker in overrides:
        return overrides[ticker]
    for pref, dest in reassign.items():
        if induty.startswith(pref):
            return dest
    return induty[:3] if induty[:2] in l3 else induty[:2]
