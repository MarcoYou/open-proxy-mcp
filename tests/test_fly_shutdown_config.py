"""배포 때 내려가는 머신이 진행 중인 도구 호출을 끝낼 시간 (261019). network 0.

fly 의 기본 종료 대기(kill_timeout)는 5초다. uvicorn 은 종료 신호를 받으면 진행 중 요청이 끝나기를 기다리지만, 5초가 지나면 fly 가
강제 종료해 그보다 오래 남은 호출이 끊긴다 — 운영 표본(회귀 기준선 141콜)의 8%가 5초를 넘었다(최장 44초). 클라이언트가 열어 둔
GET 대기 스트림은 신호 즉시 닫혀 종료를 붙들지 않는다(로컬 실측: 진행 중 호출이 없으면 0.2초에 종료) — 그래서 이 값을 늘려도
한가할 때의 배포는 늦어지지 않는다.
"""
from __future__ import annotations

import pathlib
import re

FLY = (pathlib.Path(__file__).resolve().parent.parent / "fly.toml").read_text(encoding="utf-8")


def _seconds(v: str) -> int:
    m = re.fullmatch(r"'?(\d+)(s?)'?", v.strip())
    assert m, f"kill_timeout 형식을 모른다: {v}"
    return int(m.group(1))


def test_kill_timeout_outlasts_long_tool_calls():
    m = re.search(r"^kill_timeout\s*=\s*(.+)$", FLY, re.M)
    assert m, "fly.toml 에 kill_timeout 이 없다 — fly 기본 5초면 배포 때 5초 넘게 남은 도구 호출이 끊긴다"
    assert 60 <= _seconds(m.group(1)) <= 300, "회귀 표본 최장 호출(44초)보다 길고, 배포가 한없이 늘지 않게 300초를 넘지 않는다"


def test_kill_timeout_is_top_level_not_inside_a_section():
    """[http_service] 같은 절 안에 두면 fly 가 앱 설정으로 읽지 않는다 — 첫 절 머리글보다 앞에 있어야 한다."""
    first_section = re.search(r"^\[", FLY, re.M).start()
    assert FLY.find("kill_timeout") < first_section
