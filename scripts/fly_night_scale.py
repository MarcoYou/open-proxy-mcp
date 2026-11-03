"""밤에는 상시 머신을 두지 않는다 — `min_machines_running` 을 KST 21:00~08:00 은 0, 그 밖은 1 로 바꾼다.

사용자 결정(261104): 밤 21시~아침 08시(KST)는 상시 가동 머신을 끄고, 그 사이 누가 부르면 그때 켠다.
fly 에는 시간대별 설정이 없어 예약 작업(`.github/workflows/fly-night-scale.yml`)이 매시 `--mode auto` 로
이 스크립트를 부른다 — GitHub 예약은 몇 시간씩 늦게 뜰 수 있어, 늦게 떠도 실행 시각 기준 값이 걸리게 한다.
재배포 없이 머신 관리 API(Machines API)로 각 `app` 머신 설정의 `services[].min_machines_running` 만 고친다.

- 밤(0): 한가하면 fly 프록시가 머신을 일시정지(suspend)하고, 요청이 오면 `autostart` 가 깨운다(메모리를 들고 자서
  깨는 데 보통 몇 초). 설정을 바꾸는 순간 켜져 있던 머신은 한 번 다시 뜬다(설정 교체).
- 낮(1): 설정을 되돌리고 머신 하나를 켠다 — `min_machines_running` 은 머신을 끄지 않게 할 뿐 꺼진 머신을 켜지 않는다.
- CI 배포는 `fly.toml` 값(1)을 다시 심는다. 그래서 배포 끝에 `--mode auto` 로 지금 시각에 맞는 값을 다시 건다.

토큰은 환경변수 `FLY_API_TOKEN` 에서만 읽고 어디에도 찍지 않는다(규칙 3).

    uv run python scripts/fly_night_scale.py --mode auto [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.machines.dev/v1"
APP = "open-proxy-mcp"
PROCESS_GROUP = "app"
KST = timezone(timedelta(hours=9))
NIGHT_START_HOUR = 21   # KST 21:00 부터
NIGHT_END_HOUR = 8      # KST 08:00 까지
NIGHT_MIN = 0
DAY_MIN = 1


def is_night(now: datetime) -> bool:
    """KST 21:00 이상 또는 08:00 미만이면 밤."""
    hour = now.astimezone(KST).hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def target_min(mode: str, now: datetime | None = None) -> int:
    if mode == "night":
        return NIGHT_MIN
    if mode == "day":
        return DAY_MIN
    return NIGHT_MIN if is_night(now or datetime.now(timezone.utc)) else DAY_MIN


def with_min(config: dict, value: int) -> tuple[dict, bool]:
    """머신 설정 사본의 모든 서비스에 `min_machines_running=value` 를 건다. (새 설정, 바뀌었는가)."""
    new = copy.deepcopy(config)
    changed = False
    for service in new.get("services") or []:
        if service.get("min_machines_running") != value:
            service["min_machines_running"] = value
            changed = True
    return new, changed


def _auth_header(token: str) -> str:
    # 배포용 토큰은 「FlyV1 …」 꼴로 온다 — 그대로 쓴다. 그 밖(개인 토큰)은 Bearer.
    return token if token.startswith("FlyV1 ") else f"Bearer {token}"


def _call(method: str, path: str, token: str, body: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": _auth_header(token), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as error:
        # 응답 본문만 싣는다 — 요청 머리(토큰)는 싣지 않는다.
        detail = error.read().decode(errors="replace")[:300]
        raise SystemExit(f"Machines API {method} {path} -> HTTP {error.code}: {detail}") from None
    return json.loads(raw) if raw else None


def plan(machines: list[dict], value: int) -> list[dict]:
    """바꿀 머신과 방법 — app 그룹만. 낮에는 첫 머신을 켜고(skip_launch=False), 나머지는 상태 그대로 둔다."""
    app = sorted((m for m in machines if (m.get("config") or {}).get("metadata", {}).get("fly_process_group") == PROCESS_GROUP),
                 key=lambda m: m.get("id") or "")
    steps = []
    for index, machine in enumerate(app):
        config, changed = with_min(machine.get("config") or {}, value)
        start = value > 0 and index == 0
        if not changed and not (start and machine.get("state") != "started"):
            continue
        # 켜져 있지 않은 머신은 설정만 바꾸고 켜지 않는다(밤에 잠든 머신을 깨우지 않게). 낮의 첫 머신은 켠다.
        steps.append({"id": machine["id"], "state": machine.get("state"), "config": config,
                      "skip_launch": not start and machine.get("state") != "started"})
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("auto", "night", "day"), default="auto")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("FLY_API_TOKEN", "").strip()
    if not token:
        print("FLY_API_TOKEN 이 없다", file=sys.stderr)
        return 2
    value = target_min(args.mode)
    machines = _call("GET", f"/apps/{APP}/machines", token) or []
    steps = plan(machines, value)
    print(f"mode={args.mode} → min_machines_running={value} · app 머신 {sum(1 for _ in machines)}대 중 바꿀 것 {len(steps)}대")
    for step in steps:
        print(f"- {step['id']} (state={step['state']}) skip_launch={step['skip_launch']}")
        if not args.dry_run:
            _call("POST", f"/apps/{APP}/machines/{step['id']}", token,
                  {"config": step["config"], "skip_launch": step["skip_launch"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
