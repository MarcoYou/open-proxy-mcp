# -*- coding: utf-8 -*-
"""KRX 필드 사전이 **실제 API 와 어긋나지 않는가**. network 0콜(고정 표본으로 검증).

260823 신설. 거래소가 주는 이름을 그대로 우리 이름으로 쓰기로 했는데(원문 대조를 끊지 않으려고),
그러면 사전이 낡으면 그 규약이 조용히 깨진다. 공식 명세 페이지는 JS 렌더링이라 기계로 못 읽어서
실측(20260820 · KOSPI 942 · KOSDAQ 1,821)으로 의미를 검증했고, 그 검증을 여기 고정한다.
"""
from __future__ import annotations

import json
from pathlib import Path

REG = json.loads((Path(__file__).resolve().parent.parent
                  / "open_proxy_mcp" / "data" / "krx" / "field_registry.json").read_text(encoding="utf-8"))
RAW = {k: v for k, v in REG["raw"].items() if not k.startswith("_")}

# 260820 실측 응답의 필드 — 두 엔드포인트 동일
ACTUAL = {"BAS_DD", "ISU_CD", "ISU_NM", "MKT_NM", "SECT_TP_NM", "TDD_CLSPRC",
          "CMPPREVDD_PRC", "FLUC_RT", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC",
          "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS"}


def test_registry_covers_every_api_field():
    assert set(RAW) == ACTUAL, f"누락 {ACTUAL - set(RAW)} · 초과 {set(RAW) - ACTUAL}"


def test_every_field_has_korean_name_and_desc():
    for k, v in RAW.items():
        assert v.get("ko"), f"{k} 한글명 없음"
        assert v.get("desc"), f"{k} 설명 없음"
        assert v.get("unit"), f"{k} 단위 없음"


def test_stored_columns_map_to_real_api_fields():
    """저장 컬럼의 출처가 실재하는 API 필드여야 한다 — 오타 하나로 대조가 끊긴다."""
    for tbl, cols in REG["stored"].items():
        if tbl.startswith("_"):
            continue
        for col, src in cols.items():
            assert src in ACTUAL, f"{tbl}.{col} → '{src}' 는 API 필드가 아니다"


def test_derived_columns_declare_layer_and_formula():
    """파생은 **무엇에서 어떻게** 나왔는지 반드시 적는다 — 안 적으면 원본과 구분이 안 된다."""
    for k, v in REG["derived"].items():
        if k.startswith("_"):
            continue
        assert v.get("layer") in ("🟡", "🔴"), f"{k} layer 없음"
        assert v.get("from"), f"{k} 계산식(from) 없음"
        assert v.get("desc"), f"{k} 설명 없음"


def test_traps_record_the_zero_volume_and_open_price_pitfalls():
    """실측으로 잡은 함정 둘은 반드시 남아 있어야 한다."""
    blob = " ".join(REG["traps"])
    assert "무거래" in blob and "ACC_TRDVOL=0" in blob
    assert "시가 ≠ 기준가" in blob
    assert "자기주식 포함" in blob
