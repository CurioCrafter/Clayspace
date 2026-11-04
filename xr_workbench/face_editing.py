"""Frozen ray-face extrusion/scaling with disposable previews and one undo step.

Only Object-mode, local single-user, unmodified meshes are accepted. These are
mesh edits, not object scale: the rest of a cube stays put when its face scales.
"""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Quaternion, Vector

from . import editing
from .interaction import (
    CaptureOwner, CapturePhase, CaptureState, Decision, EditRequest, EditResult,
    InputProvenance, Pose, TransformOptions, TransformTool,
)


FACE_TOOLS = {"extrude", "extrude_free", "inset", "face_scale"}


def _signature(mesh):
    # ponytail: O(mesh size) detects same-count topology edits; a native edit
    # generation counter can replace this if large-mesh profiling warrants it.
    attributes = []
    for attribute in mesh.attributes:
        field = ("color" if "COLOR" in attribute.data_type else "vector"
                 if attribute.data_type in {"FLOAT_VECTOR", "FLOAT2"}
                 else "value")
        values = tuple(getattr(item, field) for item in attribute.data)
        if field != "value" or attribute.data_type == "INT32_2D":
            values = tuple(tuple(value) for value in values)
        attributes.append((attribute.name, attribute.domain, attribute.data_type, values))
    return (
        tuple(tuple(v.co) for v in mesh.vertices),
        tuple(tuple(e.vertices) for e in mesh.edges),
        tuple((tuple(f.vertices), f.material_index) for f in mesh.polygons),
        tuple(editing._safe_pointer(m) for m in mesh.materials),
        tuple(attributes),
    )


def _require_target(context, obj):
    if context.mode != "OBJECT" or obj is None or obj.type != "MESH":
        raise editing.EditRejected("Face tools require a mesh in Object mode")
    mesh = obj.data
    if obj.hide_select or not obj.visible_get(view_layer=context.view_layer):
        raise editing.EditRejected("Face target must be visible and selectable")
    if (not obj.is_editable or not mesh.is_editable or obj.library or mesh.library
            or obj.override_library or mesh.override_library):
        raise editing.EditRejected("Linked, overridden or read-only meshes are unsupported")
    if mesh.users != 1:
        raise editing.EditRejected("Face tools require single-user mesh data")
    if mesh.shape_keys or mesh.has_custom_normals:
        raise editing.EditRejected("Shape keys and custom split normals are not yet supported")
    if any(m.show_viewport for m in obj.modifiers):
        raise editing.EditRejected("Disable viewport modifiers before editing a source face")
    if obj.constraints:
        raise editing.EditRejected("Constrained objects require a verified source-face adapter")
    if (not all(math.isfinite(value) for row in obj.matrix_world for value in row)
            or abs(obj.matrix_world.determinant()) < 1.0e-10):
        raise editing.EditRejected("Object transform is singular")
    supported = {"FLOAT", "INT", "BOOLEAN", "FLOAT_VECTOR", "FLOAT_COLOR", "BYTE_COLOR"}
    if any(a.data_type not in supported
           and not (a.name == ".edge_verts" and a.data_type == "INT32_2D")
           and not (a.data_type == "FLOAT2" and a.domain == "CORNER"
                    and a.name in mesh.uv_layers)
           for a in mesh.attributes):
        raise editing.EditRejected("Mesh has attributes not covered by the face-edit adapter")


