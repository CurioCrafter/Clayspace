"""Smoke an extracted package copy from a temporary Blender scripts root."""

from __future__ import annotations

import os
from pathlib import Path

import addon_utils
import bpy


expected = Path(os.environ["VRW_EXPECTED_INSTALL"]).resolve()
module = addon_utils.enable("vr_blender_workbench", default_set=False, persistent=False)
assert module is not None
module_path = Path(module.__file__).resolve()
assert expected in module_path.parents, (expected, module_path)
assert hasattr(bpy.types.Scene, "vrw_settings")
assert bpy.context.scene.vrw_settings.active_tool == "SELECT"
assert hasattr(bpy.ops.vrw, "start_session")
addon_utils.disable("vr_blender_workbench", default_set=False)
assert not hasattr(bpy.types.Scene, "vrw_settings")
print(f"VRW_ISOLATED_INSTALLED_COPY_PASS module={module_path}")
