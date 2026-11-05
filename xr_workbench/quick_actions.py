"""Right-stick-click Quick menu: the handful of things each workflow needs most.

It opens on the pressing hand as a native Blender menu, so every entry is a
real button with Blender's own hover and click handling. Duplicate, Delete and
Bring Here are single undo steps built on Blender data calls.
"""
from __future__ import annotations

import bpy
import math

from mathutils import Matrix, Quaternion, Vector

from . import editing, physical_tools, viewer_scale

# workflow: ((tool, label, icon), ...)
WORKFLOW_TOOLS = {
    "MODEL": (("select", "Select", "RESTRICT_SELECT_OFF"), ("extrude", "Extrude Face", "FACESEL"),
              ("bevel_edge", "Bevel Edges", "MOD_BEVEL"), ("draw_cube", "Draw Cube", "MESH_CUBE"),
              ("extrude_free", "Free Extrude", "ORIENTATION_NORMAL"), ("inset", "Inset Face", "FACESEL"),
              ("cut_difference", "Cut Away", "MOD_BOOLEAN"), ("measure", "Measure", "DRIVER_DISTANCE")),
    "SCULPT": (("sculpt_clay", "Clay", "BRUSH_DATA"), ("sculpt_grab", "Grab", "VIEW_PAN"),
               ("sculpt_smooth", "Smooth", "MOD_SMOOTH"), ("sculpt_flatten", "Flatten", "MOD_SOLIDIFY")),
    "SHADE": (("spray_material", "Spray Material", "BRUSH_DATA"), ("eyedropper", "Eyedropper", "EYEDROPPER"),
              ("uv_island", "UV Island", "UV_FACESEL")),
    "ANIMATE": (("scrub_time", "Scrub Time", "TIME"),),
    "STAGE": (("select", "Select", "RESTRICT_SELECT_OFF"), ("place_object", "Place Shape", "MESH_CUBE"),
              ("draw_curve", "3D Pen", "GREASEPENCIL"), ("measure", "Measure", "DRIVER_DISTANCE")),
}


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("quick", message, context, level=level)


def _undo_push(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def _editable_selection(context):
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode first")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release the current edit first")
    if not editing.undo_available(context):
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    objects = [obj for obj in context.selected_editable_objects if not obj.library]
    if not objects:
        raise editing.EditRejected("Select something first")
    return objects


def duplicate(context, objects):
    """Full copies beside the originals, offset to the viewer's right, selected."""
    location, rotation, units = viewer_scale.viewer_frame(context)
    offset = rotation @ Vector((0.1 * units, 0.0, 0.0))
    copies = []
    for source in objects:
        copy = source.copy()
        if source.data is not None:
            copy.data = source.data.copy()
        for collection in source.users_collection:
            collection.objects.link(copy)
        copy.matrix_world = source.matrix_world.copy()
        copy.location = copy.location + offset if copy.parent is None else copy.location
        copies.append(copy)
    for obj in objects:
        obj.select_set(False)
    for copy in copies:
        copy.select_set(True)
    context.view_layer.objects.active = copies[-1]
    return copies


def bring_here(context, objects):
    """Move the selection (as a group) to arm's reach in front of the viewer."""
    location, rotation, units = viewer_scale.viewer_frame(context)
    target = location + rotation @ Vector((0.0, -0.25 * units, -0.6 * units))
    roots = [obj for obj in objects if obj.parent not in objects]
    centre = sum((obj.matrix_world.translation for obj in roots), Vector()) / len(roots)
    shift = target - centre
    for obj in roots:
        obj.matrix_world.translation += shift
    return shift.length


class XRWB_OT_quick_duplicate(bpy.types.Operator):
    bl_idname = "xrwb.quick_duplicate"
    bl_label = "Duplicate"
    bl_description = "Copy the selection beside itself and select the copy (grab it to place it)"

    def execute(self, context):
        try:
            copies = duplicate(context, _editable_selection(context))
            _undo_push("XR Duplicate")
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, f"Duplicated {len(copies)}. Squeeze the grip on the copy to place it.")
        return {"FINISHED"}