class FaceTransaction:
    def __init__(self, context, obj, face_index, pose, tool, owner, generation):
        self.tool = tool
        self.request = EditRequest(owner, TransformTool.MOVE, generation, pose,
                                   TransformOptions())
        self.capture = CaptureState(self.request)
        self.obj = obj
        self.original = obj.data
        self.original_pointer = editing._safe_pointer(self.original)
        self.scene_pointer = editing._safe_pointer(context.scene)
        self.world = obj.matrix_world.copy()
        self.inverse = self.world.inverted()
        self.original_signature = _signature(self.original)
        self.original_slots = tuple((s.link, editing._safe_pointer(s.material))
                                    for s in obj.material_slots)
        self.base = bmesh.new()
        self.preview = None
        self.mesh = None
        self.work = None
        self.value = 1.0 if tool == "face_scale" else 0.0
        self.anchor_value = self.value
        # True once a direction switch has folded an earlier drag into the cap.
        self.baked = False
        self.anchor_origin = Vector(pose.position)
        self.right = Quaternion(pose.rotation).normalized() @ Vector((1, 0, 0))
        try:
            self.base.from_mesh(self.original)
            self.base.faces.ensure_lookup_table()
            if face_index < 0 or face_index >= len(self.base.faces):
                raise editing.EditRejected("Ray face has no verified source index")
            face = self.base.faces[face_index]
            if face.hide or face.calc_area() < 1.0e-12 or any(len(e.link_faces) > 2 for e in face.edges):
                raise editing.EditRejected("Hidden, degenerate or non-manifold face is unsupported")
            self.face_index = face_index
            self.center = self.world @ face.calc_center_median()
            self.normal = (self.inverse.transposed().to_3x3() @ face.normal).normalized()
            self.radius = max((self.world @ v.co - self.center).length for v in face.verts)
            # World-to-local length factor for the inset thickness (uniform part).
            self._local_scale = max(1.0e-9, sum(abs(v) for v in self.world.to_scale()) / 3.0)
            if self.radius < 1.0e-8:
                raise editing.EditRejected("Face has no usable scale radius")
            self._reset_work()
            self.preview = self.original.copy()
            self.preview.name = self.original.name + " [XR face preview]"
            self.mesh = self.preview
            obj.data = self.preview
            self.preview_signature = _signature(self.preview)
        except Exception:
            self._free()
            raise

    @property
    def finished(self):
        return self.capture.finished

    def _reset_work(self):
        if self.work is not None:
            self.work.free()
        self.work = self.base.copy()
        self.work.faces.ensure_lookup_table()
        face = self.work.faces[self.face_index]
        if self.tool in {"extrude", "extrude_free"}:
            # Edges must be included: a face alone leaves the original cap and
            # creates internal/non-manifold geometry on a closed cube.
            result = bmesh.ops.extrude_face_region(
                self.work, geom=[face, *face.edges], use_keep_orig=False)
            self.vertices = tuple(v for v in result["geom"] if isinstance(v, bmesh.types.BMVert))
            self.cap_faces = tuple(f for f in result["geom"] if isinstance(f, bmesh.types.BMFace))
        else:
            self.vertices = tuple(face.verts)
            self.cap_faces = (face,)
        self.coordinates = tuple(v.co.copy() for v in self.vertices)
        self.start_coordinates = self.coordinates  # Direction switches never move this.

    def _valid(self, context):
        try:
            if (context.mode != "OBJECT" or editing._safe_pointer(context.scene) != self.scene_pointer
                    or self.obj.name not in context.view_layer.objects
                    or self.obj.data != self.preview or self.preview.users != 1
                    or self.original.users != 0 or not self.obj.is_editable
                    or not self.original.is_editable or self.obj.library or self.obj.override_library
                    or self.obj.constraints or any(m.show_viewport for m in self.obj.modifiers)
                    or tuple((s.link, editing._safe_pointer(s.material)) for s in self.obj.material_slots)
                    != self.original_slots):
                return False
            if any(abs(a-b) > 1.0e-7 for ra, rb in zip(self.world, self.obj.matrix_world)
                   for a, b in zip(ra, rb)):
                return False
            return (_signature(self.original) == self.original_signature
                    and _signature(self.preview) == self.preview_signature)
        except (ReferenceError, AttributeError):
            return False

    def _free(self):
        for name in ("work", "base"):
            bm = getattr(self, name, None)
            if bm is not None:
                bm.free()
                setattr(self, name, None)
        if self.preview is not None:
            try:
                if self.preview.users == 0:
                    bpy.data.meshes.remove(self.preview)
            except ReferenceError:
                pass
            self.preview = None
        self.mesh = None

    def _finish(self, phase):
        self.capture.finish(phase)
        if editing._ACTIVE_TRANSACTION is self:
            editing._ACTIVE_TRANSACTION = None
        self._free()

    def cancel(self, context, owner=None):
        editing._require_main_thread()
        if self.finished:
            return EditResult(Decision.REJECTED, "Face edit is already finished")
        if owner is not None and owner != self.request.owner:
            return EditResult(Decision.REJECTED, "Input belongs to another capture")
        restored = False
        try:
            # An external replacement datablock is authoritative. Do not touch it.
            if (self.obj.data == self.preview and self.obj.mode == "OBJECT"
                    and _signature(self.preview) == self.preview_signature
                    and editing._safe_pointer(self.original)):
                self.obj.data = self.original
                restored = True
        except (ReferenceError, RuntimeError, AttributeError, TypeError):
            pass
        self._finish(CapturePhase.CANCELLED)
        return EditResult(Decision.CANCEL_REQUIRED, "Face preview cancelled; original mesh restored"
                          if restored else "Capture ended; externally edited mesh preserved")

    def update(self, context, sample):
        editing._require_main_thread()
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, "Face edit does not own capture")
        if not self._valid(context) or not editing.undo_available(context):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Face target or undo state changed")
        result = self.capture.evaluate(sample)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
            return result
        if result.decision not in {Decision.APPLY, Decision.REBASE}:
            return result
        position = Vector(sample.pose.position)
        if not all(math.isfinite(value) for value in position):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Controller position is outside Blender's finite range")
        if result.decision is Decision.REBASE:
            self.anchor_origin = position
            self.anchor_value = self.value
            return result
        delta = position - self.anchor_origin
        if self.tool == "extrude_free":
            # The new cap follows the hand in any direction, not just the normal.
            self.value = min(1.0e5, delta.length)
            translation = self.inverse.to_3x3() @ delta
            for vert, co in zip(self.vertices, self.coordinates):
                vert.co = co + translation
        elif self.tool == "inset":
            # Drag sideways: the border grows inward, up to nearly the whole face.
            thickness = max(0.0, min(self.radius * 0.95, self.anchor_value + delta.dot(self.right)))
            self.value = thickness
            self._reset_work()
            if thickness > 1.0e-7:
                face = self.work.faces[self.face_index]
                bmesh.ops.inset_region(self.work, faces=[face], thickness=thickness / self._local_scale,
                                       depth=0.0, use_even_offset=True)
                self.work.faces.ensure_lookup_table()
                self.cap_faces = (self.work.faces[self.face_index],)
        elif self.tool == "extrude":
            self.value = max(-1.0e5, min(1.0e5, self.anchor_value + delta.dot(self.normal)))
            translation = self.inverse.to_3x3() @ (self.normal * self.value)
            for vert, co in zip(self.vertices, self.coordinates):
                vert.co = co + translation
        else:
            factor = sample.uniform_scale
            if factor is None:
                factor = self.anchor_value * math.exp(max(-6.0, min(6.0, delta.dot(self.right) / self.radius)))
            self.value = max(0.05, min(20.0, factor))
            for vert, co in zip(self.vertices, self.coordinates):
                vert.co = self.inverse @ (self.center + (self.world @ co - self.center) * self.value)
        self.work.normal_update()
        # A neutral extrusion must not leave four zero-area side walls.
        source = (self.base if self.tool in {"extrude", "extrude_free", "inset"} and self._is_unchanged()
                  else self.work)
        source.to_mesh(self.preview)
        self.preview.update()
        self.preview_signature = _signature(self.preview)
        return EditResult(Decision.APPLY, f"{self.tool}: {self.value:.3f}")

    def tick(self, context, timestamp_seconds):
        result = self.capture.tick(timestamp_seconds)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        return result

    def _is_unchanged(self):
        return not self.baked and abs(self.value - (1.0 if self.tool == "face_scale" else 0.0)) < 1.0e-7

    def toggle_direction(self, position):
        """Switch between straight-out (normal) and free extrusion mid-drag.

        The cap keeps where it is; the new rule applies from the current hand
        position, so switching never makes the geometry jump.
        """
        if self.finished or self.tool not in {"extrude", "extrude_free"}:
            raise editing.EditRejected("Only an extrusion can switch direction")
        self.baked = self.baked or abs(self.value) > 1.0e-7
        self.coordinates = tuple(v.co.copy() for v in self.vertices)
        self.anchor_origin = Vector(position)
        self.anchor_value = 0.0
        self.value = 0.0
        self.tool = "extrude_free" if self.tool == "extrude" else "extrude"
        return "Free: the cap follows your hand" if self.tool == "extrude_free" else "Straight out along the face"

    def preview_outline(self):
        """World-space loops of the moving face(s), start outline, and side edges.

        The overlay draws these on top of everything so the face you are
        working on is always visible, even inside or behind other geometry.
        """
        if self.work is None or self.base is None:
            return [], [], []
        world = self.world
        caps = [[tuple(world @ v.co) for v in face.verts] for face in self.cap_faces if face.is_valid]
        self.base.faces.ensure_lookup_table()
        start = [tuple(world @ v.co) for v in self.base.faces[self.face_index].verts]
        sides = []
        if self.tool in {"extrude", "extrude_free"}:
            sides = [(tuple(world @ co), tuple(world @ v.co))
                     for v, co in zip(self.vertices, self.start_coordinates) if v.is_valid]
        return caps, start, sides

    def cap_center(self):
        """World centre of the moving cap (extrusions) or the edited face."""
        return self.world @ (sum((v.co for v in self.vertices), Vector()) / len(self.vertices))

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, "Face edit does not own capture")
        result = self.capture.can_finish(owner or self.request.owner)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        if result.decision is not Decision.APPLY:
            return result
        if not self._valid(context) or not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Face target or undo context changed")
        unchanged = self._is_unchanged()
        self.obj.data = self.original
        if not unchanged:
            try:
                self.work.to_mesh(self.original)
                self.original.update()
                # Drop the zero-user preview before recording history, so redo
                # cannot resurrect a hidden disposable mesh for every stroke.
                bpy.data.meshes.remove(self.preview)
                self.preview = None
                self.mesh = None
                if "FINISHED" not in bpy.ops.ed.undo_push(message="XR Face " + self.tool.replace("_", " ")):
                    raise RuntimeError("Blender rejected the undo entry")
            except Exception as exc:
                self.base.to_mesh(self.original)
                self.original.update()
                self._finish(CapturePhase.CANCELLED)
                return EditResult(Decision.REJECTED, f"Face edit restored: {exc}")
        self._finish(CapturePhase.COMMITTED)
        return EditResult(Decision.APPLY, "Face edit committed as one undo step" if not unchanged else "No mesh change")


