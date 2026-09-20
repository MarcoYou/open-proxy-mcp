#!/usr/bin/env python3
"""Offline candidate validation and private, append-only promotion records.

This promotes only a named evaluation scope, never a release or service response.
Trusted local code only: the evaluator's socket guard is not an OS sandbox.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import sysconfig
import tempfile
import uuid

HERE = Path(__file__).resolve()
EVALUATOR = HERE.with_name("parser_feedback.py")
FIELDS = ("name", "birthDate", "roleType")
REGISTRY = {"personnel_candidates": {"fields": FIELDS}}
DOCS = ("docs/PARSER_FEEDBACK.md", "wiki/tools/shareholder_meeting_notice.md")
DOC_CHECKS = (("scripts/gen_index.py", "--check"),
              ("scripts/wiki_lint.py", "--strict"),
              ("scripts/check_documentation_contract.py",))
POLICY_KEYS = {"min_real_development_cases", "min_real_holdout_cases",
               "min_real_holdout_companies", "min_positive_holdout_per_field"}
WORKER_ENV = {"PATH": os.defpath, "LANG": "C.UTF-8", "TZ": "UTC"}
TERM_SURFACE = re.compile(rb"termRaw|termDetails|term_details|parse_appointment_term")
SKIP = {"__pycache__", ".git", ".pytest_cache"}


class Blocked(ValueError):
    """Only fixed machine codes may cross the CLI/log boundary."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise Blocked("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def read_json(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Blocked("INVALID_JSON")))


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Blocked("INVALID_SCHEMA")


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise Blocked("INVALID_PRIVATE_PATH")
    parts = Path(relative).parts
    if ".." in parts:
        raise Blocked("INVALID_PRIVATE_PATH")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise Blocked("SYMLINK_FORBIDDEN")
    if not path.resolve().is_relative_to(root):
        raise Blocked("INVALID_PRIVATE_PATH")
    return path


def private_root(value):
    original = Path(value).absolute()
    if original.is_symlink():
        raise Blocked("SYMLINK_FORBIDDEN")
    root = original.resolve(strict=True)
    if (not root.is_dir() or root in {Path(root.anchor), Path.home(), Path(tempfile.gettempdir()).resolve()}
            or "raw" in root.parts or root.stat().st_uid != os.getuid()
            or stat.S_IMODE(root.stat().st_mode) & 0o077
            or any((p / ".git").exists() for p in (root, *root.parents))):
        raise Blocked("PRIVATE_ROOT_REQUIRED")
    return root