class XRWB_OT_quick_delete(bpy.types.Operator):
    bl_idname = "xrwb.quick_delete"
    bl_label = "Delete"
    bl_description = "Delete the selected objects (undo brings them back)"

    def execute(self, context):
        try:
            objects = _editable_selection(context)
            names = [obj.name for obj in objects]
            for obj in objects:
                bpy.data.objects.remove(obj, do_unlink=True)
            _undo_push("XR Delete")
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, f"Deleted {len(names)}. Left A / Quest X undoes.")
        return {"FINISHED"}


class XRWB_OT_quick_bring_here(bpy.types.Operator):
    bl_idname = "xrwb.quick_bring_here"
    bl_label = "Bring Here"
    bl_description = "Move the selection to arm's reach in front of you"

    def execute(self, context):
        try:
            distance = bring_here(context, _editable_selection(context))
            _undo_push("XR Bring Here")
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, f"Brought the selection {distance:.2f} units to you.")
        return {"FINISHED"}


def _roots(objects):
    return [obj for obj in objects if obj.parent not in objects]


def drop_to_floor(context, objects) -> int:
    """Rest each selected root on whatever is below it (its own mesh ignored)."""
    moved = 0
    depsgraph = context.evaluated_depsgraph_get()
    for obj in _roots(objects):
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        bottom = min(c.z for c in corners)
        centre = sum(corners, Vector()) / 8
        start = Vector((centre.x, centre.y, bottom - 1e-4))
        for _ in range(4):
            hit, location, _normal, _index, hit_obj, _matrix = context.scene.ray_cast(
                depsgraph, start, Vector((0.0, 0.0, -1.0)))
            if not hit or hit_obj is None or hit_obj.original != obj:
                break
            start = location - Vector((0.0, 0.0, 1e-4))
        if hit:
            obj.matrix_world.translation.z += location.z - bottom
            moved += 1
    return moved


def _upright(obj, yaw):
    location, _rotation, scale = obj.matrix_world.decompose()
    obj.matrix_world = Matrix.LocRotScale(location, Quaternion((0.0, 0.0, 1.0), yaw), scale)


def face_me(context, objects) -> int:
    """Stand each root upright with its front (-Y, Blender's front view) toward you."""
    viewer, _rotation, _units = viewer_scale.viewer_frame(context)
    for obj in _roots(objects):
        towards = viewer - obj.matrix_world.translation
        _upright(obj, math.atan2(towards.x, -towards.y))
    return len(_roots(objects))


def reset_rotation(context, objects) -> int:
    for obj in _roots(objects):
        _upright(obj, 0.0)
    return len(_roots(objects))


def apply_scale(context, objects) -> int:
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if any(obj.data.users > 1 or obj.data.library for obj in meshes):
        raise editing.EditRejected("Apply Scale needs single-user, local mesh data")
    with context.temp_override(selected_editable_objects=meshes, selected_objects=meshes,
                               object=meshes[0] if meshes else None,
                               active_object=meshes[0] if meshes else None):
        if not meshes or "FINISHED" not in bpy.ops.object.transform_apply(location=False, rotation=False,
                                                                            scale=True):
            raise editing.EditRejected("Select meshes to apply their scale")
    return len(meshes)


ARRANGE = {
    "DROP": ("Drop to Floor", "Rest on the surface below", drop_to_floor),
    "FACE": ("Face Me", "Stand upright with the front toward you", face_me),
    "RESET": ("Reset Rotation", "Stand upright, square to the world", reset_rotation),
    "APPLY_SCALE": ("Apply Scale", "Make the current size the object's 1x size", apply_scale),
}


class XRWB_OT_quick_arrange(bpy.types.Operator):
    bl_idname = "xrwb.quick_arrange"
    bl_label = "Arrange"
    bl_description = "Place and straighten the selection; one undo step"
    action: bpy.props.EnumProperty(items=tuple((key, label, description)
                                               for key, (label, description, _fn) in ARRANGE.items()),
                                   options={"SKIP_SAVE"})

    def execute(self, context):
        label, _description, function = ARRANGE[self.action]
        try:
            count = function(context, _editable_selection(context))
            if not count:
                raise editing.EditRejected(f"{label}: nothing below to rest on")
            _undo_push(f"XR {label}")
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, f"{label}: {count} object(s).")
        return {"FINISHED"}


