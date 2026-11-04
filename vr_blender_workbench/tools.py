"""Native and context-bridged VR tool implementations."""

from __future__ import annotations

from dataclasses import dataclass
import time

import bmesh
import bpy
from mathutils import Quaternion, Vector
from mathutils.bvhtree import BVHTree

from .context_bridge import parse_operator_args, run_operator
from .model import BEHAVIOR_CONTINUOUS, favorite_needs_outer_undo, tool_by_id
from .properties import get_preferences
from .transactions import (
    SculptTransaction,
    TransformTransaction,
    cancel_all_transactions,
    has_active_transactions,
)


@dataclass(slots=True)
class ControllerPose:
    location: Vector
    rotation: Quaternion
    pressure: float = 1.0

    @property
    def direction(self) -> Vector:
        return (self.rotation @ Vector((0.0, 0.0, -1.0))).normalized()


def pose_from_event(event, context=None) -> ControllerPose | None:
    xr = getattr(event, "xr", None)
    if xr is not None:
        try:
            state = xr.state
            pressure = float(state[0]) if len(state) else 1.0
            return ControllerPose(
                Vector(xr.controller_location),
                Quaternion(xr.controller_rotation),
                pressure,
            )
        except (AttributeError, TypeError, ValueError):
            pass
    context = context or bpy.context
    state = context.window_manager.xr_session_state
    if state is None:
        return None
    user_path = (
        getattr(xr, "user_path", "/user/hand/right") if xr else "/user/hand/right"
    )
    index = 0 if user_path.endswith("left") else 1
    try:
        return ControllerPose(
            Vector(state.controller_aim_location_get(context, index)),
            Quaternion(state.controller_aim_rotation_get(context, index)),
        )
    except (IndexError, RuntimeError, TypeError, ValueError):
        return None


def raycast_scene(context, origin: Vector, direction: Vector):
    settings = context.scene.vrw_settings
    depsgraph = context.evaluated_depsgraph_get()
    return context.scene.ray_cast(
        depsgraph,
        origin,
        direction,
        distance=settings.ray_distance,
    )


def placement_point(context, pose: ControllerPose) -> Vector:
    hit, location, _normal, _face, _obj, _matrix = raycast_scene(
        context, pose.location, pose.direction
    )
    if hit:
        return location
    return (
        pose.location + pose.direction * context.scene.vrw_settings.placement_distance
    )


def clear_safety_arms(context) -> None:
    """Clear destructive confirmations after another tool or target change."""

    settings = context.scene.vrw_settings
    settings.delete_armed_until = 0.0
    settings.delete_armed_signature = ""
    settings.save_armed_until = 0.0
    settings.save_armed_path = ""


def _selection_signature(context) -> str:
    """Return a stable short-lived signature for the guarded delete target."""

    if context.mode == "OBJECT":
        pointers = sorted(obj.as_pointer() for obj in context.selected_objects)
        return (
            f"OBJECT:{context.scene.as_pointer()}:"
            + ",".join(str(pointer) for pointer in pointers)
            if pointers
            else ""
        )
    if context.mode == "EDIT_MESH" and context.edit_object is not None:
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        for sequence in (bm.verts, bm.edges, bm.faces):
            sequence.ensure_lookup_table()
            sequence.index_update()
        selected = []
        for prefix, sequence in (("V", bm.verts), ("E", bm.edges), ("F", bm.faces)):
            selected.extend(
                f"{prefix}{element.index}"
                for element in sequence
                if element.select and not element.hide
            )
        if not selected:
            return ""
        select_mode = "".join(
            "1" if enabled else "0"
            for enabled in context.tool_settings.mesh_select_mode
        )
        topology = f"{len(bm.verts)}/{len(bm.edges)}/{len(bm.faces)}"
        return (
            f"EDIT:{context.edit_object.as_pointer()}:{select_mode}:{topology}:"
            + ",".join(selected)
        )
    return ""


