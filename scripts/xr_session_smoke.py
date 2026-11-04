"""One owned XR start/stop attempt using valid window-event contexts.

XR drawing may clear Python's global context. An app-timer callback must not
query that context; this modal test receives Blender's actual event context.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
import traceback

import addon_utils
import bpy


bpy.context.preferences.view.show_splash = False
addon = addon_utils.enable("xr_workbench", default_set=False, persistent=True)
assert addon is not None
expected = Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
assert expected in Path(addon.__file__).resolve().parents
bpy.context.window_manager.xr_session_settings.show_passthrough = False
camera_test_consented = os.environ.get("XRWB_CAMERA_TEST_CONSENT") == "1"
controller_test = os.environ.get("XRWB_TEST_INDEX_CONTROLLERS") == "1"
if controller_test:
    assert not camera_test_consented
    addon.ui.configure_steamvr_session(bpy.context)


class XRWB_OT_session_probe(bpy.types.Operator):
    bl_idname = "xrwb.session_probe"
    bl_label = "Isolated XR Session Probe"

    def invoke(self, context, _event):
        self.phase = 0
        self.due = time.monotonic() + 0.5
        self.objects_before = set(context.scene.objects.keys())
        self.timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER" or time.monotonic() < self.due:
            return {"PASS_THROUGH"}
        try:
            if self.phase == 0:
                self.phase = 1
                bpy.ops.xrwb.refresh_status()
                bpy.ops.xrwb.place_overlay()
                bpy.ops.xrwb.toggle_overlay()
                try:
                    result = bpy.ops.xrwb.start_session()
                    print(f"XRWB_SESSION_REQUEST result={sorted(result)}", flush=True)
                    if camera_test_consented and "FINISHED" in result:
                        # Explicit consent is supplied only by the controlled camera test runner.
                        assert addon.ui.set_runtime_passthrough(
                            context.window_manager.xr_session_settings, True
                        )
                        context.window_manager.xrwb_settings.passthrough_opt_in = True
                except RuntimeError as exc:
                    print(f"XRWB_SESSION_REQUEST unavailable={exc}", flush=True)
                self.due = time.monotonic() + 3.0
                return {"RUNNING_MODAL"}
            if self.phase == 1:
                self.phase = 2
                from xr_workbench.diagnostics import collect_report, write_report

                report = collect_report(context)
                write_report(report, os.environ["XRWB_REPORT_PATH"])
                running = addon.runtime.session_running(context)
                overlay = addon.runtime.overlay_status()
                assert not overlay["last_draw_error"], overlay
                if os.environ.get("XRWB_REQUIRE_RUNNING") == "1":
                    assert running and overlay["xr_draw_count"] > 0, overlay
                if controller_test:
                    status = context.window_manager.xrwb_settings.controller_status
                    assert status.startswith("XRWB_CONTROLLERS_ACTIONMAP_PASS"), status
                    print("XRWB_INDEX_ACTIONSET_ATTACH_PASS runtime_session=1 physical_device=0", flush=True)
                self.draws_before_recall = overlay["xr_draw_count"]
                print(
                    f"XRWB_SESSION_OBSERVED running={int(running)} "
                    f"xr_draw_count={overlay['xr_draw_count']} camera_requested={int(camera_test_consented)}",
                    flush=True,
                )
                if camera_test_consented:
                    route = report["openxr"].get("composition_route")
                    status = report["openxr"].get("passthrough_status")
                    print(
                        f"XRWB_CAMERA_ROUTE route={route} status={status} freshness=unknown",
                        flush=True,
                    )
                    assert route == "fb_passthrough" and status == "active", report[
                        "openxr"
                    ]
                bpy.ops.xrwb.place_overlay()
                self.due = time.monotonic() + 3.0
                return {"RUNNING_MODAL"}
            if self.phase == 2:
                self.phase = 3
                overlay = addon.runtime.overlay_status()
                assert not overlay["last_draw_error"], overlay
                if os.environ.get("XRWB_REQUIRE_CONTINUOUS_DRAW") == "1":
                    assert overlay["xr_draw_count"] > self.draws_before_recall, overlay
                recall_status = (
                    "PASS"
                    if overlay["xr_draw_count"] > self.draws_before_recall
                    else "UNOBSERVED"
                )
                print(
                    f"XRWB_SESSION_RECALL_{recall_status} xr_draw_count={overlay['xr_draw_count']}",
                    flush=True,
                )
                bpy.ops.xrwb.stop_session()
                self.due = time.monotonic() + 1.0
                return {"RUNNING_MODAL"}
            assert not addon.runtime.session_exists(context)
            assert not context.window_manager.xr_session_settings.show_passthrough
            assert set(context.scene.objects.keys()) == self.objects_before
            context.window_manager.event_timer_remove(self.timer)
            addon_utils.disable("xr_workbench", default_set=False)
            print(
                "XRWB_SESSION_RECOVERY_PASS desktop_usable=1 scene_unchanged=1 camera_requested=0",
                flush=True,
            )
            bpy.ops.wm.quit_blender()
            return {"FINISHED"}
        except Exception:
            traceback.print_exc()
            try:
                bpy.ops.xrwb.stop_session()
            except Exception:
                traceback.print_exc()
            finally:
                context.window_manager.event_timer_remove(self.timer)
            print("XRWB_SESSION_RECOVERY_FAIL", flush=True)
            bpy.ops.wm.quit_blender()
            return {"CANCELLED"}


bpy.utils.register_class(XRWB_OT_session_probe)
bpy.ops.xrwb.session_probe("INVOKE_DEFAULT")
