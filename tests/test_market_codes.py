# -*- coding: utf-8 -*-
"""시장 코드 — DB 는 `KS`/`KQ`, 화면은 `KOSPI`/`KOSDAQ`. network 0콜.

260823: `mkt`(6개 표) → `market` 으로 통일하며 값도 줄였다(141만 행). 이 전환의 위험은
**에러가 아니라 0건**이다 — `WHERE market='KOSPI'` 가 남아 있으면 조용히 빈 결과를 낸다.
실제로 screener 의 시장 필터가 그 상태였고(실측: 옛 리터럴로 0건, 새 코드로 942/1,822건),
valuation 렌더는 payload 키가 `market` 인데 `mkt` 을 읽어 시장·섹터 표가 통째로 비었다.

변환은 `open_proxy_mcp/market_codes.py` 한 곳에서만 한다.
"""
from __future__ import annotations

import pathlib
import re

from open_proxy_mcp.market_codes import KQ, KS, to_db, to_label

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round_trip():
    assert to_db("KOSPI") == KS and to_db("KOSDAQ") == KQ
    assert to_label(KS) == "KOSPI" and to_label(KQ) == "KOSDAQ"
    assert to_db(KS) == KS, "이미 코드인 값은 그대로 통과해야 한다(재적재 멱등)"
    assert to_label("KONEX") == "KONEX", "모르는 값은 삼키지 말고 그대로 보낸다"


def test_no_sql_compares_market_against_the_old_labels():
    """**이 테스트가 조용한 0건을 막는다.**"""
    bad = []
    for d in ("open_proxy_mcp", "scripts"):
        for p in (ROOT / d).rglob("*.py"):
            # 감사 스크립트(scripts/*_audit.py)는 DB 가 아니라 저장된 JSON 을 읽는다.
            if p.name == "market_codes.py" or p.name.endswith("_audit.py"):
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                if re.search(r"market\s*=\s*['\"]KOS(PI|DAQ)['\"]", line) or \
                   re.search(r"market['\"]\]\s*==\s*['\"]KOS(PI|DAQ)['\"]", line):
                    bad.append(f"{p.relative_to(ROOT)}:{i}")
    assert not bad, f"DB 값과 안 맞는 리터럴 비교: {bad}"


def test_no_table_uses_the_old_column_names():
    """옛 컬럼명이 SQL 에 남으면 UndefinedColumn 으로 죽는다 — 여러 줄 SQL 의 이어지는 줄을
    놓치기 쉬웠다(실측: `AND b.isu_cd IS NULL` 이 살아남아 딱지 배치가 죽었다)."""
    bad = []
    for d in ("open_proxy_mcp", "scripts"):
        for p in (ROOT / d).rglob("*.py"):
            txt = p.read_text(encoding="utf-8")
            for m in re.finditer(r"\b([a-z]+\.)?(isu_cd|bas_dd|chg_dd)\b", txt):
                line = txt[:m.start()].count("\n") + 1
                bad.append(f"{p.relative_to(ROOT)}:{line} {m.group(0)}")
    assert not bad, f"옛 컬럼명 잔존: {bad[:10]}"