def _delete_target_count(context) -> int:
    if context.mode == "OBJECT":
        return len(context.selected_objects)
    if context.mode == "EDIT_MESH" and context.edit_object is not None:
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        mode = context.tool_settings.mesh_select_mode[:]
        sequence = bm.faces if mode[2] else (bm.edges if mode[1] else bm.verts)
        return sum(1 for element in sequence if element.select and not element.hide)
    return 0


def _set_active_from_selection(context) -> None:
    selected = list(context.selected_objects)
    context.view_layer.objects.active = selected[-1] if selected else None


def _point_segment_distance_squared(point: Vector, start: Vector, end: Vector) -> float:
    segment = end - start
    length_squared = segment.length_squared
    if length_squared <= 1.0e-12:
        return (point - start).length_squared
    factor = max(0.0, min(1.0, (point - start).dot(segment) / length_squared))
    return (point - (start + segment * factor)).length_squared


def select_at_ray(
    context, pose: ControllerPose, mode: str = "REPLACE"
) -> tuple[bool, str]:
    mode = mode.upper()
    if mode not in {"REPLACE", "ADD", "SUBTRACT", "TOGGLE"}:
        return False, f"Unknown ray-selection mode: {mode}"
    if context.mode == "EDIT_MESH" and context.edit_object is not None:
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        inverse = obj.matrix_world.inverted_safe()
        local_origin = inverse @ pose.location
        local_direction = (inverse.to_3x3() @ pose.direction).normalized()
        tree = BVHTree.FromBMesh(bm)
        hit_location, _normal, face_index, _distance = tree.ray_cast(
            local_origin, local_direction, context.scene.vrw_settings.ray_distance
        )
        if face_index is None or face_index < 0:
            if mode == "REPLACE":
                for sequence in (bm.verts, bm.edges, bm.faces):
                    for element in sequence:
                        element.select = False
                bmesh.update_edit_mesh(
                    obj.data, loop_triangles=False, destructive=False
                )
                clear_safety_arms(context)
                return True, "Deselected all mesh elements"
            return False, "No editable face under the controller ray"
        if face_index >= len(bm.faces):
            return False, "Ray hit could not be mapped to the edit mesh"
        face = bm.faces[face_index]
        select_mode = context.tool_settings.mesh_select_mode[:]
        if select_mode[2]:
            target = face
            element_label = "face"
        elif select_mode[1]:
            target = min(
                face.edges,
                key=lambda edge: _point_segment_distance_squared(
                    hit_location, edge.verts[0].co, edge.verts[1].co
                ),
            )
            element_label = "edge"
        else:
            target = min(
                face.verts, key=lambda vert: (vert.co - hit_location).length_squared
            )
            element_label = "vertex"
        if mode == "REPLACE":
            for sequence in (bm.verts, bm.edges, bm.faces):
                for element in sequence:
                    element.select = False
            target.select_set(True)
        elif mode == "ADD":
            target.select_set(True)
        elif mode == "SUBTRACT":
            target.select_set(False)
        else:
            target.select_set(not target.select)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        clear_safety_arms(context)
        verb = {
            "REPLACE": "Selected",
            "ADD": "Added",
            "SUBTRACT": "Removed",
            "TOGGLE": "Toggled",
        }[mode]
        return True, f"{verb} {element_label} {target.index}"

    hit, _location, _normal, _face, obj, _matrix = raycast_scene(
        context, pose.location, pose.direction
    )
    if not hit or obj is None:
        if context.mode == "OBJECT" and mode == "REPLACE":
            for selected in list(context.selected_objects):
                selected.select_set(False)
            context.view_layer.objects.active = None
            clear_safety_arms(context)
            return True, "Deselected all objects"
        return False, "No object under the controller ray"
    if context.mode != "OBJECT":
        return False, f"Ray selection is not adapted for {context.mode} yet"
    if mode == "REPLACE":
        for selected in list(context.selected_objects):
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
    elif mode == "ADD":
        obj.select_set(True)
        context.view_layer.objects.active = obj
    elif mode == "SUBTRACT":
        obj.select_set(False)
        if context.view_layer.objects.active == obj:
            _set_active_from_selection(context)
    else:
        obj.select_set(not obj.select_get())
        if obj.select_get():
            context.view_layer.objects.active = obj
        elif context.view_layer.objects.active == obj:
            _set_active_from_selection(context)
    clear_safety_arms(context)
    verb = {
        "REPLACE": "Selected",
        "ADD": "Added",
        "SUBTRACT": "Removed",
        "TOGGLE": "Toggled",
    }[mode]
    return True, f"{verb} {obj.name}"


