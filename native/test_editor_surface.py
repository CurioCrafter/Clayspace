"""Controlled factory-startup GUI/XR smoke, coordinated with the hardware owner.

Requires XRWB_EDITOR_TEST_ALLOW=1, XRWB_TASK_TEST_ALLOW=1 and a rendering OpenXR
runtime (the camera-off simulator is sufficient; it does not prove headset appearance).
Set DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough=1 to exclude the camera layer.
Optional XRWB_EDITOR_CAPTURE_DIR saves real editor textures (not the camera).
Optional XRWB_EDITOR_TEST_SEPARATED=1 requests separated native panels. This smoke
checks composition/lifecycle/dispatch; native/test_editor_input.py checks crop ray geometry.
Optional XRWB_EDITOR_TEST_TRANSFORM=1 checks rig move/resize/query round trips and capture rejection.
Never run against a user's open scene/profile. No bpy.app.timer context access.
"""
import os
from pathlib import Path
import time
import traceback

import bpy
from mathutils import Quaternion, Vector

assert os.environ.get("XRWB_EDITOR_TEST_ALLOW") == "1", "Controlled test opt-in required"
assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1", "Synthetic input test opt-in required"
assert not bpy.app.background, "This test needs a real GUI window"
bpy.context.preferences.view.show_splash = False
bpy.context.window_manager.xr_session_settings.show_passthrough = False


