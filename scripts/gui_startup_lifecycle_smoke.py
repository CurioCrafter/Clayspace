"""Foreground event-loop regression for startup, preferences, and file reload.

This script owns one test-only persistent app timer so Blender's real event
loop advances across a home-file reload. The add-on itself must own no app
timer and must never start XR in this probe.
"""

from __future__ import annotations

import addon_utils
import bpy
from bpy.app.handlers import persistent
import inspect
import os
from pathlib import Path
import sys
import traceback


EXPECTED = Path(os.environ["VRW_EXPECTED_INSTALL"]).resolve()
EXPECT_PRE_ENABLED = os.environ.get("VRW_EXPECT_PRE_ENABLED", "0") == "1"
MODULE_NAME = "vr_blender_workbench"
_phase = 0
_load_complete = False


def _module():
    module = sys.modules.get(MODULE_NAME)
    if module is None:
        raise AssertionError("VR Workbench module is not loaded")
    module_path = Path(module.__file__).resolve()
    if EXPECTED not in module_path.parents:
        raise AssertionError((EXPECTED, module_path))
    return module


def _assert_runtime_clean(label: str):
    module = _module()
    assert hasattr(bpy.types.Scene, "vrw_settings"), label
    assert (
        bpy.app.handlers.xr_session_start_pre.count(
            module.operators.xr_session_start_pre
        )
        == 1
    ), label
    assert module.runtime._draw_handle is None, label
    assert not hasattr(module.runtime, "_runtime_tick"), label
    for value in vars(module.runtime).values():
        if inspect.isfunction(value) and value.__module__ == module.runtime.__name__:
            assert not bpy.app.timers.is_registered(value), (label, value.__name__)


@persistent
def _loaded(_dummy=None):
    global _load_complete
    _load_complete = True


def _quit_with_failure():
    traceback.print_exc()
    print("VRW_STARTUP_NO_RUNTIME_TIMER_FAIL", flush=True)
    try:
        bpy.ops.wm.quit_blender()
    except Exception:
        pass


def _event_loop_probe():
    global _phase
    try:
        if _phase == 0:
            _assert_runtime_clean("before_homefile")
            _phase = 1
            bpy.ops.wm.read_homefile(use_empty=True)
            return 1.0
        if not _load_complete:
            return 0.1
        _assert_runtime_clean("after_homefile")
        print(
            "VRW_STARTUP_NO_RUNTIME_TIMER_PASS "
            f"pre_enabled={int(EXPECT_PRE_ENABLED)} "
            "handler_count=1 draw_handle=0 runtime_timers=0 after_file_load=1",
            flush=True,
        )
        if _loaded in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_loaded)
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        _quit_with_failure()
        return None


_default_enabled, pre_loaded = addon_utils.check(MODULE_NAME)
assert bool(pre_loaded) is EXPECT_PRE_ENABLED, (pre_loaded, EXPECT_PRE_ENABLED)
addon = addon_utils.enable(MODULE_NAME, default_set=True, persistent=True)
assert addon is not None
_assert_runtime_clean("after_enable")
bpy.ops.wm.save_userpref()
if _loaded not in bpy.app.handlers.load_post:
    bpy.app.handlers.load_post.append(_loaded)
bpy.app.timers.register(_event_loop_probe, first_interval=1.0, persistent=True)
