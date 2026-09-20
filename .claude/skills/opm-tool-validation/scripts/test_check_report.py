"""Offline normal and fault-injection checks for the report aggregator."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

MODULE_PATH = Path(__file__).with_name("check_report.py")
spec = importlib.util.spec_from_file_location("validation_report", MODULE_PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

# Independent workflow contract: do not derive test inputs from the implementation.
# Deliberate duplication lets tests catch accidental removal of a required stage.
EXPECTED_PROFILE_STAGES = {
    "collection": {"harness", "collection"},
    "parser": {"harness", "corpus", "parser", "mcp", "output"},
    "mcp": {"harness", "mcp"},
    "output": {"harness", "mcp", "output"},
    "docs": {"output"},
    "consumer": {"harness", "mcp", "output", "consumer"},
    "skill": {"harness"},
}


class ReportGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proof = self.root / "proof.txt"
        self.proof.write_text("synthetic test results\n")
        self.path = self.root / "report.json"
        self.report = {
            "schema_version": 1, "profiles": ["skill"], "claim": "offline",
            "limitations": ["Synthetic aggregator test; not product accuracy"],
            "stages": [self.stage("harness")],
        }

    def stage(self, stage_id, status="PASS"):
        return {"id": stage_id, "status": status, "scope": "Synthetic evidence aggregation",
                "reason": "Test fixture", "evidence": [{"path": "proof.txt",
                "sha256": hashlib.sha256(self.proof.read_bytes()).hexdigest()}]}

    def check(self, report=None):
        self.path.write_text(json.dumps(self.report if report is None else report))
        return gate.check_report(self.path)

    def test_valid_normal_control(self):
        code, summary = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "PASS")

    def test_profile_map_matches_reviewed_contract(self):
        self.assertEqual(gate.PROFILE_STAGES, EXPECTED_PROFILE_STAGES)

    def test_each_profile_requires_its_stages(self):
        for profile, required in EXPECTED_PROFILE_STAGES.items():
            with self.subTest(profile=profile):
                report = deepcopy(self.report)
                report["profiles"] = [profile]
                report["stages"] = [self.stage(s) for s in sorted(required)]
                self.assertEqual(self.check(report)[0], 0)
                for missing in required:
                    with self.subTest(missing=missing):
                        incomplete = deepcopy(report)
                        incomplete["stages"] = [s for s in report["stages"] if s["id"] != missing]
                        self.assertNotEqual(self.check(incomplete)[0], 0)

    def test_multiple_profiles_use_union(self):
        self.report["profiles"] = ["parser", "collection"]
        self.report["stages"] = [self.stage(s) for s in EXPECTED_PROFILE_STAGES["parser"]]
        self.assertEqual(self.check()[0], 1)
        self.report["stages"].append(self.stage("collection"))
        self.assertEqual(self.check()[0], 0)

    def test_live_requires_pilot_and_live(self):
        self.report["claim"] = "live"
        self.report["stages"].append(self.stage("live"))
        self.assertEqual(self.check()[0], 1)
        self.report["stages"].append(self.stage("pilot"))
        self.assertEqual(self.check()[0], 0)

    def test_pilot_requires_pilot(self):
        self.report["claim"] = "pilot"
        self.assertEqual(self.check()[0], 1)
        self.report["stages"].append(self.stage("pilot"))
        self.assertEqual(self.check()[0], 0)

    def test_required_nonpass_always_blocks(self):
        for status in gate.STATUSES - {"PASS"}:
            with self.subTest(status=status):
                self.report["stages"][0]["status"] = status
                self.assertEqual(self.check()[0], 1)

    def test_optional_failure_is_not_hidden(self):
        for status in ("FAIL", "REVIEW", "BLOCKED"):
            self.report["stages"] = [self.stage("harness"), self.stage("output", status)]
            self.assertEqual(self.check()[0], 1)

    def test_optional_notrun_can_remain_unclaimed(self):
        for status in ("NOT_RUN", "SKIP"):
            self.report["stages"] = [self.stage("harness"), self.stage("live", status)]
            self.assertEqual(self.check()[0], 0)

    def test_empty_pass_evidence_blocks(self):
        self.report["stages"][0]["evidence"] = []
        self.assertEqual(self.check()[0], 1)

    def test_changed_missing_and_empty_evidence_block(self):
        for content in ("modified", "", None):
            with self.subTest(content=content):
                if content is None:
                    self.proof.unlink()
                else:
                    self.proof.write_text(content)
                self.assertEqual(self.check()[0], 1)

    def test_duplicate_stage_and_unknown_stage_rejected(self):
        for value in (self.stage("harness"), self.stage("unknown")):
            self.report["stages"] = [self.stage("harness"), value]
            self.assertEqual(self.check()[0], 2)

    def test_invalid_types_and_empty_contract_rejected(self):
        mutations = [("schema_version", True), ("profiles", []), ("profiles", ["skill", "skill"]),
                     ("profiles", [None]), ("profiles", ["unknown"]), ("claim", []),
                     ("claim", "production"), ("stages", []), ("limitations", [""])]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                report = deepcopy(self.report)
                report[key] = value
                self.assertEqual(self.check(report)[0], 2)

    def test_invalid_stage_fields_rejected(self):
        mutations = [("status", "SUCCESS"), ("status", {}), ("scope", ""),
                     ("id", []), ("evidence", None), ("reason", None)]
        for key, value in mutations:
            report = deepcopy(self.report)
            report["stages"][0][key] = value
            self.assertEqual(self.check(report)[0], 2)

    def test_nonpass_requires_reason(self):
        self.report["stages"][0].update(status="SKIP", reason="")
        self.assertEqual(self.check()[0], 2)

    def test_unknown_fields_and_missing_fields_rejected(self):
        report = deepcopy(self.report)
        report["auto_deploy"] = True
        self.assertEqual(self.check(report)[0], 2)
        del report["auto_deploy"]
        del report["claim"]
        self.assertEqual(self.check(report)[0], 2)

    def test_bad_hash_and_duplicate_evidence(self):
        self.report["stages"][0]["evidence"][0]["sha256"] = "not-a-hash"
        self.assertEqual(self.check()[0], 2)
        self.report["stages"] = [self.stage("harness")]
        self.report["stages"][0]["evidence"] *= 2
        self.assertEqual(self.check()[0], 1)

    def test_self_referencing_report_rejected(self):
        self.report["stages"][0]["evidence"][0]["path"] = "report.json"
        self.assertEqual(self.check()[0], 1)

    def test_directory_and_oversized_evidence_rejected(self):
        self.report["stages"][0]["evidence"][0]["path"] = "."
        self.assertEqual(self.check()[0], 1)
        self.report["stages"] = [self.stage("harness")]
        with self.proof.open("wb") as stream:
            stream.truncate(gate.MAX_BYTES + 1)
        self.assertEqual(self.check()[0], 1)

    def test_absolute_evidence_path(self):
        self.report["stages"][0]["evidence"][0]["path"] = str(self.proof)
        self.assertEqual(self.check()[0], 0)

    def test_bad_json_duplicate_keys_and_nonfinite_rejected(self):
        for content in ("", "not-json", '{"schema_version":1,"schema_version":1}',
                        '{"value":NaN}', '[]', 'null'):
            self.path.write_text(content)
            self.assertEqual(gate.check_report(self.path)[0], 2)

    def test_missing_report_is_safe(self):
        code, result = gate.check_report(self.path)
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_summary_does_not_echo_sensitive_input(self):
        marker = "synthetic-sensitive-marker"
        self.report["stages"][0].update(scope=marker, reason=marker)
        self.report["stages"][0]["evidence"][0]["path"] = marker
        code, result = self.check()
        self.assertEqual(code, 1)
        self.assertNotIn(marker, json.dumps(result))

    def test_cli_exit_codes(self):
        self.check()
        for expected in (0, 1, 2):
            if expected == 1:
                self.proof.write_text("changed")
            elif expected == 2:
                self.path.write_text("broken JSON")
            run = subprocess.run([sys.executable, "-B", str(MODULE_PATH), str(self.path)],
                                 capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, expected)
            self.assertIn("status", json.loads(run.stdout))
            self.assertEqual(run.stderr, "")


if __name__ == "__main__":
    unittest.main()