class XRWB_MT_arrange(bpy.types.Menu):
    bl_idname = "XRWB_MT_arrange"
    bl_label = "Arrange"

    def draw(self, _context):
        layout = self.layout
        for key, icon in (("DROP", "TRIA_DOWN_BAR"), ("FACE", "USER"), ("RESET", "ORIENTATION_GLOBAL"),
                          ("APPLY_SCALE", "FULLSCREEN_EXIT")):
            layout.operator("xrwb.quick_arrange", text=ARRANGE[key][0], icon=icon).action = key
        layout.operator("xrwb.quick_bring_here", text="Bring Here", icon="IMPORT")


class XRWB_OT_pick_place(bpy.types.Operator):
    bl_idname = "xrwb.pick_place"
    bl_label = "Pick Shape"
    bl_description = "Choose a shape; a ghost follows your ray until the trigger places it"
    kind: bpy.props.EnumProperty(items=tuple((key, value[0], "") for key, value in physical_tools.PLACE_SHAPES.items()),
                                 options={"SKIP_SAVE"})

    def execute(self, context):
        from . import ui

        reason = ui._scene_tool_reason(context, "place_object")
        if reason:
            _log(context, reason, "WARNING")
            return {"CANCELLED"}
        settings = context.window_manager.xrwb_settings
        settings.place_kind = self.kind
        settings.edit_tool = "place_object"
        label = physical_tools.PLACE_SHAPES[self.kind][0]
        _log(context, f"{label}: the ghost shows where it lands. Trigger places it; hold to slide.")
        return {"FINISHED"}


class XRWB_MT_add(bpy.types.Menu):
    """The basic menu: shapes first, nothing else to learn."""
    bl_idname = "XRWB_MT_add"
    bl_label = "Add"

    def draw(self, context):
        layout = self.layout
        layout.label(text="ADD — PICK, SEE IT, PLACE IT", icon="ADD")
        layout.operator("xrwb.open_hand_menu", text="Prefabs & Starter Scenes  ›", icon="PACKAGE").menu = "XRWB_MT_prefabs"
        layout.operator("xrwb.open_hand_menu", text="SDF Blobs  ›", icon="META_BALL").menu = "XRWB_MT_sdf"
        for key, (label, icon, _size) in physical_tools.PLACE_SHAPES.items():
            layout.operator("xrwb.pick_place", text=label, icon=icon).kind = key
        layout.operator("xrwb.add_clay", text="Clay Ball", icon="MESH_ICOSPHERE")
        layout.separator()
        layout.label(text="Draw by hand (hold, size, release)")
        for tool, text, icon in (("draw_cube", "Draw Box", "MESH_CUBE"), ("draw_sphere", "Draw Sphere", "MESH_UVSPHERE"),
                                 ("draw_cylinder", "Draw Cylinder", "MESH_CYLINDER")):
            layout.operator("xrwb.set_edit_tool", text=text, icon=icon).tool = tool


# The whole Blender menu bar as a stick-walkable list; each entry drills in.
BLENDER_MENUS = (("TOPBAR_MT_file", "File", "FILE_FOLDER"), ("TOPBAR_MT_edit", "Edit", "EDITMODE_HLT"),
                 ("VIEW3D_MT_add", "Add", "ADD"), ("{mode}", "Object / Mesh", "OBJECT_DATA"),
                 ("VIEW3D_MT_select_{mode}", "Select", "RESTRICT_SELECT_OFF"),
                 ("{context}", "Context Menu", "COLLAPSEMENU"), ("VIEW3D_MT_view", "View", "HIDE_OFF"),
                 ("TOPBAR_MT_render", "Render", "RENDER_STILL"), ("TOPBAR_MT_window", "Window", "WINDOW"),
                 ("TOPBAR_MT_help", "Help", "HELP"))