def _selection_action(context, action: str) -> tuple[bool, str]:
    operator_id = "object.select_all" if context.mode == "OBJECT" else "mesh.select_all"
    if context.mode not in {"OBJECT", "EDIT_MESH"}:
        return False, f"Selection commands are not adapted for {context.mode} yet"
    ok, message = run_operator(operator_id, context=context, kwargs={"action": action})
    if ok:
        clear_safety_arms(context)
        labels = {
            "SELECT": "Selected all",
            "DESELECT": "Deselected all",
            "INVERT": "Inverted selection",
        }
        message = labels[action]
    return ok, message


def _unhide(context) -> tuple[bool, str]:
    if context.mode == "OBJECT":
        ok, message = run_operator(
            "object.hide_view_clear", context=context, kwargs={"select": False}
        )
    elif context.mode == "EDIT_MESH":
        ok, message = run_operator(
            "mesh.reveal", context=context, kwargs={"select": False}
        )
    else:
        return False, f"Reveal is not adapted for {context.mode} yet"
    if ok:
        clear_safety_arms(context)
        message = "Revealed hidden elements"
    return ok, message


_PRIMITIVE_OPERATORS = {
    "ADD_CUBE": "mesh.primitive_cube_add",
    "ADD_UV_SPHERE": "mesh.primitive_uv_sphere_add",
    "ADD_CYLINDER": "mesh.primitive_cylinder_add",
    "ADD_CONE": "mesh.primitive_cone_add",
    "ADD_PLANE": "mesh.primitive_plane_add",
    "ADD_TORUS": "mesh.primitive_torus_add",
    "ADD_EMPTY": "object.empty_add",
    "ADD_LIGHT": "object.light_add",
    "ADD_CAMERA": "object.camera_add",
}


def _add_primitive(context, tool: str, pose: ControllerPose) -> tuple[bool, str]:
    if context.mode != "OBJECT":
        return False, "Return to Object Mode before adding objects"
    location = placement_point(context, pose)
    kwargs = {"location": tuple(location)}
    if tool == "ADD_CAMERA":
        kwargs["rotation"] = tuple(pose.rotation.to_euler())
    ok, message = run_operator(
        _PRIMITIVE_OPERATORS[tool], context=context, kwargs=kwargs
    )
    if ok and context.active_object:
        message = f"Added {context.active_object.name}"
    return ok, message


def _duplicate(context) -> tuple[bool, str]:
    if context.mode == "OBJECT":
        originals = list(context.selected_objects)
        if not originals:
            return False, "Nothing selected to duplicate"
        for obj in originals:
            obj.select_set(False)
        copies = []
        for obj in originals:
            clone = obj.copy()
            if obj.data is not None:
                clone.data = obj.data.copy()
            target_collection = (
                obj.users_collection[0] if obj.users_collection else context.collection
            )
            target_collection.objects.link(clone)
            clone.select_set(True)
            copies.append(clone)
        context.view_layer.objects.active = copies[-1]
        return True, f"Duplicated {len(copies)} object(s)"
    if context.mode == "EDIT_MESH":
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        geom = [
            element
            for sequence in (bm.verts, bm.edges, bm.faces)
            for element in sequence
            if element.select
        ]
        if not geom:
            return False, "Nothing selected to duplicate"
        result = bmesh.ops.duplicate(bm, geom=geom)
        for element in geom:
            element.select = False
        for element in result["geom"]:
            element.select = True
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        return True, "Duplicated mesh selection"
    return False, "Duplicate currently supports Object and Mesh Edit modes"


