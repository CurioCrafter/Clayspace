"""Tool-coloured ray endings drawn in the headset, plus hover haptic ticks.

A main-thread timer ray-casts each tracked controller aim into the scene and
caches plain coordinates. The XR draw callback only reads that cache: XR draw
has no usable bContext (see edge_editing). Blender's own controller rays stay
on, so if this overlay fails the native ray is still there.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import (controllers, editing, interaction_log, menu_pointer, modifier_tower, node_garden, polish, runtime,
               tool_wheel, undo_ring)

TOOL_COLOURS = {
    "select": (0.95, 0.95, 1.0), "move": (0.95, 0.95, 1.0), "rotate": (0.95, 0.95, 1.0),
    "scale": (0.95, 0.95, 1.0), "extrude": (0.2, 0.9, 1.0), "face_scale": (0.2, 0.9, 1.0),
    "select_edge": (0.08, 0.65, 1.0), "bevel_edge": (0.08, 0.65, 1.0),
    "draw_cube": (0.3, 1.0, 0.45), "draw_sphere": (0.3, 1.0, 0.45), "draw_cylinder": (0.3, 1.0, 0.45),
    "cut_difference": (1.0, 0.25, 0.2), "cut_union": (0.75, 1.0, 0.2), "cut_intersect": (0.75, 0.4, 1.0),
    "sculpt_grab": (1.0, 0.6, 0.2), "sculpt_clay": (1.0, 0.6, 0.2), "sculpt_smooth": (1.0, 0.6, 0.2),
    "spray_material": (1.0, 1.0, 1.0), "scrub_time": (1.0, 0.9, 0.2),
    "place_object": (0.3, 1.0, 0.45), "measure": (1.0, 0.85, 0.2),
    "sculpt_flatten": (1.0, 0.6, 0.2), "eyedropper": (0.9, 0.9, 1.0),
    "extrude_free": (0.2, 0.9, 1.0), "inset": (0.2, 0.9, 1.0), "uv_island": (0.95, 0.45, 0.85),
    "draw_curve": (0.3, 1.0, 0.45), "paint_vertex": (0.9, 0.25, 0.2), "edit_extrude": (1.0, 0.62, 0.15),
    "place_prefab": (0.3, 1.0, 0.45), "sdf_blob": (1.0, 0.6, 0.2),
}
DRAW_TIP_M = 0.2  # Draw tools start 20 cm ahead of the controller (draw_editing).
GHOST_M = 0.05
SEGMENTS = 32
_cache: dict[str, tuple] = {}
# Shape outlines: (line points, colour, alpha). Ghosts before a press, live
# outlines of what is being drawn or placed while the trigger is held.
_shapes: list[tuple] = []
# Button labels: (world matrix of the text's lower-left, lines). Text units are
# font pixels, scaled so one line is HINT_LINE_M tall in the world.
_hints: list[tuple] = []
# Node garden links: (points, colour, alpha), drawn whatever the ring setting.
_links: list[tuple] = []
# Translucent fills: (triangle points, colour, alpha), drawn under the lines.
_fills: list[tuple] = []
FACE_TOOLS = {"extrude", "extrude_free", "inset", "face_scale", "uv_island"}
HINT_LINE_M = 0.011
HINT_FONT_PX = 48
_hovered: dict[str, str] = {}
# Latest aim pose per hand (plain tuples) for menu actions that place things at your hand.
last_poses: dict[str, tuple] = {}
_handle = None
_was_running = False
_last_error = ""


def brush_radius(settings, tool, units):
    if tool.startswith("sculpt_"):
        return float(settings.sculpt_radius_m) * units
    if tool in {"spray_material", "paint_vertex"}:
        return float(settings.spray_radius_m) * units
    return 0.012 * units


def tool_colour(settings, tool, hit_object=None):
    colour = TOOL_COLOURS.get(tool, (1.0, 1.0, 1.0))
    if tool == "paint_vertex":
        return tuple(settings.paint_colour)[:3]
    if tool in {"spray_material", "eyedropper"}:
        name = settings.spray_material
        if hit_object is not None and "xrwb_swatch" in hit_object.keys():
            name = hit_object["xrwb_swatch"]  # Show what trigger would load.
        material = bpy.data.materials.get(name)
        if material is not None:
            colour = tuple(material.diffuse_color)[:3]
    return colour


def ring(centre, normal, radius, segments=SEGMENTS):
    """Closed ring as LINE pairs in the plane facing ``normal``."""
    normal = Vector(normal).normalized() if Vector(normal).length > 1e-9 else Vector((0, 0, 1))
    tangent = normal.orthogonal().normalized()
    bitangent = normal.cross(tangent)
    lift = normal * radius * 0.02  # Sit just above the surface.
    points = [Vector(centre) + lift + (tangent * math.cos(a) + bitangent * math.sin(a)) * radius
              for a in (2 * math.pi * i / segments for i in range(segments))]
    return [p for i in range(segments) for p in (points[i], points[(i + 1) % segments])]


def _circle(radius, z, segments=24):
    points = [Vector((radius * math.cos(a), radius * math.sin(a), z))
              for a in (2 * math.pi * i / segments for i in range(segments))]
    return [p for i in range(segments) for p in (points[i], points[(i + 1) % segments])]


def shape_lines(kind):
    """Unit-sized (1 x 1 x 1, centred) wireframe as LINE point pairs."""
    if kind == "SPHERE":
        ring = _circle(0.5, 0.0)
        return (ring + [Vector((p.x, 0.0, p.y)) for p in ring] + [Vector((0.0, p.x, p.y)) for p in ring])
    if kind in {"CYLINDER", "CONE"}:
        lines = _circle(0.5, -0.5)
        if kind == "CYLINDER":
            lines += _circle(0.5, 0.5)
        for angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
            base = Vector((0.5 * math.cos(angle), 0.5 * math.sin(angle), -0.5))
            lines += [base, Vector((base.x, base.y, 0.5)) if kind == "CYLINDER" else Vector((0.0, 0.0, 0.5))]
        return lines
    if kind == "TORUS":
        return _circle(0.5, 0.0) + _circle(0.3, 0.0)
    if kind == "PLANE":
        square = [Vector(v) for v in ((-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0))]
        return [p for i in range(4) for p in (square[i], square[(i + 1) % 4])]
    corners = [Vector((x, y, z)) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8)
             if sum(abs(corners[a][i] - corners[b][i]) > 0.5 for i in range(3)) == 1]
    return [corners[i] for edge in edges for i in edge]


def outline(kind, matrix):
    return [tuple(matrix @ point) for point in shape_lines(kind)]


def poses_direction(poses, gesture):
    pose = poses.get(getattr(gesture, "user_path", ""))
    return pose[2] if pose is not None else Vector((0.0, 0.0, 1.0))


def face_loop(points):
    """LINES pairs around a polygon."""
    return [p for i in range(len(points)) for p in (points[i], points[(i + 1) % len(points)])]


def face_fan(points):
    """TRIS of a polygon (a fan: exact for the convex faces modelling produces)."""
    return [p for i in range(1, len(points) - 1) for p in (points[0], points[i], points[i + 1])]


def face_highlight(points, normal, colour, units, arrow=True):
    """Fill, outline and (optionally) a short normal arrow for one face; appends the fill."""
    _fills.append((face_fan(points), colour, 0.28))
    lines = face_loop(points)
    if arrow:
        centre = sum((Vector(p) for p in points), Vector()) / len(points)
        tip = centre + Vector(normal).normalized() * 0.06 * units
        side = Vector(normal).orthogonal().normalized() * 0.012 * units
        back = tip - Vector(normal).normalized() * 0.018 * units
        lines += [tuple(centre), tuple(tip), tuple(tip), tuple(back + side), tuple(tip), tuple(back - side)]
    return lines


EDIT_COLOUR = (1.0, 0.62, 0.15)  # Blender's selection orange.


def _edit_hover(context, pose, units):
    """In Edit Mode: the vertex, edge or face the trigger would pick, in the mode's shape."""
    from . import component_mode
    if pose is None:
        return []
    obj, bm = component_mode.edit_bmesh(context)
    if bm is None:
        return []
    origin, _rotation, direction = pose
    mode = component_mode.current_mode(context)
    try:
        element, _location = component_mode.pick(obj, bm, origin, direction, mode)
    except (ReferenceError, ValueError):
        return []
    if element is None:
        return []
    points = component_mode.element_points(obj, element)
    if mode == "VERT":
        rotation = Quaternion(pose[1])
        towards = rotation @ Vector((0.0, 0.0, 1.0))
        return [(ring(points[0], towards, 0.008 * units, 12), EDIT_COLOUR, 1.0),
                (ring(points[0], towards, 0.003 * units, 8), EDIT_COLOUR, 1.0)]
    if mode == "EDGE":
        a, b = Vector(points[0]), Vector(points[1])
        offset = (b - a).orthogonal().normalized() * 0.0015 * units
        return [([tuple(a), tuple(b), tuple(a + offset), tuple(b + offset), tuple(a - offset), tuple(b - offset)],
                 EDIT_COLOUR, 1.0)]
    normal = (obj.matrix_world.inverted_safe().transposed().to_3x3() @ element.normal).normalized()
    return [(face_highlight(points, normal, EDIT_COLOUR, units, arrow=False), EDIT_COLOUR, 1.0)]