class XRTEST_OT_editor(bpy.types.Operator):
    bl_idname = "wm.xrtest_editor"
    bl_label = "Native XR Editor Test"

    def invoke(self, context, event):
        self.phase = 0
        self.due = time.monotonic() + 0.5
        self.deadline = time.monotonic() + 60
        self.timer = context.window_manager.event_timer_add(0.15, window=context.window)
        self.window = context.window
        self.capture_dir = os.environ.get("XRWB_EDITOR_CAPTURE_DIR")
        self.separated = os.environ.get("XRWB_EDITOR_TEST_SEPARATED") == "1"
        self.test_transform = os.environ.get("XRWB_EDITOR_TEST_TRANSFORM") == "1"
        self.eye_capture_path = os.environ.pop("XRWB_EDITOR_EYE_CAPTURE_PATH", None)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def key(self, kind, text="", **modifiers):
        for value in ("PRESS", "RELEASE"):
            result = bpy.ops.wm.xr_editor_surface_event(
                type=kind, value=value, unicode=text if value == "PRESS" else "", **modifiers)
            assert "FINISHED" in result, (kind, value, result)

    def toggle_session(self, context):
        area = next(a for a in self.window.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(window=self.window, area=area, region=region):
            return bpy.ops.wm.xr_session_toggle()

    def capture(self, name):
        if self.capture_dir:
            target = Path(self.capture_dir) / f"{name}.png"
            assert target.parent.is_dir(), target.parent
            assert "FINISHED" in bpy.ops.wm.xr_editor_surface_capture(filepath=str(target.resolve()))

    def transform_round_trip(self, context):
        state = bpy.types.XrSessionState
        original = tuple(state.editor_surface_pose(context))
        assert original[7] > 0 and original[8] > 0
        rotation = Quaternion(original[3:7])
        moved = Vector(original[:3]) + rotation @ Vector((original[7] * 0.1, 0, 0))
        try:
            assert "FINISHED" in bpy.ops.wm.xr_editor_surface_transform(
                position=moved, rotation=original[3:7])
            actual = tuple(state.editor_surface_pose(context))
            assert (Vector(actual[:3]) - moved).length < 1e-5
            assert abs(actual[7] - original[7]) < 1e-5
            origin = moved + rotation @ Vector((0, 0, original[7]))
            direction = rotation @ Vector((0, 0, -2))
            hit = tuple(state.editor_surface_hit(context, ray_origin=origin, ray_direction=direction))
            assert hit[0] == 1 and hit[1] == 0 and abs(hit[4] - original[7]) < 1e-4, hit
            assert "FINISHED" in bpy.ops.wm.xr_editor_surface_transform(
                position=moved, rotation=original[3:7], width=original[7] * 1.1)
            actual = tuple(state.editor_surface_pose(context))
            assert abs(actual[7] - original[7] * 1.1) < 1e-5
        finally:
            assert "FINISHED" in bpy.ops.wm.xr_editor_surface_transform(
                position=original[:3], rotation=original[3:7], width=original[7])
        print("XR_EDITOR_TRANSFORM_API_PASS moved=1 resized=1 restored=1 hit_query=1", flush=True)

    def modal(self, context, event):
        if event.type != "TIMER" or time.monotonic() < self.due:
            return {"PASS_THROUGH"}
        try:
            assert time.monotonic() < self.deadline, "Editor test timeout"
            if self.phase == 0:
                assert "FINISHED" in self.toggle_session(context)
                self.phase = 1
                self.due = time.monotonic() + 2
            elif self.phase == 1:
                if not bpy.ops.wm.xr_editor_surface.poll():
                    return {"RUNNING_MODAL"}
                options = {"separated": True} if self.separated else {}
                assert "FINISHED" in bpy.ops.wm.xr_editor_surface(
                    visible=True, recall=True, **options)
                self.phase = 2
                self.due = time.monotonic() + 1
            elif self.phase == 2:
                state = tuple(bpy.types.XrSessionState.editor_surface_info(context))
                assert state[0] and state[1] > 0 and state[2] > 0 and state[5] > 0, state
                self.frames_before = state[5]
                if self.test_transform:
                    self.transform_round_trip(context)
                if self.eye_capture_path:
                    os.environ["XRWB_EDITOR_EYE_CAPTURE_PATH"] = self.eye_capture_path
                area = next(a for a in self.window.screen.areas if a.type == "VIEW_3D")
                region = next(r for r in area.regions if r.type == "WINDOW")
                u = (region.x + region.width / 2) / (state[1] - 1)
                v = (region.y + region.height / 2) / (state[2] - 1)
                self.uv = (u, v)
                assert "FINISHED" in bpy.ops.wm.xr_editor_surface_event(
                    type="MOUSEMOVE", value="NOTHING", use_ray=False, u=u, v=v)
                self.capture("editor-window")
                self.phase = 3
                self.due = time.monotonic() + 0.5
            elif self.phase == 3:
                cube = context.scene.objects.get("Cube")
                assert cube is not None, "Factory Cube required"
                for obj in context.selected_objects:
                    obj.select_set(False)
                cube.select_set(True)
                context.view_layer.objects.active = cube
                self.original_x = cube.location.x
                self.key("G")
                self.key("X")
                self.key("ONE", "1")
                self.key("RET")
                self.phase = 4
                self.due = time.monotonic() + 1
            elif self.phase == 4:
                assert abs(context.scene.objects["Cube"].location.x - self.original_x - 1) < 1e-4
                print("XR_EDITOR_NUMERIC_TRANSFORM_PASS", flush=True)
                self.key("Z", ctrl=True)
                self.phase = 5
                self.due = time.monotonic() + 1
            elif self.phase == 5:
                assert abs(context.scene.objects["Cube"].location.x - self.original_x) < 1e-4
                print("XR_EDITOR_UNDO_PASS", flush=True)
                self.key("F3")
                self.phase = 6
                self.due = time.monotonic() + 0.5
            elif self.phase == 6:
                for letter in "cube":
                    self.key(letter.upper(), letter)
                self.phase = 7
                self.due = time.monotonic() + 0.5
            elif self.phase == 7:
                self.capture("editor-search-popup")
                self.phase = 8
                self.due = time.monotonic() + 0.5
            elif self.phase == 8:
                self.key("ESC")
                state = tuple(bpy.types.XrSessionState.editor_surface_info(context))
                assert state[5] > self.frames_before, state
                u, v = self.uv
                assert "FINISHED" in bpy.ops.wm.xr_editor_surface_event(
                    type="LEFTMOUSE", value="PRESS", use_ray=False, u=u, v=v)
                assert bpy.types.XrSessionState.editor_surface_info(context)[3] == 0
                if self.test_transform:
                    pose = tuple(bpy.types.XrSessionState.editor_surface_pose(context))
                    assert "CANCELLED" in bpy.ops.wm.xr_editor_surface_transform(
                        position=pose[:3], rotation=pose[3:7])
                    assert tuple(bpy.types.XrSessionState.editor_surface_pose(context)) == pose
                    print("XR_EDITOR_TRANSFORM_CAPTURE_GUARD_PASS", flush=True)
                assert "FINISHED" in bpy.ops.wm.xr_editor_surface(visible=False)
                state = tuple(bpy.types.XrSessionState.editor_surface_info(context))
                assert state[0] == 0 and state[3] == -1 and state[4] == 0, state
                self.phase = 9
                self.due = time.monotonic() + 0.5
            elif self.phase == 9:
                assert "FINISHED" in self.toggle_session(context)
                self.phase = 10
                self.due = time.monotonic() + 1
            else:
                assert not bpy.types.XrSessionState.is_running(context)
                assert not context.window_manager.xr_session_settings.show_passthrough
                context.window_manager.event_timer_remove(self.timer)
                print("XR_EDITOR_SURFACE_PASS composed=1 numeric=1 undo=1 capture_released=1 "
                      f"camera_requested=0 separated_requested={int(self.separated)}", flush=True)
                bpy.ops.wm.quit_blender()
                return {"FINISHED"}
            return {"RUNNING_MODAL"}
        except Exception:
            traceback.print_exc()
            if bpy.ops.wm.xr_editor_surface.poll():
                bpy.ops.wm.xr_editor_surface(visible=False)
            if bpy.types.XrSessionState.is_running(context):
                self.toggle_session(context)
            context.window_manager.event_timer_remove(self.timer)
            print("XR_EDITOR_SURFACE_FAIL", flush=True)
            bpy.ops.wm.quit_blender()
            return {"CANCELLED"}


bpy.utils.register_class(XRTEST_OT_editor)
bpy.ops.wm.xrtest_editor("INVOKE_DEFAULT")
