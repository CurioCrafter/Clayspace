"""Structured user-action failures reach the existing persistent console sink."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("xr_interaction_log", Path(__file__).resolve().parents[1] / "xr_workbench/interaction_log.py")
log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log)


class InteractionLogTest(unittest.TestCase):
    def test_action_context_and_exception_are_logged_without_poses(self):
        context = SimpleNamespace(active_object=SimpleNamespace(type="MESH", modifiers=[]),
                                  mode="OBJECT", area=SimpleNamespace(type="VIEW_3D"),
                                  region=SimpleNamespace(type="WINDOW"), selected_objects=[1])
        output = io.StringIO()
        try:
            raise ValueError("Fixture operator rejected input")
        except ValueError as exc:
            with redirect_stdout(output):
                log.emit("error", str(exc), context, action="pointer", hand="/user/hand/right",
                         tool="extrude", level="ERROR", error=exc)
        record = json.loads(output.getvalue().removeprefix("XR_INTERACTION "))
        self.assertEqual(record["area"], "VIEW_3D")
        self.assertEqual(record["selected"], 1)
        self.assertIn("ValueError: Fixture operator rejected input", record["exception"])
        self.assertNotIn("controller_location", record)
        self.assertEqual(log.last_level(), "ERROR")
        self.assertEqual(log.last_message(), "Fixture operator rejected input")

    def test_unavailable_context_does_not_hide_the_failure(self):
        output = io.StringIO()
        with redirect_stdout(output):
            log.emit("rejected", "No editable target", SimpleNamespace(), level="WARNING")
        record = json.loads(output.getvalue().removeprefix("XR_INTERACTION "))
        self.assertFalse(record["context_available"])
        self.assertEqual(record["message"], "No editable target")


if __name__ == "__main__":
    unittest.main()