def _hover_face(obj, face_index, tool, colour, units):
    """The face a face tool would act on, highlighted before you press."""
    if (obj is None or obj.type != "MESH" or tool not in FACE_TOOLS
            or any(m.show_viewport for m in obj.modifiers) or not 0 <= face_index < len(obj.data.polygons)):
        return None
    polygon = obj.data.polygons[face_index]
    world = obj.matrix_world
    points = [tuple(world @ obj.data.vertices[i].co) for i in polygon.vertices]
    normal = (world.inverted_safe().transposed().to_3x3() @ polygon.normal).normalized()
    return face_highlight(points, normal, colour, units, arrow=tool in {"extrude", "extrude_free"}), colour, 1.0


def _face_preview(transaction, colour, units):
    """The moving face, filled and outlined; where it started; the new side edges."""
    caps, start, sides = transaction.preview_outline()
    shapes = []
    if start:
        shapes.append((face_loop(start), colour, 0.35))
    for cap in caps:
        if len(cap) >= 3:
            shapes.append((face_highlight(cap, transaction.normal, colour, units, arrow=False), colour, 1.0))
    if sides:
        shapes.append(([p for pair in sides for p in pair], colour, 0.8))
    return shapes


def _preview_shapes(context, settings, tool, units, poses):
    """Ghost of what the next press makes, or the live outline of what is being made."""
    from . import draw_editing, face_editing, physical_tools

    shapes = []
    colour = tool_colour(settings, tool)
    transaction = getattr(controllers._active_gesture, "transaction", None)
    if isinstance(transaction, face_editing.FaceTransaction):
        return _face_preview(transaction, TOOL_COLOURS.get(transaction.tool, colour), units)
    from . import component_mode
    if isinstance(transaction, component_mode.EditExtrudeStroke):
        faces = [f for f in transaction.bm.faces if f.select][:400]
        world = transaction.obj.matrix_world
        normal = transaction.normal_world or Vector((0.0, 0.0, 1.0))
        for face in faces:
            points = [tuple(world @ v.co) for v in face.verts]
            shapes.append((face_highlight(points, normal, colour, units, arrow=False), colour, 1.0))
        if not faces:
            shapes.append(([tuple(world @ v.co) for e in transaction.bm.edges if e.select for v in e.verts],
                           colour, 1.0))
        return shapes
    if context.mode == "EDIT_MESH" and controllers._active_gesture is None:
        shapes += _edit_hover(context, poses.get(controllers._pointer_hand), units)
        return shapes
    if isinstance(transaction, draw_editing.DrawTransaction):
        if transaction.obj is not None:
            shapes.append((outline(transaction.shape, transaction.obj.matrix_world), colour, 1.0))
        return shapes
    from . import node_garden, uv_tools
    if isinstance(transaction, node_garden.NodeLinkStroke):
        shapes.append((list(transaction.lines), transaction.colour, 1.0))
        return shapes
    from . import creative_tools, prefabs, sdf_modeling
    if isinstance(transaction, prefabs.PrefabStroke):
        root = transaction.objects[0]
        frame = Matrix.LocRotScale(root.matrix_world.translation, root.matrix_world.to_quaternion(), None)
        if len(transaction.objects) > 1:
            corners = [o.matrix_world @ Vector(c) for o in transaction.objects for c in o.bound_box]
            centre = sum(corners, Vector()) / len(corners)
            frame = Matrix.Translation(centre)
        dims = Vector(tuple(value or 1e-4 for value in transaction.dimensions))
        shapes.append((outline("CUBE", frame @ Matrix.Diagonal((*dims, 1.0))), colour, 1.0))
        return shapes
    if isinstance(transaction, sdf_modeling.SdfStroke):
        if transaction.moved is not None:
            element = transaction.moved[0]
            centre = sdf_modeling.world_centre(transaction.obj, element)
            shapes.append((ring(centre, poses_direction(poses, controllers._active_gesture), element.radius, 24),
                           colour, 1.0))
        return shapes
    if isinstance(transaction, creative_tools.CurvePenStroke):
        shapes.append((list(transaction.lines), colour, 1.0))
        return shapes
    if isinstance(transaction, uv_tools.UVIslandStroke):
        shapes.append((list(transaction.lines), colour, 1.0))
        return shapes
    if isinstance(transaction, physical_tools.MeasureStroke):
        size = 0.01 * units
        line = [tuple(transaction.start), tuple(transaction.end)]
        for point in (transaction.start, transaction.end):
            line += [tuple(p) for p in ring(point, transaction.end - transaction.start
                                             if transaction.length > 1e-9 else Vector((0, 0, 1)), size, 12)]
        shapes.append((line, colour, 1.0))
        return shapes
    if isinstance(transaction, physical_tools.PlaceStroke):
        dims = Vector(tuple(value or 1e-4 for value in transaction.dimensions))
        matrix = transaction.obj.matrix_world @ Matrix.Diagonal((*dims, 1.0))
        shapes.append((outline(settings.place_kind, matrix), colour, 1.0))
        return shapes
    if controllers._active_gesture is not None:
        return shapes
    pose = poses.get(controllers._pointer_hand)
    if pose is None:
        return shapes
    origin, rotation, direction = pose
    if tool == "place_prefab":
        from . import prefabs
        dims = Vector(prefabs.kind_dimensions(str(settings.prefab_kind)))
        location, yaw, _hit = physical_tools.placement(context, origin, direction, dims)
        matrix = (Matrix.Translation(location) @ yaw.to_matrix().to_4x4()
                  @ Matrix.Diagonal((*(value or 1e-4 for value in dims), 1.0)))
        shapes.append((outline("CUBE", matrix), colour, 0.7))
    elif tool in {"draw_curve", "sdf_blob"}:
        from . import creative_tools
        tip = origin + direction * creative_tools.PEN_TIP_M * units
        radius = float(settings.pen_radius_m if tool == "draw_curve" else settings.sdf_radius_m) * units
        shapes.append(([tuple(p) for p in ring(tip, direction, max(radius, 0.004 * units), 16)], colour, 0.9))
    elif tool.startswith(("draw_", "cut_")):
        kind = settings.cutter_shape if tool.startswith("cut_") else tool.removeprefix("draw_").upper()
        tip = origin + rotation @ Vector((0.0, 0.0, -DRAW_TIP_M * units))
        size = GHOST_M * units
        # Same upright frame the drawn box will use.
        matrix = (Matrix.Translation(tip) @ draw_editing.draw_frame(rotation).to_matrix().to_4x4()
                  @ Matrix.Diagonal((size, size, size, 1.0)))
        shapes.append((outline(kind, matrix), colour, 0.6))
    elif tool == "place_object":
        size = physical_tools.PLACE_SHAPES[settings.place_kind][2]
        dims = Vector(tuple(value * units for value in size))
        location, yaw, _hit = physical_tools.placement(context, origin, direction, dims)
        matrix = (Matrix.Translation(location) @ yaw.to_matrix().to_4x4()
                  @ Matrix.Diagonal((*(value or 1e-4 for value in dims), 1.0)))
        shapes.append((outline(settings.place_kind, matrix), colour, 0.7))
    return shapes


