"""Vertex, edge and face editing in VR, the way Blender's Edit Mode works.

* Switch modes from the wheel or the Edit page: Vertex, Edge, Face or Object.
  Entering one from Object Mode enters Edit Mode on the active mesh.
* Trigger picks the vertex, edge or face under your ray, whichever mode you
  are in. Hold and sweep to paint more into the selection. With Multi-Select
  on, a click toggles and the sweep keeps doing what the first click did.
  Trigger on empty space clears the selection.
* Grip moves the selection (Blender's own edit-mode transform, one undo).
* Extrude: hold the trigger anywhere and pull. The selection extrudes like
  Blender's E, along its average normal (freely for vertices and edges
  without faces), live, and release makes it one undo step.
* The element under your ray is highlighted before you press, in the mode's
  shape: a dot for a vertex, a bar for an edge, a tinted face for a face.

Everything happens on the real edit mesh through bmesh. Cancel (B / Y)
restores the exact selection or geometry.
"""
from __future__ import annotations

from time import monotonic

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import intersect_point_line

from . import editing, physical_tools, viewer_scale

MODES = (("VERT", "Vertex", "VERTEXSEL"), ("EDGE", "Edge", "EDGESEL"), ("FACE", "Face", "FACESEL"),
         ("OBJECT", "Object", "OBJECT_DATAMODE"))
EDIT_TOOLS = {"edit_extrude"}
_bvh_cache: dict = {}


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("component", message, context, level=level)


def current_mode(context) -> str:
    if context.mode != "EDIT_MESH":
        return "OBJECT"
    flags = tuple(context.tool_settings.mesh_select_mode)
    return "VERT" if flags[0] else "EDGE" if flags[1] else "FACE"


def set_mode(context, mode) -> str:
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")
    if mode == "OBJECT":
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        return "Object Mode: hand tools work on whole objects"
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        raise editing.EditRejected("Select a mesh to edit its vertices, edges or faces")
    if obj.library or obj.override_library or obj.data.library:
        raise editing.EditRejected("Linked meshes cannot be edited")
    if context.mode != "EDIT_MESH":
        bpy.ops.object.mode_set(mode="EDIT")
    context.tool_settings.mesh_select_mode = (mode == "VERT", mode == "EDGE", mode == "FACE")
    name = next(label for key, label, _icon in MODES if key == mode)
    return f"{name} mode: trigger picks, sweep adds, grip moves, Extrude pulls"


class XRWB_OT_component_mode(bpy.types.Operator):
    bl_idname = "xrwb.component_mode"
    bl_label = "Select Mode"
    bl_description = "Vertex, Edge or Face (enters Edit Mode), or back to Object Mode"
    mode: bpy.props.EnumProperty(items=[(key, label, "", icon, index) for index, (key, label, icon) in enumerate(MODES)],
                                 options={"SKIP_SAVE"})

    def execute(self, context):
        try:
            message = set_mode(context, self.mode)
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, message)
        return {"FINISHED"}


# ---- Picking ------------------------------------------------------------------------


def edit_bmesh(context):
    obj = context.edit_object
    if obj is None or obj.type != "MESH":
        return None, None
    return obj, bmesh.from_edit_mesh(obj.data)


def _bvh(obj, bm):
    """A BVH of the edit mesh, reused while its shape stays the same."""
    key = (obj.name, len(bm.verts), len(bm.faces))
    cached = _bvh_cache.get("tree")
    stamp = sum(v.co.x + 2.0 * v.co.y + 3.0 * v.co.z for v in bm.verts[:64]) if bm.verts else 0.0
    if cached is not None and cached[0] == key and abs(cached[1] - stamp) < 1e-9 and monotonic() - cached[2] < 0.5:
        return cached[3]
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm)
    _bvh_cache["tree"] = (key, stamp, monotonic(), tree)
    return tree


def forget_cache():
    _bvh_cache.clear()