def _delete(context) -> tuple[bool, str]:
    settings = context.scene.vrw_settings
    signature = _selection_signature(context)
    if not signature:
        settings.delete_armed_until = 0.0
        settings.delete_armed_signature = ""
        return False, "Nothing selected to delete"
    now = time.monotonic()
    if (
        now > settings.delete_armed_until
        or settings.delete_armed_signature != signature
    ):
        target_changed = bool(settings.delete_armed_signature)
        target_count = _delete_target_count(context)
        settings.delete_armed_until = now + 2.5
        settings.delete_armed_signature = signature
        prefix = (
            "Delete target changed; confirmation reset"
            if target_changed
            else "Delete armed"
        )
        return False, (
            f"{prefix} for {target_count} target(s): "
            "press the trigger again within 2.5 seconds"
        )
    settings.delete_armed_until = 0.0
    settings.delete_armed_signature = ""
    if context.mode == "OBJECT":
        objects = list(context.selected_objects)
        if not objects:
            return False, "Nothing selected to delete"
        ok, message = run_operator(
            "object.delete", context=context, kwargs={"use_global": False}
        )
        if ok:
            message = f"Removed {len(objects)} object(s) from the current scene"
        return ok, message
    if context.mode == "EDIT_MESH":
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        mode = context.tool_settings.mesh_select_mode[:]
        if mode[2]:
            geom = [face for face in bm.faces if face.select and not face.hide]
            delete_context = "FACES"
        elif mode[1]:
            geom = [edge for edge in bm.edges if edge.select and not edge.hide]
            delete_context = "EDGES"
        else:
            geom = [vert for vert in bm.verts if vert.select and not vert.hide]
            delete_context = "VERTS"
        if not geom:
            return False, "Nothing selected to delete"
        bmesh.ops.delete(bm, geom=geom, context=delete_context)
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        return True, "Deleted mesh selection"
    return False, "Delete currently supports Object and Mesh Edit modes"


def _hide(context) -> tuple[bool, str]:
    if context.mode == "OBJECT":
        objects = list(context.selected_objects)
        for obj in objects:
            obj.hide_set(True)
        return (
            bool(objects),
            f"Hid {len(objects)} object(s)" if objects else "Nothing selected to hide",
        )
    if context.mode == "EDIT_MESH":
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        count = 0
        for sequence in (bm.verts, bm.edges, bm.faces):
            for element in sequence:
                if element.select:
                    element.hide_set(True)
                    count += 1
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        return (
            count > 0,
            f"Hid {count} mesh elements" if count else "Nothing selected to hide",
        )
    return False, "Hide currently supports Object and Mesh Edit modes"


def _mesh_extrude(context) -> tuple[bool, str]:
    if context.mode != "EDIT_MESH":
        return False, "Extrude requires Mesh Edit Mode"
    obj = context.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    faces = [face for face in bm.faces if face.select]
    if not faces:
        return False, "Select one or more faces to extrude"
    normal = sum((face.normal for face in faces), Vector()).normalized()
    result = bmesh.ops.extrude_face_region(bm, geom=faces, use_select_history=True)
    new_verts = [
        element for element in result["geom"] if isinstance(element, bmesh.types.BMVert)
    ]
    for sequence in (bm.verts, bm.edges, bm.faces):
        for element in sequence:
            element.select = False
    for vert in new_verts:
        vert.select = True
    bmesh.ops.translate(
        bm, verts=new_verts, vec=normal * context.scene.vrw_settings.mesh_amount
    )
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return True, f"Extruded {len(faces)} face(s)"


