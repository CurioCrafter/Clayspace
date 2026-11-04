from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "xr_workbench_diagnostics_test", ROOT / "xr_workbench" / "diagnostics.py"
)
diagnostics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diagnostics
spec.loader.exec_module(diagnostics)


class DiagnosticsSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.registry = {
            r"SOFTWARE\Khronos\OpenXR\1": [
                ("ActiveRuntime", r"C:\private\steamxr_win64.json")
            ],
            r"SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit": [
                (r"C:\private\implicit.json", 0)
            ],
            r"SOFTWARE\Khronos\OpenXR\1\ApiLayers\Explicit": [
                (r"C:\private\explicit.json", 0)
            ],
        }
        self.manifests = {
            r"C:\private\steamxr_win64.json": {
                "runtime": {
                    "name": "SteamVR",
                    "library_path": r"C:\private\vrclient.dll",
                }
            },
            r"C:\private\implicit.json": {
                "api_layer": {"name": "XR_APILAYER_IMPLICIT"}
            },
            r"C:\private\explicit.json": {
                "api_layer": {"name": "XR_APILAYER_EXPLICIT"}
            },
        }

    def collect(self, native):
        with (
            mock.patch.object(
                diagnostics,
                "_registry_values",
                side_effect=lambda key: self.registry[key],
            ),
            mock.patch.object(
                diagnostics,
                "_load_manifest",
                side_effect=lambda path: self.manifests.get(path),
            ),
            mock.patch.object(
                diagnostics,
                "_blender_report",
                return_value={
                    "status": "available",
                    "version": "5.1.2",
                    "version_tuple": [5, 1, 2],
                    "build_hash": "abc",
                    "build_branch": "main",
                    "build_date": "today",
                    "openxr_compiled": True,
                },
            ),
            mock.patch.object(
                diagnostics,
                "_graphics_report",
                return_value={
                    "status": "available",
                    "backend": "VULKAN",
                    "vendor": "GPU Vendor",
                    "renderer": "GPU Model",
                    "driver": "1.2.3",
                    "device_type": "DISCRETE",
                    "evidence_source": "test",
                },
            ),
        ):
            return diagnostics.collect_report({"native_xr": native})

    def test_registered_advertised_enabled_and_active_are_not_conflated(self):
        report = self.collect(
            {
                "available_extensions": ["XR_EXT_hand_tracking"],
                "enabled_api_layers": ["XR_APILAYER_EXPLICIT"],
                "room_image": "must never escape",
                "camera": {"pixels": "must never escape", "availability": "unknown"},
            }
        )

        extension = report["openxr"]["extensions"][0]
        self.assertTrue(extension["advertised"])
        self.assertIsNone(extension["enabled"])
        layers = {layer["name"]: layer for layer in report["openxr"]["api_layers"]}
        self.assertTrue(layers["XR_APILAYER_EXPLICIT"]["registered"])
        self.assertTrue(layers["XR_APILAYER_EXPLICIT"]["active"])
        self.assertTrue(layers["XR_APILAYER_IMPLICIT"]["registered"])
        self.assertIsNone(layers["XR_APILAYER_IMPLICIT"]["active"])
        self.assertNotIn("room_image", report["native_xr"])
        self.assertNotIn("pixels", report["native_xr"].get("camera", {}))
        self.assertEqual(report["m0"]["status"], "incomplete")
        self.assertIn("stereo_calibration", report["m0"]["blockers"])
        self.assertIn("frame_timing", report["m0"]["blockers"])

    def test_redacts_paths_and_device_identifiers_by_default(self):
        report = self.collect(
            {
                "system_name": "Index serial 123",
                "headset": {"connected": True, "device_identifier": "SERIAL-123"},
            }
        )
        encoded = json.dumps(report)
        self.assertNotIn(r"C:\private", encoded)
        self.assertNotIn("SERIAL-123", encoded)
        self.assertNotIn("Index serial 123", encoded)
        self.assertEqual(
            report["sensors"]["headset"]["device_identifier"], "<redacted>"
        )
        self.assertEqual(
            report["openxr"]["runtime"]["manifest_path"],
            "<redacted>/steamxr_win64.json",
        )

    def test_native_log_parser_keeps_repeated_enablement_evidence(self):
        parsed = diagnostics._parse_native_log(
            "noise\n"
            "XR_WORKBENCH runtime_name=SteamVR\n"
            "XR_WORKBENCH enabled_extension=XR_FB_passthrough\n"
            "XR_WORKBENCH enabled_extension=XR_KHR_vulkan_enable2\n"
            "XR_WORKBENCH enabled_api_layer=XR_APILAYER_TEST\n"
        )
        self.assertEqual(parsed["runtime_name"], "SteamVR")
        self.assertEqual(
            parsed["enabled_extensions"],
            ["XR_FB_passthrough", "XR_KHR_vulkan_enable2"],
        )
        self.assertEqual(parsed["enabled_api_layers"], ["XR_APILAYER_TEST"])

    def test_native_log_parser_resets_at_latest_runtime_instance(self):
        parsed = diagnostics._parse_native_log(
            "XR_WORKBENCH runtime_name=Old Runtime\n"
            "XR_WORKBENCH enabled_extension=XR_OLD\n"
            "XR_WORKBENCH system_name=Old Headset\n"
            "XR_WORKBENCH runtime_name=Current Runtime\n"
            "XR_WORKBENCH session_state=0\n"
        )
        self.assertEqual(parsed["runtime_name"], "Current Runtime")
        self.assertEqual(parsed["session_state"], "0")
        self.assertNotIn("enabled_extensions", parsed)
        self.assertNotIn("system_name", parsed)

    def test_only_launch_unique_environment_log_is_labeled_current(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "native.log"
            log_path.write_text(
                "XR_WORKBENCH runtime_name=Current Runtime\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                "os.environ",
                {
                    "XRWB_NATIVE_LOG": str(log_path),
                    "XRWB_NATIVE_LOG_SESSION": "unique-launch-id",
                },
                clear=False,
            ):
                current = diagnostics._current_process_native_evidence()
        imported = diagnostics._native_evidence(
            {"native_xr_log": "XR_WORKBENCH runtime_name=Imported Runtime\n"}
        )
        self.assertEqual(current["evidence_scope"], "current_process_log")
        self.assertEqual(imported["evidence_scope"], "historical_import")

    def test_background_blender_never_calls_native_gpu_getters(self):
        class ExplodingPlatform:
            def backend_type_get(self):
                raise AssertionError("background GPU getter was called")

        fake_bpy = SimpleNamespace(app=SimpleNamespace(background=True))
        fake_gpu = SimpleNamespace(platform=ExplodingPlatform())
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy, "gpu": fake_gpu}):
            report = diagnostics._graphics_report(None)
        self.assertEqual(report["status"], "unknown")
        self.assertIsNone(report["backend"])

    def test_write_report_redacts_even_an_unredacted_input(self):
        payload = {
            "schema_version": diagnostics.SCHEMA_VERSION,
            "manifest_path": r"C:\private\runtime.json",
            "device_identifier": "serial-123",
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = diagnostics.write_report(
                payload, Path(directory) / "report.json"
            )
            written = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(written["manifest_path"], "<redacted>/runtime.json")
        self.assertEqual(written["device_identifier"], "<redacted>")


if __name__ == "__main__":
    unittest.main()
