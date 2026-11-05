"""Headless M0 smoke: registration, report schema, and reversible overlay state."""

from __future__ import annotations

from pathlib import Path
import os
import sys
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
expected_install = os.environ.get("XRWB_EXPECTED_INSTALL")
import_root = Path(expected_install).resolve().parent if expected_install else ROOT
if str(import_root) not in sys.path:
    sys.path.insert(0, str(import_root))

import xr_workbench  # noqa: E402
from xr_workbench import runtime  # noqa: E402
from xr_workbench.diagnostics import collect_report  # noqa: E402
from xr_workbench.ui import XRWB_OT_place_overlay, set_runtime_passthrough  # noqa: E402
from mathutils import Vector  # noqa: E402


assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0), bpy.app.version_string
if expected_install:
    assert (
        Path(xr_workbench.__file__).resolve()
        == Path(expected_install).resolve() / "__init__.py"
    )
else:
    expected_source = os.environ.get("XRWB_EXPECTED_SOURCE")
    if expected_source:
        expected = Path(expected_source).resolve()
        assert expected in Path(xr_workbench.__file__).resolve().parents, (
            expected,
            xr_workbench.__file__,
        )


if hasattr(bpy.types.WindowManager, "xrwb_settings"):
    xr_workbench.unregister()
xr_workbench.register()
assert hasattr(bpy.types.WindowManager, "xrwb_settings")
settings = bpy.context.window_manager.xrwb_settings
assert not settings.overlay_enabled
assert settings.panel_opacity == 0.25
for opacity in (0.0, 1.0):
    settings.panel_opacity = opacity
    assert runtime._panel_opacity == opacity

fake_xr_settings = SimpleNamespace(show_passthrough=False)
matrix = XRWB_OT_place_overlay.viewer_matrix((0, 0, 0), (1, 0, 0, 0))
assert (matrix.translation - Vector((0.26, 0, -1.8))).length < 1e-6
assert (matrix.to_3x3() @ Vector((0, -1, 0)) - Vector((0, 0, 1))).length < 1e-6
assert abs(matrix.determinant() - 1.0) < 1e-6
assert set_runtime_passthrough(fake_xr_settings, True)
assert fake_xr_settings.show_passthrough is True
assert set_runtime_passthrough(fake_xr_settings, False)
assert fake_xr_settings.show_passthrough is False
runtime._draw_cache_ensure()
cached_text = runtime._text_geometry
runtime._draw_cache_ensure()
assert runtime._text_geometry is cached_text
runtime.set_overlay_report("incomplete", "unknown", "unknown")
assert runtime._panel_geometry is None
runtime._draw_cache_ensure()
assert runtime._text_geometry is not cached_text
runtime.configure_overlay(True, 0.4)
state = runtime.overlay_status()
assert state["enabled"] and state["window_handler"] and state["xr_handler"], state
runtime.configure_overlay(False, 0.4)
state = runtime.overlay_status()
assert (
    not state["enabled"] and not state["window_handler"] and not state["xr_handler"]
), state

report = collect_report(bpy.context)
for key in (
    "schema_version",
    "blender",
    "graphics",
    "openxr",
    "sensors",
    "evidence",
    "m0",
):
    assert key in report, key

xr_workbench.unregister()
assert runtime._panel_geometry is None
assert not hasattr(bpy.types.WindowManager, "xrwb_settings")
print(
    "XR_WORKBENCH_SMOKE_PASS report_m0="
    + str(report.get("m0", {}).get("status", "unknown")),
    flush=True,
)
