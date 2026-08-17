# -*- coding: utf-8 -*-
"""커밋된 법령 corpus 가 **온전한가** — 조문 수·번호로는 안 보이는 자리. network 0콜.

260817: 원문 소스가 자본시장법·공정거래법 법률에서 목(가./나./다.)을 통째로 잃은
채 갱신됐고, 주간 배치의 게이트 셋이 전부 통과시켰다(조문 수 137→137 · SSOT 는
조문 *번호*만 대조 · 공포일자는 더 최신). 판정 로직이 인용하는 조문의 하위 요건이
사라져도 응답 모양은 평소와 같아 밖에서 안 보인다.

주간 배치에도 같은 게이트가 있지만, 여기서도 본다 — 손으로 corpus 를 만지는 경로
(수동 재복사·부분 반영·머지 사고)는 배치를 안 타기 때문이다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_committed_corpus_has_no_missing_subitems():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_law_corpus_integrity.py")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"corpus 온전성 검사 실패:\n{r.stdout}\n{r.stderr}"


def test_gate_actually_catches_missing_subitems(tmp_path):
    """**게이트가 살아 있나** — 검사기가 조용히 무력해지면 위 테스트는 늘 통과한다."""
    law = tmp_path / "kr" / "독점규제및공정거래에관한법률"
    law.mkdir(parents=True)
    (law / "법률.md").write_text(
        "\n".join(
            f'  {i}\\. "용어{i}"란 다음 각 목의 어느 하나에 해당하는 것을 말한다.'
            for i in range(1, 11)
        ),
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_law_corpus_integrity.py"),
         "--corpus", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1, f"목이 전부 빠졌는데 통과시켰다:\n{r.stdout}"
    assert "목" in r.stdout