def _mesh_inset(context) -> tuple[bool, str]:
    if context.mode != "EDIT_MESH":
        return False, "Inset requires Mesh Edit Mode"
    obj = context.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    faces = [face for face in bm.faces if face.select]
    if not faces:
        return False, "Select one or more faces to inset"
    try:
        result = bmesh.ops.inset_region(
            bm,
            faces=faces,
            thickness=context.scene.vrw_settings.mesh_amount,
            depth=0.0,
            use_boundary=True,
            use_even_offset=True,
            use_interpolate=True,
            use_relative_offset=False,
        )
    except Exception as exc:
        return False, f"Inset failed: {exc}"
    for face in bm.faces:
        face.select = False
    for face in result.get("faces", ()):
        face.select = True
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return True, f"Inset {len(faces)} face(s)"


def _mesh_bevel(context) -> tuple[bool, str]:
    if context.mode != "EDIT_MESH":
        return False, "Bevel requires Mesh Edit Mode"
    return run_operator(
        "mesh.bevel",
        context=context,
        kwargs={
            "offset": context.scene.vrw_settings.mesh_amount,
            "segments": 2,
            "affect": "EDGES",
        },
    )


def _mesh_subdivide(context) -> tuple[bool, str]:
    if context.mode != "EDIT_MESH":
        return False, "Subdivide requires Mesh Edit Mode"
    obj = context.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    edges = [edge for edge in bm.edges if edge.select]
    if not edges:
        return False, "Select edges or faces to subdivide"
    bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return True, f"Subdivided {len(edges)} edge(s)"


def _mesh_merge_center(context) -> tuple[bool, str]:
    if context.mode != "EDIT_MESH":
        return False, "Merge requires Mesh Edit Mode"
    obj = context.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    verts = [vert for vert in bm.verts if vert.select]
    if len(verts) < 2:
        return False, "Select at least two vertices to merge"
    center = sum((vert.co for vert in verts), Vector()) / len(verts)
    bmesh.ops.pointmerge(bm, verts=verts, merge_co=center)
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return True, f"Merged {len(verts)} vertices"


def _set_mode(context, mode: str) -> tuple[bool, str]:
    if context.active_object is None:
        return False, "Select an object before changing modes"
    mode_operator = {
        "MODE_OBJECT": "OBJECT",
        "MODE_EDIT": "EDIT",
        "MODE_SCULPT": "SCULPT",
        "MODE_POSE": "POSE",
    }[mode]
    ok, message = run_operator(
        "object.mode_set", context=context, kwargs={"mode": mode_operator}
    )
    return ok, f"Entered {mode_operator.title()} Mode" if ok else message


def _animation_action(context, tool: str) -> tuple[bool, str]:
    scene = context.scene
    if tool == "FRAME_PREV":
        scene.frame_set(max(scene.frame_start, scene.frame_current - 1))
        return True, f"Frame {scene.frame_current}"
    if tool == "FRAME_NEXT":
        scene.frame_set(min(scene.frame_end, scene.frame_current + 1))
        return True, f"Frame {scene.frame_current}"
    if tool == "AUTO_KEY":
        scene.tool_settings.use_keyframe_insert_auto = (
            not scene.tool_settings.use_keyframe_insert_auto
        )
        return (
            True,
            f"Auto Key {'on' if scene.tool_settings.use_keyframe_insert_auto else 'off'}",
        )
    mapping = {
        "INSERT_KEY": ("anim.keyframe_insert_menu", {"type": "LocRotScale"}),
        "DELETE_KEY": ("anim.keyframe_delete_v3d", {}),
        "PLAY_PAUSE": ("screen.animation_play", {}),
        "KEY_PREV": ("screen.keyframe_jump", {"next": False}),
        "KEY_NEXT": ("screen.keyframe_jump", {"next": True}),
    }
    operator_id, kwargs = mapping[tool]
    return run_operator(operator_id, context=context, kwargs=kwargs)


