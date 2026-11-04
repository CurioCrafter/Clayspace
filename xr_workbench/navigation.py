"""Moving yourself: grab the world with one or both hands, go to things, reset.

Blender draws the world as Nav(L, R, s) @ Base @ tracking. Writing "local"
for Base @ tracking, a hand at local point p appears in the world at
L + R (s p). Grabbing the world means keeping the world points under your
hands fixed while your hands move, so we solve for L, R and s directly:

* One hand: L = w - R (s p). The world slides with your hand.
* Two hands: s = |w2 - w1| / |p2 - p1| (pull apart to shrink the world, which
  makes you bigger), R turns only about the vertical (yaw, so the horizon never
  tilts), and L keeps the midpoint fixed. Size clicks at exactly 1x and ticks
  every 10 %.

Both hands' poses are read from the session state in the same frame, so they
always agree with the navigation they were computed from. Nothing here
touches scene data: navigation is the viewer, not the file.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Quaternion, Vector

from . import editing, viewer_scale

SNAP_ONE = 0.05  # Within 5 % of 1x, two-hand scaling clicks to exactly 1x.


def nav(state):
    return Vector(state.navigation_location), Quaternion(state.navigation_rotation), float(state.navigation_scale)


def to_local(world, location, rotation, scale):
    return rotation.inverted() @ (Vector(world) - location) / scale


def to_world(local, location, rotation, scale):
    return location + rotation @ (Vector(local) * scale)


def yaw_of(vector):
    return math.atan2(vector.y, vector.x)


def solve_one(anchor_world, hand_local, rotation, scale):
    """Navigation location that puts the grabbed world point back under the hand."""
    return Vector(anchor_world) - rotation @ (Vector(hand_local) * scale)


def solve_two(anchors, hands, start_rotation, start_scale, scale_limits=(viewer_scale.MIN_SCALE,
                                                                        viewer_scale.MAX_SCALE)):
    """(L, R, s) keeping two world anchors under two hands; yaw-only turn, uniform scale."""
    w1, w2 = Vector(anchors[0]), Vector(anchors[1])
    p1, p2 = Vector(hands[0]), Vector(hands[1])
    local_span, world_span = p2 - p1, w2 - w1
    if local_span.length < 1.0e-4 or world_span.length < 1.0e-9:
        return None
    scale = world_span.length / local_span.length
    if abs(scale - 1.0) < SNAP_ONE:
        scale = 1.0
    scale = max(scale_limits[0], min(scale_limits[1], scale))
    # Yaw that turns the hands' horizontal span onto the anchors' horizontal span.
    rotated_span = start_rotation @ local_span
    flat_world, flat_local = Vector((world_span.x, world_span.y, 0.0)), Vector((rotated_span.x, rotated_span.y, 0.0))
    turn = 0.0
    if flat_world.length > 1.0e-6 and flat_local.length > 1.0e-6:
        turn = yaw_of(flat_world) - yaw_of(flat_local)
    rotation = Quaternion((0.0, 0.0, 1.0), turn) @ start_rotation
    middle_world, middle_local = (w1 + w2) / 2.0, (p1 + p2) / 2.0
    location = middle_world - rotation @ (middle_local * scale)
    return location, rotation, scale


class WorldGrab:
    """One- or two-hand world grab. Plain data; the controller feeds it poses."""

    def __init__(self, state, hand, hand_world):
        location, rotation, scale = nav(state)
        self.hands = {hand: tuple(to_world(to_local(hand_world, location, rotation, scale),
                                           location, rotation, scale))}
        self.order = [hand]
        self.start = (location, rotation, scale)
        self.readout = "Moving the world"

    def join(self, state, hand, hand_world):
        """A second hand joins: re-anchor both hands where they are now."""
        location, rotation, scale = nav(state)
        self.hands[hand] = tuple(Vector(hand_world))
        self.order = [self.order[0], hand]
        self.rebase(state, {h: self.hands[h] for h in self.order})

    def leave(self, state, hand, hands_world):
        if hand in self.hands and len(self.order) == 2:
            self.order.remove(hand)
            del self.hands[hand]
            self.rebase(state, {self.order[0]: hands_world[self.order[0]]})

    def rebase(self, state, hands_world):
        """World points currently under the hands become the new anchors."""
        self.hands = {hand: tuple(Vector(world)) for hand, world in hands_world.items()}
        self.start = nav(state)

    def update(self, state, hands_world):
        """Apply navigation for the hands' current world positions (same frame)."""
        location, rotation, scale = nav(state)
        locals_ = [to_local(hands_world[h], location, rotation, scale) for h in self.order]
        if len(self.order) == 1:
            new_location = solve_one(self.hands[self.order[0]], locals_[0], rotation, scale)
            state.navigation_location = new_location
            self.readout = "Moving the world"
            return
        solved = solve_two([self.hands[h] for h in self.order], locals_, self.start[1], self.start[2])
        if solved is None:
            return
        new_location, new_rotation, new_scale = solved
        state.navigation_location = new_location
        state.navigation_rotation = new_rotation
        state.navigation_scale = new_scale
        turned = math.degrees((new_rotation @ self.start[1].inverted()).angle)
        self.readout = f"You ×{new_scale:.2f}" + (f" · turned {turned:.0f}°" if turned >= 1.0 else "")

    def detent(self, state):
        scale = float(state.navigation_scale)
        return (round(math.log(scale) / math.log(1.1)), scale == 1.0)


