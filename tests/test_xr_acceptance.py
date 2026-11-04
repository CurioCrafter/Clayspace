from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "xr_acceptance", ROOT / "scripts" / "xr_acceptance.py"
)
acceptance = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = acceptance
spec.loader.exec_module(acceptance)


class AcceptanceLedgerTests(unittest.TestCase):
    def test_all_spec_cases_start_unverified(self):
        report = acceptance.acceptance_report()
        self.assertEqual(len(report["cases"]), 22)
        self.assertEqual(report["milestone"]["state"], "incomplete")
        self.assertTrue(
            all(case["state"] == "unverified" for case in report["cases"].values())
        )

    def test_only_explicit_evidence_changes_a_case_and_is_redacted(self):
        report = acceptance.acceptance_report(
            {"AT-19": {"state": "blocked", "evidence": [{"device_id": "private"}]}}
        )
        self.assertEqual(report["cases"]["AT-19"]["state"], "blocked")
        self.assertEqual(
            report["cases"]["AT-19"]["evidence"][0]["device_id"], "<redacted>"
        )
        self.assertEqual(report["cases"]["AT-20"]["state"], "unverified")

    def test_unknown_case_and_invalid_state_fail_loudly(self):
        with self.assertRaises(KeyError):
            acceptance.acceptance_report({"AT-23": {"state": "passed"}})
        with self.assertRaises(ValueError):
            acceptance.acceptance_report({"AT-01": {"state": "maybe"}})

    def test_harness_never_uses_application_timer(self):
        source = (ROOT / "scripts" / "xr_acceptance.py").read_text(encoding="utf-8")
        self.assertNotIn("bpy.app." + "timers", source)
        self.assertIn("modal_handler_add", source)
        self.assertIn("event_timer_add", source)

    def test_editing_probe_runs_expanded_self_closing_smoke(self):
        runner = (ROOT / "scripts" / "run_xr_probe.ps1").read_text("utf-8")
        self.assertIn("if ($Mode -eq 'editing')", runner)
        self.assertIn("@('--', '--staged-smoke')", runner)

    def test_absolute_string_evidence_is_redacted(self):
        report = acceptance.acceptance_report(
            {"AT-19": {"state": "blocked", "evidence": ["D:\\private\\log.txt"]}}
        )
        self.assertEqual(report["cases"]["AT-19"]["evidence"], ["<redacted>"])


if __name__ == "__main__":
    unittest.main()
