"""One consented live Index-camera pinch check in the Monado desktop simulator."""

import os
import sys
import time
from importlib import import_module
from pathlib import Path

import bpy


assert os.environ.get("XRWB_LIVE_CAMERA_CONSENT") == "1"
assert os.environ.get("SIMULATED_ENABLE") == "1"
assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1"
assert os.environ.get("DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough") == "1"
assert not bpy.app.background

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
addon = import_module("xr_workbench")
gestures = import_module("xr_workbench.image_gestures")
if not hasattr(bpy.types.WindowManager, "xrwb_settings"):
    addon.register()
bpy.context.preferences.view.show_splash = False
bpy.context.window_manager.xr_session_settings.show_passthrough = False


class XRTEST_OT_live_image_gesture(bpy.types.Operator):
    bl_idname = "wm.xrtest_live_image_gesture"
    bl_label = "Live Index Image Gesture Probe"

    def invoke(self, context, _event):
        self.phase = 0
        self.started = time.monotonic()
        self.deadline = self.started + 50
        self.timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def finish(self, context, passed, reason=""):
        frames, actions = gestures.stream_stats()
        diag = gestures.stream_diagnostics()
        gestures.stop_stream(context)
        try:
            if bpy.types.XrSessionState.is_running(context):
                bpy.ops.xrwb.stop_session()
        except (AttributeError, RuntimeError):
            passed = False
            reason = "session_stop_failed"
        context.window_manager.event_timer_remove(self.timer)
        opacity = getattr(self, "last_opacity", 0)
        marker = "LIVE_IMAGE_GESTURE_PASS" if passed else "LIVE_IMAGE_GESTURE_FAIL"
        print(
            f"{marker} frames={frames} actions={actions} opacity={opacity} "
            f"records={diag.get('records', 0)} valid={sum(diag.get(k, 0) for k in ('open', 'closed', 'middle'))} "
            f"open={diag.get('open', 0)} closed={diag.get('closed', 0)} pressed={diag.get('pressed', 0)} "
            f"empty={diag.get('empty', 0)} low_score={diag.get('low_score', 0)} "
            f"ambiguous={diag.get('ambiguous', 0)} small={diag.get('small', 0)} "
            f"reason={reason or 'none'} optical_world=0 headset_verified=0",
            flush=True,
        )
        bpy.ops.wm.quit_blender()
        return {"FINISHED" if passed else "CANCELLED"}

    @staticmethod
    def current_opacity(context):
        return int(bpy.types.XrSessionState.task_card_info(context)[5])

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        now = time.monotonic()
        try:
            if now > self.deadline:
                return self.finish(context, False, "timeout")
            if self.phase == 0:
                if "FINISHED" not in bpy.ops.xrwb.start_session():
                    return self.finish(context, False, "session_start_failed")
                self.phase = 1
            elif self.phase == 1:
                if (
                    not bpy.ops.wm.xr_task_card.poll()
                    or not bpy.ops.xrwb.task_card.poll()
                ):
                    return {"RUNNING_MODAL"}
                if "FINISHED" not in bpy.ops.xrwb.task_card():
                    return {"RUNNING_MODAL"}
                self.phase = 2
            elif self.phase == 2:
                info = bpy.types.XrSessionState.task_card_info(context)
                if not info[0] or not info[1] or info[1] != info[2]:
                    return {"RUNNING_MODAL"}
                self.initial_opacity = int(info[5])
                self.last_opacity = self.initial_opacity
                self.observed_change = False
                result = bpy.ops.xrwb.image_gesture_stream(
                    executable=os.environ["XRWB_GESTURE_EXE"],
                    camera=os.environ["XRWB_GESTURE_CAMERA"],
                    ffmpeg=os.environ["XRWB_GESTURE_FFMPEG"],
                    consent=True,
                )
                if "RUNNING_MODAL" not in result:
                    return self.finish(context, False, "camera_stream_failed")
                self.watch_until = now + 20
                print(
                    f"LIVE_IMAGE_GESTURE_READY window_seconds=20 camera_pid={gestures.stream_pid()} optical_world=0",
                    flush=True,
                )
                self.phase = 3
            else:
                self.last_opacity = self.current_opacity(context)
                self.observed_change |= self.last_opacity != self.initial_opacity
                if now >= self.watch_until:
                    frames, actions = gestures.stream_stats()
                    return self.finish(
                        context,
                        frames > 0 and actions > 0 and self.observed_change,
                        "none" if actions else "no_gesture_action",
                    )
            return {"RUNNING_MODAL"}
        except Exception as exc:
            return self.finish(context, False, type(exc).__name__)


bpy.utils.register_class(XRTEST_OT_live_image_gesture)
bpy.ops.wm.xrtest_live_image_gesture("INVOKE_DEFAULT")
