"""밤 상시 머신 끄기(사용자 결정 261104) — KST 21:00~08:00 은 min 0, 그 밖은 1. 네트워크 없이 판단만 시험한다."""
from datetime import datetime, timezone
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "fly_night_scale.py"
_spec = importlib.util.spec_from_file_location("fly_night_scale", _PATH)
fns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fns)


def _utc(h, m=0):
    return datetime(2026, 11, 4, h, m, tzinfo=timezone.utc)


def test_night_window_is_kst_21_to_08():
    assert fns.is_night(_utc(12, 0))        # KST 21:00
    assert fns.is_night(_utc(22, 59))       # KST 07:59
    assert not fns.is_night(_utc(23, 0))    # KST 08:00
    assert not fns.is_night(_utc(11, 59))   # KST 20:59
    assert fns.target_min("auto", _utc(15)) == 0 and fns.target_min("auto", _utc(3)) == 1
    assert fns.target_min("night") == 0 and fns.target_min("day") == 1


def _machine(mid, state, minimum=1, group="app"):
    return {"id": mid, "state": state,
            "config": {"metadata": {"fly_process_group": group},
                       "services": [{"internal_port": 8000, "min_machines_running": minimum, "autostart": True}]}}


def test_night_plan_sets_zero_without_waking_suspended_machines():
    steps = fns.plan([_machine("b", "suspended"), _machine("a", "started"), _machine("x", "started", group="worker")], 0)
    assert [s["id"] for s in steps] == ["a", "b"]
    assert all(s["config"]["services"][0]["min_machines_running"] == 0 for s in steps)
    assert {s["id"]: s["skip_launch"] for s in steps} == {"a": False, "b": True}


def test_day_plan_restores_one_and_starts_the_first_machine():
    steps = fns.plan([_machine("a", "suspended", 0), _machine("b", "stopped", 0)], 1)
    assert {s["id"]: s["skip_launch"] for s in steps} == {"a": False, "b": True}
    # 이미 낮 설정이고 첫 머신이 켜져 있으면 할 일이 없다.
    assert fns.plan([_machine("a", "started", 1), _machine("b", "suspended", 1)], 1) == []


def test_auth_header_keeps_deploy_token_shape():
    assert fns._auth_header("FlyV1 abc") == "FlyV1 abc"
    assert fns._auth_header("xyz") == "Bearer xyz"


def test_workflows_wire_the_schedule():
    root = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
    night = (root / "fly-night-scale.yml").read_text(encoding="utf-8")
    assert "'0 12 * * *'" in night and "'0 23 * * *'" in night and "group: fly-night-scale" in night
    assert "scripts/fly_night_scale.py --mode auto" in (root / "deploy.yml").read_text(encoding="utf-8")