def begin(context, pose_origin, pose_rotation, tool="extrude", owner=None, target_generation=0):
    editing._require_main_thread()
    if tool not in FACE_TOOLS:
        raise editing.EditRejected("Unknown face tool")
    if not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    pose = Pose(tuple(pose_origin), tuple(pose_rotation))
    rotation = Quaternion(pose.rotation)
    if (not all(math.isfinite(value) for value in Vector(pose.position))
            or not math.isfinite(rotation.magnitude) or rotation.magnitude < 1.0e-8):
        raise editing.EditRejected("Controller pose is outside Blender's finite range")
    rotation.normalize()
    context.view_layer.update()
    hit, _location, _normal, index, evaluated, matrix = context.scene.ray_cast(
        context.evaluated_depsgraph_get(), Vector(pose.position), rotation @ Vector((0, 0, -1)))
    if not hit or evaluated is None:
        raise editing.EditRejected("Point at a visible mesh face")
    obj = evaluated.original
    _require_target(context, obj)
    if any(abs(a-b) > 1.0e-7 for ra, rb in zip(matrix, obj.matrix_world) for a, b in zip(ra, rb)):
        raise editing.EditRejected("Instanced faces need an explicit source adapter")
    owner = owner or CaptureOwner(InputProvenance.CONTROLLER, "/user/hand/right", 0)
    transaction = FaceTransaction(context, obj, index, pose, tool, owner, target_generation)
    editing._ACTIVE_TRANSACTION = transaction
    return transaction


