#!/usr/bin/env python3
"""live-opm ↔ pilot-opm 차이 추적.

둘은 목적이 다른 별개 대상이고(→ wiki/architecture/mcp-endpoints.md), 전송방식이 같아서
**남는 차이는 코드 시점 하나**다. 그 하나를 항상 눈에 보이게 만드는 것이 이 스크립트다.

무엇을 어디서 읽나
  live  = 배포된 커밋. deploy.yml 이 배포마다 GitHub Deployment(ref=github.sha)를 남기므로
          그것이 권위 있는 출처다. tool 개수는 live `/health` 에서 읽는다(키 불필요).
  pilot = 지금 워킹트리. HEAD + 미커밋 변경. 떠 있으면 pilot `/health` 도 읽는다.

원격이 안 되면(오프라인·gh 미인증) 조용히 "확인 불가"로 낮추고 로컬 정보만 낸다 —
추적기가 작업을 막아서는 안 된다.

사용:
  python3 scripts/live_pilot_diff.py          # 사람이 읽는 요약
  python3 scripts/live_pilot_diff.py --json   # 기계용
  python3 scripts/live_pilot_diff.py --quiet  # 차이가 없으면 아무것도 안 냄 (훅용)
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "MarcoYou/open-proxy-mcp"
LIVE_HEALTH = "https://open-proxy-mcp.fly.dev/health"
PILOT_HEALTH = "http://127.0.0.1:8000/health"
CODE_PREFIXES = ("open_proxy_mcp/", "scripts/", "tests/", "pyproject.toml", "fly.toml")


def sh(*args: str) -> str:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except Exception:
        return ""


def get_json(url: str, timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def deployed() -> dict:
    """마지막으로 '성공한' 배포의 커밋. 실패한 배포는 live 가 아니다."""
    raw = sh(
        "gh", "api",
        f"repos/{REPO}/deployments?environment=production&per_page=10",
        "--jq", ".[] | {id, sha, created_at} | tostring",
    )
    for line in raw.splitlines():
        try:
            d = json.loads(line.replace("'", '"'))
        except ValueError:
            continue
        st = sh("gh", "api", f"repos/{REPO}/deployments/{d['id']}/statuses",
                "--jq", ".[0].state")
        if st == "success":
            return {"sha": d["sha"], "at": d["created_at"], "state": "success"}
        if st:                      # 최신이 실패/진행중이면 그 사실을 알린다
            return {"sha": d["sha"], "at": d["created_at"], "state": st}
    return {"sha": None, "at": None, "state": "확인 불가"}


def collect() -> dict:
    head = sh("git", "rev-parse", "HEAD")
    dirty = [
        ln[3:] for ln in sh("git", "status", "--porcelain").splitlines() if ln
    ]
    dep = deployed()

    ahead: list[str] = []
    if dep["sha"]:
        log = sh("git", "log", "--oneline", f"{dep['sha']}..HEAD")
        ahead = [ln for ln in log.splitlines() if ln]

    live_h = get_json(LIVE_HEALTH, 8)
    pilot_h = get_json(PILOT_HEALTH, 2)

    return {
        "live": {
            "sha": dep["sha"], "at": dep["at"], "state": dep["state"],
            "tools": (live_h or {}).get("tools"), "reachable": live_h is not None,
        },
        "pilot": {
            "sha": head, "running": pilot_h is not None,
            "tools": (pilot_h or {}).get("tools"),
        },
        "diff": {
            "undeployed_commits": ahead,
            "dirty_code": [f for f in dirty if f.startswith(CODE_PREFIXES)],
            "dirty_other": [f for f in dirty if not f.startswith(CODE_PREFIXES)],
        },
    }


def render(s: dict) -> list[str]:
    live, pilot, diff = s["live"], s["pilot"], s["diff"]
    out = ["━━ live ↔ pilot ━━"]

    sha = (live["sha"] or "????????")[:8]
    tools = f"tools={live['tools']}" if live["tools"] is not None else "tools=?"
    mark = "" if live["state"] == "success" else f"  ⚠ 배포상태={live['state']}"
    when = (live["at"] or "")[:16].replace("T", " ")
    out.append(f"  live   {sha}  {tools}  ({when}){mark}")

    run = f"tools={pilot['tools']}" if pilot["running"] else "안 떠 있음"
    out.append(f"  pilot  {pilot['sha'][:8]}  {run}")

    same_sha = live["sha"] and live["sha"] == pilot["sha"]
    n = len(diff["undeployed_commits"]) + len(diff["dirty_code"])
    if same_sha and n == 0 and not diff["dirty_other"]:
        out.append("  → 차이 없음 (live 와 워킹트리가 같은 코드)")
        return out

    out.append("━━ 차이 (pilot 에만 있고 live 에는 없는 것) ━━")
    if diff["undeployed_commits"]:
        out.append(f"  배포 안 된 커밋 {len(diff['undeployed_commits'])}개:")
        out += [f"    {c}" for c in diff["undeployed_commits"][:8]]
    if diff["dirty_code"]:
        out.append(f"  미커밋 코드 {len(diff['dirty_code'])}개:  ← 동작이 달라지는 쪽")
        out += [f"    {f}" for f in diff["dirty_code"][:8]]
    if diff["dirty_other"]:
        out.append(f"  미커밋 기타 {len(diff['dirty_other'])}개 (문서·설정)")

    if (live["tools"] is not None and pilot["tools"] is not None
            and live["tools"] != pilot["tools"]):
        out.append(f"  ⚠ tool 개수가 다름: live={live['tools']} pilot={pilot['tools']}"
                   " — tool 추가/제거가 아직 배포 안 됨")
    return out


def main() -> int:
    argv = sys.argv[1:]
    s = collect()
    if "--json" in argv:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    d = s["diff"]
    if "--quiet" in argv and not (
        d["undeployed_commits"] or d["dirty_code"] or d["dirty_other"]
    ):
        return 0
    print("\n".join(render(s)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
