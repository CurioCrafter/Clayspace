"""Native Blender drag-drawn primitives and editable non-destructive cutters.

No third-party add-on is required. A frozen controller-aligned box has its first
corner 20 cm ahead of the aim pose; dragging the tip defines its opposite corner.
"""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

from . import editing, face_editing, runtime
from .interaction import (
    CaptureOwner, CapturePhase, CaptureState, Decision, EditRequest, EditResult,
    InputProvenance, Pose, TransformOptions, TransformTool,
)

TOOLS = {"draw_cube", "draw_sphere", "draw_cylinder", "cut_difference", "cut_union", "cut_intersect"}


def _world_scale(context):
    value = (float(bpy.types.XrSessionState.hand_tracking_world_transform(context)[0])
             if runtime.session_running(context) else 1.0)
    if not math.isfinite(value) or not 1.0e-6 <= value <= 1.0e6:
        raise editing.EditRejected("XR world scale is outside the drawing range")
    return value


def _pose_tip(pose, scale):
    origin, rotation = Vector(pose.position), Quaternion(pose.rotation)
    if (not all(math.isfinite(value) for value in origin)
            or not math.isfinite(rotation.magnitude) or rotation.magnitude < 1.0e-8):
        raise editing.EditRejected("Controller pose is outside Blender's finite range")
    rotation.normalize()
    return origin + rotation @ Vector((0, 0, -.2 * scale)), rotation


def draw_frame(rotation):
    """Upright box axes from the controller's heading only.

    Tilting or rolling the wrist must not tilt the box: up is always height,
    left/right width and forward/back depth, so every direction stretches as
    expected. Pointing straight up or down falls back to the controller's top.
    """
    rotation = Quaternion(rotation).normalized()
    forward = rotation @ Vector((0.0, 0.0, -1.0))
    forward.z = 0.0
    if forward.length < 0.2:
        forward = rotation @ Vector((0.0, 1.0, 0.0))
        forward.z = 0.0
    if forward.length < 1.0e-6:
        return Quaternion()
    return Quaternion((0.0, 0.0, 1.0), math.atan2(-forward.x, forward.y))


def _mesh_signature(mesh):
    # ponytail: source topology/coordinate validation is O(mesh size); use a
    # native edit-generation counter if large-mesh cutter profiling warrants it.
    return (tuple(tuple(v.co) for v in mesh.vertices),
            tuple(tuple(e.vertices) for e in mesh.edges),
            tuple((tuple(f.vertices), f.material_index) for f in mesh.polygons),
            tuple(editing._safe_pointer(material) for material in mesh.materials),
            tuple((attribute.name, attribute.data_type, attribute.domain) for attribute in mesh.attributes))


def _modifier_signature(obj, exclude=None):
    result = []
    for modifier in obj.modifiers:
        if modifier == exclude:
            continue
        values = []
        for prop in modifier.bl_rna.properties:
            if prop.is_readonly or prop.identifier in {"rna_type", "is_active"} or prop.type == "COLLECTION":
                continue
            value = getattr(modifier, prop.identifier)
            if prop.type == "POINTER":
                value = editing._safe_pointer(value)
            elif getattr(prop, "is_array", False):
                value = tuple(value)
            values.append((prop.identifier, value))
        result.append((editing._safe_pointer(modifier), tuple(values)))
    return tuple(result)


def _owned_modifier_state(target, modifier):
    pointer = editing._safe_pointer(modifier)
    return next((entry for entry in _modifier_signature(target) if entry[0] == pointer), None)


def _require_target(context):
    obj = context.view_layer.objects.active
    if (obj is None or obj.type != "MESH" or not obj.select_get()
            or obj not in context.selected_editable_objects or obj.hide_select
            or not obj.visible_get(view_layer=context.view_layer)):
        raise editing.EditRejected("Select an editable visible mesh as the cutter target")
    if (not obj.is_editable or not obj.data.is_editable or obj.library or obj.data.library
            or obj.override_library or obj.data.override_library):
        raise editing.EditRejected("Linked, overridden or read-only Boolean targets are unsupported")
    if not obj.data.polygons:
        raise editing.EditRejected("Boolean target has no faces")
    if (not all(math.isfinite(value) for row in obj.matrix_world for value in row)
            or abs(obj.matrix_world.determinant()) < 1.0e-10):
        raise editing.EditRejected("Boolean target has an invalid transform")
    return obj