def hint_lines(context, settings, hand_path):
    from . import onboarding
    gesture = controllers._active_gesture
    if controllers.hand_menu_active(context):
        state = "menu"
    elif controllers._held_object_transaction(gesture) is not None:
        state = "holding"
    elif controllers._extrusion(gesture) is not None:
        state = "extruding"
    elif context.mode == "EDIT_MESH" and gesture is None:
        from . import component_mode
        state = "edit"
        mode = component_mode.current_mode(context)
        tool_label = {"VERT": "vertex", "EDGE": "edge", "FACE": "face"}[mode]
        if settings.edit_tool == "edit_extrude":
            tool_label = "extrude selection"
    else:
        state = "idle"
    from . import ui
    tool = next((label for key, label, _d in ui.ALL_TOOLS if key == settings.edit_tool), "")
    if state == "edit":
        tool = tool_label
    mode = ""
    if state == "holding":
        mode = controllers.hold_mode_name(gesture.hold_mode + 1)
        mode = f"-> {mode}"
    slots = {slot: onboarding.slot_label(slot) for slot, _label, _d in controllers.BUTTON_SLOTS}
    return onboarding.button_hints(settings.controller_profile,
                                   "LEFT" if hand_path == controllers.LEFT_HAND else "RIGHT",
                                   state, tool, mode, slots, bool(onboarding.prefs()["left_handed"]))