def _favorite(context, tool: str) -> tuple[bool, str]:
    preferences = get_preferences(context)
    if preferences is None:
        return False, "VR Workbench preferences are unavailable"
    index = int(tool.rsplit("_", 1)[1])
    operator_id = getattr(preferences, f"favorite_{index}_id").strip()
    raw_args = getattr(preferences, f"favorite_{index}_args")
    kwargs, error = parse_operator_args(raw_args)
    if kwargs is None:
        return False, error
    return run_operator(operator_id, context=context, kwargs=kwargs)


def _save(context) -> tuple[bool, str]:
    settings = context.scene.vrw_settings
    if not bpy.data.filepath:
        return False, "Unsaved files need SteamVR Desktop View for Save As"
    now = time.monotonic()
    filepath = bpy.data.filepath
    if now > settings.save_armed_until or settings.save_armed_path != filepath:
        settings.save_armed_until = now + 2.5
        settings.save_armed_path = filepath
        return False, "Save armed: press the trigger again within 2.5 seconds"
    settings.save_armed_until = 0.0
    settings.save_armed_path = ""
    return run_operator("wm.save_mainfile", context=context)


def execute_press_tool(context, tool: str, pose: ControllerPose) -> tuple[bool, str]:
    if has_active_transactions():
        if tool == "UNDO":
            cancel_all_transactions()
            return True, "Cancelled active VR gesture"
        return False, "Finish or cancel the active VR gesture first"
    if tool not in {"DELETE", "SAVE"}:
        clear_safety_arms(context)
    if tool in {"SELECT", "SELECT_REPLACE"}:
        return select_at_ray(context, pose, "REPLACE")
    if tool == "SELECT_ADD":
        return select_at_ray(context, pose, "ADD")
    if tool == "SELECT_SUBTRACT":
        return select_at_ray(context, pose, "SUBTRACT")
    if tool == "SELECT_TOGGLE":
        return select_at_ray(context, pose, "TOGGLE")
    if tool == "SELECT_ALL":
        return _selection_action(context, "SELECT")
    if tool == "DESELECT_ALL":
        return _selection_action(context, "DESELECT")
    if tool == "SELECT_INVERT":
        return _selection_action(context, "INVERT")
    if tool == "UNHIDE":
        return _unhide(context)
    if tool in _PRIMITIVE_OPERATORS:
        return _add_primitive(context, tool, pose)
    if tool == "DUPLICATE":
        return _duplicate(context)
    if tool == "DELETE":
        return _delete(context)
    if tool == "HIDE":
        return _hide(context)
    if tool == "UNDO":
        return run_operator("ed.undo", context=context)
    if tool == "REDO":
        return run_operator("ed.redo", context=context)
    if tool in {"MODE_OBJECT", "MODE_EDIT", "MODE_SCULPT", "MODE_POSE"}:
        return _set_mode(context, tool)
    if tool == "SELECT_VERTEX":
        context.tool_settings.mesh_select_mode = (True, False, False)
        return True, "Vertex Select"
    if tool == "SELECT_EDGE":
        context.tool_settings.mesh_select_mode = (False, True, False)
        return True, "Edge Select"
    if tool == "SELECT_FACE":
        context.tool_settings.mesh_select_mode = (False, False, True)
        return True, "Face Select"
    if tool == "EXTRUDE":
        return _mesh_extrude(context)
    if tool == "INSET":
        return _mesh_inset(context)
    if tool == "BEVEL":
        return _mesh_bevel(context)
    if tool == "SUBDIVIDE":
        return _mesh_subdivide(context)
    if tool == "MERGE_CENTER":
        return _mesh_merge_center(context)
    if tool == "BRUSH_SMALLER":
        context.scene.vrw_settings.brush_radius *= 0.8
        return True, f"Brush radius {context.scene.vrw_settings.brush_radius:.3f} m"
    if tool == "BRUSH_LARGER":
        context.scene.vrw_settings.brush_radius *= 1.25
        return True, f"Brush radius {context.scene.vrw_settings.brush_radius:.3f} m"
    if tool == "STRENGTH_DOWN":
        context.scene.vrw_settings.brush_strength = max(
            0.01, context.scene.vrw_settings.brush_strength - 0.05
        )
        return True, f"Brush strength {context.scene.vrw_settings.brush_strength:.2f}"
    if tool == "STRENGTH_UP":
        context.scene.vrw_settings.brush_strength = min(
            1.0, context.scene.vrw_settings.brush_strength + 0.05
        )
        return True, f"Brush strength {context.scene.vrw_settings.brush_strength:.2f}"
    if tool in {
        "INSERT_KEY",
        "DELETE_KEY",
        "PLAY_PAUSE",
        "FRAME_PREV",
        "FRAME_NEXT",
        "KEY_PREV",
        "KEY_NEXT",
        "AUTO_KEY",
    }:
        return _animation_action(context, tool)
    if tool == "SHADE_SMOOTH":
        return run_operator("object.shade_smooth_by_angle", context=context)
    if tool == "SHADE_FLAT":
        return run_operator("object.shade_flat", context=context)
    if tool == "APPLY_TRANSFORMS":
        return run_operator(
            "object.transform_apply",
            context=context,
            kwargs={"location": True, "rotation": True, "scale": True},
        )
    if tool == "JOIN":
        return run_operator("object.join", context=context)
    if tool == "SEPARATE":
        return run_operator(
            "mesh.separate", context=context, kwargs={"type": "SELECTED"}
        )
    if tool == "ORIGIN_GEOMETRY":
        return run_operator(
            "object.origin_set",
            context=context,
            kwargs={"type": "ORIGIN_GEOMETRY", "center": "MEDIAN"},
        )
    if tool == "SAVE":
        return _save(context)
    if tool == "DESKTOP":
        return (
            True,
            "Press the SteamVR system button, then choose Desktop to use the full Blender UI",
        )
    if tool.startswith("FAVORITE_"):
        return _favorite(context, tool)
    return False, f"{tool_by_id(tool).label} is not implemented in this build"