def pick(obj, bm, origin, direction, mode):
    """(element, hit location in world) under a world ray, or (None, None)."""
    world = obj.matrix_world
    inverse = world.inverted_safe()
    local_origin = inverse @ Vector(origin)
    local_direction = (inverse.to_3x3() @ Vector(direction)).normalized()
    location, _normal, index, _distance = _bvh(obj, bm).ray_cast(local_origin, local_direction)
    if location is None or index is None:
        return None, None
    bm.faces.ensure_lookup_table()
    face = bm.faces[index]
    if face.hide:
        return None, None
    if mode == "VERT":
        element = min(face.verts, key=lambda v: (v.co - location).length)
    elif mode == "EDGE":
        def distance(edge):
            point, factor = intersect_point_line(location, edge.verts[0].co, edge.verts[1].co)
            factor = max(0.0, min(1.0, factor))
            return (edge.verts[0].co.lerp(edge.verts[1].co, factor) - location).length
        element = min(face.edges, key=distance)
    else:
        element = face
    return element, world @ location


def element_points(obj, element):
    """World points for drawing an element: 1 (vertex), 2 (edge) or the face loop."""
    world = obj.matrix_world
    if isinstance(element, bmesh.types.BMVert):
        return [tuple(world @ element.co)]
    return [tuple(world @ v.co) for v in element.verts]


# ---- Selection stroke ---------------------------------------------------------------


def _selection_state(bm):
    return ([v.select for v in bm.verts], [e.select for e in bm.edges], [f.select for f in bm.faces])


class EditSelectStroke(physical_tools._Stroke):
    """Click picks one element; hold and sweep paints more into (or out of) the selection."""

    label = "Select"

    def __init__(self, context, pose, owner, generation, units, toggle):
        super().__init__(context, pose, owner, generation, units)
        self.obj, self.bm = edit_bmesh(context)
        if self.bm is None:
            raise editing.EditRejected("Enter Edit Mode on a mesh first")
        self.mode = current_mode(context)
        self.before = _selection_state(self.bm)
        self.toggle = toggle
        self.adding = True
        self.count = 0
        origin, direction, _rotation = physical_tools._pose_ray(pose)
        element, _location = pick(self.obj, self.bm, origin, direction, self.mode)
        if element is None:
            if not toggle:
                self._select_all(False)
                self.changed = any(any(flags) for flags in self.before)
            self.message = "Selection cleared" if self.changed else "Nothing under the ray"
            return
        if toggle:
            self.adding = not element.select
        else:
            self._select_all(False)
        self._set(element, self.adding)
        self.changed = True

    def _select_all(self, value):
        for sequence in (self.bm.verts, self.bm.edges, self.bm.faces):
            for item in sequence:
                item.select = value
        self.bm.select_history.clear()
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)

    def _set(self, element, value):
        if element.select == value:
            return
        element.select_set(value)
        if value:
            self.bm.select_history.add(element)
        self.bm.select_flush_mode()
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        self.count += 1

    def _valid(self, context):
        try:
            return (editing._safe_pointer(context.scene) == self.scene_pointer and context.mode == "EDIT_MESH"
                    and context.edit_object == self.obj and self.bm.is_valid
                    and len(self.bm.verts) == len(self.before[0]))
        except ReferenceError:
            return False

    def _restore(self, context):
        if not self.bm.is_valid:
            return
        for sequence, flags in zip((self.bm.verts, self.bm.edges, self.bm.faces), self.before):
            for item, flag in zip(sequence, flags):
                item.select = flag
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)

    def _apply(self, context, pose):
        origin, direction, _rotation = physical_tools._pose_ray(pose)
        element, _location = pick(self.obj, self.bm, origin, direction, self.mode)
        if element is not None and element.select != self.adding:
            self._set(element, self.adding)
            self.changed = True
        return f"{'Selecting' if self.adding else 'Deselecting'}: {self.selected_count()} selected"

    def selected_count(self):
        sequence = {"VERT": self.bm.verts, "EDGE": self.bm.edges, "FACE": self.bm.faces}[self.mode]
        return sum(1 for item in sequence if item.select)

    def commit_message(self):
        name = {"VERT": "vertices", "EDGE": "edges", "FACE": "faces"}[self.mode]
        return f"{self.selected_count()} {name} selected (one undo)"


# ---- Extrude stroke ---------------------------------------------------------------