def hint_matrix(position, viewer_rotation, side, units, label_scale=1.0):
    """Text plate beside a controller, turned to face the viewer, readable size."""
    rotation = Quaternion(viewer_rotation)
    right, up = rotation @ Vector((1.0, 0.0, 0.0)), rotation @ Vector((0.0, 1.0, 0.0))
    # Left-hand labels sit to the left of the controller, right-hand labels to the right.
    anchor = (Vector(position) + up * 0.05 * units
              + right * (0.04 * units if side > 0 else -0.16 * units * label_scale))
    scale = HINT_LINE_M * units * label_scale / HINT_FONT_PX
    return Matrix.Translation(anchor) @ rotation.to_matrix().to_4x4() @ Matrix.Diagonal((scale, scale, scale, 1.0))


def _menu_cursor(context, poses):
    """A bright dot where a ray touches the hand menu, and a tick per row crossed."""
    rays = {path: (origin, direction) for path, (origin, _rotation, direction) in poses.items()}
    hand, row_changed = menu_pointer.update(context, rays, controllers._pointer_hand)
    if not hand:
        return []
    if row_changed and controllers._active_gesture is None:
        controllers.pulse(context, hand, "tick")
    cursor = menu_pointer.cursor
    size = cursor["size"]
    return [(ring(cursor["point"], cursor["normal"], size, 16), polish.ACCENT, 1.0),
            (ring(cursor["point"], cursor["normal"], size * 0.35, 8), polish.TEXT, 1.0)]


