"""산출물에 엔진 내부 용어가 새지 않는다 — 래칫.

wiki 「산출물 표기 규칙」은 예전부터 있었는데 **강제 장치가 없어서** 지켜지지 않았다. 260808 전수
스윕에서 127건이 나왔고, 그중엔 「퇴직금 raw 5건 detect」·「독립성/결격사유 모두 clean」·「재무
reference: FY2024」처럼 사용자가 실제로 읽은 것들이 있다.

여기서 잡는 건 「용어를 잘못 골랐다」가 아니라 **어휘 경계가 없다**는 구조다 — facts 라벨 사전(93개)을
거치는 필드는 유출이 0인데, 코드가 한글 문장을 직접 쓰는 자리(판정 사유·표 헤더·유의사항)에서만 샌다.

**래칫**: 남은 건수를 파일별로 박아두고 늘면 실패한다. 127건을 다 고치기 전에도 재발이 멈추고,
고칠 때마다 baseline 을 낮추라고 알려주므로 진척이 숫자로 보인다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from output_vocab_lint import BANNED, check, load_baseline, scan_all  # noqa: E402


def test_no_new_engine_terms_in_user_facing_text() -> None:
    """새 사유 문장에 코드 용어가 들어가면 그 자리에서 막는다.

    실패하면 메시지가 어느 파일인지 알려준다. 고쳤으면 baseline 을 낮추고, 새로 넣었으면
    문장을 한국어로 고친다 — baseline 을 올려서 통과시키는 것은 규칙을 지운다는 뜻이다.
    """
    problems = check()
    assert not problems, "\n".join(["산출물 어휘 래칫 위반:", *problems])


def test_baseline_only_shrinks() -> None:
    """baseline 총계가 최초 측정(127)을 넘지 않는다 — 전체가 늘지 않았음을 한 줄로 보장."""
    assert sum(load_baseline().values()) <= 127


def test_the_lint_actually_catches_a_leak() -> None:
    """게이트가 살아 있는지 — 통과만 하고 아무것도 안 잡는 lint 를 방지한다."""
    hits = scan_all()
    assert hits, "유출을 하나도 못 찾았다면 스캐너가 죽은 것이다"
    caught = {t for v in hits.values() for _, _, bad in v for t in bad}
    assert caught & BANNED


def test_lint_runs_standalone() -> None:
    """CI·/ship 에서 스크립트로 직접 돌 수 있어야 한다."""
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(
        [sys.executable, "scripts/output_vocab_lint.py"],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "새는 엔진 용어" in r.stdout
