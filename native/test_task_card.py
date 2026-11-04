"""Controlled GUI test of native semantic card, explicitly SIMULATOR not optical.

Requires XRWB_TASK_TEST_ALLOW=1, isolated factory profile, camera layer disabled.
Hardware owner must coordinate execution. No application timer/global context use.
"""
import os
import shutil
import sys
import time
import traceback
from io import BytesIO
from importlib import import_module
from pathlib import Path

import bpy
from mathutils import Vector, Quaternion

assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1"
assert os.environ.get("SIMULATED_ENABLE") == "1"
assert os.environ.get("DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough") == "1"
assert not bpy.app.background
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
xr_workbench = import_module("xr_workbench")
editing = import_module("xr_workbench.editing")
hands = import_module("xr_workbench.hands")
interaction = import_module("xr_workbench.interaction")
image_gestures = import_module("xr_workbench.image_gestures")
if not hasattr(bpy.types.WindowManager, "xrwb_settings"):
    xr_workbench.register()
bpy.context.preferences.view.show_splash = False
bpy.context.window_manager.xr_session_settings.show_passthrough = False


class XRTEST_OT_task_card(bpy.types.Operator):
    bl_idname = "wm.xrtest_task_card"
    bl_label = "Native semantic task test"

    def invoke(self, context, event):
        self.window = context.window
        self.phase = 0
        self.due = time.monotonic() + .5
        self.deadline = time.monotonic() + 60
        self.timer = context.window_manager.event_timer_add(.15, window=self.window)
        self.original = tuple(context.scene.objects["Cube"].matrix_world)
        self.original_objects = set(context.scene.objects.keys())
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def toggle(self, context):
        area = next(a for a in self.window.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(window=self.window, area=area, region=region):
            return bpy.ops.wm.xr_session_toggle()

    def info(self, context):
        return tuple(bpy.types.XrSessionState.task_card_info(context))

    @staticmethod
    def image_pinch_packet(sequence, closed):
        points = [(480.0, 480.0)] * 21
        points[5], points[17], points[8] = (470.0, 480.0), (550.0, 480.0), (490.0, 480.0)
        points[4] = (475.0, 480.0) if closed else (430.0, 480.0)
        timestamp_ns = 1_000_000_000 + sequence * 50_000_000
        header = image_gestures.HEADER.pack(
            image_gestures.MAGIC, 1, 960, 960, sequence,
            timestamp_ns & 0xFFFFFFFF, timestamp_ns >> 32, 1, 0)
        record = image_gestures.RECORD.pack(
            0, 0, 0.99, 0.99, *(value for point in points for value in point))
        return image_gestures.read_frame(BytesIO(header + record))

    def send(self, phase, action=1, generation=None, pointer=0, cancel_only=False):
        # Snapshot viewer pose when recalling; card is intentionally stationary.
        xy = {
            1: (265, 200), 2: (735, 200), 3: (820, 360), 4: (820, 360),
            5: (200, 250), 6: (500, 250), 7: (200, 120), 8: (800, 250),
            9: (500, 120), 10: (800, 120),
            11: (265, 120), 12: (735, 120), 13: (265, 360),
            0: (500, 400),
        }
        x, y = xy[action]
        direction = self.rotation @ Vector(((x - 500) * .0008 * self.scale,
                                            (y - 250) * .0008 * self.scale,
                                            -.72 * self.scale))
        return bpy.ops.wm.xr_task_card_event(
            phase=phase, generation=self.generation if generation is None else generation,
            pointer_id=pointer, ray_origin=self.origin, ray_direction=direction,
            cancel_only=cancel_only)

    def support_pinch(self, context, action, start):
        x, y = {4: (820, 360), 13: (265, 360)}[action]
        direction = self.rotation @ Vector(((x - 500) * .0008 * self.scale,
                                            (y - 250) * .0008 * self.scale,
                                            -.72 * self.scale))
        tip = tuple(self.origin)
        proximal = tuple(self.origin - direction.normalized() * .1 * self.scale)
        primary = hands._capture
        result = False
        for offset, distance in ((0.0, .06), (.1, .015), (.16, .015), (.2, .06)):
            observation = hands.HandObservation(
                hand="LEFT", generation=7, timestamp=start + offset,
                thumb_tip=(tip[0] + distance * self.scale, tip[1], tip[2]),
                index_proximal=proximal, index_tip=tip,
                viewer_scale=self.scale, navigation_signature=(),
            )
            result = hands._process_support_cancel(context, primary, observation)
        return result

    def modal(self, context, event):
        if event.type != "TIMER" or time.monotonic() < self.due:
            return {"PASS_THROUGH"}
        try:
            assert time.monotonic() < self.deadline, "Native task test timeout"
            if self.phase == 0:
                assert "FINISHED" in self.toggle(context)
                self.phase = 1
                self.due = time.monotonic() + 2
            elif self.phase == 1:
                if not bpy.ops.wm.xr_task_card.poll():
                    return {"RUNNING_MODAL"}
                state = context.window_manager.xr_session_state
                self.origin = state.viewer_pose_location.copy()
                self.rotation = state.viewer_pose_rotation.copy()
                self.scale = bpy.types.XrSessionState.hand_tracking_world_transform(context)[0]
                assert "FINISHED" in bpy.ops.xrwb.task_card()
                pose = tuple(bpy.types.XrSessionState.task_card_pose(context))
                self.rotation = Quaternion(pose[3:7])
                self.origin = Vector(pose[:3]) + self.rotation @ Vector((0, 0, .72 * self.scale))
                self.generation = self.info(context)[1]
                # New presentation has not yet been composed.
                assert "CANCELLED" in self.send("MOVE")
                assert "CANCELLED" in bpy.ops.wm.xr_task_card_cycle_opacity(
                    generation=self.generation)
                self.phase = 2
                self.due = time.monotonic() + 1
            elif self.phase == 2:
                info = self.info(context)
                assert info[0] and info[1] == info[2] == self.generation, info
                assert "CANCELLED" in self.send("PRESS")  # Already pinched on entry.
                assert "FINISHED" in self.send("MOVE")
                assert "CANCELLED" in self.send("PRESS", pointer=1)
                assert "FINISHED" in self.send("PRESS")
                assert self.info(context)[3:5] == (0, 1)
                assert "CANCELLED" in bpy.ops.wm.xr_task_card_cycle_opacity(
                    generation=self.generation)
                assert "CANCELLED" in bpy.ops.wm.xr_task_card(recall=True)
                assert "CANCELLED" in bpy.ops.wm.xr_editor_surface_event(type="MOUSEMOVE")
                assert "FINISHED" in self.send("RELEASE")
                assert self.info(context)[5] == 25
                assert "CANCELLED" in self.send("RELEASE")
                assert "FINISHED" in self.send("MOVE", action=3)
                assert "FINISHED" in self.send("PRESS", action=3)
                assert "FINISHED" in self.send("RELEASE", action=3)
                self.session_generation = self.generation
                self.generation = self.info(context)[1]
                assert self.generation != self.session_generation
                assert "CANCELLED" in self.send("MOVE", action=5, generation=self.session_generation)
                self.phase = 3
                self.due = time.monotonic() + 1
            elif self.phase == 3:
                assert self.info(context)[1:3] == (self.generation, self.generation)
                if not hasattr(self, "image_pointer"):
                    assert "CANCELLED" in bpy.ops.wm.xr_task_card_cycle_opacity(
                        generation=self.generation - 1)
                    assert "FINISHED" in bpy.ops.wm.xr_task_card_cycle_opacity(
                        generation=self.generation)
                    assert self.info(context)[5] == 55
                    pinch = image_gestures.ImagePinch()
                    for sequence, closed, moment in ((1, False, 1.00), (2, True, 1.02),
                                                      (3, True, 1.13), (4, False, 1.15)):
                        assert image_gestures.process_frame(
                            context, self.image_pinch_packet(sequence, closed), pinch,
                            now=moment) == (sequence == 4)
                    assert self.info(context)[5] == 85
                    self.image_pointer = image_gestures.ImageCardPointer()
                    capture_path = os.environ.get("XRWB_TASK_CAPTURE_PATH")
                    if capture_path:
                        self.cursor_capture = Path(capture_path).with_name("native-cursor-eye.png")
                        os.environ["XRWB_TASK_CURSOR_CAPTURE_PATH"] = str(self.cursor_capture)
                    assert image_gestures.process_card_frame(
                        context, self.image_pinch_packet(1, False), self.image_pointer,
                        now=2.00) is False
                    self.due = time.monotonic() + .5
                    return {"RUNNING_MODAL"}
                if hasattr(self, "cursor_capture"):
                    assert self.cursor_capture.is_file(), "Image card cursor was not composed"
                for sequence, closed, moment in ((2, True, 2.02),
                                                  (3, True, 2.13), (4, False, 2.15)):
                    assert image_gestures.process_card_frame(
                        context, self.image_pinch_packet(sequence, closed), self.image_pointer,
                        now=moment) == (sequence == 4)
                assert context.window_manager.xrwb_settings.edit_tool == "move"
                assert self.info(context)[3:5] == (-1, 0)
                os.environ.pop("XRWB_TASK_CURSOR_CAPTURE_PATH", None)
                capture_path = os.environ.get("XRWB_TASK_CAPTURE_PATH")
                if capture_path:
                    capture = Path(capture_path)
                    assert capture.is_file()
                    shutil.copyfile(capture, capture.with_name("native-arrange-eye.png"))
                settings = context.window_manager.xrwb_settings
                for action, tool in ((6, "move"), (7, "scale"), (8, "rotate"),
                                     (5, "select")):
                    assert "FINISHED" in self.send("MOVE", action=action)
                    assert "FINISHED" in self.send("PRESS", action=action)
                    assert "FINISHED" in self.send("RELEASE", action=action)
                    assert settings.edit_tool == tool
                assert "FINISHED" in self.send("MOVE", action=10)
                assert "CANCELLED" in self.send("PRESS", action=10)
                assert settings.edit_tool == "select"
                assert "FINISHED" in bpy.ops.wm.xr_task_card(
                    recall=False, allow_simulated_input=True)
                self.generation = self.info(context)[1]
                self.phase = 4
                self.due = time.monotonic() + 1
            elif self.phase == 4:
                assert self.info(context)[1:3] == (self.generation, self.generation)
                assert "FINISHED" in self.send("MOVE", action=5)  # Recall kept Arrange.
                assert "FINISHED" in self.send("PRESS", action=5)
                assert "FINISHED" in self.send("RELEASE", action=5)
                assert "FINISHED" in self.send("MOVE", action=9)
                assert "FINISHED" in self.send("PRESS", action=9)
                assert "FINISHED" in self.send("RELEASE", action=9)
                assert len(set(context.scene.objects.keys()) - self.original_objects) == 1
                self.phase = 5
                self.due = time.monotonic() + .25
            elif self.phase == 5:
                assert self.info(context)[1:3] == (self.generation, self.generation)
                if not hasattr(self, "cancel_stage"):
                    assert bpy.ops.xrwb.history_undo.poll()
                    self.cancel_stage = 1
                    owner = interaction.CaptureOwner(
                        interaction.InputProvenance.SIMULATOR, "cancel-card", 0)
                    transaction = editing.begin(context, interaction.EditRequest(
                        owner, interaction.TransformTool.MOVE, 0,
                        interaction.Pose((0.0, 0.0, 0.0))))
                    self.cancel_transaction = transaction
                    target = context.view_layer.objects.active
                    self.cancel_target = target.name
                    self.cancel_matrix = target.matrix_world.copy()
                    assert transaction.update(context, interaction.InputSample(
                        owner, 0, interaction.Pose((0.2, 0.0, 0.0)), 1.0)).applied
                    assert target.matrix_world != self.cancel_matrix
                    hands._capture = hands._Capture(
                        "RIGHT", 7, "edit", owner, transaction,
                        navigation_signature=(),
                    )
                    self.router_marker = object()
                    hands._router_operator = self.router_marker
                    assert "CANCELLED" in self.send(
                        "MOVE", action=1, pointer=0, cancel_only=True)
                    assert "CANCELLED" in self.send(
                        "PRESS", action=1, pointer=0, cancel_only=True)
                    assert self.info(context)[5] == 85
                    assert "CANCELLED" in self.send("MOVE", action=4)
                    self.generation = self.info(context)[1]
                    self.due = time.monotonic() + 1
                    return {"RUNNING_MODAL"}
                if self.cancel_stage == 1:
                    assert not self.support_pinch(context, 4, 1.0)
                    assert editing.active_transaction() is self.cancel_transaction
                    self.cancel_stage = 2
                    self.generation = self.info(context)[1]
                    self.due = time.monotonic() + 1
                    return {"RUNNING_MODAL"}
                if self.cancel_stage == 2:
                    assert self.support_pinch(context, 13, 2.0)
                    assert editing.active_transaction() is None
                    assert hands._capture is None
                    assert hands._router_operator is self.router_marker
                    hands._router_operator = None
                    assert context.scene.objects[self.cancel_target].matrix_world == self.cancel_matrix
                    assert len(set(context.scene.objects.keys()) - self.original_objects) == 1
                    self.cancel_stage = 3
                    self.generation = self.info(context)[1]
                    self.due = time.monotonic() + 1
                    return {"RUNNING_MODAL"}
                assert bpy.ops.xrwb.history_undo.poll()
                owner = interaction.CaptureOwner(
                    interaction.InputProvenance.SIMULATOR, "history-guard", 0)
                transaction = editing.begin(context, interaction.EditRequest(
                    owner, interaction.TransformTool.MOVE, 0,
                    interaction.Pose((0.0, 0.0, 0.0))))
                assert not bpy.ops.xrwb.history_undo.poll()
                assert not bpy.ops.xrwb.history_redo.poll()
                transaction.cancel(context, owner)
                assert bpy.ops.xrwb.history_undo.poll()
                assert "FINISHED" in self.send("MOVE", action=11)
                assert "FINISHED" in self.send("PRESS", action=11)
                assert "FINISHED" in self.send("RELEASE", action=11)
                assert set(context.scene.objects.keys()) == self.original_objects
                self.generation = self.info(context)[1]
                self.phase = 6
                self.due = time.monotonic() + 1
            elif self.phase == 6:
                assert self.info(context)[1:3] == (self.generation, self.generation)
                assert "FINISHED" in self.send("MOVE", action=12)
                assert "FINISHED" in self.send("PRESS", action=12)
                assert "FINISHED" in self.send("RELEASE", action=12)
                assert len(set(context.scene.objects.keys()) - self.original_objects) == 1
                self.generation = self.info(context)[1]
                self.phase = 7
                self.due = time.monotonic() + 1
            elif self.phase == 7:
                assert self.info(context)[1:3] == (self.generation, self.generation)
                assert "FINISHED" in self.send("MOVE", action=11)
                assert "FINISHED" in self.send("PRESS", action=11)
                assert "FINISHED" in self.send("RELEASE", action=11)
                assert set(context.scene.objects.keys()) == self.original_objects
                self.generation = self.info(context)[1]
                self.phase = 8
                self.due = time.monotonic() + 1
            elif self.phase == 8:
                assert self.info(context)[1:3] == (self.generation, self.generation)
                assert "FINISHED" in self.send("MOVE", action=2)
                assert "FINISHED" in self.send("PRESS", action=2)
                assert "FINISHED" in self.send("CANCEL", action=2, generation=0)
                assert self.info(context)[3:5] == (-1, 0)
                assert bpy.types.XrSessionState.is_running(context)
                assert "FINISHED" in self.send("MOVE", action=2)
                assert "FINISHED" in self.send("PRESS", action=2)
                assert "CANCELLED" in self.send("RELEASE", action=2, generation=0)
                assert bpy.types.XrSessionState.is_running(context)
                assert "FINISHED" in self.send("MOVE", action=0)  # Backplate blocks scene input.
                assert "CANCELLED" in self.send("PRESS", action=0)
                assert "FINISHED" in self.send("MOVE", action=2)
                assert "FINISHED" in self.send("PRESS", action=2)
                assert "FINISHED" in self.send("RELEASE", action=2)  # Actual native exit.
                self.phase = 9
                self.due = time.monotonic() + 1
            else:
                assert not bpy.types.XrSessionState.is_running(context)
                assert tuple(context.scene.objects["Cube"].matrix_world) == self.original
                assert set(context.scene.objects.keys()) == self.original_objects
                assert not context.window_manager.xr_session_settings.show_passthrough
                print("XR_TASK_CARD_PASS simulator=1 composed=1 opacity=1 nonspatial_opacity=1 image_gesture_sim=1 image_card_pointer=1 image_cursor_composed=1 generation=1 capture=1 cancel=1 cancel_edit=1 support_cancel=1 cancel_no_history=1 arrange=1 tools=4 add_cube=1 history_undo=2 history_redo=1 history_guard=1 disabled_more=1 native_exit=1 scene_unchanged=1 optical_verified=0", flush=True)
                context.window_manager.event_timer_remove(self.timer)
                bpy.ops.wm.quit_blender()
                return {"FINISHED"}
            return {"RUNNING_MODAL"}
        except Exception:
            traceback.print_exc()
            if bpy.types.XrSessionState.is_running(context):
                self.toggle(context)
            context.window_manager.event_timer_remove(self.timer)
            print("XR_TASK_CARD_FAIL", flush=True)
            bpy.ops.wm.quit_blender()
            return {"CANCELLED"}


bpy.utils.register_class(XRTEST_OT_task_card)
bpy.ops.wm.xrtest_task_card("INVOKE_DEFAULT")