def _is_overlay_object(obj):
    keys = obj.keys()
    return any(key in keys for key in ("xrwb_garden_socket", "xrwb_garden_label", "xrwb_swatch_label"))


def plate_matrix(position, viewer_rotation, units, lift_m, scale=1.0):
    """A centred text plate lift_m above a point, facing the viewer."""
    rotation = Quaternion(viewer_rotation)
    up = rotation @ Vector((0.0, 1.0, 0.0))
    size = HINT_LINE_M * units * scale / HINT_FONT_PX
    return (Matrix.Translation(Vector(position) + up * lift_m * units) @ rotation.to_matrix().to_4x4()
            @ Matrix.Diagonal((size, size, size, 1.0)))


def _status_plates(context, settings, tool, poses, viewer_rotation, units):
    """The live readout over the working hand, then the newest outcome as a fading toast."""
    plates = []
    gesture = controllers._active_gesture
    label_scale = float(settings.label_scale)
    readout_hand = ""
    if gesture is not None and settings.readouts and getattr(gesture, "readout", ""):
        pose = poses.get(gesture.user_path)
        position = pose[0] if pose is not None else gesture.primary_position
        if position is not None:
            readout_hand = gesture.user_path
            plates.append(Plate(plate_matrix(position, viewer_rotation, units, 0.13, 1.35 * label_scale),
                                [gesture.readout], tool_colour(settings, tool), 1.0, True))
    toast = polish.current_toast() if settings.toasts else None
    if toast is not None:
        message, level, hand, alpha = toast
        hand = hand if hand in poses else (controllers._pointer_hand if controllers._pointer_hand in poses
                                           else next(iter(poses), ""))
        if hand in poses:
            lift = 0.2 if hand == readout_hand else 0.13
            plates.append(Plate(plate_matrix(poses[hand][0], viewer_rotation, units, lift, 1.1 * label_scale),
                                _wrap(message, 46)[:2], polish.LEVEL_COLOURS.get(level, polish.ACCENT), alpha, True))
    return plates


