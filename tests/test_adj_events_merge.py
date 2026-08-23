# -*- coding: utf-8 -*-
"""수정계수 표 통합 — 측정과 라벨이 **한 표**여야 한다. network 0콜.

260823 이전: `krx_base_resets`(측정)와 `krx_adj_factor_v3`(라벨)이 별개였다. (isu_cd, 날짜)로
1:1 이고 `v3.raw_factor == base_resets.factor` 가 3,633/3,633 완전 동일 — 사실상 한 표를 둘로
쪼개 한쪽 값을 통째로 복사한 구조였다.

값이 아니라 **절차**가 문제였다. 갱신이 2단계라 뒤쪽만 깨져도 앞쪽이 돌면 멀쩡해 보인다.
실제로 260705 v2 드랍 때 「코드참조 0건 확인」이 틀려 라벨 스크립트가 그날부터 실행 불가였는데,
cron 이 없어 아무도 몰랐다. 게다가 그 스크립트는 `DELETE` 후 재생성이라 실행하면 라벨이
통째로 날아갔다(실제로 날렸고 git 이력의 CSV 로 복구).

이제 한 표이고 라벨 작업은 빈 칸 UPDATE 다 — 지울 것이 없다.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import open_proxy_mcp.services.valuation as V

ROOT = Path(__file__).resolve().parent.parent


def _code_only(fn) -> str:
    """docstring·주석의 이력 언급은 지우지 않는다 — **실행되는 코드**만 본다
    (tests/test_law_data_wiring.py 와 같은 관례)."""
    src = inspect.getsource(fn)
    body = src.split('"""')
    src = body[0] + "".join(body[2:]) if len(body) > 2 else src
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def test_serving_reads_the_merged_table():
    code = _code_only(V._eps_adj_factor)
    assert "krx_adj_events" in code
    assert "krx_adj_factor_v3" not in code, "옛 라벨 표로 되돌아갔다"
    assert "krx_base_resets" not in code


def test_serving_uses_the_applied_factor_not_the_raw_one():
    """`adj_factor`(보정 후)가 소비용이다 — `_raw`(실측)를 쓰면 액면비율 스냅이 빠진다."""
    code = _code_only(V._eps_adj_factor)
    assert "SELECT adj_factor FROM" in code
    assert "adj_factor_raw" not in code


def test_label_pass_is_update_only_never_delete():
    """**이 테스트가 사고의 재발을 막는다** — DELETE 후 재생성이면 실행 중 죽을 때 라벨이 날아간다."""
    src = (ROOT / "scripts" / "adj_factor_v3.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "DELETE FROM" not in code, "라벨 패스가 다시 지우고 시작한다"
    assert "UPDATE krx_adj_events" in code, "라벨 패스가 UPDATE 가 아니다"
    assert "INSERT INTO krx_adj_factor" not in code


def test_sweep_writes_measurement_and_leaves_labels_empty():
    """측정은 라벨 칸을 비워 두고 넣는다 — 그래야 미판정이 눈에 보인다."""
    src = (ROOT / "scripts" / "krx_base_resets.py").read_text(encoding="utf-8")
    assert "INSERT INTO krx_adj_events" in src
    assert "INSERT INTO krx_base_resets" not in src, "옛 표에 계속 쓴다"
    assert "'unlabeled'" in src


def test_flags_script_joins_the_merged_table():
    src = (ROOT / "scripts" / "krx_stock_flags.py").read_text(encoding="utf-8")
    assert "krx_adj_events" in src and "krx_base_resets" not in src
