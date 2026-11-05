"""Unwrap by parts, in VR: point at a face, grow an island, release to unwrap it.

The island floods across neighbouring faces while the angle between them stays
under a crease limit, and never crosses an existing seam. While the trigger is
held, dragging sideways widens or narrows the limit and the island's border is
outlined live. Release marks the border as seams and unwraps just that island
with Blender's own unwrapper, as one undo step. Everything else is untouched.
"""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector

from . import editing, physical_tools, viewer_scale
from .interaction import CapturePhase, Decision, EditResult


def region_faces(bm, start, angle_degrees):
    """Face indices reachable from ``start`` without crossing seams or creases."""
    bm.faces.ensure_lookup_table()
    limit = math.radians(angle_degrees)
    seen = {start}
    stack = [start]
    while stack:
        face = bm.faces[stack.pop()]
        for edge in face.edges:
            if edge.seam:
                continue
            for other in edge.link_faces:
                if other.index in seen or other.hide:
                    continue
                if face.normal.angle(other.normal, 0.0) <= limit:
                    seen.add(other.index)
                    stack.append(other.index)
    return seen


def boundary_edges(bm, faces):
    """Edges with exactly one side in the island (the future seams)."""
    edges = set()
    for index in faces:
        for edge in bm.faces[index].edges:
            if sum(1 for linked in edge.link_faces if linked.index in faces) == 1:
                edges.add(edge.index)
    return edges


