"""Radial tool wheel: press, flick the hand toward a wedge, press again.

The wheel opens as a flat ring in front of the hand that pressed its button,
turned to face you. Moving that hand a few centimetres toward a wedge
highlights it (a haptic tick on every change); the same button or the trigger
picks it, and pressing in the middle closes the wheel. Wedges follow the
current workflow (Model, Sculpt, Shade, Animate) unless a fixed set is chosen
in Settings. Only plain values are cached for the XR draw callback.
"""
from __future__ import annotations

import math
from time import monotonic

import bpy
from mathutils import Matrix, Quaternion, Vector

# (kind, value, label). kind: "tool" sets the trigger tool, "op" runs an
# operator path with keyword arguments, "menu" opens a hand menu, "toggle"
# flips a boolean setting.
MORE = ("menu", "XRWB_MT_quick", "More ›")
WHEELS = {
    "MODEL": (("tool", "select", "Select"), ("tool", "extrude", "Extrude"),
              ("tool", "extrude_free", "Free Extrude"), ("tool", "inset", "Inset"),
              ("menu", "XRWB_MT_edit_mesh", "Edit V·E·F ›"), ("tool", "cut_difference", "Cut Away"),
              ("menu", "XRWB_MT_modifiers", "Modifiers ›"), MORE),
    "SCULPT": (("tool", "sculpt_clay", "Clay"), ("tool", "sculpt_grab", "Grab"),
               ("tool", "sculpt_smooth", "Smooth"), ("tool", "sculpt_flatten", "Flatten"),
               ("toggle", "sculpt_invert", "Dig"), ("toggle", "sculpt_mirror_x", "Mirror X"),
               ("menu", "XRWB_MT_sdf", "SDF ›"), MORE),
    "SHADE": (("tool", "spray_material", "Spray"), ("tool", "eyedropper", "Eyedropper"),
              ("tool", "uv_island", "UV Island"), ("op", ("xrwb.material_shelf", {}), "Shelf"),
              ("op", ("xrwb.node_garden", {"action": "TOGGLE"}), "Node Garden"),
              ("op", ("xrwb.uv_action", {"action": "SMART"}), "Smart UV"),
              ("tool", "paint_vertex", "Paint Colour"), MORE),
    "ANIMATE": (("tool", "scrub_time", "Scrub"), ("op", ("xrwb.anim_key", {}), "Key"),
                ("op", ("xrwb.anim_play", {}), "Play"), ("tool", "select", "Select"),
                ("tool", "place_object", "Place"), ("tool", "measure", "Measure"), MORE),
    "EDIT": (("op", ("xrwb.component_mode", {"mode": "VERT"}), "Vertex"),
             ("op", ("xrwb.component_mode", {"mode": "EDGE"}), "Edge"),
             ("op", ("xrwb.component_mode", {"mode": "FACE"}), "Face"),
             ("tool", "edit_extrude", "Extrude"), ("tool", "select", "Select"),
             ("op", ("xrwb.mesh_action", {"action": "DELETE"}), "Delete"),
             ("op", ("xrwb.component_mode", {"mode": "OBJECT"}), "Object Mode"),
             ("menu", "XRWB_MT_edit_mesh", "Mesh ›")),
    "STAGE": (("op", ("xrwb.light", {"kind": "POINT"}), "Light Here"),
              ("op", ("xrwb.light", {"kind": "SPOT"}), "Spot Here"),
              ("op", ("xrwb.camera", {"action": "VIEW"}), "Camera Here"),
              ("op", ("xrwb.render", {"action": "LIVE"}), "Live Render"),
              ("op", ("xrwb.render", {"action": "STILL"}), "Render"),
              ("tool", "draw_curve", "3D Pen"), ("menu", "XRWB_MT_studio", "Studio ›"), MORE),
}
WHEEL_SETS = (("AUTO", "Follow Workflow", "Wedges for the current workflow"),
              ("MODEL", "Modeling", "Extrude, inset, bevel, cut"),
              ("SCULPT", "Sculpting", "Clay brushes and symmetry"),
              ("SHADE", "Shading", "Spray, UVs, materials, nodes"),
              ("ANIMATE", "Animation", "Keys, playback and scrubbing"),
              ("STAGE", "Stage", "Lights, camera, render, pen"))