@contextmanager
def lock(root):
    path = inside(root, ".pipeline.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def inventory(root, *, environment=False):
    """Hash actual bytes, not just versions/RECORD claims; never follow code symlinks."""
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(p in SKIP for p in rel.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            if not environment or path.is_dir():
                raise Blocked("SYMLINK_DEPENDENCY")
        if path.is_file():
            result[rel.as_posix()] = file_hash(path)
    return result


def environment_identity():
    # Include the stdlib, native extensions, installed package/data bytes and .pth
    # files. Site packages may be outside stdlib in a virtual environment.
    roots = sorted({Path(sysconfig.get_path(k)).resolve()
                    for k in ("stdlib", "platstdlib", "purelib", "platlib")})
    roots = [p for p in roots if not any(p != q and p.is_relative_to(q) for q in roots)]
    return {"python": sys.version, "executable_sha256": file_hash(sys.executable),
            "platform": sys.platform, "worker_env": WORKER_ENV,
            "runtime_trees": {str(p): digest(encoded(inventory(p, environment=True))) for p in roots}}


def code_identity(root):
    root = Path(root).resolve(strict=True)
    parser = root / "open_proxy_mcp/services/shareholder_meeting_parser.py"
    if not parser.is_file():
        raise Blocked("PARSER_MISSING")
    files = {}
    for directory in ("open_proxy_mcp", "scripts"):
        path = root / directory
        if path.is_symlink():
            raise Blocked("SYMLINK_DEPENDENCY")
        if path.exists():
            files.update({f"{directory}/{k}": v for k, v in inventory(path).items()})
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise Blocked("ENVIRONMENT_CONTRACT_MISSING")
        files[name] = file_hash(path)
    # Source content identifies dirty worktrees too. No git mutation is needed.
    return {"root": str(root), "files": files, "sha256": digest(encoded(files))}


def has_term_output(identity):
    # Conservative scope boundary, including a term-capable baseline. A caller
    # cannot make terms "validated" by choosing the lab as both baseline/candidate.
    for name in identity["files"]:
        if not name.startswith("open_proxy_mcp/") or not name.endswith(".py"):
            continue
        if name.endswith("/personnel_term.py"):
            return True
        if TERM_SURFACE.search((Path(identity["root"]) / name).read_bytes()):
            return True
    return False


def docs_identity(root):
    # These are the actual inputs of the existing documentation checks, including
    # untracked docs. Private archive/raw/corpus trees are deliberately excluded.
    files = {}
    for directory in ("docs", "wiki"):
        base = root / directory
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(root)
            if any(p in {"raw", "corpus", "archive", "anecdotes"} for p in rel.parts):
                continue
            inside(root, rel.as_posix())
            files[rel.as_posix()] = file_hash(path)
    for path in sorted(root.glob("README*.md")):
        inside(root, path.name)
        files[path.name] = file_hash(path)
    return files


def load_config(root, config_path):
    config = read_json(inside(root, config_path))
    if not isinstance(config, dict):
        raise Blocked("INVALID_SCHEMA")
    exact({k: v for k, v in config.items() if k != "input_root"},
                  {"schema_version", "data_type", "manifest", "cache_dir", "review",
                   "baseline_code_root", "candidate_code_roots", "max_attempts", "policy", "documents"})
    if "input_root" in config:
        value = config["input_root"]
        if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).is_symlink():
            raise Blocked("INVALID_INPUT_ROOT")
        if not Path(value).resolve(strict=True).is_dir():
            raise Blocked("INVALID_INPUT_ROOT")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise Blocked("INVALID_SCHEMA")
    if not isinstance(config["data_type"], str) or config["data_type"] not in REGISTRY:
        raise Blocked("UNSUPPORTED_DATA_TYPE")
    count = config["max_attempts"]
    candidates = config["candidate_code_roots"]
    if (type(count) is not int or not 1 <= count <= 10 or not isinstance(candidates, list)
            or not 1 <= len(candidates) <= 10):
        raise Blocked("INVALID_ATTEMPT_BUDGET")
    for value in [config["baseline_code_root"], *candidates]:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise Blocked("ABSOLUTE_CODE_ROOT_REQUIRED")
        code = Path(value).resolve(strict=True)
        if root.is_relative_to(code) or code.is_relative_to(root):
            raise Blocked("CODE_AND_PRIVATE_ROOT_OVERLAP")
    exact(config["policy"], POLICY_KEYS)
    if any(type(v) is not int or v < 1 for v in config["policy"].values()):
        raise Blocked("POSITIVE_SUFFICIENCY_REQUIRED")
    exact(config["documents"], DOCS)
    if not all(is_hash(v) for v in config["documents"].values()):
        raise Blocked("DOCUMENT_HASH_REQUIRED")
    for key in ("manifest", "cache_dir", "review"):
        path = input_path(root, config, key)
        if ((key != "review" and not path.exists()) or path.is_relative_to(root / "runs")
                or path in {root, root / "last_good.json", root / ".pipeline.lock"}):
            raise Blocked("INVALID_INPUT_PATH")
    return config


def input_path(root, config, key):
    # A separately declared source root is read-only, so existing private-storage
    # corpora need not be copied into the output root (which must be outside Git).
    return inside(Path(config.get("input_root", root)).resolve(), config[key])


def inputs_identity(root, config):
    cache = input_path(root, config, "cache_dir")
    review = input_path(root, config, "review")
    return {"manifest": file_hash(input_path(root, config, "manifest")),
            "review": file_hash(review) if review.is_file() else None,
            "documents": inventory(cache)}


def corpus_gate(root, config):
    manifest = read_json(input_path(root, config, "manifest"))
    review_path = input_path(root, config, "review")
    if not review_path.is_file():
        raise Blocked("REVIEW_REQUIRED")
    review = read_json(review_path)
    exact(review, {"schema_version", "manifest_sha256", "cases"})
    if (type(review["schema_version"]) is not int or review["schema_version"] != 1
            or review["manifest_sha256"] != file_hash(input_path(root, config, "manifest"))
            or not isinstance(review["cases"], dict)
            or set(review["cases"]) != {c["id"] for c in manifest["cases"]}):
        raise Blocked("REVIEW_NOT_BOUND_TO_GOLD")
    counts = {"real_development_cases": 0, "real_holdout_cases": 0}
    groups = set()
    positive = dict.fromkeys(FIELDS, 0)
    for case in manifest["cases"]:
        record = review["cases"][case["id"]]
        exact(record, {"sample_type", "reviewer", "independent", "holdout_unseen"})
        if (record["sample_type"] not in ("real", "synthetic") or record["independent"] is not True
                or type(record["holdout_unseen"]) is not bool
                or not isinstance(record["reviewer"], str) or not record["reviewer"].strip()
                or record["reviewer"].strip() == case["review"]["reviewer"].strip()):
            raise Blocked("INDEPENDENT_REVIEW_REQUIRED")
        if record["sample_type"] == "real":
            counts[f"real_{case['split']}_cases"] += 1
            if case["split"] == "holdout":
                if not record["holdout_unseen"]:
                    raise Blocked("HOLDOUT_ALREADY_USED")
                groups.add(case["company_group"])
                for field in FIELDS:
                    positive[field] += int(any(isinstance(c[field], str) and c[field].strip()
                                               for c in case["expected"]))
    counts["real_holdout_companies"] = len(groups)
    policy = config["policy"]
    if (any(counts[k] < policy[f"min_{k}"] for k in counts)
            or any(v < policy["min_positive_holdout_per_field"] for v in positive.values())):
        raise Blocked("CORPUS_INSUFFICIENT")
    return {**counts, "positive_holdout_cases": positive, "policy": policy,
            "review_is_attestation_not_verified_independence": True}


def worker(arguments):
    spec = importlib.util.spec_from_file_location("pipeline_evaluator", EVALUATOR)
    feedback = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feedback)
    try:
        mode, root, *rest = arguments
        if mode == "evaluate":
            manifest, cache, baseline = rest
            report = feedback.evaluate(manifest, cache, baseline or None, code_root=root)
            # Reports are from a freshly executed evaluator, never supplied by caller.
            print(json.dumps(report, ensure_ascii=True, allow_nan=False))
            return 0 if report["status"] == "pass" else 1
        if mode == "docs":
            results = []
            for command in DOC_CHECKS:
                with feedback._offline() as network, feedback._quiet_parser():
                    sys.path[:0] = [str(Path(root) / "scripts"), root]
                    sys.argv = [str(Path(root) / command[0]), *command[1:]]
                    try:
                        runpy.run_path(sys.argv[0], run_name="__main__")
                        code = 0
                    except SystemExit as exc:
                        code = exc.code or 0
                results.append({"check": command[0], "passed": code == 0 and not network["attempts"]})
            print(json.dumps({"checks": results}))
            return 0 if all(r["passed"] for r in results) else 1
        raise Blocked("UNKNOWN_WORKER")
    except BaseException:
        print('{"worker_error":"EXECUTION_FAILED"}')
        return 2


