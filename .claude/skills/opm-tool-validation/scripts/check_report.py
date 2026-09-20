#!/usr/bin/env python3
"""Validate evidence aggregation only; never run collection, tests, or deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

PROFILE_STAGES = {
    "collection": {"harness", "collection"},
    "parser": {"harness", "corpus", "parser", "mcp", "output"},
    "mcp": {"harness", "mcp"},
    "output": {"harness", "mcp", "output"},
    "docs": {"output"},
    "consumer": {"harness", "mcp", "output", "consumer"},
    "skill": {"harness"},
}
STAGES = set().union(*PROFILE_STAGES.values()) | {"pilot", "live"}
STATUSES = {"PASS", "FAIL", "REVIEW", "BLOCKED", "NOT_RUN", "SKIP"}
MAX_BYTES = 16 * 1024 * 1024


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _keys(value, expected):
    return isinstance(value, dict) and set(value) == set(expected)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonfinite number")


def _read(path):
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("file unavailable or too large")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("empty file or too large")
    return raw


def _valid_schema(report):
    if not _keys(report, {"schema_version", "profiles", "claim", "limitations", "stages"}):
        return False
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        return False
    profiles = report["profiles"]
    if (not isinstance(profiles, list) or not profiles
            or any(not isinstance(p, str) or p not in PROFILE_STAGES for p in profiles)
            or len(set(profiles)) != len(profiles)):
        return False
    if not isinstance(report["claim"], str) or report["claim"] not in {"offline", "pilot", "live"}:
        return False
    if not isinstance(report["limitations"], list) or not all(_text(v) for v in report["limitations"]):
        return False
    stages = report["stages"]
    if not isinstance(stages, list) or not stages:
        return False
    seen = set()
    for stage in stages:
        if not _keys(stage, {"id", "status", "scope", "reason", "evidence"}):
            return False
        stage_id, status = stage["id"], stage["status"]
        if not isinstance(stage_id, str) or stage_id not in STAGES or stage_id in seen:
            return False
        seen.add(stage_id)
        if not isinstance(status, str) or status not in STATUSES or not _text(stage["scope"]):
            return False
        if not isinstance(stage["reason"], str) or (status != "PASS" and not _text(stage["reason"])):
            return False
        if not isinstance(stage["evidence"], list):
            return False
        for item in stage["evidence"]:
            if not _keys(item, {"path", "sha256"}) or not _text(item["path"]):
                return False
            if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                return False
    return True


def check_report(path):
    """Return (exit_code, safe summary). Never echo input text, paths, or exceptions."""
    try:
        path = Path(path).resolve()
        report = json.loads(_read(path), object_pairs_hook=_pairs, parse_constant=_constant)
        if not _valid_schema(report):
            raise ValueError("schema")
    except (OSError, ValueError, UnicodeError, RecursionError, RuntimeError):
        return 2, {"status": "BLOCKED", "issues": ["REPORT_INVALID"]}

    required = set().union(*(PROFILE_STAGES[p] for p in report["profiles"]))
    if report["claim"] in {"pilot", "live"}:
        required.add("pilot")
    if report["claim"] == "live":
        required.add("live")
    stages = {stage["id"]: stage for stage in report["stages"]}
    issues = [f"MISSING_STAGE:{stage_id}" for stage_id in sorted(required - stages.keys())]
    for stage_id, stage in sorted(stages.items()):
        status = stage["status"]
        if (stage_id in required and status != "PASS") or status in {"FAIL", "REVIEW", "BLOCKED"}:
            issues.append(f"STAGE_NOT_PASS:{stage_id}")
        if status == "PASS" and not stage["evidence"]:
            issues.append(f"EVIDENCE_MISSING:{stage_id}")
        seen_paths = set()
        for item in stage["evidence"]:
            try:
                evidence_path = (path.parent / item["path"]).resolve()
                if evidence_path == path or evidence_path in seen_paths:
                    raise ValueError("self reference or duplicate evidence")
                seen_paths.add(evidence_path)
                if hashlib.sha256(_read(evidence_path)).hexdigest() != item["sha256"]:
                    raise ValueError("hash mismatch")
            except (OSError, ValueError, RuntimeError):
                issues.append(f"EVIDENCE_INVALID:{stage_id}")
    return (1 if issues else 0), {
        "status": "BLOCKED" if issues else "PASS",
        "profiles": report["profiles"], "claim": report["claim"], "issues": issues,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    code, result = check_report(args.report)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