def blender_menu_ids(context):
    """Resolve mode-aware entries to menus that exist in this Blender."""
    mode = {"edit_armature": "armature", "edit_grease_pencil": "greasepencil_edit"}.get(
        context.mode.lower(), context.mode.lower())
    result = []
    for idname, label, icon in BLENDER_MENUS:
        idname = (idname.replace("{mode}", f"VIEW3D_MT_{mode}") if idname == "{mode}"
                  else idname.replace("{context}", f"VIEW3D_MT_{mode}_context_menu")
                  .replace("{mode}", mode))
        if hasattr(bpy.types, idname):
            result.append((idname, label, icon))
    return result


class XRWB_OT_open_hand_menu(bpy.types.Operator):
    bl_idname = "xrwb.open_hand_menu"
    bl_label = "Open Menu"
    bl_description = "Show this menu on your hand; B goes back"
    menu: bpy.props.StringProperty(options={"SKIP_SAVE"})
    push: bpy.props.BoolProperty(default=True, options={"SKIP_SAVE"})

    def execute(self, context):
        from . import controllers, runtime

        if not hasattr(bpy.types, self.menu):
            _log(context, f"Menu is not available here: {self.menu}", "WARNING")
            return {"CANCELLED"}
        if not runtime.session_running(context):
            # Desktop: the same menu as an ordinary popup.
            bpy.ops.wm.call_menu(name=self.menu)
            return {"FINISHED"}
        ok, message = controllers._open_hand_native_menu(context, self.menu, controllers._menu_hand,
                                                         push=self.push)
        if not ok:
            _log(context, message, "WARNING")
            return {"CANCELLED"}
        return {"FINISHED"}


class XRWB_MT_blender(bpy.types.Menu):
    """Blender's own menus, one level at a time: stick picks, click opens, B back."""
    bl_idname = "XRWB_MT_blender"
    bl_label = "Blender"

    def draw(self, context):
        layout = self.layout
        layout.label(text="BLENDER MENUS — STICK PICKS, CLICK OPENS, B BACK", icon="BLENDER")
        for idname, label, icon in blender_menu_ids(context):
            layout.operator("xrwb.open_hand_menu", text=f"{label}  ›", icon=icon).menu = idname


