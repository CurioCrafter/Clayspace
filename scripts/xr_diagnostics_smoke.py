"""Factory-startup smoke for the read-only XR Workbench capability report."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "xr_workbench_diagnostics_smoke", ROOT / "xr_workbench" / "diagnostics.py"
)
diagnostics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diagnostics
spec.loader.exec_module(diagnostics)

report = diagnostics.collect_report(None)
json.dumps(report)

assert report["schema_version"] == diagnostics.SCHEMA_VERSION
assert report["target"]["blender_version"] == "5.1.2"
assert report["privacy"]["collector_camera_access_attempted"] is False
assert report["privacy"]["camera_pixels_included"] is False
assert report["graphics"]["status"] == "unknown", report["graphics"]
assert report["m0"]["status"] != "pass", report["m0"]
print(
    "XR_WORKBENCH_DIAGNOSTICS_SMOKE_PASS "
    f"blender={report['blender']['version']} m0={report['m0']['status']} "
    f"runtime_registration={report['openxr']['runtime']['registration_status']}"
)
