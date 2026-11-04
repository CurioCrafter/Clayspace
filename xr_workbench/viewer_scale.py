"""Change your size in the scene through Blender's native XR navigation scale.

Blender draws the world as Nav(location, rotation, scale) @ Base @ tracking, so
``navigation_scale`` is how many world units one physical metre covers. Larger
means you are a giant; smaller means you shrink to work on fine detail. Every
brush and handle in this add-on is sized in physical metres times that scale.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Quaternion, Vector

from . import editing, runtime

MIN_SCALE = 0.01
MAX_SCALE = 100.0
# (identifier, label, your size relative to normal, description)
PRESETS = (
    ("GIANT", "Giant x10", 10.0, "Tower over the scene to block out layouts"),
    ("LARGE", "Large x3", 3.0, "Arrange rooms and big props"),
    ("HUMAN", "Human x1", 1.0, "Real-world size"),
    ("DETAIL", "Detail 1/4", 0.25, "Shrink to sculpt and paint small features"),
    ("MICRO", "Micro 1/20", 0.05, "Become tiny for the finest details"),
)
WORKFLOWS = (("MODEL", "Model", "Shape objects"), ("SCULPT", "Sculpt", "Clay tools"),
             ("SHADE", "Shade", "Materials and spray"), ("ANIMATE", "Animate", "Keys and time"),
             ("STAGE", "Stage", "Lights, camera, render, physics"))
# Size remembered per workflow for this session only; user files stay untouched.
_workflow_scales: dict[str, float] = {}


def clamp_scale(value: float) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("Viewer size must be a finite positive number")
    return max(MIN_SCALE, min(MAX_SCALE, float(value)))


def rescaled_location(navigation_location, head_world, old_scale, new_scale):
    """Navigation location that keeps the eyes at the same world point.

    head = L + R (s q)  =>  L' = head + (s'/s)(L - head).
    """
    if old_scale <= 0.0 or new_scale <= 0.0:
        raise ValueError("Scales must be positive")
    head = Vector(head_world)
    return head + (Vector(navigation_location) - head) * (new_scale / old_scale)


def world_units_per_metre(context) -> float:
    if not runtime.session_running(context):
        return 1.0
    value = float(bpy.types.XrSessionState.hand_tracking_world_transform(context)[0]) \
        if hasattr(bpy.types.XrSessionState, "hand_tracking_world_transform") \
        else (float(context.window_manager.xr_session_state.navigation_scale)
              * float(context.window_manager.xr_session_settings.base_scale))
    if not math.isfinite(value) or not 1.0e-6 <= value <= 1.0e6:
        raise editing.EditRejected("XR world scale is outside the supported range")
    return value


def viewer_frame(context):
    """(location, rotation, world units per metre) in front of the headset.

    Without a running session the 3D cursor stands in, so desktop and tests
    place things predictably.
    """
    if runtime.session_running(context):
        state = context.window_manager.xr_session_state
        return (Vector(state.viewer_pose_location), Quaternion(state.viewer_pose_rotation),
                world_units_per_metre(context))
    cursor = context.scene.cursor
    return Vector(cursor.location) + Vector((0.0, 0.7, 0.0)), Quaternion((0.7071068, 0.7071068, 0, 0)), 1.0


def current_scale(context) -> float:
    state = getattr(context.window_manager, "xr_session_state", None)
    return float(state.navigation_scale) if state is not None and runtime.session_running(context) else 1.0


def set_scale(context, value: float) -> float:
    """Apply a new size around the head. Refused while any edit is captured."""
    from . import controllers

    if not runtime.session_running(context):
        raise editing.EditRejected("Start XR to change your size")
    if editing.active_transaction() is not None or controllers._active_gesture is not None:
        raise editing.EditRejected("Release the current tool before changing size")
    state = context.window_manager.xr_session_state
    old = float(state.navigation_scale)
    new = clamp_scale(value)
    if old <= 0.0 or not math.isfinite(old):
        raise editing.EditRejected("Native navigation scale is invalid")
    state.navigation_location = rescaled_location(
        state.navigation_location, state.viewer_pose_location, old, new)
    state.navigation_scale = new
    settings = context.window_manager.xrwb_settings
    _workflow_scales[settings.workflow] = new
    return new


def remembered_scale(workflow: str) -> float | None:
    return _workflow_scales.get(workflow)


def reset_memory() -> None:
    _workflow_scales.clear()


class XRWB_OT_viewer_scale(bpy.types.Operator):
    bl_idname = "xrwb.viewer_scale"
    bl_label = "Change Your Size"
    bl_description = "Grow or shrink yourself around your head; tools keep their physical size"

    preset: bpy.props.EnumProperty(
        items=(("NONE", "Step", ""), *((key, label, desc) for key, label, _value, desc in PRESETS)),
        default="NONE", options={"SKIP_SAVE"})
    factor: bpy.props.FloatProperty(default=1.0, min=0.1, max=10.0, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not runtime.session_running(context):
            cls.poll_message_set("Start XR to change your size")
            return False
        return True

    def execute(self, context):
        from . import interaction_log

        target = (next(value for key, _label, value, _desc in PRESETS if key == self.preset)
                  if self.preset != "NONE" else current_scale(context) * self.factor)
        try:
            value = set_scale(context, target)
        except (editing.EditRejected, ValueError, AttributeError, RuntimeError) as exc:
            interaction_log.emit("rejected", str(exc), context, level="WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        interaction_log.emit("scale", f"Your size: x{value:g}. Tools keep their physical size.", context)
        return {"FINISHED"}


CLASSES = (XRWB_OT_viewer_scale,)
