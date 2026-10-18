"""law_lookup 근거 목록(evidence_refs) 순서 — 결과에 처음 나온 순서(순위)대로, 프로세스가 바뀌어도 같다 (261018). network 0.

종전엔 법 이름 집합(set)을 돌아 문자열 해시 씨앗(PYTHONHASHSEED)에 따라 프로세스마다 순서가 바뀌었다 — 실측 「자기주식 처분」:
씨앗 1 상법·자본시장법·공정거래법 / 씨앗 2 상법·공정거래법·자본시장법 / 씨앗 3 자본시장법·공정거래법·상법. 같은 질의가 머신·재시작마다
다른 응답이 됐다(141건 회귀 비교의 유일한 비시각 노이즈). 씨앗을 바꾼 하위 프로세스 셋으로 확인한다.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

_PROBE = """
import json, sys
sys.path.insert(0, {root!r})
from open_proxy_mcp.services.law_lookup import build_law_lookup_payload
p = build_law_lookup_payload("자기주식 처분")
print(json.dumps({{"results": [r["law"] for r in p["data"]["results"]],
                  "evidence": [e["evidence_id"] for e in p["evidence_refs"]]}}, ensure_ascii=False))
"""


def _run(seed: str) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", _PROBE.format(root=str(ROOT))], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=120, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_evidence_follows_result_rank_whatever_the_hash_seed():
    runs = [_run(seed) for seed in ("1", "2", "3")]
    first = runs[0]
    laws_by_rank = list(dict.fromkeys(first["results"]))
    assert len(laws_by_rank) >= 3, "법이 셋 이상 섞이는 질의여야 순서를 가린다"
    assert first["evidence"] == [f"law_corpus:{law}" for law in laws_by_rank]
    assert all(r == first for r in runs), runs