class DrawTransaction:
    def __init__(self, context, pose, tool, owner, generation):
        self.request = EditRequest(owner, TransformTool.MOVE, generation, pose, TransformOptions())
        self.capture = CaptureState(self.request)
        self.tool = tool
        settings = getattr(context.window_manager, "xrwb_settings", None)
        self.shape = (str(getattr(settings, "cutter_shape", "CUBE")) if tool.startswith("cut_")
                      else tool.removeprefix("draw_").upper())
        if self.shape not in {"CUBE", "SPHERE", "CYLINDER"}:
            raise editing.EditRejected("Unsupported cutter primitive")
        self.scale = _world_scale(context)
        self.anchor, hand_rotation = _pose_tip(pose, self.scale)
        self.rotation = draw_frame(hand_rotation)
        self.last_tip = self.anchor.copy()
        self.tip_offset = Vector()
        self.scene = context.scene
        self.view_layer_pointer = editing._safe_pointer(context.view_layer)
        self.collection = context.collection
        if (self.collection is None or not self.collection.is_editable or self.collection.library
                or self.collection.override_library):
            raise editing.EditRejected("Choose a local editable collection for the new object")
        self.selection = editing._selection_snapshot(context)
        self.selected = tuple(editing._safe_pointer(o) for o in context.selected_objects)
        self.active_pointer = editing._safe_pointer(context.view_layer.objects.active)
        self.target = _require_target(context) if tool.startswith("cut_") else None
        self.target_mesh = self.target.data if self.target is not None else None
        self.target_geometry = _mesh_signature(self.target_mesh) if self.target is not None else None
        self.target_world = self.target.matrix_world.copy() if self.target is not None else None
        self.target_modifiers = _modifier_signature(self.target) if self.target is not None else None
        self.target_active_modifier = (next((m for m in self.target.modifiers if m.is_active), None)
                                       if self.target is not None else None)
        self.obj = None
        self.mesh = None
        self.modifier = None
        self.preview_matrix = None
        self.preview_geometry = None
        self.preview_slots = None
        self.preview_ready = False
        self.modifier_state = None

    def _preview_untouched(self):
        try:
            return (self.obj.data == self.mesh and self.obj.users == 1 and self.mesh.users == 1
                    and tuple(self.obj.users_collection) == (self.collection,)
                    and self.obj.parent is None and not self.obj.constraints and not self.obj.modifiers
                    and self.obj.name == self.preview_name and not tuple(self.obj.keys())
                    and face_editing._signature(self.mesh) == self.preview_geometry
                    and tuple((slot.link, editing._safe_pointer(slot.material))
                              for slot in self.obj.material_slots) == self.preview_slots
                    and all(abs(a-b) <= 1.0e-6 for ra, rb in zip(self.obj.matrix_world, self.preview_matrix)
                            for a, b in zip(ra, rb)))
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False

    @property
    def finished(self):
        return self.capture.finished

    def _valid(self, context):
        try:
            if (context.mode != "OBJECT" or context.scene != self.scene
                    or editing._safe_pointer(context.view_layer) != self.view_layer_pointer
                    or context.collection != self.collection or not self.collection.is_editable
                    or abs(_world_scale(context)-self.scale) > 1.0e-6 * self.scale
                    or tuple(editing._safe_pointer(o) for o in context.selected_objects) != self.selected
                    or editing._safe_pointer(context.view_layer.objects.active) != self.active_pointer):
                return False
            if self.target is not None:
                if (_require_target(context) != self.target or self.target.data != self.target_mesh
                        or _mesh_signature(self.target_mesh) != self.target_geometry
                        or self.target.matrix_world != self.target_world
                        or _modifier_signature(self.target, self.modifier) != self.target_modifiers):
                    return False
            if self.obj is not None:
                if not self._preview_untouched():
                    return False
                if self.target is not None and _owned_modifier_state(self.target, self.modifier) != self.modifier_state:
                    return False
            return True
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            return False

    def _drop_preview(self):
        preserve = self.obj is not None and self.preview_ready and not self._preview_untouched()
        if self.modifier is not None:
            try:
                if self.modifier in self.target.modifiers[:]:
                    if self.modifier_state is None or _owned_modifier_state(self.target, self.modifier) == self.modifier_state:
                        restore_active = self.modifier.is_active
                        self.target.modifiers.remove(self.modifier)
                        if (restore_active and self.target_active_modifier is not None
                                and self.target_active_modifier in self.target.modifiers[:]):
                            self.target_active_modifier.is_active = True
                    else:
                        preserve = True
            except (ReferenceError, AttributeError):
                pass
            self.modifier = None
        if preserve:
            self.obj = self.mesh = None
            self.preview_ready = False
            return True
        if self.obj is not None:
            try:
                # Remove our collection link, not an external link/parent use
                # created while this private preview was active.
                if self.obj.name in self.collection.objects:
                    self.collection.objects.unlink(self.obj)
                if self.obj.users == 0:
                    bpy.data.objects.remove(self.obj)
            except ReferenceError:
                pass
            self.obj = None
        if self.mesh is not None:
            try:
                if self.mesh.users == 0:
                    bpy.data.meshes.remove(self.mesh)
            except ReferenceError:
                pass
            self.mesh = None
        self.preview_ready = False
        return False

    def _create_preview(self):
        label = self.tool.replace("_", " ").title()
        self.mesh = bpy.data.meshes.new("XR " + label)
        bm = bmesh.new()
        try:
            if self.shape == "SPHERE":
                bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=.5)
                for face in bm.faces:
                    face.smooth = True
            elif self.shape == "CYLINDER":
                bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32,
                                      radius1=.5, radius2=.5, depth=1)
            else:
                bmesh.ops.create_cube(bm, size=1)
            bm.normal_update()
            bm.to_mesh(self.mesh)
        finally:
            bm.free()
        self.obj = bpy.data.objects.new("XR " + label, self.mesh)
        self.preview_name = self.obj.name
        self.collection.objects.link(self.obj)
        # A drawn object previews as the real solid shape you will get; a cutter
        # stays wireframe in front so the live Boolean result shows through it.
        self.obj.display_type = "WIRE" if self.target is not None else "TEXTURED"
        self.obj.hide_render = self.target is not None
        self.obj.show_in_front = self.target is not None
        self.obj.color = (.05, .4, 1.0, 1.0)
        self.preview_geometry = face_editing._signature(self.mesh)
        self.preview_slots = tuple((slot.link, editing._safe_pointer(slot.material))
                                   for slot in self.obj.material_slots)
        if self.target is not None:
            self.modifier = self.target.modifiers.new("XR " + label, "BOOLEAN")
            self.modifier.operation = self.tool[4:].upper()
            self.modifier.solver = "EXACT"
            self.modifier.object = self.obj
            self.modifier_state = _owned_modifier_state(self.target, self.modifier)

    def _finish(self, phase):
        self.capture.finish(phase)
        if editing._ACTIVE_TRANSACTION is self:
            editing._ACTIVE_TRANSACTION = None

    def cancel(self, context, owner=None):
        editing._require_main_thread()
        if self.finished:
            return EditResult(Decision.REJECTED, "Draw gesture is already finished")
        if owner is not None and owner != self.request.owner:
            return EditResult(Decision.REJECTED, "Input belongs to another capture")
        preserved = self._drop_preview()
        # Do not overwrite a selection changed externally during capture.
        if editing._safe_pointer(context.view_layer) == self.view_layer_pointer:
            current = tuple(editing._safe_pointer(o) for o in context.selected_objects)
            if current == self.selected:
                editing._restore_object_selection(context, self.selection)
        self._finish(CapturePhase.CANCELLED)
        return EditResult(Decision.CANCEL_REQUIRED, "External draw/cutter edits preserved; capture ended"
                          if preserved else "Own draw preview removed; source objects preserved")

    def update(self, context, sample):
        editing._require_main_thread()
        if self.finished or editing.active_transaction() is not self:
            return EditResult(Decision.REJECTED, "Draw gesture does not own capture")
        if not editing.undo_available(context) or not self._valid(context):
            cancelled = self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Draw context or target changed; " + cancelled.message)
        result = self.capture.evaluate(sample)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
            return result
        if result.decision not in {Decision.APPLY, Decision.REBASE}:
            return result
        try:
            tip, _rotation = _pose_tip(sample.pose, self.scale)
            if result.decision is Decision.REBASE:
                self.tip_offset = self.last_tip-tip
                return result
            tip += self.tip_offset
            local = self.rotation.inverted() @ (tip-self.anchor)
            if not all(math.isfinite(value) for value in local):
                raise editing.EditRejected("Draw dimensions are not finite")
            if local.length < .01 * self.scale:
                self._drop_preview()
                self.last_tip = tip
                return EditResult(Decision.APPLY, "Move at least 1 cm to draw")
            local = Vector(tuple(max(-100*self.scale, min(100*self.scale, value)) for value in local))
            dimensions = Vector(tuple(max(.005*self.scale, abs(value)) for value in local))
            # Symmetric thin axes remain centered on the drawn plane. Other axes
            # keep the initial tip fixed as the first corner, including negatives.
            center = self.anchor + self.rotation @ (local * .5)
            if self.obj is None:
                self._create_preview()
            self.obj.matrix_world = (Matrix.Translation(center) @ self.rotation.to_matrix().to_4x4()
                                     @ Matrix.Diagonal((*dimensions, 1)))
            self.last_tip = self.anchor + self.rotation @ local
            context.view_layer.update()
            self.preview_matrix = self.obj.matrix_world.copy()
            self.preview_ready = True
            return EditResult(Decision.APPLY, "Release to keep editable cutter" if self.target is not None
                              else "Release to create object")
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, f"Draw preview cancelled: {exc}")

    def tick(self, context, timestamp_seconds):
        editing._require_main_thread()
        result = self.capture.tick(timestamp_seconds)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        return result

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if self.finished or editing.active_transaction() is not self:
            return EditResult(Decision.REJECTED, "Draw gesture does not own capture")
        result = self.capture.can_finish(owner or self.request.owner)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        if result.decision is not Decision.APPLY:
            return result
        if not self._valid(context) or not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Draw target or undo context changed")
        if self.obj is None:
            self._finish(CapturePhase.COMMITTED)
            return EditResult(Decision.APPLY, "No draw; scene and undo history unchanged")
        try:
            # The mesh belongs solely to this gesture. Baking only its dimensions
            # keeps Bevel and later local edits usable without an Apply Scale step.
            self.mesh.transform(Matrix.Diagonal((*self.obj.scale, 1)))
            self.mesh.update()
            self.obj.scale = (1, 1, 1)
            if self.target is None:
                self.obj.display_type = "TEXTURED"
                self.obj.show_in_front = False
                for obj in context.selected_objects:
                    obj.select_set(False)
                self.obj.select_set(True)
                context.view_layer.objects.active = self.obj
            context.view_layer.update()
            self.preview_matrix = self.obj.matrix_world.copy()
            self.preview_geometry = face_editing._signature(self.mesh)
            self.preview_slots = tuple((slot.link, editing._safe_pointer(slot.material))
                                       for slot in self.obj.material_slots)
            if "FINISHED" not in editing.push_undo(message="XR " + self.tool.replace("_", " ")):
                raise RuntimeError("Blender rejected the undo entry")
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            self._drop_preview()
            editing._restore_object_selection(context, self.selection)
            self._finish(CapturePhase.CANCELLED)
            return EditResult(Decision.REJECTED, f"Draw restored: {exc}")
        self._finish(CapturePhase.COMMITTED)
        return EditResult(Decision.APPLY, "Committed as one Blender undo step")


def begin(context, pose_origin, pose_rotation, tool="draw_cube", owner=None, target_generation=0):
    editing._require_main_thread()
    if tool not in TOOLS:
        raise editing.EditRejected("Unknown native draw/cutter tool")
    if context.mode != "OBJECT":
        raise editing.EditRejected("Draw and cutter tools require Object mode")
    if not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    owner = owner or CaptureOwner(InputProvenance.CONTROLLER, "/user/hand/right", 0)
    transaction = DrawTransaction(context, Pose(tuple(pose_origin), tuple(pose_rotation)),
                                  tool, owner, target_generation)
    editing._ACTIVE_TRANSACTION = transaction
    return transaction


CLASSES = ()