class XRWB_MT_quick(bpy.types.Menu):
    bl_idname = "XRWB_MT_quick"
    bl_label = "Quick"

    def draw(self, context):
        from . import ui

        layout = self.layout
        settings = context.window_manager.xrwb_settings
        workflow = settings.workflow
        label = next(item[1] for item in viewer_scale.WORKFLOWS if item[0] == workflow)
        layout.label(text=f"QUICK — {label.upper()}", icon="SOLO_ON")
        layout.operator("xrwb.open_hand_menu", text="Studio  ›", icon="PRESET").menu = "XRWB_MT_studio"
        layout.operator("xrwb.open_hand_menu", text="Blender Menus  ›", icon="BLENDER").menu = "XRWB_MT_blender"
        layout.operator("xrwb.open_hand_menu", text="Add Shape  ›", icon="ADD").menu = "XRWB_MT_add"
        for tool, text, icon in WORKFLOW_TOOLS.get(workflow, ()):
            row = layout.row()
            row.enabled = not ui._scene_tool_reason(context, tool)
            row.operator("xrwb.set_edit_tool", text=text, icon=icon,
                         depress=settings.edit_tool == tool).tool = tool
        if workflow == "SCULPT":
            layout.prop(settings, "sculpt_invert", text="Dig (carve)")
            layout.prop(settings, "sculpt_mirror_x", text="Mirror X")
            layout.operator("xrwb.add_clay", text="Clay Ball", icon="MESH_ICOSPHERE")
            layout.operator("xrwb.open_hand_menu", text="SDF Blobs  ›", icon="META_BALL").menu = "XRWB_MT_sdf"
        elif workflow == "SHADE":
            layout.operator("xrwb.open_hand_menu", text="UV  ›", icon="UV").menu = "XRWB_MT_uv"
            layout.operator("xrwb.material_shelf", text="Material Shelf", icon="MATERIAL")
            layout.operator("xrwb.show_material_logic", text="Show Logic", icon="NODETREE")
            layout.operator("xrwb.open_hand_menu", text="Node Garden  ›", icon="NODETREE").menu = "XRWB_MT_node_garden"
            layout.operator("xrwb.apply_material", text="Apply to Selected")
        elif workflow == "STAGE":
            for kind, label, icon in (("POINT", "Point Light Here", "LIGHT_POINT"),
                                      ("SPOT", "Spot Light Here", "LIGHT_SPOT")):
                layout.operator("xrwb.light", text=label, icon=icon).kind = kind
            layout.operator("xrwb.camera", text="Camera From My Eyes", icon="VIEW_CAMERA").action = "VIEW"
            layout.operator("xrwb.render", text="Live Render in Headset", icon="SHADING_RENDERED").action = "LIVE"
            layout.operator("xrwb.render", text="Render Image", icon="RENDER_STILL").action = "STILL"
            for menu, label, icon in (("XRWB_MT_lights", "Lights", "LIGHT"),
                                      ("XRWB_MT_camera_render", "Camera & Render", "CAMERA_DATA"),
                                      ("XRWB_MT_world", "World", "WORLD"), ("XRWB_MT_physics", "Physics", "PHYSICS")):
                layout.operator("xrwb.open_hand_menu", text=f"{label}  ›", icon=icon).menu = menu
        elif workflow == "ANIMATE":
            layout.operator("xrwb.anim_key", text="Key Selected", icon="KEY_HLT")
            layout.operator("xrwb.anim_play", text="Play / Pause", icon="PLAY")
            layout.prop(context.scene.tool_settings, "use_keyframe_insert_auto", text="Record")
        else:
            layout.prop(settings, "multi_select", text="Multi-Select")
            layout.operator("xrwb.boolean_selected", text="Subtract Selected", icon="MOD_BOOLEAN").operation = "DIFFERENCE"
            layout.operator("xrwb.bevel_object", text="Bevel Object", icon="MOD_BEVEL")
            layout.operator("xrwb.open_hand_menu", text="Modifiers  ›", icon="MODIFIER").menu = "XRWB_MT_modifiers"
            layout.operator("xrwb.open_hand_menu", text="Modifier Tower  ›", icon="MOD_ARRAY").menu = "XRWB_MT_modifier_tower"
            layout.operator("xrwb.open_hand_menu", text="Edit Vertices / Edges / Faces  ›",
                            icon="EDITMODE_HLT").menu = "XRWB_MT_edit_mesh"
        layout.separator()
        layout.operator("xrwb.quick_duplicate", text="Duplicate", icon="DUPLICATE")
        layout.operator("xrwb.open_hand_menu", text="Arrange  ›", icon="SNAP_ON").menu = "XRWB_MT_arrange"
        layout.operator("xrwb.toggle_desktop", text="Show / Hide Desktop", icon="WINDOW")
        layout.operator("xrwb.quick_delete", text="Delete", icon="TRASH")
        layout.operator("xrwb.history_redo", text="Redo", icon="LOOP_FORWARDS")
        layout.operator("xrwb.undo_ring", text="Undo Ring", icon="RECOVER_LAST")
        layout.separator()
        for key, text, _description in viewer_scale.WORKFLOWS:
            if key != workflow:
                layout.operator("xrwb.workflow", text=f"Switch to {text}").workflow = key
        layout.operator("xrwb.command_menu", text="Settings", icon="PREFERENCES").page = "CONTROLS"


class XRWB_OT_toggle_desktop(bpy.types.Operator):
    bl_idname = "xrwb.toggle_desktop"
    bl_label = "Show / Hide Desktop"
    bl_description = "Show or hide the Blender desktop panels (also left stick click, or B with nothing open)"

    def execute(self, context):
        return bpy.ops.xrwb.controller_recall("EXEC_DEFAULT")


CLASSES = (XRWB_OT_quick_duplicate, XRWB_OT_toggle_desktop, XRWB_OT_open_hand_menu, XRWB_MT_blender, XRWB_OT_quick_delete, XRWB_OT_quick_bring_here, XRWB_OT_quick_arrange,
           XRWB_MT_arrange, XRWB_OT_pick_place,
           XRWB_MT_add, XRWB_MT_quick)
