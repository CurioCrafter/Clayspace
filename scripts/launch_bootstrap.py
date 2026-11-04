"""Load VR Workbench from source into the isolated launcher profile."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import addon_utils
import bpy


ROOT = Path(__file__).resolve().parents[1]
addon = addon_utils.enable("vr_blender_workbench", default_set=True, persistent=True)
if addon is None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    addon = addon_utils.enable(
        "vr_blender_workbench", default_set=True, persistent=True
    )

if addon is None:
    import vr_blender_workbench as addon  # noqa: E402

    if not hasattr(bpy.types.Scene, "vrw_settings"):
        addon.register()


def _auto_start():
    """Attempt XR only in this script's valid startup context; never poll it."""

    context = bpy.context
    if context.window is None:
        message = "Auto-start skipped because no Blender window context is available"
        try:
            context.scene.vrw_settings.last_error = message
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        print(f"VRW_AUTO_START_DEFERRED {message}")
        return
    try:
        result = bpy.ops.vrw.start_session("EXEC_DEFAULT")
        print(f"VRW_AUTO_START result={sorted(result)}")
    except Exception as exc:
        try:
            context.scene.vrw_settings.last_error = f"Auto-start failed: {exc}"
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        print(f"VRW_AUTO_START_FAIL {exc}")


if os.environ.get("VRW_AUTO_START", "1").lower() in {"1", "true", "yes"}:
    _auto_start()

print(
    "VRW_BOOTSTRAP_PASS "
    f"module={Path(addon.__file__).resolve()} "
    f"auto_start={os.environ.get('VRW_AUTO_START', '1')}"
)