def execute(mode, code_root, *arguments):
    try:
        result = subprocess.run([sys.executable, "-I", "-B", str(HERE), "--worker", mode,
                                 str(code_root), *map(str, arguments)], cwd=code_root,
                                env=WORKER_ENV, capture_output=True, timeout=60)
        value = json.loads(result.stdout, object_pairs_hook=pairs)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        raise Blocked("WORKER_FAILED") from None
    if result.returncode not in (0, 1) or not isinstance(value, dict) or "worker_error" in value:
        raise Blocked("WORKER_FAILED")
    return result.returncode, value


def evaluate(root, config, code_root, baseline=""):
    code, report = execute("evaluate", code_root, input_path(root, config, "manifest"),
                           input_path(root, config, "cache_dir"), baseline)
    if (report.get("harness_sha256") != file_hash(EVALUATOR)
            or report.get("evaluation_scope", {}).get("candidate_fields") != list(FIELDS)
            or report.get("status") not in ("pass", "fail")
            or (code == 0) != (report["status"] == "pass")):
        raise Blocked("EVALUATOR_CONTRACT_CHANGED")
    return report


def previous_run(root):
    pointer = inside(root, "last_good.json")
    if not pointer.exists():
        return None
    previous = read_json(pointer)
    exact(previous, {"run_id", "seal_sha256", "data_type", "scope", "deployment_authorized"})
    if (not isinstance(previous["run_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", previous["run_id"])
            or previous["data_type"] not in REGISTRY or previous["scope"] != list(FIELDS)
            or previous["deployment_authorized"] is not False):
        raise Blocked("ARCHIVE_INVALID")
    bundle = inside(root, f"runs/{previous['run_id']}")
    seal = inside(bundle, "seal.json")
    if not is_hash(previous["seal_sha256"]) or file_hash(seal) != previous["seal_sha256"]:
        raise Blocked("ARCHIVE_INVALID")
    hashes = read_json(seal)
    actual = inventory(bundle)
    actual.pop("seal.json", None)
    if hashes != actual or read_json(bundle / "run.json").get("status") != "PASS":
        raise Blocked("ARCHIVE_INVALID")
    return previous


def seal_bundle(bundle, record):
    write_new(bundle / "run.json", record)
    write_new(bundle / "seal.json", inventory(bundle))
    seal = file_hash(bundle / "seal.json")
    for path in bundle.iterdir():
        path.chmod(0o400)
    bundle.chmod(0o500)
    return seal


def set_last_good(root, pointer):
    # Previous pointer remains valid if writing or replacement fails.
    temp = root / (".pointer-" + uuid.uuid4().hex)
    try:
        write_new(temp, pointer)
        inside(root, "last_good.json")
        os.replace(temp, root / "last_good.json")
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def run_pipeline(approved_root, config_path):
    root = private_root(approved_root)
    config = load_config(root, config_path)
    with lock(root):
        prior = previous_run(root)
        runs = inside(root, "runs")
        runs.mkdir(mode=0o700, exist_ok=True)
        run_id = uuid.uuid4().hex
        bundle = runs / run_id
        bundle.mkdir(mode=0o700)
        record = {"schema_version": 1, "run_id": run_id, "data_type": config["data_type"],
                  "scope": list(FIELDS), "deployment_authorized": False, "prior": prior,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "config_sha256": digest(encoded(config)), "status": "REVIEW", "attempts": []}
        try:
            write_new(bundle / "config.json", config)
            fixed_inputs = inputs_identity(root, config)
            env = environment_identity()
            tools = {"pipeline": file_hash(HERE), "evaluator": file_hash(EVALUATOR)}
            baseline_code = code_identity(config["baseline_code_root"])
            record.update(inputs=fixed_inputs, environment=env, tools=tools, baseline_code=baseline_code)
            if prior:
                old = read_json(root / "runs" / prior["run_id"] / "run.json")
                if old["winner"]["code_sha256"] != baseline_code["sha256"]:
                    raise Blocked("BASELINE_NOT_LAST_GOOD")
            baseline = evaluate(root, config, config["baseline_code_root"])
            write_new(bundle / "baseline.json", baseline)
            if (baseline["errors"] or any(c["status"] == "error" for c in baseline["cases"])
                    or not baseline["cases"]):
                raise Blocked("BASELINE_INPUT_OR_EXECUTION_ERROR")
            seen = set()
            for number, candidate in enumerate(config["candidate_code_roots"][:config["max_attempts"]], 1):
                attempt = {"number": number, "status": "REVIEW"}
                record["attempts"].append(attempt)
                try:
                    identity = code_identity(candidate)
                    attempt["code_sha256"] = identity["sha256"]
                    if identity["sha256"] in seen:
                        raise Blocked("UNCHANGED_CANDIDATE")
                    seen.add(identity["sha256"])
                    write_new(bundle / f"code-{number}.json", identity)
                    before_docs = docs_identity(Path(candidate))

                    def unchanged():
                        if (inputs_identity(root, config) != fixed_inputs
                                or code_identity(candidate) != identity
                                or code_identity(config["baseline_code_root"]) != baseline_code
                                or environment_identity() != env
                                or docs_identity(Path(candidate)) != before_docs
                                or file_hash(HERE) != tools["pipeline"]
                                or file_hash(EVALUATOR) != tools["evaluator"]):
                            raise Blocked("RUN_INPUT_CHANGED")

                    unchanged()
                    report = evaluate(root, config, candidate, bundle / "baseline.json")
                    write_new(bundle / f"evaluation-{number}.json", report)
                    unchanged()
                    if report["status"] != "pass":
                        attempt.update(status="FAIL", reason="EVALUATION_FAILED")
                        continue
                    attempt["corpus"] = corpus_gate(root, config)
                    if has_term_output(identity):
                        raise Blocked("TERM_OUTPUT_REQUIRES_UNSUPPORTED_TERM_GATE")
                    if any(before_docs.get(k) != v for k, v in config["documents"].items()):
                        raise Blocked("DOCUMENT_HASH_MISMATCH")
                    docs_code, docs_report = execute("docs", candidate)
                    write_new(bundle / f"docs-{number}.json", {"hashes": before_docs, **docs_report})
                    if docs_code != 0:
                        raise Blocked("DOCUMENT_CHECK_FAILED")
                    unchanged()
                    confirmed = evaluate(root, config, candidate, bundle / "baseline.json")
                    write_new(bundle / f"confirmation-{number}.json", confirmed)
                    unchanged()
                    if confirmed != report or confirmed["status"] != "pass":
                        raise Blocked("CONFIRMATION_CHANGED")
                    attempt["status"] = "PASS"
                    record.update(status="PASS", winner={"attempt": number, "code_sha256": identity["sha256"]})
                    break
                except Blocked as exc:
                    attempt.update(status="BLOCKED", reason=str(exc))
                    # Mutation invalidates this execution, not an opportunity to learn.
                    if str(exc) == "RUN_INPUT_CHANGED":
                        raise
            if record["status"] != "PASS":
                blocked = all(a["status"] == "BLOCKED" for a in record["attempts"])
                record["status"] = "BLOCKED" if blocked else "REVIEW"
                record["reason"] = (record["attempts"][0]["reason"]
                                    if blocked and len(record["attempts"]) == 1
                                    else "ATTEMPTS_EXHAUSTED_REVIEW")
        except Blocked as exc:
            record.update(status="BLOCKED", reason=str(exc))
        except Exception:
            record.update(status="BLOCKED", reason="EXECUTION_FAILED")
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        seal = seal_bundle(bundle, record)
        if record["status"] == "PASS":
            set_last_good(root, {"run_id": run_id, "seal_sha256": seal, "data_type": config["data_type"],
                                 "scope": list(FIELDS), "deployment_authorized": False})
        return {"status": "PROMOTED" if record["status"] == "PASS" else record["status"],
                "run_id": run_id, "reason": record.get("reason"), "deployment_authorized": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--approved-root", type=Path, required=True, help="Existing owner-only directory outside Git repositories")
    ap.add_argument("--config", required=True, help="Configuration path relative to approved root")
    args = ap.parse_args(argv)
    try:
        result = run_pipeline(args.approved_root, args.config)
    except Blocked as exc:
        result = {"status": "BLOCKED", "reason": str(exc)}
    except Exception:
        result = {"status": "BLOCKED", "reason": "EXECUTION_FAILED"}
    print(json.dumps(result))
    return 0 if result["status"] == "PROMOTED" else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        raise SystemExit(worker(sys.argv[2:]))
    raise SystemExit(main())
