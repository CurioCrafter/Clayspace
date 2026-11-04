"""Animate in VR with Blender's own keyframes, timeline and auto-key setting.

Scrubbing time is a physical drag tool (see physical_tools.ScrubStroke). These
operators key, unkey and step through the selected objects' real actions, so the
Dope Sheet and Graph Editor on the desktop panel always show the same data.
"""
from __future__ import annotations

import bpy
from bpy_extras import anim_utils

from . import editing

TRANSFORM_PATHS = ("location", "scale")


def _rotation_path(obj):
    return {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(
        obj.rotation_mode, "rotation_euler")


def _targets(context):
    return [obj for obj in context.selected_editable_objects if not obj.library and "xrwb_swatch" not in obj.keys()]


def _fcurves(obj):
    data = obj.animation_data
    if data is None or data.action is None:
        return ()
    bag = anim_utils.animdata_get_channelbag_for_assigned_slot(data)
    return tuple(bag.fcurves) if bag is not None else ()


def key_frames(objects) -> list[float]:
    frames = {point.co.x for obj in objects for curve in _fcurves(obj) for point in curve.keyframe_points}
    return sorted(frames)


def insert_keys(context, objects) -> int:
    frame = context.scene.frame_current
    count = 0
    for obj in objects:
        for path in (*TRANSFORM_PATHS, _rotation_path(obj)):
            if obj.keyframe_insert(path, frame=frame, group="Object Transforms"):
                count += 1
    return count


def delete_keys(context, objects) -> int:
    frame = context.scene.frame_current
    count = 0
    for obj in objects:
        for path in (*TRANSFORM_PATHS, _rotation_path(obj)):
            try:
                count += int(bool(obj.keyframe_delete(path, frame=frame)))
            except RuntimeError:
                pass  # Nothing keyed on this channel at this frame.
    return count


def autokey_after_transform(context) -> int:
    """Native auto-key for controller transforms; returns keyed channels (0 when off)."""
    if not context.scene.tool_settings.use_keyframe_insert_auto:
        return 0
    count = insert_keys(context, _targets(context))
    if count:
        _undo_push("XR Auto Key")
    return count


def _undo_push(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in bpy.ops.ed.undo_push(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("animate", message, context, level=level)


def _guard(context):
    if context.mode != "OBJECT":
        return "Switch to Object Mode to key objects"
    if editing.active_transaction() is not None:
        return "Release or cancel the current edit first"
    return ""


class XRWB_OT_anim_key(bpy.types.Operator):
    bl_idname = "xrwb.anim_key"
    bl_label = "Key Selected"
    bl_description = "Insert or delete location, rotation and scale keys on the current frame"
    delete: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        reason = _guard(context) or ("" if editing.undo_available(context) else "Enable Global Undo")
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def execute(self, context):
        objects = _targets(context)
        if not objects:
            _log(context, "Select the objects to key", "WARNING")
            return {"CANCELLED"}
        count = (delete_keys if self.delete else insert_keys)(context, objects)
        if not count:
            _log(context, "No keys on this frame" if self.delete else "Nothing could be keyed", "WARNING")
            return {"CANCELLED"}
        _undo_push("XR Delete Keys" if self.delete else "XR Insert Keys")
        verb = "Deleted" if self.delete else "Keyed"
        _log(context, f"{verb} {count} channels on frame {context.scene.frame_current}.")
        return {"FINISHED"}


class XRWB_OT_anim_frame(bpy.types.Operator):
    bl_idname = "xrwb.anim_frame"
    bl_label = "Change Frame"
    bl_description = "Step frames or jump between the selected objects' keys"
    jump: bpy.props.EnumProperty(items=(("STEP", "Step", ""), ("PREV_KEY", "Previous Key", ""),
                                        ("NEXT_KEY", "Next Key", ""), ("START", "Start", ""),
                                        ("END", "End", "")), default="STEP", options={"SKIP_SAVE"})
    delta: bpy.props.IntProperty(default=1, options={"SKIP_SAVE"})

    def execute(self, context):
        scene = context.scene
        current = scene.frame_current
        if self.jump == "STEP":
            frame = max(0, current + self.delta)
        elif self.jump == "START":
            frame = scene.frame_start
        elif self.jump == "END":
            frame = scene.frame_end
        else:
            frames = key_frames(_targets(context) or context.scene.objects)
            candidates = ([f for f in frames if f > current + 0.5] if self.jump == "NEXT_KEY"
                          else [f for f in frames if f < current - 0.5])
            if not candidates:
                _log(context, "No more keys in that direction", "WARNING")
                return {"CANCELLED"}
            frame = int(round(candidates[0] if self.jump == "NEXT_KEY" else candidates[-1]))
        scene.frame_set(frame)
        _log(context, f"Frame {frame}")
        return {"FINISHED"}


class XRWB_OT_anim_play(bpy.types.Operator):
    bl_idname = "xrwb.anim_play"
    bl_label = "Play / Pause"
    bl_description = "Toggle native playback"

    def execute(self, context):
        from . import ui

        with ui._view3d_override(context):
            try:
                result = bpy.ops.screen.animation_play()
            except RuntimeError as exc:
                _log(context, f"Playback unavailable: {exc}", "WARNING")
                return {"CANCELLED"}
        return result


CLASSES = (XRWB_OT_anim_key, XRWB_OT_anim_frame, XRWB_OT_anim_play)
