"""Offline failure injection with synthetic sources; not real market gold.

The policy's 'real' attestations below deliberately exercise metadata validation.
They do not establish factual independence or market accuracy.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from scripts import parser_pipeline as pipeline


PERSON = {"name": "홍길동", "birthDate": "1970.03.01", "roleType": "사내이사"}
GOOD = 'def parse_personnel_xml(html):\n    return ' + repr({
    "appointments": [{"action": "선임", "candidates": [PERSON]}]}) + '\n'
BAD = 'def parse_personnel_xml(html):\n    return {"appointments": []}\n'


def save(path, value):
    path.write_bytes(pipeline.encoded(value))


def checkout(path, source=GOOD):
    package = path / "open_proxy_mcp/services"
    package.mkdir(parents=True)
    (path / "open_proxy_mcp/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "shareholder_meeting_parser.py").write_text(source)
    (path / "pyproject.toml").write_text('[project]\nname="synthetic"\nversion="0"\n')
    (path / "uv.lock").write_text("version = 1\n")
    for relative in pipeline.DOCS:
        doc = path / relative
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("# Synthetic pipeline contract\n")
    for relative, *arguments in pipeline.DOC_CHECKS:
        script = path / relative
        script.parent.mkdir(exist_ok=True)
        script.write_text("raise SystemExit(0)\n")
    return path


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    cache = private / "documents"
    cache.mkdir()
    cases = []
    for index, split in enumerate(("development", "holdout"), 1):
        receipt = f"20260301{index:06d}"
        raw = pipeline.encoded({"html": "<p>Synthetic boundary</p>", "text": "Synthetic"})
        (cache / f"{receipt}.json").write_bytes(raw)
        cases.append({"id": f"synthetic-{index}", "rcept_no": receipt,
                      "company_group": f"group-{index}", "split": split,
                      "source_sha256": pipeline.digest(raw),
                      "review": {"reviewer": "label-author", "source_locator": "synthetic table"},
                      "expected": [PERSON]})
    save(private / "gold.json", {"schema_version": 1, "cases": cases})
    review = {"schema_version": 1, "manifest_sha256": pipeline.file_hash(private / "gold.json"),
              "cases": {case["id"]: {"sample_type": "real", "reviewer": "second-reader",
                         "independent": True, "holdout_unseen": True} for case in cases}}
    save(private / "review.json", review)
    baseline = checkout(tmp_path / "baseline", BAD)
    candidate = checkout(tmp_path / "candidate")
    config = {"schema_version": 1, "data_type": "personnel_candidates",
              "manifest": "gold.json", "cache_dir": "documents", "review": "review.json",
              "baseline_code_root": str(baseline), "candidate_code_roots": [str(candidate)],
              "max_attempts": 3, "policy": dict.fromkeys(pipeline.POLICY_KEYS, 1),
              "documents": {name: pipeline.file_hash(candidate / name) for name in pipeline.DOCS}}
    save(private / "config.json", config)
    # Filesystem environment hashing is tested separately. Every parser/docs
    # evaluation still executes a fresh real worker and the unchanged evaluator.
    monkeypatch.setattr(pipeline, "environment_identity", lambda: {"synthetic_environment": "fixed"})
    return private, config, baseline, candidate


def run(scenario):
    root, config, *_ = scenario
    save(root / "config.json", config)
    result = pipeline.run_pipeline(root, "config.json")
    bundle = root / "runs" / result["run_id"]
    return result, pipeline.read_json(bundle / "run.json"), bundle


def assert_stopped(result, record, root, reason):
    assert result["status"] != "PROMOTED"
    assert not (root / "last_good.json").exists()
    assert reason in [record.get("reason"), *(a.get("reason") for a in record["attempts"])]


def test_executes_baseline_candidate_confirmation_and_seals_archive(scenario):
    root, config, baseline, candidate = scenario
    before = pipeline.inputs_identity(root, config)
    result, record, bundle = run(scenario)
    assert result["status"] == "PROMOTED", record
    assert record["scope"] == list(pipeline.FIELDS)
    assert record["deployment_authorized"] is False
    assert pipeline.read_json(bundle / "baseline.json")["status"] == "fail"
    assert pipeline.read_json(bundle / "evaluation-1.json")["status"] == "pass"
    assert pipeline.read_json(bundle / "confirmation-1.json")["status"] == "pass"
    assert all(c["passed"] for c in pipeline.read_json(bundle / "docs-1.json")["checks"])
    assert pipeline.inputs_identity(root, config) == before
    assert bundle.stat().st_mode & 0o777 == 0o500
    assert all(p.stat().st_mode & 0o777 == 0o400 for p in bundle.iterdir())
    prior = pipeline.previous_run(root)
    frozen = pipeline.inventory(bundle)
    config["baseline_code_root"] = str(candidate)
    result2, record2, bundle2 = run(scenario)
    assert result2["status"] == "PROMOTED", record2
    assert record2["prior"] == prior and bundle2 != bundle
    assert pipeline.inventory(bundle) == frozen


def test_failed_candidate_moves_to_explicit_different_candidate(scenario):
    root, config, baseline, candidate = scenario
    config["candidate_code_roots"] = [str(baseline), str(candidate)]
    result, record, bundle = run(scenario)
    assert result["status"] == "PROMOTED", record
    assert [a["status"] for a in record["attempts"]] == ["FAIL", "PASS"]
    assert not (bundle / "confirmation-1.json").exists()
    assert (bundle / "confirmation-2.json").exists()


def test_duplicate_code_does_not_count_as_learning_and_budget_is_bounded(scenario):
    root, config, baseline, candidate = scenario
    clone = baseline.parent / "clone"
    shutil.copytree(baseline, clone)
    config.update(candidate_code_roots=[str(baseline), str(clone), str(candidate)], max_attempts=2)
    result, record, bundle = run(scenario)
    assert_stopped(result, record, root, "UNCHANGED_CANDIDATE")
    assert len(record["attempts"]) == 2
    assert not (bundle / "evaluation-2.json").exists()
    assert not (bundle / "evaluation-3.json").exists()


@pytest.mark.parametrize("data_type", ["personnel_terms", "financials", "unknown"])
def test_unsupported_data_types_are_blocked_before_execution(scenario, data_type):
    root, config, *_ = scenario
    config["data_type"] = data_type
    save(root / "config.json", config)
    with pytest.raises(pipeline.Blocked, match="UNSUPPORTED_DATA_TYPE"):
        pipeline.run_pipeline(root, "config.json")
    assert not (root / "runs").exists()


@pytest.mark.parametrize("same_baseline", [False, True])
def test_candidate_success_never_authorizes_term_output_without_term_gold(scenario, same_baseline):
    root, config, _, candidate = scenario
    parser = candidate / "open_proxy_mcp/services/shareholder_meeting_parser.py"
    parser.write_text(GOOD + '\nTERM_FIELD = "termRaw"\n')
    if same_baseline:
        config["baseline_code_root"] = str(candidate)
    result, record, bundle = run(scenario)
    assert pipeline.read_json(bundle / "evaluation-1.json")["status"] == "pass"
    assert_stopped(result, record, root, "TERM_OUTPUT_REQUIRES_UNSUPPORTED_TERM_GATE")


@pytest.mark.parametrize("problem,reason", [
    ("synthetic", "CORPUS_INSUFFICIENT"), ("reviewer", "INDEPENDENT_REVIEW_REQUIRED"),
    ("independent", "INDEPENDENT_REVIEW_REQUIRED"), ("holdout", "HOLDOUT_ALREADY_USED"),
    ("hash", "REVIEW_NOT_BOUND_TO_GOLD"), ("missing", "REVIEW_NOT_BOUND_TO_GOLD"),
    ("threshold", "CORPUS_INSUFFICIENT"),
])
def test_promotion_requires_bound_independent_real_holdout_metadata(scenario, problem, reason):
    root, config, *_ = scenario
    review = pipeline.read_json(root / "review.json")
    record = review["cases"]["synthetic-2"]
    if problem == "synthetic":
        record["sample_type"] = "synthetic"
    elif problem == "reviewer":
        record["reviewer"] = "label-author"
    elif problem == "independent":
        record["independent"] = False
    elif problem == "holdout":
        record["holdout_unseen"] = False
    elif problem == "hash":
        review["manifest_sha256"] = "0" * 64
    elif problem == "missing":
        del review["cases"]["synthetic-2"]
    else:
        config["policy"]["min_real_holdout_companies"] = 2
    save(root / "review.json", review)
    result, record, bundle = run(scenario)
    assert_stopped(result, record, root, reason)
    assert not (bundle / "confirmation-1.json").exists()


@pytest.mark.parametrize("problem,reason", [("hash", "DOCUMENT_HASH_MISMATCH"),
                                             ("check", "DOCUMENT_CHECK_FAILED")])
def test_documents_require_current_hash_and_executed_checks(scenario, problem, reason):
    root, config, _, candidate = scenario
    if problem == "hash":
        config["documents"][pipeline.DOCS[0]] = "0" * 64
    else:
        (candidate / pipeline.DOC_CHECKS[0][0]).write_text('print("SECRET_SENTINEL")\nraise SystemExit(1)\n')
    result, record, bundle = run(scenario)
    assert_stopped(result, record, root, reason)
    assert "SECRET_SENTINEL" not in (bundle / "run.json").read_text()


@pytest.mark.parametrize("target", ["dependency", "docs", "gold", "raw", "environment"])
def test_changes_during_evaluation_block_entire_run(scenario, monkeypatch, target):
    root, config, _, candidate = scenario
    original = pipeline.evaluate
    environment = {"value": "fixed"}
    monkeypatch.setattr(pipeline, "environment_identity", lambda: environment.copy())

    def changing(*args, **kwargs):
        report = original(*args, **kwargs)
        if str(args[2]) == str(candidate):
            if target == "environment":
                environment["value"] = "changed"
            else:
                path = {"dependency": candidate / "open_proxy_mcp/services/__init__.py",
                        "docs": candidate / pipeline.DOCS[0], "gold": root / "gold.json",
                        "raw": next((root / "documents").glob("*.json"))}[target]
                path.write_bytes(path.read_bytes() + b"\n")
        return report

    monkeypatch.setattr(pipeline, "evaluate", changing)
    result, record, _ = run(scenario)
    assert_stopped(result, record, root, "RUN_INPUT_CHANGED")


def test_confirmation_is_reexecuted_and_must_match(scenario, monkeypatch):
    root, config, _, candidate = scenario
    original = pipeline.evaluate
    calls = []

    def altered(*args, **kwargs):
        report = original(*args, **kwargs)
        calls.append(str(args[2]))
        if calls.count(str(candidate)) == 2:
            report["summary"]["injected_change"] = True
        return report

    monkeypatch.setattr(pipeline, "evaluate", altered)
    result, record, _ = run(scenario)
    assert_stopped(result, record, root, "CONFIRMATION_CHANGED")
    assert calls.count(str(candidate)) == 2


def test_failed_run_preserves_last_good_and_old_bundle(scenario):
    root, config, baseline, candidate = scenario
    _, _, bundle = run(scenario)
    pointer = (root / "last_good.json").read_bytes()
    archived = pipeline.inventory(bundle)
    config["baseline_code_root"] = str(candidate)
    config["candidate_code_roots"] = [str(baseline)]
    result, record, _ = run(scenario)
    assert result["status"] == "REVIEW"
    assert (root / "last_good.json").read_bytes() == pointer
    assert pipeline.inventory(bundle) == archived


def test_archive_tampering_and_wrong_baseline_cannot_reuse_success(scenario):
    root, config, baseline, candidate = scenario
    _, _, bundle = run(scenario)
    result, record, _ = run(scenario)
    assert record["reason"] == "BASELINE_NOT_LAST_GOOD"
    victim = bundle / "evaluation-1.json"
    victim.chmod(0o600)
    victim.write_text('{}')
    with pytest.raises(pipeline.Blocked, match="ARCHIVE_INVALID"):
        pipeline.previous_run(root)


def test_pointer_replacement_failure_keeps_prior_pointer(scenario, monkeypatch):
    root, config, _, candidate = scenario
    run(scenario)
    config["baseline_code_root"] = str(candidate)
    save(root / "config.json", config)
    old = (root / "last_good.json").read_bytes()
    monkeypatch.setattr(pipeline.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("SECRET_SENTINEL")))
    with pytest.raises(OSError):
        pipeline.run_pipeline(root, "config.json")
    assert (root / "last_good.json").read_bytes() == old
    assert not list(root.glob(".pointer-*"))


def test_network_attempt_swallowed_by_parser_is_still_failure(scenario):
    root, config, _, candidate = scenario
    parser = candidate / "open_proxy_mcp/services/shareholder_meeting_parser.py"
    parser.write_text('import socket\n' + GOOD.replace('    return ',
        '    try:\n        socket.getaddrinfo("example.invalid", 443)\n    except Exception:\n        pass\n    return '))
    result, record, bundle = run(scenario)
    assert_stopped(result, record, root, "EVALUATION_FAILED")
    assert pipeline.read_json(bundle / "evaluation-1.json")["network_attempts"] > 0


def test_cli_redacts_exception_body_and_corpus(scenario, capsys):
    root, config, _, candidate = scenario
    (candidate / "open_proxy_mcp/services/shareholder_meeting_parser.py").write_text(
        'def parse_personnel_xml(html):\n    print("SECRET_SENTINEL")\n    raise ValueError(html)\n')
    assert pipeline.main(["--approved-root", str(root), "--config", "config.json"]) == 1
    output = capsys.readouterr()
    assert "SECRET_SENTINEL" not in output.out + output.err
    assert "홍길동" not in output.out + output.err
    assert str(root) not in output.out + output.err


def test_private_root_symlinks_permissions_and_git_are_rejected(scenario, tmp_path):
    root, *_ = scenario
    root.chmod(0o755)
    with pytest.raises(pipeline.Blocked, match="PRIVATE_ROOT_REQUIRED"):
        pipeline.private_root(root)
    root.chmod(0o700)
    (root / ".git").write_text("gitdir: /not-followed")
    with pytest.raises(pipeline.Blocked, match="PRIVATE_ROOT_REQUIRED"):
        pipeline.private_root(root)
    (root / ".git").unlink()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(pipeline.Blocked, match="SYMLINK_FORBIDDEN"):
        pipeline.private_root(alias)
    (root / "runs").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(pipeline.Blocked, match="SYMLINK_FORBIDDEN"):
        pipeline.run_pipeline(root, "config.json")


def test_lock_prevents_concurrent_or_recursive_execution(scenario):
    root, *_ = scenario
    with pipeline.lock(root):
        with pytest.raises(pipeline.Blocked, match="ALREADY_RUNNING"):
            pipeline.run_pipeline(root, "config.json")


def test_code_and_environment_content_hashes_cover_transitive_files(scenario, tmp_path, monkeypatch):
    _, _, _, candidate = scenario
    before = pipeline.code_identity(candidate)
    (candidate / "open_proxy_mcp/services/helper.py").write_text("VALUE = 1\n")
    assert pipeline.code_identity(candidate)["sha256"] != before["sha256"]
    env = tmp_path / "env"
    env.mkdir()
    (env / "dependency.so").write_bytes(b"binary-a")
    before = pipeline.inventory(env, environment=True)
    (env / "dependency.so").write_bytes(b"binary-b")
    assert pipeline.inventory(env, environment=True) != before


def test_empty_corpus_never_reaches_candidate(scenario):
    root, *_ = scenario
    save(root / "gold.json", {"schema_version": 1, "cases": []})
    result, record, bundle = run(scenario)
    assert_stopped(result, record, root, "BASELINE_INPUT_OR_EXECUTION_ERROR")
    assert not record["attempts"]


def test_external_read_only_corpus_executes_but_missing_review_blocks(scenario, tmp_path):
    root, config, *_ = scenario
    source = tmp_path / "existing-private-storage"
    source.mkdir()
    (source / ".git").mkdir()  # Private storage may itself be versioned.
    shutil.copytree(root / "documents", source / "documents")
    shutil.copyfile(root / "gold.json", source / "gold.json")
    config["input_root"] = str(source)
    before = pipeline.inventory(source)
    result, record, bundle = run(scenario)
    assert_stopped(result, record, root, "REVIEW_REQUIRED")
    assert result["status"] == "BLOCKED" and result["reason"] == "REVIEW_REQUIRED"
    assert pipeline.read_json(bundle / "evaluation-1.json")["status"] == "pass"
    assert pipeline.inventory(source) == before


def test_positive_holdout_fields_cannot_be_replaced_with_null(scenario):
    root, config, baseline, candidate = scenario
    gold = pipeline.read_json(root / "gold.json")
    for case in gold["cases"]:
        case["expected"][0]["birthDate"] = None
    save(root / "gold.json", gold)
    review = pipeline.read_json(root / "review.json")
    review["manifest_sha256"] = pipeline.file_hash(root / "gold.json")
    save(root / "review.json", review)
    (candidate / "open_proxy_mcp/services/shareholder_meeting_parser.py").write_text(
        GOOD.replace("'1970.03.01'", "None"))
    result, record, bundle = run(scenario)
    assert pipeline.read_json(bundle / "evaluation-1.json")["status"] == "pass"
    assert_stopped(result, record, root, "CORPUS_INSUFFICIENT")