class EditExtrudeStroke(physical_tools._Stroke):
    """Blender's E, by hand: extrude the selection and pull it out live."""

    label = "Extrude"

    def __init__(self, context, pose, owner, generation, units):
        super().__init__(context, pose, owner, generation, units)
        self.obj, self.bm = edit_bmesh(context)
        if self.bm is None:
            raise editing.EditRejected("Enter Edit Mode on a mesh first")
        selected = ([v for v in self.bm.verts if v.select] + [e for e in self.bm.edges if e.select]
                    + [f for f in self.bm.faces if f.select])
        if not selected:
            raise editing.EditRejected("Select vertices, edges or faces to extrude")
        self.backup = bpy.data.meshes.new("XR extrude backup")
        self.bm.to_mesh(self.backup)
        faces = [f for f in selected if isinstance(f, bmesh.types.BMFace)]
        normal = sum((f.normal for f in faces), Vector())
        self.normal_local = normal.normalized() if normal.length > 1e-9 else None
        world = self.obj.matrix_world
        self.inverse3 = world.inverted_safe().to_3x3()
        self.normal_world = ((world.inverted_safe().transposed().to_3x3() @ self.normal_local).normalized()
                             if self.normal_local is not None else None)
        result = bmesh.ops.extrude_face_region(self.bm, geom=selected, use_keep_orig=False)
        new = result["geom"]
        for item in selected:
            if item.is_valid:  # Extruded faces replace the originals.
                item.select_set(False)
        for item in new:
            item.select_set(True)
        self.bm.select_flush_mode()
        self.verts = [v for v in new if isinstance(v, bmesh.types.BMVert)]
        self.start = [v.co.copy() for v in self.verts]
        self.distance = 0.0
        self.changed = False
        bmesh.update_edit_mesh(self.obj.data)

    def _valid(self, context):
        try:
            return (editing._safe_pointer(context.scene) == self.scene_pointer and context.mode == "EDIT_MESH"
                    and context.edit_object == self.obj and self.bm.is_valid)
        except ReferenceError:
            return False

    def _restore(self, context):
        if self.bm.is_valid:
            self.bm.clear()
            self.bm.from_mesh(self.backup)
            bmesh.update_edit_mesh(self.obj.data)
        forget_cache()

    def _finish(self, phase):
        super()._finish(phase)
        backup, self.backup = getattr(self, "backup", None), None
        if backup is not None and backup.users == 0:
            bpy.data.meshes.remove(backup)

    def _apply(self, context, pose):
        delta = Vector(pose.position) - self.anchor
        if self.normal_world is not None:
            self.distance = delta.dot(self.normal_world)
            move = self.inverse3 @ (self.normal_world * self.distance)
        else:
            self.distance = delta.length
            move = self.inverse3 @ delta
        for vert, co in zip(self.verts, self.start):
            vert.co = co + move
        self.bm.normal_update()
        bmesh.update_edit_mesh(self.obj.data)
        self.changed = abs(self.distance) > 1.0e-6
        return f"Extrude {self.distance * 100:.1f} cm"

    def commit(self, context, owner=None):
        if not self.changed and editing.active_transaction() is self and not self.finished:
            # Released without pulling: no zero-length duplicate geometry.
            self._restore(context)
        forget_cache()
        return super().commit(context, owner)

    def commit_message(self):
        from . import polish
        return f"Extruded {polish.length(abs(self.distance))} (one undo)"


def begin(context, tool, pose, owner, generation=0):
    """Start the edit-mode stroke for ``tool``; any non-extrude tool selects."""
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    if not editing.undo_available(context):
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    units = viewer_scale.world_units_per_metre(context)
    if tool == "edit_extrude":
        stroke = EditExtrudeStroke(context, pose, owner, generation, units)
    else:
        toggle = bool(getattr(context.window_manager.xrwb_settings, "multi_select", False))
        stroke = EditSelectStroke(context, pose, owner, generation, units, toggle)
    editing._ACTIVE_TRANSACTION = stroke
    return stroke


# ---- Menu of mesh operations ------------------------------------------------------------

