"""Verify an Extension Manager installation in an isolated repository."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import bpy


expected = Path(os.environ["VRW_EXPECTED_EXTENSION"]).resolve()
module = importlib.import_module("bl_ext.user_default.vr_blender_workbench")
module_path = Path(module.__file__).resolve()
assert expected in module_path.parents, (expected, module_path)
module.register()
assert hasattr(bpy.types.Scene, "vrw_settings")
assert bpy.context.scene.vrw_settings.active_tool == "SELECT"
module.unregister()
assert not hasattr(bpy.types.Scene, "vrw_settings")
print(f"VRW_EXTENSION_MANAGER_COPY_PASS module={module_path}")