# ---- Go to, reset -----------------------------------------------------------------


def go_to_selection(context) -> str:
    """Stand in front of the selection at a comfortable distance, facing it."""
    from . import runtime
    if not runtime.session_running(context):
        raise editing.EditRejected("Start XR to travel")
    objects = [obj for obj in context.selected_objects]
    if not objects:
        raise editing.EditRejected("Select something to go to")
    corners = [obj.matrix_world @ Vector(c) for obj in objects for c in obj.bound_box]
    low = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    high = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    centre, size = (low + high) / 2.0, max((high - low).length, 1.0e-3)
    state = context.window_manager.xr_session_state
    location, rotation, scale = nav(state)
    head = Vector(state.viewer_pose_location)
    towards = centre - head
    towards.z = 0.0
    if towards.length < 1.0e-6:
        towards = rotation @ Vector((0.0, 1.0, 0.0))
    # Stop so the selection fills a comfortable view: about its own size away, at least 0.6 m.
    distance = max(0.6 * scale, size * 0.9)
    target_head = centre - towards.normalized() * distance
    target_head.z = head.z + (centre.z - head.z) * 0.5 if abs(centre.z - head.z) > 1.5 * scale else head.z
    state.navigation_location = location + (target_head - head)
    return f"Moved to {objects[0].name if len(objects) == 1 else f'{len(objects)} objects'}"


def reset_view(context) -> str:
    """Normal size, upright, standing where the scene origin is under your feet."""
    from . import runtime
    if not runtime.session_running(context):
        raise editing.EditRejected("Start XR to reset your view")
    state = context.window_manager.xr_session_state
    state.navigation_scale = 1.0
    state.navigation_rotation = Quaternion()
    state.navigation_location = Vector((0.0, 0.0, 0.0))
    return "View reset: normal size, upright, at the scene origin"


class XRWB_OT_travel(bpy.types.Operator):
    bl_idname = "xrwb.travel"
    bl_label = "Travel"
    bl_description = "Go to the selection, or reset your view to normal size at the origin"
    action: bpy.props.EnumProperty(items=(("GO_TO", "Go to Selection", ""), ("RESET", "Reset View", "")),
                                   options={"SKIP_SAVE"})

    def execute(self, context):
        from . import controllers, interaction_log
        if controllers._active_gesture is not None or editing.active_transaction() is not None:
            interaction_log.emit("travel", "Release the current tool first", context, level="WARNING")
            return {"CANCELLED"}
        try:
            message = go_to_selection(context) if self.action == "GO_TO" else reset_view(context)
        except (editing.EditRejected, AttributeError, RuntimeError) as exc:
            interaction_log.emit("travel", str(exc), context, level="WARNING")
            return {"CANCELLED"}
        interaction_log.emit("travel", message, context)
        return {"FINISHED"}


CLASSES = (XRWB_OT_travel,)