MESH_ACTIONS = (
    ("SELECT_ALL", "Select All", "SELECT_SET", "mesh.select_all", {"action": "SELECT"}),
    ("SELECT_NONE", "Select None", "SELECT_SUBTRACT", "mesh.select_all", {"action": "DESELECT"}),
    ("INVERT", "Invert Selection", "SELECT_DIFFERENCE", "mesh.select_all", {"action": "INVERT"}),
    ("LINKED", "Select Linked", "LINKED", "mesh.select_linked", {}),
    ("GROW", "Grow Selection", "ADD", "mesh.select_more", {}),
    ("SHRINK", "Shrink Selection", "REMOVE", "mesh.select_less", {}),
    ("FILL", "Fill (make face / edge)", "FACESEL", "mesh.edge_face_add", {}),
    ("MERGE", "Merge at Center", "AUTOMERGE_ON", "mesh.merge", {"type": "CENTER"}),
    ("SUBDIVIDE", "Subdivide", "MOD_SUBSURF", "mesh.subdivide", {"number_cuts": 1}),
    ("DUPLICATE", "Duplicate in Place", "DUPLICATE", "mesh.duplicate", {}),
    ("SEPARATE", "Separate to Object", "MOD_EXPLODE", "mesh.separate", {"type": "SELECTED"}),
    ("FLIP", "Flip Normals", "NORMALS_FACE", "mesh.flip_normals", {}),
    ("RECALC", "Recalculate Normals", "NORMALS_VERTEX_FACE", "mesh.normals_make_consistent", {"inside": False}),
    ("CLEAN", "Merge by Distance", "AUTOMERGE_OFF", "mesh.remove_doubles", {}),
    ("DISSOLVE", "Dissolve", "X", "mesh.dissolve_mode", {}),
    ("DELETE", "Delete", "TRASH", "mesh.delete", {}),
)


def mesh_action(context, key):
    if context.mode != "EDIT_MESH":
        raise editing.EditRejected("Enter Vertex, Edge or Face mode first")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")
    _key, label, _icon, path, arguments = next(item for item in MESH_ACTIONS if item[0] == key)
    arguments = dict(arguments)
    if key == "DELETE":
        arguments["type"] = {"VERT": "VERT", "EDGE": "EDGE", "FACE": "FACE"}[current_mode(context)]
    family, name = path.split(".")
    operator = getattr(getattr(bpy.ops, family), name)
    if not operator.poll():
        raise editing.EditRejected(f"{label} is not possible with this selection")
    if "FINISHED" not in operator(**arguments):
        raise editing.EditRejected(f"Blender could not {label.lower()}")
    forget_cache()
    if bpy.ops.ed.undo_push.poll():
        editing.push_undo(message=f"XR {label}")
    return f"{label}: done (one undo)"


class XRWB_OT_mesh_action(bpy.types.Operator):
    bl_idname = "xrwb.mesh_action"
    bl_label = "Mesh Action"
    bl_description = "Edit Mode mesh operations on the current selection; one undo each"
    action: bpy.props.EnumProperty(items=[(key, label, "") for key, label, _i, _p, _a in MESH_ACTIONS],
                                   options={"SKIP_SAVE"})

    def execute(self, context):
        try:
            message = mesh_action(context, self.action)
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, message)
        return {"FINISHED"}


class XRWB_MT_edit_mesh(bpy.types.Menu):
    bl_idname = "XRWB_MT_edit_mesh"
    bl_label = "Edit Mesh"

    def draw(self, context):
        layout = self.layout
        mode = current_mode(context)
        layout.label(text="EDIT MESH — TRIGGER PICKS, SWEEP ADDS, GRIP MOVES", icon="EDITMODE_HLT")
        row = layout.row(align=True)
        for key, label, icon in MODES:
            row.operator("xrwb.component_mode", text=label, icon=icon, depress=mode == key).mode = key
        layout.operator("xrwb.set_edit_tool", text="Extrude (hold trigger, pull)", icon="EXPORT").tool = "edit_extrude"
        layout.operator("xrwb.set_edit_tool", text="Select (trigger / sweep)", icon="RESTRICT_SELECT_OFF").tool = "select"
        layout.prop(context.window_manager.xrwb_settings, "multi_select", text="Multi-Select (toggle picks)")
        if context.mode != "EDIT_MESH":
            layout.label(text="Pick Vertex, Edge or Face to start editing the active mesh.")
            return
        for key, label, icon, _path, _arguments in MESH_ACTIONS:
            layout.operator("xrwb.mesh_action", text=label, icon=icon).action = key


CLASSES = (XRWB_OT_component_mode, XRWB_OT_mesh_action, XRWB_MT_edit_mesh)