def chain(context, transaction, pose_origin, pose_rotation):
    """Commit this extrusion as one undo step and keep extruding from its new cap.

    Right A while extruding: each press leaves a segment behind, like stamping,
    so arms, pipes and stairs grow segment by segment in one continuous drag.
    Returns the next transaction, already owning capture.
    """
    editing._require_main_thread()
    if transaction.tool not in {"extrude", "extrude_free"} or transaction.finished:
        raise editing.EditRejected("Chaining works while extruding")
    if transaction._is_unchanged():
        raise editing.EditRejected("Pull the face out first, then press A to extrude again")
    obj, tool, owner = transaction.obj, transaction.tool, transaction.request.owner
    generation = transaction.request.target_generation
    local = transaction.inverse @ transaction.cap_center()
    normal = transaction.inverse.transposed().to_3x3().inverted() @ transaction.normal
    result = transaction.commit(context, owner)
    if result.decision is not Decision.APPLY:
        raise editing.EditRejected(result.message)
    polygons = obj.data.polygons
    index = min(range(len(polygons)), key=lambda i: ((polygons[i].center - local).length,
                                                     -polygons[i].normal.dot(normal)))
    pose = Pose(tuple(pose_origin), tuple(pose_rotation))
    following = FaceTransaction(context, obj, index, pose, tool, owner, generation)
    editing._ACTIVE_TRANSACTION = following
    return following


CLASSES = ()