DEAD_ZONE = 0.28  # Fraction of the radius where nothing is highlighted.
LABEL_FONT_PX = 40

_state: dict = {}


def is_open() -> bool:
    return bool(_state)


def items_for(settings) -> tuple:
    if getattr(bpy.context, "mode", "") == "EDIT_MESH":
        return WHEELS["EDIT"]  # Editing components: vertex / edge / face tools first.
    chosen = str(getattr(settings, "wheel_set", "AUTO"))
    if chosen == "AUTO":
        chosen = str(getattr(settings, "workflow", "MODEL"))
    return WHEELS.get(chosen, WHEELS["MODEL"])


def open_wheel(context, hand: str, position, viewer_rotation, units: float) -> str:
    settings = context.window_manager.xrwb_settings
    items = items_for(settings)
    radius = float(getattr(settings, "wheel_size_m", 0.11)) * units
    rotation = Quaternion(viewer_rotation).normalized()
    _state.clear()
    _state.update(hand=hand, centre=tuple(position), rotation=tuple(rotation), radius=radius,
                  items=items, highlight=-1, current=str(settings.edit_tool), units=units,
                  opened=monotonic())
    return f"Tool wheel: {len(items)} choices; move toward one, press again"


def close() -> None:
    _state.clear()


def wedge_at(offset_x: float, offset_y: float, radius: float, count: int) -> int:
    """Wedge index for an offset in the wheel plane; -1 inside the dead zone.

    Wedge 0 is straight up, then clockwise, matching how labels are drawn.
    """
    if count <= 0 or math.hypot(offset_x, offset_y) < radius * DEAD_ZONE:
        return -1
    angle = math.atan2(offset_x, offset_y) % math.tau  # 0 = up, clockwise positive.
    return int((angle + math.pi / count) // (math.tau / count)) % count


def follow(hand_position) -> bool:
    """Update the highlight from the wheel hand; True when it changed."""
    if not _state:
        return False
    rotation = Quaternion(_state["rotation"])
    offset = Vector(hand_position) - Vector(_state["centre"])
    right, up = rotation @ Vector((1.0, 0.0, 0.0)), rotation @ Vector((0.0, 1.0, 0.0))
    index = wedge_at(offset.dot(right), offset.dot(up), _state["radius"], len(_state["items"]))
    changed = index != _state["highlight"]
    _state["highlight"] = index
    return changed


def highlighted():
    if not _state or _state["highlight"] < 0:
        return None
    return _state["items"][_state["highlight"]]


def pick(context) -> tuple[bool, str]:
    """Run the highlighted wedge and close; the middle just closes."""
    item = highlighted()
    hand = _state.get("hand", "")
    close()
    if item is None:
        return True, "Tool wheel closed"
    kind, value, label = item
    settings = context.window_manager.xrwb_settings
    try:
        if kind == "tool":
            result = bpy.ops.xrwb.set_edit_tool(tool=value)
            return "FINISHED" in result, f"Tool: {label}"
        if kind == "toggle":
            setattr(settings, value, not getattr(settings, value))
            return True, f"{label}: {'on' if getattr(settings, value) else 'off'}"
        if kind == "menu":
            from . import controllers
            return controllers._open_hand_native_menu(context, value, hand)
        path, arguments = value
        group, name = path.split(".")
        operator = getattr(getattr(bpy.ops, group), name)
        return "FINISHED" in operator(**arguments), label
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"{label}: {exc}"


# ---- Drawing (reads only the cached plain values in _state) ----

def _wheel_matrix():
    rotation = Quaternion(_state["rotation"])
    return Matrix.Translation(Vector(_state["centre"])) @ rotation.to_matrix().to_4x4()


OPEN_SECONDS = 0.16
LIFT = 0.07  # The highlighted wedge moves out by this fraction of the radius.


def geometry(segments_per_wedge: int = 10, highlight: int = -1, grow: float = 1.0):
    """Triangles of each wedge in wheel-plane units (grow animates the opening)."""
    count = len(_state["items"])
    radius = _state["radius"] * grow
    inner = radius * DEAD_ZONE
    gap = 0.035
    wedges = []
    for index in range(count):
        lift = radius * LIFT if index == highlight else 0.0
        centre = index * math.tau / count
        start = centre - math.pi / count + gap
        end = centre + math.pi / count - gap
        offset = (math.sin(centre) * lift, math.cos(centre) * lift)
        points = []
        for step in range(segments_per_wedge + 1):
            angle = start + (end - start) * step / segments_per_wedge
            # Clockwise from straight up: x = sin, y = cos.
            points.append(((math.sin(angle) * inner + offset[0], math.cos(angle) * inner + offset[1]),
                           (math.sin(angle) * radius + offset[0], math.cos(angle) * radius + offset[1])))
        triangles = []
        for (a_in, a_out), (b_in, b_out) in zip(points, points[1:]):
            triangles += [a_in, a_out, b_out, a_in, b_out, b_in]
        wedges.append(triangles)
    return wedges


def disc(radius, segments=32):
    return [(0.0, 0.0)] + [(math.sin(i * math.tau / segments) * radius, math.cos(i * math.tau / segments) * radius)
                           for i in range(segments + 1)]


def label_position(index: int, grow: float = 1.0):
    count = len(_state["items"])
    angle = index * math.tau / count
    distance = _state["radius"] * grow * (DEAD_ZONE + 1.0) / 2.0
    if index == _state.get("highlight", -1):
        distance += _state["radius"] * grow * LIFT
    return math.sin(angle) * distance, math.cos(angle) * distance


def opening(now=None) -> float:
    """0..1 eased progress of the opening animation."""
    from . import polish
    if not _state:
        return 0.0
    return polish.ease_out(((monotonic() if now is None else now) - _state["opened"]) / OPEN_SECONDS)


def draw() -> None:
    """World-space wheel; called from the XR draw callback with gpu state set."""
    if not _state:
        return
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader

    from . import polish

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    items, highlight, current = _state["items"], _state["highlight"], _state["current"]
    grow = 0.6 + 0.4 * opening()
    fade = opening()
    radius = _state["radius"] * grow
    with gpu.matrix.push_pop():
        gpu.matrix.multiply_matrix(_wheel_matrix())
        for index, triangles in enumerate(geometry(highlight=highlight, grow=grow)):
            kind, value, _label = items[index]
            if index == highlight:
                colour = (*polish.ACCENT, 0.95 * fade)
            elif kind == "tool" and value == current:
                colour = (0.16, 0.26, 0.4, 0.88 * fade)
            else:
                colour = (*polish.PLATE, polish.PLATE_ALPHA * fade)
            shader.uniform_float("color", colour)
            batch_for_shader(shader, "TRIS", {"pos": triangles}).draw(shader)
        # Centre: names what a press will do right now.
        shader.uniform_float("color", (*polish.PLATE, 0.9 * fade))
        batch_for_shader(shader, "TRI_FAN", {"pos": disc(radius * (DEAD_ZONE - 0.04))}).draw(shader)
        font = 0
        blf.size(font, LABEL_FONT_PX)
        scale = _state["radius"] / 9.0 / LABEL_FONT_PX
        for index, (kind, value, label) in enumerate(items):
            x, y = label_position(index, grow)
            width, height = blf.dimensions(font, label)
            chosen = index == highlight
            with gpu.matrix.push_pop():
                gpu.matrix.translate((x, y, 0.001 * _state["units"]))
                gpu.matrix.scale_uniform(scale * (1.12 if chosen else 1.0))
                colour = polish.TEXT if chosen or kind != "tool" or value != current else polish.ACCENT
                blf.color(font, *colour, fade)
                blf.position(font, -width / 2.0, -height / 2.0, 0.0)
                blf.draw(font, label)
        centre_text = items[highlight][2] if highlight >= 0 else "Close"
        width, height = blf.dimensions(font, centre_text)
        with gpu.matrix.push_pop():
            gpu.matrix.translate((0.0, 0.0, 0.002 * _state["units"]))
            fit = min(1.0, radius * (DEAD_ZONE - 0.08) * 2.0 / max(1.0, width * scale))
            gpu.matrix.scale_uniform(scale * 0.9 * fit)
            blf.color(font, *(polish.TEXT if highlight >= 0 else polish.MUTED), fade)
            blf.position(font, -width / 2.0, -height / 2.0, 0.0)
            blf.draw(font, centre_text)


CLASSES = ()
