"""Owned camera-off in-process simulator; real UI rendering, not hardware proof.

Run via run_xr_probe.ps1 -Mode hand-menu with the simulator runtime selected.
The same hand-menu generator also runs through the independent SteamVR smoke.
"""
import os
from pathlib import Path
import runpy
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
assert os.environ.get("XRWB_HAND_MENU_SIMULATOR") == "1"
assert os.environ.get("SIMULATED_ENABLE") == "1"
assert os.environ.get("XRWB_EDITOR_TEST_ALLOW") == "1"
assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1"
assert os.environ.get("DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough") == "1"
assert Path(os.environ["XR_RUNTIME_JSON"]).resolve() == (
    ROOT / "native/steamvr_free/monado-inproc-build/openxr_monado-dev.json"
).resolve()
PROFILE = Path(os.environ["BLENDER_USER_CONFIG"]).resolve().parent
assert PROFILE.parent == ROOT and PROFILE.name.startswith(".tmp_xr_probe_")
assert not bpy.app.background and not bpy.data.filepath
assert not any(os.environ.get(name) == "1" for name in (
    "XRWB_INDEX_CAMERA_CONSENT", "XRWB_INDEX_CAMERA_OVERLAY", "XRWB_INDEX_CAMERA_GESTURE",
))
bpy.ops.wm.read_homefile(use_empty=True)
sys.path.insert(0, str(ROOT))
import xr_workbench as addon  # noqa: E402

addon.register()
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.ui_scale = 1.5  # Match the production VR bootstrap.
bpy.context.window_manager.xr_session_settings.show_passthrough = False
bpy.context.window_manager.xrwb_settings.controller_fallback = True


def check_open_menu_lifecycle(context, output):
    """Stop with native UI still open, restart, and inspect the clean editor."""
    addon.controllers.stop_router(context)
    for cycle in range(2):
        assert bpy.ops.wm.xr_hand_menu(
            visible=True, panel="XRWB_PT_command_deck", allow_simulated_input=True
        ) == {"FINISHED"}
        while context.window_manager.xr_session_state.hand_menu_info(context)[5] < 2:
            yield
        assert context.window_manager.xr_session_state.hand_menu_info(context)[0]
        assert bpy.ops.wm.xr_session_toggle() == {"FINISHED"}
        while addon.runtime.session_exists(context):
            yield
        assert tuple(bpy.types.XrSessionState.hand_menu_info(context)) == (0, 0, 0, -1, 0, 0)
        assert addon.ui.XRWB_PT_workspace.poll(context) and addon.ui.XRWB_PT_panel.poll(context)
        assert not context.scene.objects
        if cycle == 0:
            assert bpy.ops.wm.xr_session_toggle() == {"FINISHED"}
            while not bpy.ops.wm.xr_editor_surface.poll():
                yield
            while bpy.ops.wm.xr_editor_surface(visible=True) != {"FINISHED"}:
                yield
            path = output / "desktop-after-open-menu-session-restart.png"
            assert not path.exists()
            assert bpy.ops.wm.xr_editor_surface_capture(filepath=str(path)) == {"FINISHED"}
            while not path.is_file() or path.stat().st_size < 8:
                yield
    print("XR_HAND_MENU_LIFECYCLE_PASS stop_open=2 restart=1 scene_unchanged=1 hardware=0", flush=True)


class XRTEST_OT_hand_menu(bpy.types.Operator):
    bl_idname = "wm.xrtest_hand_menu"
    bl_label = "Owned Native Hand Menu Simulator Check"

    def invoke(self, context, _event):
        self.deadline = time.monotonic() + 140
        self.phase = 0
        self.test = None
        self.features_done = False
        self.timer = context.window_manager.event_timer_add(0.15, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            assert time.monotonic() < self.deadline, "Native menu simulator timed out"
            with addon.ui._view3d_override(context):
                if self.phase == 0:
                    assert bpy.ops.wm.xr_session_toggle() == {"FINISHED"}
                    self.phase = 1
                elif self.phase == 1:
                    if not bpy.ops.wm.xr_editor_surface.poll():
                        return {"PASS_THROUGH"}
                    if bpy.ops.wm.xr_editor_surface(visible=True) != {"FINISHED"}:
                        return {"PASS_THROUGH"}
                    self.phase = 2
                elif self.phase == 2:
                    if not self.features_done:  # Lifecycle phase intentionally stops XR.
                        assert not addon.ui.XRWB_PT_workspace.poll(context)
                        assert not addon.ui.XRWB_PT_panel.poll(context)
                    if self.test is None:
                        if context.window_manager.xr_session_state.editor_surface_info(context)[5] < 2:
                            return {"PASS_THROUGH"}
                        module = runpy.run_path(str(ROOT / "scripts/xr_hand_menu_features_test.py"))
                        self.output = ROOT / "reports/hand-menu-simulator" / PROFILE.name.removeprefix(".tmp_xr_probe_")
                        self.test = module["run"](context, addon, self.output)
                    try:
                        next(self.test)
                    except StopIteration:
                        if not self.features_done:
                            self.features_done = True
                            self.test = check_open_menu_lifecycle(context, self.output)
                        else:
                            self.phase = 3
                elif not addon.runtime.session_exists(context):
                    assert not context.scene.objects
                    return self.finish(context, True)
        except Exception:
            traceback.print_exc()
            return self.finish(context, False)
        return {"PASS_THROUGH"}

    def finish(self, context, passed):
        context.window_manager.event_timer_remove(self.timer)
        if addon.runtime.session_exists(context):
            with addon.ui._view3d_override(context):
                bpy.ops.wm.xr_session_toggle()
        print(f"XR_HAND_MENU_SIMULATOR_{'PASS' if passed else 'FAIL'} camera=0 hardware=0", flush=True)
        bpy.ops.wm.quit_blender()
        return {"FINISHED"} if passed else {"CANCELLED"}


bpy.utils.register_class(XRTEST_OT_hand_menu)
window = bpy.context.window_manager.windows[0]
with bpy.context.temp_override(window=window):
    with addon.ui._view3d_override(bpy.context):
        bpy.ops.wm.xrtest_hand_menu("INVOKE_DEFAULT")