class UVIslandStroke(physical_tools._Stroke):
    label = "UV Island"

    def __init__(self, context, pose, obj, face_index, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        physical_tools.require_mesh_target(context, obj, "UV unwrap")
        if any(m.show_viewport for m in obj.modifiers):
            raise editing.EditRejected("Unwrap the base mesh: hide viewport modifiers first")
        self.obj = obj
        self.bm = bmesh.new()
        self.bm.from_mesh(obj.data)
        self.bm.faces.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        if not 0 <= face_index < len(self.bm.faces):
            raise editing.EditRejected("Point at a face of the mesh")
        self.start = face_index
        self.start_angle = float(settings.uv_island_angle)
        self.angle = self.start_angle
        self.right = physical_tools.Quaternion(pose.rotation).normalized() @ Vector((1.0, 0.0, 0.0))
        self._flood()
        self.changed = True

    def _flood(self):
        self.faces = region_faces(self.bm, self.start, self.angle)
        self.border = boundary_edges(self.bm, self.faces)
        world = self.obj.matrix_world
        self.lines = [tuple(world @ vert.co) for index in self.border for vert in self.bm.edges[index].verts]

    def _valid(self, context):
        try:
            return (super()._valid(context) and self.obj.name in context.view_layer.objects
                    and len(self.obj.data.polygons) == len(self.bm.faces))
        except ReferenceError:
            return False

    def _restore(self, context):
        pass  # Nothing is written until release.

    def _finish(self, phase):
        super()._finish(phase)
        if self.bm is not None:
            self.bm.free()
            self.bm = None

    def _apply(self, context, pose):
        # 10 cm sideways = 20 degrees wider (or narrower going the other way).
        metres = (Vector(pose.position) - self.anchor).dot(self.right) / self.units
        self.angle = max(1.0, min(89.0, self.start_angle + metres * 200.0))
        self._flood()
        return f"Island: {len(self.faces)} faces (crease limit {self.angle:.0f}°)"

    def commit(self, context, owner=None):
        # Unwrap before the base class pushes its single undo step.
        editing._require_main_thread()
        if editing.active_transaction() is self and not self.finished and self._valid(context):
            seams = [edge.use_seam for edge in self.obj.data.edges]
            try:
                unwrap_faces(context, self.obj, self.faces, self.border)
            except (editing.EditRejected, RuntimeError) as exc:
                if context.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                self.obj.data.edges.foreach_set("use_seam", seams)
                self._finish(CapturePhase.CANCELLED)
                return EditResult(Decision.REJECTED, f"Unwrap failed: {exc}")
        return super().commit(context, owner)

    def commit_message(self):
        return f"Unwrapped an island of {len(self.faces)} faces; its border is now a seam"


def unwrap_faces(context, obj, faces, seams, method="ANGLE_BASED"):
    """Mark ``seams`` and unwrap only ``faces`` with Blender's unwrapper."""
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    view_layer = context.view_layer
    previous_active = view_layer.objects.active
    view_layer.objects.active = obj
    with context.temp_override(active_object=obj, object=obj, selected_objects=[obj],
                               selected_editable_objects=[obj]):
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_mode(type="FACE")
            bpy.ops.mesh.select_all(action="DESELECT")
            # Select inside Edit Mode: Object Mode face flags are re-derived from
            # the vertex selection on entry and would pull in the whole mesh.
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            for index in seams:
                bm.edges[index].seam = True
            for index in faces:
                bm.faces[index].select_set(True)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            if "FINISHED" not in bpy.ops.uv.unwrap(method=method, margin=0.02):
                raise editing.EditRejected("Blender's unwrapper refused this island")
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = previous_active


def begin(context, pose, owner, generation):
    units = viewer_scale.world_units_per_metre(context)
    origin, direction, _rotation = physical_tools._pose_ray(pose)
    context.view_layer.update()
    hit, _location, _normal, index, evaluated, _matrix = context.scene.ray_cast(
        context.evaluated_depsgraph_get(), origin, direction)
    if not hit or evaluated is None:
        raise editing.EditRejected("Point at a face to start an island")
    settings = context.window_manager.xrwb_settings
    return UVIslandStroke(context, pose, evaluated.original, index, owner, generation, units, settings)


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("uv", message, context, level=level)


def _meshes(context):
    if context.mode != "OBJECT" or editing.active_transaction() is not None:
        raise editing.EditRejected("Switch to Object Mode and release the current edit")
    meshes = [obj for obj in context.selected_editable_objects if obj.type == "MESH" and obj.data.users == 1
              and not obj.data.library]
    if not meshes:
        raise editing.EditRejected("Select a local mesh first")
    return meshes


def _edit_all(context, meshes, operator, **properties):
    view_layer = context.view_layer
    previous = view_layer.objects.active
    view_layer.objects.active = meshes[0]
    with context.temp_override(active_object=meshes[0], object=meshes[0], selected_objects=meshes,
                               selected_editable_objects=meshes):
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_all(action="SELECT")
            result = operator(**properties)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = previous
    return result


UV_ACTIONS = {
    "SMART": ("Smart UV (whole object)", "One click: islands from angles", "UV"),
    "PACK": ("Pack Islands", "Arrange every island without overlaps", "UV_ISLANDSEL"),
    "CLEAR_SEAMS": ("Clear Seams", "Remove all seams on the selected meshes", "X"),
    "CHECKER": ("UV Checker", "Show stretching with a UV checker material", "TEXTURE"),
    "EDITOR": ("Open UV Editor", "The UV editor on the desktop panel", "WINDOW"),
}


class XRWB_OT_uv_action(bpy.types.Operator):
    bl_idname = "xrwb.uv_action"
    bl_label = "UV"
    bl_description = "UV tools for the selected meshes; one undo step each"
    action: bpy.props.EnumProperty(items=tuple((key, value[0], value[1]) for key, value in UV_ACTIONS.items()),
                                   options={"SKIP_SAVE"})

    def execute(self, context):
        from . import materials_lab

        try:
            if self.action == "EDITOR":
                return bpy.ops.xrwb.workspace_editor(editor="UV")
            meshes = _meshes(context)
            if self.action == "SMART":
                for obj in meshes:
                    if not obj.data.uv_layers:
                        obj.data.uv_layers.new(name="UVMap")
                _edit_all(context, meshes, bpy.ops.uv.smart_project, island_margin=0.02)
            elif self.action == "PACK":
                _edit_all(context, meshes, bpy.ops.uv.pack_islands, margin=0.02)
            elif self.action == "CLEAR_SEAMS":
                for obj in meshes:
                    for edge in obj.data.edges:
                        edge.use_seam = False
            else:
                checker = materials_lab.build_preset("UV_CHECKER")
                for obj in meshes:
                    if not obj.material_slots:
                        obj.data.materials.append(checker)
                    else:
                        obj.active_material = checker
            if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(
                    message=f"XR {UV_ACTIONS[self.action][0]}"):
                raise RuntimeError("Blender rejected the undo entry")
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, f"{UV_ACTIONS[self.action][0]}: done.")
        return {"FINISHED"}


class XRWB_MT_uv(bpy.types.Menu):
    bl_idname = "XRWB_MT_uv"
    bl_label = "UV"

    def draw(self, context):
        layout = self.layout
        layout.label(text="UV — POINT AT A FACE, HOLD, DRAG TO GROW", icon="UV")
        layout.operator("xrwb.set_edit_tool", text="UV Island (unwrap a part)", icon="UV_FACESEL").tool = "uv_island"
        layout.prop(context.window_manager.xrwb_settings, "uv_island_angle", text="Crease Limit")
        for key, (label, _description, icon) in UV_ACTIONS.items():
            layout.operator("xrwb.uv_action", text=label, icon=icon).action = key


CLASSES = (XRWB_OT_uv_action, XRWB_MT_uv)