def begin_continuous_tool(context, tool: str, pose: ControllerPose):
    if tool in {"MOVE", "ROTATE", "SCALE"}:
        return TransformTransaction(context, tool, pose.location, pose.rotation)
    if tool in {"SCULPT_DRAW", "SCULPT_SMOOTH", "SCULPT_INFLATE", "SCULPT_DEFLATE"}:
        return SculptTransaction(context, tool)
    return None


def tool_is_continuous(tool: str) -> bool:
    return tool_by_id(tool).behavior == BEHAVIOR_CONTINUOUS


_DATA_MUTATING_TOOLS = {
    "ADD_CUBE",
    "ADD_UV_SPHERE",
    "ADD_CYLINDER",
    "ADD_CONE",
    "ADD_PLANE",
    "ADD_TORUS",
    "ADD_EMPTY",
    "ADD_LIGHT",
    "ADD_CAMERA",
    "DUPLICATE",
    "DELETE",
    "HIDE",
    "UNHIDE",
    "EXTRUDE",
    "INSET",
    "BEVEL",
    "SUBDIVIDE",
    "MERGE_CENTER",
    "INSERT_KEY",
    "DELETE_KEY",
    "SHADE_SMOOTH",
    "SHADE_FLAT",
    "APPLY_TRANSFORMS",
    "JOIN",
    "SEPARATE",
    "ORIGIN_GEOMETRY",
}


def tool_needs_outer_undo(context, tool: str) -> bool:
    """Whether Primary must finish to preserve a data mutation in history."""

    if tool.startswith("FAVORITE_"):
        preferences = get_preferences(context)
        if preferences is None:
            return False
        index = int(tool.rsplit("_", 1)[1])
        operator_id = getattr(preferences, f"favorite_{index}_id")
        creates_undoable_data = getattr(preferences, f"favorite_{index}_creates_undo")
        return favorite_needs_outer_undo(operator_id, creates_undoable_data)
    return tool in _DATA_MUTATING_TOOLS
