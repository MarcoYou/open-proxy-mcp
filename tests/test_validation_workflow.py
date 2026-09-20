"""Keep the skill's offline evidence-gate tests in the regular regression suite."""
import importlib.util
from pathlib import Path


_path = (Path(__file__).resolve().parents[1] / ".claude" / "skills"
         / "opm-tool-validation" / "scripts" / "test_check_report.py")
_spec = importlib.util.spec_from_file_location("opm_skill_report_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

TestValidationReport = _module.ReportGateTests
