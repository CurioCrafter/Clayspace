"""Bounded real-launch check: same bootstrap, native panels, stop, own-process quit.

No camera, user-scene save, simulated runtime, or physical visibility claim. This runs
only through the explicit SteamVR launcher's -SmokeTest option.
Feature checks use explicitly synthetic controller events on its empty scene.
"""
import os
from pathlib import Path
import runpy
import time
import traceback

import bpy

assert os.environ.get("XRWB_STEAMVR_SMOKE") == "1"
assert os.environ.get("XRWB_STEAMVR") == "1"
assert os.environ.get("XRWB_AUTO_START_XR") == "1"
assert not os.environ.get("SIMULATED_ENABLE")
assert not bpy.app.background
os.environ["XRWB_TASK_TEST_ALLOW"] = "1"  # Owned camera-off synthetic menu placement only.
boot = runpy.run_path(str(Path(__file__).with_name("xr_launch_bootstrap.py")))
addon = boot["addon"]


class XRWB_OT_steamvr_smoke(bpy.types.Operator):
    bl_idname = "xrwb.steamvr_smoke"
    bl_label = "Automatic SteamVR Startup Check"

    def invoke(self, context, _event):
        self.started = time.monotonic()
        self.stopped = False
        self.frames = 0
        self.features = None
        self.features_done = False
        self.objects = set(context.scene.objects.keys())
        self.timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        print("XRWB_STEAMVR_SMOKE_BEGIN timeout_seconds=150 simulated=0 synthetic_controller_events=1 camera=0", flush=True)
        return {"RUNNING_MODAL"}

    def finish(self, context, passed):
        context.window_manager.event_timer_remove(self.timer)
        print(
            f"XRWB_STEAMVR_SMOKE_{'PASS' if passed else 'FAIL'} "
            f"panel_frames={self.frames} simulated=0 synthetic_controller_events=1 camera=0 physical_visibility_verified=0",
            flush=True,
        )
        # Only this explicitly owned, empty diagnostic Blender process exits.
        bpy.ops.wm.quit_blender()
        return {"FINISHED"} if passed else {"CANCELLED"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            assert time.monotonic() - self.started < 150, "Real SteamVR startup/stop timed out"
            assert not context.window_manager.xr_session_settings.show_passthrough
            status_text = Path(os.environ["XRWB_AUTO_START_STATUS_PATH"]).read_text(encoding="utf-8-sig")
            assert "XRWB_AUTO_START_FAIL " not in status_text, "Actual launcher bootstrap failed; see status log"
            if self.stopped:
                if addon.runtime.session_exists(context):
                    return {"PASS_THROUGH"}
                assert set(context.scene.objects.keys()) == self.objects, "Startup changed the empty scene"
                return self.finish(context, True)
            if not addon.runtime.session_running(context):
                return {"PASS_THROUGH"}
            assert not addon.ui.XRWB_PT_workspace.poll(context)
            assert not addon.ui.XRWB_PT_panel.poll(context)
            status = context.window_manager.xrwb_settings.controller_status
            family = context.window_manager.xrwb_settings.controller_profile
            contract = addon.controllers.action_spec_summary(family)
            expected_actions = contract["total_actions"]
            assert status == (f"XRWB_CONTROLLERS_ACTIONMAP_PASS actions={expected_actions} "
                              f"bindings={contract['total_bindings']} profile={family}"), status
            info = context.window_manager.xr_session_state.editor_surface_info(context)
            self.frames = int(info[5])
            if self.features is None and (not info[0] or self.frames < 60):
                return {"PASS_THROUGH"}
            if os.environ.get("XRWB_PERSONAL_ADDONS") == "1":
                assert not boot["personal_report"]["failed"], "Personal add-on loading failed"
            if not self.features_done:
                if self.features is None:
                    module = runpy.run_path(str(Path(__file__).with_name("xr_controller_features_test.py")))
                    output = Path(__file__).resolve().parents[1] / "reports" / "controller-features" / os.environ["XRWB_NATIVE_LOG_SESSION"]
                    output.mkdir(parents=True, exist_ok=True)
                    self.features = module["run"](context, addon, output)
                with addon.ui._view3d_override(context):
                    try:
                        next(self.features)
                    except StopIteration:
                        self.features_done = True
                return {"PASS_THROUGH"}
            print(f"XRWB_STEAMVR_SMOKE_OBSERVED panels=1 actions={expected_actions} panel_frames={self.frames}", flush=True)
            assert bpy.ops.xrwb.stop_session("EXEC_DEFAULT") == {"FINISHED"}
            self.stopped = True
        except Exception:
            traceback.print_exc()
            try:
                bpy.ops.xrwb.stop_session("EXEC_DEFAULT")
            except Exception:
                traceback.print_exc()
            return self.finish(context, False)
        return {"PASS_THROUGH"}


bpy.utils.register_class(XRWB_OT_steamvr_smoke)
# read_homefile in the real bootstrap clears the global window context.
window = bpy.context.window_manager.windows[0]
area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
with bpy.context.temp_override(window=window, area=area, region=region):
    bpy.ops.xrwb.steamvr_smoke("INVOKE_DEFAULT")
