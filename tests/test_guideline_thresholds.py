# -*- coding: utf-8 -*-
"""가이드라인 수치 임계값이 **데이터에서 온다**. network 0콜.

260814 신설. 종전에는 소진율 30%·인상률 50%·배당성향 200% 같은 정책 수치가
`_decide_*` 함수 8곳에 매직넘버로 박혀 있었고, 정책 문서(open-proxy-guideline.md)가
같은 숫자를 산문으로 따로 서술해 **손으로 동기화**했다 — 문서를 20%로 고쳐도
코드는 30으로 돌았다.

법령 40룰(`data/laws/law_layer_rules.json`)은 이미 데이터인데 자체 가이드라인만
코드에 있던 비대칭을 해소한다.
"""
from __future__ import annotations

import inspect
import json
import re
from importlib.resources import files

import pytest

import open_proxy_mcp.services.proxy_advise as PA

_DECIDERS = [
    PA._decide_dividend, PA._decide_director_election, PA._decide_director_compensation,
    PA._decide_audit_compensation, PA._decide_retirement_pay,
]
#: 정책이 정한 값. 부호 판정(0)·인덱스는 정책이 아니라 산수라 제외한다.
_POLICY_NUMBERS = {"2", "3", "5", "6", "10", "30", "50", "100", "200"}


def test_no_policy_magic_numbers_left_in_deciders():
    """판정 함수가 정책 수치를 직접 들고 있으면 문서와 갈라진다."""
    left = []
    for fn in _DECIDERS:
        for line in inspect.getsource(fn).splitlines():
            t = line.strip()
            if t.startswith("#") or "_th(" in t:
                continue
            for m in re.finditer(r"(\w+)\s*(<=|>=|<|>)\s*(-?\d+)", t):
                if m.group(3).lstrip("-") in _POLICY_NUMBERS:
                    left.append(f"{fn.__name__}: {t[:70]}")
    assert not left, "정책 임계값이 코드에 남아 있다:\n  " + "\n  ".join(sorted(set(left)))


def test_json_and_fallback_agree():
    """폴백은 파일을 못 읽을 때의 안전망이지 **두 번째 장부가 아니다.**
    둘이 갈리면 어느 쪽이 진짜인지 아무도 모른다."""
    raw = json.loads((files("open_proxy_mcp.data.guideline")
                      / "guideline_thresholds.json").read_text(encoding="utf-8"))
    doc = raw["thresholds"]
    for cat, keys in PA._THRESHOLD_FALLBACK.items():
        for key, expected in keys.items():
            entry = doc.get(cat, {}).get(key)
            assert entry is not None, f"{cat}.{key} 가 JSON 에 없다"
            got = entry["value"] if isinstance(entry, dict) else entry
            assert got == expected, f"{cat}.{key}: JSON {got} ≠ 코드 폴백 {expected}"


def test_every_threshold_documents_itself():
    """숫자만 있으면 6개월 뒤에 왜 30인지 아무도 모른다."""
    raw = json.loads((files("open_proxy_mcp.data.guideline")
                      / "guideline_thresholds.json").read_text(encoding="utf-8"))
    for cat, block in raw["thresholds"].items():
        for key, entry in block.items():
            if key.startswith("_"):
                continue
            assert isinstance(entry, dict), f"{cat}.{key} 가 설명 없는 숫자다"
            assert entry.get("meaning"), f"{cat}.{key} 에 meaning 이 없다"
            assert entry.get("used_as"), f"{cat}.{key} 에 used_as(비교식)가 없다"


@pytest.mark.parametrize("cat,key,expected", [
    ("director_compensation", "utilization_low_pct", 30),
    ("director_compensation", "increase_high_pct", 50),
    ("cash_dividend", "payout_ratio_high_pct", 200),
    ("director_election", "long_tenure_years", 6),
    ("retirement_pay", "multiplier_high", 3),
])
def test_known_thresholds_are_stable(cat, key, expected):
    """값이 바뀌면 판정이 바뀐다 — 의도한 변경이면 이 테스트를 같이 고칠 것."""
    assert PA._th(cat, key) == expected


def test_loader_falls_back_loudly(monkeypatch, caplog):
    """파일이 깨져도 판정이 조용히 달라지면 안 된다."""
    monkeypatch.setattr(PA, "_GUIDELINE_THRESHOLDS_CACHE", None)
    monkeypatch.setattr(PA, "files", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level("ERROR"):
        got = PA._thresholds()
    assert got["director_compensation"]["utilization_low_pct"] == 30, "폴백이 안 걸렸다"
    assert any("임계값" in r.message for r in caplog.records), "실패를 조용히 삼켰다"
    monkeypatch.setattr(PA, "_GUIDELINE_THRESHOLDS_CACHE", None)
