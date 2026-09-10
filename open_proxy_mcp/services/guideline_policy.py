"""Small, deterministic evaluator for the OPM guideline policy source.

The policy source is intentionally narrower than the existing proxy decision
engine.  It records which candidate rules can be evaluated from the facts
already available to the tool, and keeps missing evidence explicit.  It does
not turn an unknown into a negative fact and it does not submit a ballot.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


_POLICY_CACHE: dict[str, Any] | None = None
_UNKNOWN = object()


def load_guideline_policy() -> dict[str, Any] | None:
    """Load the versioned OPM v2 policy source shipped with the package."""
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    try:
        path = files("open_proxy_mcp.data.guideline") / "opm-guideline-v2.json"
        _POLICY_CACHE = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _POLICY_CACHE = None
    return _POLICY_CACHE


def clear_guideline_policy_cache() -> None:
    global _POLICY_CACHE
    _POLICY_CACHE = None


def load_pilot_guideline_policy() -> dict[str, Any]:
    """Load the unreviewed LLM pilot separately from the synthetic shadow policy."""
    path = files("open_proxy_mcp.data.guideline") / "opm-guideline-v2-pilot.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _value(expr: Any, metrics: dict[str, Any], parameters: dict[str, Any]) -> Any:
    if not isinstance(expr, dict):
        return _UNKNOWN
    if "const" in expr:
        return expr["const"]
    if "literal" in expr:
        return expr["literal"]
    if "metric" in expr:
        return metrics.get(expr["metric"], _UNKNOWN)
    if "parameter" in expr:
        item = parameters.get(expr["parameter"], _UNKNOWN)
        if isinstance(item, dict):
            return item.get("value", item.get("default", _UNKNOWN))
        return item
    return _UNKNOWN


def _compare(expr: Any, metrics: dict[str, Any], parameters: dict[str, Any]) -> bool | None:
    """Evaluate the tiny expression vocabulary and preserve unknown values."""
    if not isinstance(expr, dict):
        return None
    if expr.get("const") is not None:
        return bool(expr["const"])
    op = expr.get("op")
    if not op:
        return None
    left = _value(expr.get("left"), metrics, parameters)
    right = _value(expr.get("right"), metrics, parameters)
    if left is _UNKNOWN or right is _UNKNOWN or left is None or right is None:
        return None
    try:
        if op == "eq":
            return left == right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
    except (TypeError, ValueError):
        return None
    return None


def _missing(expr: Any, metrics: dict[str, Any], parameters: dict[str, Any]) -> list[str]:
    if not isinstance(expr, dict):
        return []
    out: list[str] = []
    if "metric" in expr and (expr["metric"] not in metrics or metrics[expr["metric"]] is None):
        out.append(f"metric:{expr['metric']}")
    if "parameter" in expr and expr["parameter"] not in parameters:
        out.append(f"parameter:{expr['parameter']}")
    for key in ("left", "right"):
        out.extend(_missing(expr.get(key), metrics, parameters))
    return list(dict.fromkeys(out))


def evaluate_guideline_policy(
    policy: dict[str, Any],
    metrics: dict[str, Any],
    *,
    applicable: bool = True,
) -> dict[str, Any]:
    """Return a trace suitable for a proxy advice payload.

    ``fired_effects`` is advisory in this release.  The caller must keep the
    current decision engine authoritative until a separately approved policy
    adapter exists.
    """
    if not applicable:
        return {
            "status": "not_applicable",
            "policy_id": policy.get("id"),
            "version": policy.get("version"),
            "rule_results": [],
            "fired_effects": [],
            "unresolved": [],
            "gate": "not_applicable",
        }

    parameters = policy.get("parameters") or {}
    rules: list[dict[str, Any]] = []
    fired: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for rule in policy.get("rules") or []:
        applies = _compare(rule.get("applies_when"), metrics, parameters)
        test = _compare(rule.get("test"), metrics, parameters)
        exception = _compare(rule.get("exception"), metrics, parameters)
        missing = list(dict.fromkeys(
            _missing(rule.get("applies_when"), metrics, parameters)
            + _missing(rule.get("test"), metrics, parameters)
            + _missing(rule.get("exception"), metrics, parameters)
        ))
        if applies is False:
            state = "not_triggered"
        elif applies is None:
            state = "unresolved"
        elif test is False:
            # An exception is irrelevant when the opposition trigger is false.
            state = "not_triggered"
        elif test is None or exception is None:
            state = "unresolved"
        elif test and not exception:
            state = "fired"
            fired.append({"rule_id": rule.get("id"), "effect": rule.get("effect")})
        elif test and exception:
            state = "excepted"
        else:
            state = "not_triggered"
        result = {"rule_id": rule.get("id"), "state": state, "effect": rule.get("effect")}
        if missing and state == "unresolved":
            result["missing"] = missing
        rules.append(result)
        if state == "unresolved":
            unresolved.append({"rule_id": rule.get("id"), "missing": missing})

    gate = _compare(policy.get("positive_gate"), metrics, parameters)
    gate_state = "pass" if gate is True else "fail" if gate is False else "unresolved"
    return {
        "status": "evaluated",
        "policy_id": policy.get("id"),
        "version": policy.get("version"),
        "rule_results": rules,
        "fired_effects": fired,
        "unresolved": unresolved,
        "gate": gate_state,
        "metrics_present": sorted(k for k, v in metrics.items() if v is not None),
        "metrics_missing": sorted(k for k, v in metrics.items() if v is None),
    }
