"""Pointing at the hand menu: which hand, where, and a tick per row.

The native hand menu reports its world pose and size. With that we know,
for either controller, exactly where its ray meets the menu, using the same
maths as the native hit test: a plane at local z = 0 facing +Z, centred on
the pose, v growing upward. That lets us:

* hover with whichever hand is actually pointing at the menu, instead of
  the hand that last pulled a trigger (usually the one holding the menu);
* draw a bright cursor dot where the ray touches the menu;
* give a light tick each time the ray crosses into another row, so moving
  down a list feels like running a finger down it.
"""
from __future__ import annotations

import math

from mathutils import Quaternion, Vector

ROW_PIXELS = 20.0  # Blender's UI_UNIT_Y at 1x; scaled by the UI scale.
_state = {"row": None, "hand": ""}
cursor: dict = {}


def menu_pose(context):
    """(position, rotation, width, height) of the visible hand menu, or None."""
    try:
        values = tuple(context.window_manager.xr_session_state.hand_menu_pose(context))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return None
    if len(values) < 9 or values[7] <= 0.0 or values[8] <= 0.0:
        return None
    return Vector(values[:3]), Quaternion(values[3:7]).normalized(), values[7], values[8]


def hit(pose, origin, direction):
    """(u, v, distance, world point) where a ray meets the menu, or None."""
    if pose is None:
        return None
    position, rotation, width, height = pose
    direction = Vector(direction)
    if direction.length < 1.0e-9:
        return None
    inverse = rotation.inverted()
    local_origin = inverse @ (Vector(origin) - position)
    local_direction = inverse @ direction.normalized()
    if local_origin.z <= 0.0 or local_direction.z >= -1.0e-7:
        return None  # Behind the menu, or pointing away from it.
    distance = -local_origin.z / local_direction.z
    x = local_origin.x + local_direction.x * distance
    y = local_origin.y + local_direction.y * distance
    u, v = x / width + 0.5, y / height + 0.5
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0) or not math.isfinite(distance):
        return None
    return u, v, distance, position + rotation @ Vector((x, y, 0.0))


def choose_hand(pose, rays, preferred=""):
    """The hand whose ray is on the menu: the preferred one if it is, else any that is."""
    ordered = sorted(rays.items(), key=lambda item: item[0] != preferred)
    for path, (origin, direction) in ordered:
        if hit(pose, origin, direction) is not None:
            return path
    return ""


def row_of(v, pixel_height, ui_scale):
    """Menu row under a v coordinate (0 at the top)."""
    return int(max(0.0, (1.0 - v)) * pixel_height // max(1.0, ROW_PIXELS * ui_scale))


def update(context, rays, preferred=""):
    """Refresh the cursor and return (hand, row_changed) for the hand on the menu."""
    pose = menu_pose(context)
    cursor.clear()
    if pose is None:
        _state.update(row=None, hand="")
        return "", False
    hand = choose_hand(pose, rays, preferred)
    if not hand:
        _state.update(row=None, hand="")
        return "", False
    u, v, _distance, point = hit(pose, *rays[hand])
    cursor.update(point=tuple(point), normal=tuple(pose[1] @ Vector((0.0, 0.0, 1.0))),
                  size=min(pose[2], pose[3]) * 0.012, hand=hand)
    try:
        from . import controllers
        pixel_height = controllers.hand_menu_info(context)[2]
        ui_scale = float(context.preferences.system.ui_scale)
    except (AttributeError, IndexError, TypeError, ValueError):
        pixel_height, ui_scale = 0, 1.0
    row = row_of(v, pixel_height, ui_scale) if pixel_height else None
    changed = row is not None and _state["hand"] == hand and _state["row"] is not None and row != _state["row"]
    _state.update(row=row, hand=hand)
    return hand, changed


CLASSES = ()