def _wrap(text, width):
    words, lines, line = str(text).split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


class Plate(tuple):
    """(matrix, lines, accent colour or None, alpha, centred): one rounded text plate."""

    def __new__(cls, matrix, lines, accent=None, alpha=1.0, centred=False):
        return super().__new__(cls, (matrix, list(lines), accent, float(alpha), bool(centred)))


def _garden_links(context):
    """Keep the node garden in step with its tree and return its link lines."""
    if not node_garden.is_out(context):
        return []
    if controllers._active_gesture is None and editing.active_transaction() is None:
        node_garden.sync(context)
    return [([start, end], colour, 0.95) for start, end, colour in node_garden.link_lines(context)]


def _socket_labels(context, poses, viewer_rotation, units):
    """Name the socket each ray is on, so you know what you are about to link."""
    labels = []
    tree = node_garden.tree_of(node_garden.root(context))
    for origin, _rotation, direction in poses.values():
        sphere = node_garden.nearest_socket(context, origin, direction)
        socket = node_garden.socket_ref(tree, sphere) if sphere is not None else None
        if socket is None:
            continue
        side = 1 if socket.is_output else -1
        text = f"{socket.node.label or socket.node.name}: {socket.name}"
        labels.append(Plate(hint_matrix(sphere.matrix_world.translation, viewer_rotation, side, units, 0.8),
                            [text], node_garden.SOCKET_COLOURS.get(socket.type)))
    return labels


def _undo_ring(poses, viewer_rotation, units):
    """(shapes, plates) of the open Undo Ring: a dot per step around the ring hand, names above."""
    pose = poses.get(undo_ring.hand())
    found = undo_ring.steps()
    if pose is None or not found:
        return [], []
    rotation = Quaternion(viewer_rotation)
    right, up, normal = (rotation @ Vector(axis) for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    centre, radius = Vector(pose[0]), undo_ring.RADIUS_M * units
    shapes = [(ring(centre, normal, radius, 48), polish.MUTED, 0.35)]
    for angle, _name, kind in undo_ring.layout(found):
        point = centre + (right * math.sin(angle) + up * math.cos(angle)) * radius
        colour = {"current": polish.ACCENT, "past": polish.TEXT}.get(kind, polish.MUTED)
        shapes.append((ring(point, normal, radius * (0.16 if kind == "current" else 0.07), 12), colour, 1.0))
        if kind == "current":
            shapes.append((ring(point, normal, radius * 0.06, 8), polish.TEXT, 1.0))
    position = undo_ring.active_position(found)
    older = found[position - 1][1] if position > 0 else "(oldest)"
    newer = found[position + 1][1] if position + 1 < len(found) else "(newest)"
    lines = [f"{found[position][1]}   {position + 1}/{len(found)}", f"Stick down: {older}", f"Stick up: {newer}"]
    return shapes, [Plate(plate_matrix(centre, viewer_rotation, units, undo_ring.RADIUS_M + 0.03),
                          lines, polish.ACCENT, 1.0, True)]


def refresh(context) -> None:
    """Recompute the cache; plain values only."""
    settings = context.window_manager.xrwb_settings
    rings = bool(settings.ray_markers)
    _fills.clear()
    _links[:] = _garden_links(context)
    if controllers._active_gesture is None and editing.active_transaction() is None:
        modifier_tower.sync(context)  # A gripped block dropped at a new height reorders the stack.
    if (not rings and not getattr(settings, "button_hints", False) and not tool_wheel.is_open()
            and not undo_ring.is_open() and not controllers.hand_menu_active(context)):
        _cache.clear()
        _shapes.clear()
        _hints.clear()
        return
    state = context.window_manager.xr_session_state
    actionmap = controllers._find_actionmap(state)
    if actionmap is None:
        _cache.clear()
        return
    aim = next(item for item in actionmap.actionmap_items if item.name == "controller_aim")
    units = controllers._world_units_per_metre(context)
    tool = str(settings.edit_tool)
    depsgraph = context.evaluated_depsgraph_get()
    capture = controllers._active_gesture
    poses = {}
    hover_lines = []
    for index, path in enumerate(entry.path for entry in aim.user_paths):
        if not state.controller_aim_active_get(context, index):
            _cache.pop(path, None)
            continue
        pose = controllers._validated_pose(state.controller_aim_location_get(context, index),
                                           state.controller_aim_rotation_get(context, index))
        if pose is None:
            _cache.pop(path, None)
            continue
        origin, rotation, direction = pose
        origin, direction = Vector(origin), Vector(direction)
        poses[path] = (origin, Quaternion(rotation), direction)
        last_poses[path] = (tuple(origin), tuple(rotation), tuple(direction))
        if path == tool_wheel._state.get("hand") and tool_wheel.follow(origin):
            controllers.pulse(context, path, "tick")  # Feel each wedge as you cross into it.
        if not rings:
            _cache.pop(path, None)
            continue
        hit, location, normal, _face, obj, _matrix = context.scene.ray_cast(
            depsgraph, origin, direction, distance=50.0 * units)
        obj = obj.original if hit and obj is not None else None
        end = Vector(location) if hit else origin + direction * 3.0 * units
        _cache[path] = (tuple(origin + direction * 0.04 * units), tuple(end), bool(hit),
                        tuple(normal) if hit else tuple(-direction),
                        brush_radius(settings, tool, units), tool_colour(settings, tool, obj))
        name = obj.name if obj is not None else ""
        object_mode = context.mode == "OBJECT"
        face = (_hover_face(obj, _face, tool, tool_colour(settings, tool, obj), units)
                if capture is None and object_mode else None)
        if face is not None and path == controllers._pointer_hand:
            hover_lines.append(face)
        elif (obj is not None and capture is None and settings.hover_outline and not tool_wheel.is_open()
                and object_mode and not _is_overlay_object(obj)):
            hover_lines.append((polish.corner_brackets(obj.matrix_world, [tuple(c) for c in obj.bound_box]),
                                tool_colour(settings, tool, obj), 0.7))
        if name != _hovered.get(path, "") and name and capture is None and settings.hover_ticks:
            controllers.pulse(context, path, "tick")  # Feel the ray land on something.
        _hovered[path] = name
    _shapes[:] = (_preview_shapes(context, settings, tool, units, poses) + hover_lines) if rings else hover_lines
    _shapes.extend(_menu_cursor(context, poses))
    _hints.clear()
    if undo_ring.is_open() and hasattr(state, "viewer_pose_rotation"):
        ring_shapes, ring_plates = _undo_ring(poses, tuple(state.viewer_pose_rotation), units)
        _shapes.extend(ring_shapes)
        _hints.extend(ring_plates)
    if hasattr(state, "viewer_pose_rotation"):
        _hints.extend(_status_plates(context, settings, tool, poses, tuple(state.viewer_pose_rotation), units))
    if node_garden.is_out(context) and hasattr(state, "viewer_pose_rotation"):
        _hints.extend(_socket_labels(context, poses, tuple(state.viewer_pose_rotation), units))
    if getattr(settings, "button_hints", False) and hasattr(state, "viewer_pose_rotation"):
        viewer_rotation = tuple(state.viewer_pose_rotation)
        for path, (origin, _rotation, _direction) in poses.items():
            side = 1 if path == controllers.RIGHT_HAND else -1
            _hints.append(Plate(hint_matrix(origin, viewer_rotation, side, units, float(settings.label_scale)),
                                hint_lines(context, settings, path)))


def _draw():
    global _last_error
    data = tuple(_cache.values())
    shapes = tuple(_shapes) + tuple(_links)
    fills = tuple(_fills)
    hints = tuple(_hints)
    if not data and not shapes and not hints and not fills and not tool_wheel.is_open():
        return
    previous = None
    try:
        polish.sync_theme()
        import gpu
        from gpu_extras.batch import batch_for_shader
        positions, colours = [], []
        for _start, end, hit, normal, radius, colour in data:
            # No ray line of our own: Blender's controller rays are exact,
            # and a second, timer-lagged line looked like a double ray.
            if hit:
                points = ring(end, normal, radius)
                positions += [tuple(p) for p in points]
                colours += [(*colour, 0.95)] * len(points)
                dot = ring(end, normal, max(radius * 0.12, 1e-6), 8)
                positions += [tuple(p) for p in dot]
                colours += [(*colour, 1.0)] * len(dot)
        for points, colour, alpha in shapes:
            positions += points
            colours += [(*colour, alpha)] * len(points)
        previous = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        shader = gpu.shader.from_builtin("POLYLINE_FLAT_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", polish.LINE_WIDTH)
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        if fills:
            fill_positions, fill_colours = [], []
            for points, colour, alpha in fills:
                fill_positions += points
                fill_colours += [(*colour, alpha)] * len(points)
            fill_shader = gpu.shader.from_builtin("SMOOTH_COLOR")
            batch_for_shader(fill_shader, "TRIS", {"pos": fill_positions, "color": fill_colours}).draw(fill_shader)
        if positions:
            batch_for_shader(shader, "LINES", {"pos": positions, "color": colours}).draw(shader)
        if hints:
            draw_hints(hints)
        tool_wheel.draw()
        _last_error = ""
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if message != _last_error:
            print(f"XR ray feedback draw failed: {message}", flush=True)
            _last_error = message
    finally:
        if previous is not None:
            try:
                gpu.state.blend_set(previous[0])
                gpu.state.depth_test_set(previous[1])
                gpu.state.depth_mask_set(previous[2])
            except Exception:
                pass


def draw_hints(hints):
    """World-space text on rounded dark plates; only cached values, no context.

    A plate may carry an accent colour (a slim bar down its left edge, the
    tool or outcome colour), a fade alpha, and be centred on its anchor.
    """
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader

    font = 0
    blf.size(font, HINT_FONT_PX)
    line_px = HINT_FONT_PX * 1.25
    pad = HINT_FONT_PX * 0.45
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    for plate in hints:
        matrix, lines = plate[0], plate[1]
        accent, alpha, centred = (plate[2], plate[3], plate[4]) if len(plate) >= 5 else (None, 1.0, False)
        if not lines or alpha <= 0.0:
            continue
        width = max(blf.dimensions(font, line)[0] for line in lines)
        height = line_px * len(lines) - line_px * 0.25
        bar = HINT_FONT_PX * 0.16 if accent is not None else 0.0
        x0 = -width / 2.0 - pad - bar if centred else -pad - bar
        x1 = x0 + width + 2.0 * pad + bar
        y0, y1 = -pad, height + pad * 0.6
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(matrix)
            shader.uniform_float("color", (*polish.PLATE, polish.PLATE_ALPHA * alpha))
            batch_for_shader(shader, "TRI_FAN", {"pos": polish.rounded_rect(x0, y0, x1, y1, pad)}).draw(shader)
            if accent is not None:
                shader.uniform_float("color", (*accent, 0.95 * alpha))
                batch_for_shader(shader, "TRI_FAN", {
                    "pos": polish.rounded_rect(x0 + pad * 0.35, y0 + pad * 0.45, x0 + pad * 0.35 + bar,
                                               y1 - pad * 0.45, bar / 2.0, 3)}).draw(shader)
            text_x = x0 + pad + bar
            blf.color(font, *polish.TEXT, alpha)
            for index, line in enumerate(reversed(lines)):
                blf.position(font, text_x, index * line_px, 0.0)
                blf.draw(font, line)


def _timer():
    global _was_running, _last_error
    from . import ui

    try:
        with ui._view3d_override(bpy.context):
            context = bpy.context
            if not runtime.session_running(context):
                if _was_running:
                    stop()
                    return None
                return 0.25
            _was_running = True
            refresh(context)
    except Exception as exc:  # Never let feedback break a session.
        _cache.clear()
        message = f"Ray feedback paused: {type(exc).__name__}: {exc}"
        if message != _last_error:  # Log once per distinct failure, not every second.
            interaction_log.emit("error", message, level="WARNING", error=exc)
            _last_error = message
        return 1.0
    return 1.0 / 30.0


def start() -> None:
    global _handle, _was_running
    if bpy.app.background:
        return
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "XR", "POST_VIEW")
    _was_running = False
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.1, persistent=False)


def stop() -> None:
    global _handle
    _cache.clear()
    _shapes.clear()
    _links.clear()
    _fills.clear()
    _hints.clear()
    _hovered.clear()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "XR")
        _handle = None


CLASSES = ()
