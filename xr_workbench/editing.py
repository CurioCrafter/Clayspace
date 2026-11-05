"""Main-thread Blender transactions for XR object and mesh transforms.

The collector/controller side supplies immutable :mod:`interaction` requests and
samples.  This module is deliberately pull-driven: it installs no timers and it
never commits on tracking loss or source disappearance.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import threading
import uuid
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from .interaction import (
    CaptureOwner,
    CapturePhase,
    CaptureState,
    Constraint,
    Decision,
    EditRequest,
    EditResult,
    InputSample,
    Pose,
    SelectionClickState,
    SelectionRequest,
    TransformTool,
    bounded_uniform_scale,
    constrained_vector,
    quantize,
    scale_axes,
    snapped_translation,
    uniform_scale,
)


class EditRejected(RuntimeError):
    """Raised when a requested edit cannot be made transaction-safe."""


@dataclass(frozen=True)
class _ObjectSnapshot:
    object_ref: Any
    pointer: int
    matrix_world: Matrix


@dataclass(frozen=True)
class _MeshSnapshot:
    object_ref: Any
    object_pointer: int
    data_pointer: int
    topology: tuple[int, int, int]
    modifier_signature: tuple[tuple[int, str, bool, bool], ...]
    selected_vertices: tuple[int, ...]
    vertex_coordinates: tuple[Vector, ...]
    vertex_selection: tuple[bool, ...]
    edge_selection: tuple[bool, ...]
    face_selection: tuple[bool, ...]


@dataclass(frozen=True)
class _SelectionSnapshot:
    selected_objects: tuple[Any, ...]
    active_object: Any | None


@dataclass
class _CommitPayload:
    mode: str
    objects: tuple[_ObjectSnapshot, ...]
    mesh: _MeshSnapshot | None
    selection: _SelectionSnapshot

    def apply(self, context: Any) -> None:
        if self.mode == "OBJECT":
            _apply_object_snapshots(self.objects)
        else:
            if self.mesh is None:
                raise EditRejected("Mesh commit payload is incomplete")
            _apply_mesh_snapshot(self.mesh)
        _restore_object_selection(context, self.selection)


@dataclass
class _SelectionCommitPayload:
    selection: _SelectionSnapshot

    def apply(self, context: Any) -> None:
        _restore_object_selection(context, self.selection)


_ACTIVE_TRANSACTION: Any | None = None
_COMMIT_PAYLOADS: OrderedDict[str, _CommitPayload | _SelectionCommitPayload] = (
    OrderedDict()
)
_MAX_COMMIT_PAYLOADS = 128
_TRANSFORM_EPSILON = 1.0e-8


def _require_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise EditRejected("Blender edits must run on the main thread")


def undo_available(context: Any) -> bool:
    settings = context.preferences.edit
    return settings.use_global_undo and settings.undo_steps > 0


def _safe_pointer(value: Any) -> int:
    try:
        return int(value.as_pointer())
    except (ReferenceError, AttributeError):
        return 0


def _mesh_topology(obj: Any) -> tuple[int, int, int]:
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        return len(bm.verts), len(bm.edges), len(bm.faces)
    mesh = obj.data
    return len(mesh.vertices), len(mesh.edges), len(mesh.polygons)


def _modifier_signature(obj: Any) -> tuple[tuple[int, str, bool, bool], ...]:
    return tuple(
        (
            _safe_pointer(modifier),
            modifier.type,
            bool(modifier.show_in_editmode),
            bool(modifier.show_on_cage),
        )
        for modifier in obj.modifiers
    )


def _restore_object_selection(context: Any, snapshot: _SelectionSnapshot) -> None:
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None:
        return
    for obj in view_layer.objects:
        obj.select_set(False)
    for obj in snapshot.selected_objects:
        try:
            if _safe_pointer(obj):
                obj.select_set(True)
        except (ReferenceError, RuntimeError):
            continue
    try:
        if snapshot.active_object is None or _safe_pointer(snapshot.active_object):
            view_layer.objects.active = snapshot.active_object
    except (ReferenceError, RuntimeError):
        pass


def _apply_object_snapshots(snapshots: tuple[_ObjectSnapshot, ...]) -> None:
    # Parents first, then overwrite every captured child's world transform.  This
    # preserves exact world-space targets even with rotated/scaled parents.
    remaining = list(snapshots)
    captured = {item.pointer for item in snapshots}
    ordered: list[_ObjectSnapshot] = []
    while remaining:
        moved = False
        for item in tuple(remaining):
            parent = item.object_ref.parent
            if (
                parent is None
                or _safe_pointer(parent) not in captured
                or any(done.pointer == _safe_pointer(parent) for done in ordered)
            ):
                ordered.append(item)
                remaining.remove(item)
                moved = True
        if not moved:
            ordered.extend(remaining)
            break
    for item in ordered:
        if not _matrix_close(item.object_ref.matrix_world, item.matrix_world):
            item.object_ref.matrix_world = item.matrix_world.copy()


def _matrix_close(left: Matrix, right: Matrix) -> bool:
    return all(
        abs(left[row][column] - right[row][column]) <= _TRANSFORM_EPSILON
        for row in range(4)
        for column in range(4)
    )


def _apply_mesh_snapshot(snapshot: _MeshSnapshot) -> None:
    obj = snapshot.object_ref
    if _safe_pointer(obj) != snapshot.object_pointer:
        raise EditRejected("Edited object no longer exists")
    if _safe_pointer(obj.data) != snapshot.data_pointer:
        raise EditRejected("Edited mesh datablock changed")
    if _mesh_topology(obj) != snapshot.topology:
        raise EditRejected("Edited mesh topology changed")
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        changed = False
        for index, coordinate in enumerate(snapshot.vertex_coordinates):
            if (bm.verts[index].co - coordinate).length > _TRANSFORM_EPSILON:
                bm.verts[index].co = coordinate.copy()
                changed = True
        for index, selected in enumerate(snapshot.face_selection):
            changed = changed or bm.faces[index].select != selected
            bm.faces[index].select = selected
        for index, selected in enumerate(snapshot.edge_selection):
            changed = changed or bm.edges[index].select != selected
            bm.edges[index].select = selected
        # Vertices are last because deselecting an edge/face may cascade down.
        for index, selected in enumerate(snapshot.vertex_selection):
            changed = changed or bm.verts[index].select != selected
            bm.verts[index].select = selected
        if changed:
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        return
    for index, coordinate in enumerate(snapshot.vertex_coordinates):
        obj.data.vertices[index].co = coordinate.copy()
    for index, selected in enumerate(snapshot.face_selection):
        obj.data.polygons[index].select = selected
    for index, selected in enumerate(snapshot.edge_selection):
        obj.data.edges[index].select = selected
    for index, selected in enumerate(snapshot.vertex_selection):
        obj.data.vertices[index].select = selected
    obj.data.update()


def _selection_snapshot(context: Any) -> _SelectionSnapshot:
    return _SelectionSnapshot(
        selected_objects=tuple(context.selected_objects),
        active_object=context.view_layer.objects.active,
    )


def _snapshot_object_mode(context: Any) -> tuple[_ObjectSnapshot, ...]:
    objects = tuple(context.selected_editable_objects)
    if not objects:
        raise EditRejected("Select at least one editable object")
    snapshots: list[_ObjectSnapshot] = []
    for obj in objects:
        if obj.library is not None or obj.override_library is not None:
            raise EditRejected("Linked and library-override objects are not editable")
        if obj.constraints:
            raise EditRejected(
                "Constrained objects require an explicit evaluated adapter"
            )
        snapshots.append(
            _ObjectSnapshot(obj, _safe_pointer(obj), obj.matrix_world.copy())
        )
    return tuple(snapshots)


def _snapshot_edit_mesh(context: Any, edit_cage: bool) -> _MeshSnapshot:
    objects_in_mode = tuple(getattr(context, "objects_in_mode", ()))
    if len(objects_in_mode) != 1:
        raise EditRejected("Component transforms require exactly one edit-mode mesh")
    obj = context.edit_object
    if obj is None or obj.type != "MESH":
        raise EditRejected("Component transforms require an active edit-mode mesh")
    if obj.library is not None or obj.data.library is not None:
        raise EditRejected("Linked meshes are not editable")
    if obj.data.users != 1:
        raise EditRejected(
            "Multi-user mesh data requires an explicit isolation adapter"
        )
    if obj.data.shape_keys is not None:
        raise EditRejected(
            "Shape-key meshes require an explicit source mapping adapter"
        )
    modifiers = tuple(obj.modifiers)
    if modifiers and not edit_cage:
        raise EditRejected("Modifier stacks require explicit edit-cage mode")
    if modifiers and any(
        not modifier.show_in_editmode or not modifier.show_on_cage
        for modifier in modifiers
    ):
        raise EditRejected("Every modifier must expose a verified edit cage")

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    selected = tuple(
        vertex.index for vertex in bm.verts if vertex.select and not vertex.hide
    )
    if not selected:
        raise EditRejected("Select at least one visible mesh vertex")
    return _MeshSnapshot(
        object_ref=obj,
        object_pointer=_safe_pointer(obj),
        data_pointer=_safe_pointer(obj.data),
        topology=(len(bm.verts), len(bm.edges), len(bm.faces)),
        modifier_signature=_modifier_signature(obj),
        selected_vertices=selected,
        vertex_coordinates=tuple(vertex.co.copy() for vertex in bm.verts),
        vertex_selection=tuple(vertex.select for vertex in bm.verts),
        edge_selection=tuple(edge.select for edge in bm.edges),
        face_selection=tuple(face.select for face in bm.faces),
    )


def _axis_for_constraint(constraint: Constraint) -> Vector | None:
    return {
        Constraint.X: Vector((1.0, 0.0, 0.0)),
        Constraint.Y: Vector((0.0, 1.0, 0.0)),
        Constraint.Z: Vector((0.0, 0.0, 1.0)),
    }.get(constraint)


def _around_pivot(matrix: Matrix, pivot: Vector) -> Matrix:
    return Matrix.Translation(pivot) @ matrix @ Matrix.Translation(-pivot)


def _quat_delta(start: Quaternion, current: Quaternion) -> Quaternion:
    start.normalize()
    current.normalize()
    value = current @ start.inverted()
    value.normalize()
    return value


def _twist_about_axis(rotation: Quaternion, axis: Vector) -> Quaternion:
    """Extract signed quaternion twist while discarding off-axis swing."""

    axis = axis.normalized()
    vector = Vector((rotation.x, rotation.y, rotation.z))
    projected = vector.dot(axis)
    if abs(rotation.w) <= 1.0e-12 and abs(projected) <= 1.0e-12:
        return Quaternion()
    angle = 2.0 * math.atan2(projected, rotation.w)
    angle = (angle + math.pi) % (2.0 * math.pi) - math.pi
    return Quaternion(axis, angle)


class EditTransaction:
    """A captured, previewable edit with exact cancel and one-step commit."""

    def __init__(self, context: Any, request: EditRequest):
        _require_main_thread()
        self.identifier = uuid.uuid4().hex
        self.request = request
        self.capture = CaptureState(request)
        self.mode = str(context.mode)
        self.selection = _selection_snapshot(context)
        self.original_objects: tuple[_ObjectSnapshot, ...] = ()
        self.original_mesh: _MeshSnapshot | None = None
        if self.mode == "OBJECT":
            self.original_objects = _snapshot_object_mode(context)
        elif self.mode == "EDIT_MESH":
            self.original_mesh = _snapshot_edit_mesh(context, request.options.edit_cage)
        else:
            raise EditRejected("Only object and mesh edit modes are supported")
        if request.tool is TransformTool.ROTATE and request.options.constraint in {
            Constraint.XY,
            Constraint.XZ,
            Constraint.YZ,
        }:
            raise EditRejected("Rotation accepts a free or single-axis constraint")

        self.pivot = self._resolve_pivot(request.options.pivot_world)
        self.anchor_pose = request.pose
        self.anchor_objects = self.original_objects
        self.anchor_mesh = self.original_mesh
        # Two-handed manipulation switches a live capture to GRAB via rebase().
        self.tool_override: TransformTool | None = None
        # Optional live snap (world grid step, rotation step in radians) for
        # object-mode captures; the first target snaps, the rest keep formation.
        self.snap: tuple[float, float] | None = None
        # Two-hand axis stretch: world direction to scale along (else uniform).
        self.stretch_axis: tuple[float, float, float] | None = None

    @property
    def phase(self) -> CapturePhase:
        return self.capture.phase

    @property
    def finished(self) -> bool:
        return self.capture.finished

    def _resolve_pivot(self, requested: tuple[float, float, float] | None) -> Vector:
        if requested is not None:
            return Vector(requested)
        if self.mode == "OBJECT":
            total = sum(
                (
                    snapshot.matrix_world.translation
                    for snapshot in self.original_objects
                ),
                Vector(),
            )
            return total / len(self.original_objects)
        assert self.original_mesh is not None
        world = self.original_mesh.object_ref.matrix_world
        total = sum(
            (
                world @ self.original_mesh.vertex_coordinates[index]
                for index in self.original_mesh.selected_vertices
            ),
            Vector(),
        )
        return total / len(self.original_mesh.selected_vertices)

    def _targets_valid(self, context: Any) -> tuple[bool, str]:
        if str(context.mode) != self.mode:
            return False, "Blender mode changed during capture"
        if self.mode == "OBJECT":
            for snapshot in self.original_objects:
                if _safe_pointer(snapshot.object_ref) != snapshot.pointer:
                    return False, "An edited object disappeared"
                if snapshot.object_ref.constraints:
                    return False, "An edited object gained a constraint"
            current = {_safe_pointer(obj) for obj in context.selected_editable_objects}
            expected = {item.pointer for item in self.original_objects}
            if current != expected:
                return False, "Object selection changed during capture"
            return True, ""
        assert self.original_mesh is not None
        mesh = self.original_mesh
        if context.edit_object is not mesh.object_ref:
            return False, "Active edit mesh changed during capture"
        if _safe_pointer(mesh.object_ref) != mesh.object_pointer:
            return False, "Edited object disappeared"
        if _safe_pointer(mesh.object_ref.data) != mesh.data_pointer:
            return False, "Edited mesh datablock changed"
        if _mesh_topology(mesh.object_ref) != mesh.topology:
            return False, "Edited mesh topology changed"
        if mesh.object_ref.data.users != 1:
            return False, "Edited mesh became multi-user"
        if _modifier_signature(mesh.object_ref) != mesh.modifier_signature:
            return False, "Modifier stack changed during capture"
        bm = bmesh.from_edit_mesh(mesh.object_ref.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        selected = tuple(
            vertex.index for vertex in bm.verts if vertex.select and not vertex.hide
        )
        if selected != mesh.selected_vertices:
            return False, "Component selection changed during capture"
        return True, ""

    def _capture_current_anchor(self, context: Any) -> None:
        if self.mode == "OBJECT":
            self.anchor_objects = tuple(
                _ObjectSnapshot(
                    item.object_ref, item.pointer, item.object_ref.matrix_world.copy()
                )
                for item in self.original_objects
            )
        else:
            assert self.original_mesh is not None
            current = _snapshot_edit_mesh(
                context,
                self.request.options.edit_cage,
            )
            self.anchor_mesh = current

    @property
    def tool(self) -> TransformTool:
        return self.tool_override or self.request.tool

    def rebase(self, context: Any, pose: Pose, tool: TransformTool | None = None) -> None:
        """Continue from the current preview with a new hand anchor (and tool)."""
        _require_main_thread()
        if self.finished:
            raise EditRejected("Transaction is already finished")
        self._capture_current_anchor(context)
        self.anchor_pose = pose
        if tool is not None:
            self.tool_override = tool
        self.stretch_axis = None

    def _delta_matrix(self, sample: InputSample) -> Matrix:
        options = self.request.options
        start_position = Vector(self.anchor_pose.position)
        current_position = Vector(sample.pose.position)
        if self.tool is TransformTool.GRAB:
            # The target keeps its offset from the hand: it moves and turns with it.
            anchor = Matrix.LocRotScale(start_position, Quaternion(self.anchor_pose.rotation).normalized(), None)
            current = Matrix.LocRotScale(current_position, Quaternion(sample.pose.rotation).normalized(), None)
            factor = (bounded_uniform_scale(sample.uniform_scale, options)
                      if sample.uniform_scale is not None else 1.0)
            if self.stretch_axis is not None:
                # Stretch along one world axis through the anchor, then carry rigidly.
                axis = Vector(self.stretch_axis).normalized()
                stretch = Matrix([[(1.0 if row == column else 0.0) + (factor - 1.0) * axis[row] * axis[column]
                                   for column in range(3)] for row in range(3)])
                around = (Matrix.Translation(start_position) @ stretch.to_4x4()
                          @ Matrix.Translation(-start_position))
                return current @ anchor.inverted_safe() @ around
            return current @ Matrix.Diagonal((factor, factor, factor, 1.0)) @ anchor.inverted_safe()
        if self.tool is TransformTool.MOVE:
            delta = snapped_translation(
                current_position - start_position,
                options.constraint,
                options.translation_snap,
            )
            return Matrix.Translation(Vector(delta))

        if self.tool is TransformTool.ROTATE:
            rotation = _quat_delta(
                Quaternion(self.anchor_pose.rotation),
                Quaternion(sample.pose.rotation),
            )
            axis = _axis_for_constraint(options.constraint)
            if axis is not None:
                rotation = _twist_about_axis(rotation, axis)
                _source_axis, angle = rotation.to_axis_angle()
                if Vector((rotation.x, rotation.y, rotation.z)).dot(axis) < 0.0:
                    angle = -angle
                angle = quantize(angle, options.rotation_snap_radians)
                rotation = Quaternion(axis, angle)
            elif options.rotation_snap_radians > 0.0:
                source_axis, angle = rotation.to_axis_angle()
                rotation = Quaternion(
                    source_axis,
                    quantize(angle, options.rotation_snap_radians),
                )
            return _around_pivot(rotation.to_matrix().to_4x4(), self.pivot)

        if sample.uniform_scale is not None:
            factor = bounded_uniform_scale(sample.uniform_scale, options)
        else:
            constrained_start = Vector(
                constrained_vector(start_position - self.pivot, options.constraint)
            )
            constrained_current = Vector(
                constrained_vector(current_position - self.pivot, options.constraint)
            )
            factor = uniform_scale(
                constrained_start.length,
                constrained_current.length,
                options,
            )
        axes = scale_axes(options.constraint)
        values = tuple(factor if enabled else 1.0 for enabled in axes)
        return _around_pivot(Matrix.Diagonal((*values, 1.0)), self.pivot)

    def _snapped(self, matrix: Matrix) -> Matrix:
        step, angle = self.snap
        location, rotation, scale = matrix.decompose()
        if step > 0.0:
            location = Vector(round(value / step) * step for value in location)
        if angle > 0.0:
            euler = rotation.to_euler()
            rotation = Euler(tuple(round(value / angle) * angle for value in euler)).to_quaternion()
        return Matrix.LocRotScale(location, rotation, scale)

    def _apply_delta(self, context: Any, delta: Matrix) -> None:
        if self.mode == "OBJECT":
            matrices = [delta @ item.matrix_world for item in self.anchor_objects]
            if self.snap is not None and matrices:
                correction = self._snapped(matrices[0]) @ matrices[0].inverted_safe()
                matrices = [correction @ matrix for matrix in matrices]
            targets = tuple(
                _ObjectSnapshot(item.object_ref, item.pointer, matrix)
                for item, matrix in zip(self.anchor_objects, matrices)
            )
            _apply_object_snapshots(targets)
            return
        assert self.anchor_mesh is not None
        snapshot = self.anchor_mesh
        obj = snapshot.object_ref
        world = obj.matrix_world.copy()
        world_inverse = world.inverted_safe()
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        changed = False
        for index in snapshot.selected_vertices:
            target = world_inverse @ delta @ world @ snapshot.vertex_coordinates[index]
            if (bm.verts[index].co - target).length > _TRANSFORM_EPSILON:
                bm.verts[index].co = target
                changed = True
        if changed:
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    def _is_noop(self) -> bool:
        if self.mode == "OBJECT":
            return all(
                _matrix_close(item.object_ref.matrix_world, item.matrix_world)
                for item in self.original_objects
            )
        assert self.original_mesh is not None
        bm = bmesh.from_edit_mesh(self.original_mesh.object_ref.data)
        bm.verts.ensure_lookup_table()
        return all(
            (bm.verts[index].co - self.original_mesh.vertex_coordinates[index]).length
            <= _TRANSFORM_EPSILON
            for index in self.original_mesh.selected_vertices
        )

    def update(self, context: Any, sample: InputSample) -> EditResult:
        _require_main_thread()
        if _ACTIVE_TRANSACTION is not self:
            return EditResult(Decision.REJECTED, "Transaction does not own capture")
        valid, reason = self._targets_valid(context)
        if not valid:
            self._cancel_invalid(context)
            return EditResult(Decision.CANCEL_REQUIRED, reason)
        decision = self.capture.evaluate(sample)
        if decision.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
            return decision
        if decision.decision is Decision.REBASE:
            self._capture_current_anchor(context)
            self.anchor_pose = sample.pose
            return decision
        if decision.decision is Decision.APPLY:
            self._apply_delta(context, self._delta_matrix(sample))
        return decision

    def tick(self, context: Any, timestamp_seconds: float) -> EditResult:
        _require_main_thread()
        result = self.capture.tick(timestamp_seconds)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        return result

    def _restore_original(self, context: Any) -> None:
        if self.mode == "OBJECT":
            _apply_object_snapshots(self.original_objects)
        else:
            assert self.original_mesh is not None
            _apply_mesh_snapshot(self.original_mesh)
        _restore_object_selection(context, self.selection)

    def _cancel_invalid(self, context: Any) -> None:
        """End safely when a target vanished or changed structure externally."""

        global _ACTIVE_TRANSACTION
        try:
            self._restore_original(context)
        except (EditRejected, ReferenceError, RuntimeError):
            # Structural external edits are authoritative; never overwrite them
            # with an index-based transform snapshot.
            _restore_object_selection(context, self.selection)
        self.capture.finish(CapturePhase.CANCELLED)
        if _ACTIVE_TRANSACTION is self:
            _ACTIVE_TRANSACTION = None

    def cancel(
        self,
        context: Any,
        owner: CaptureOwner | None = None,
    ) -> EditResult:
        global _ACTIVE_TRANSACTION
        _require_main_thread()
        if self.finished:
            return EditResult(Decision.REJECTED, "Transaction is already finished")
        if owner is not None and owner != self.request.owner:
            return EditResult(Decision.REJECTED, "Input is owned by another capture")
        self._restore_original(context)
        self.capture.finish(CapturePhase.CANCELLED)
        if _ACTIVE_TRANSACTION is self:
            _ACTIVE_TRANSACTION = None
        return EditResult(
            Decision.CANCEL_REQUIRED, "Transaction cancelled and restored"
        )

    def _final_payload(self, context: Any) -> _CommitPayload:
        if self.mode == "OBJECT":
            objects = tuple(
                _ObjectSnapshot(
                    item.object_ref, item.pointer, item.object_ref.matrix_world.copy()
                )
                for item in self.original_objects
            )
            return _CommitPayload(self.mode, objects, None, self.selection)
        mesh = _snapshot_edit_mesh(context, self.request.options.edit_cage)
        return _CommitPayload(self.mode, (), mesh, self.selection)

    def commit(self, context: Any, owner: CaptureOwner) -> EditResult:
        global _ACTIVE_TRANSACTION
        _require_main_thread()
        if _ACTIVE_TRANSACTION is not self:
            return EditResult(Decision.REJECTED, "Transaction does not own capture")
        if not undo_available(context):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Enable Global Undo and at least one undo step")
        allowed = self.capture.can_finish(owner)
        if allowed.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
            return allowed
        if allowed.decision is not Decision.APPLY:
            return allowed
        valid, reason = self._targets_valid(context)
        if not valid:
            self._cancel_invalid(context)
            return EditResult(Decision.CANCEL_REQUIRED, reason)

        if self._is_noop():
            self.capture.finish(CapturePhase.COMMITTED)
            _ACTIVE_TRANSACTION = None
            return EditResult(
                Decision.APPLY, "No transform change; undo history unchanged"
            )

        try:
            payload = self._final_payload(context)
        except EditRejected as exc:
            self._cancel_invalid(context)
            return EditResult(Decision.CANCEL_REQUIRED, str(exc))
        self._restore_original(context)
        _COMMIT_PAYLOADS[self.identifier] = payload
        _COMMIT_PAYLOADS.move_to_end(self.identifier)
        while len(_COMMIT_PAYLOADS) > _MAX_COMMIT_PAYLOADS:
            _COMMIT_PAYLOADS.popitem(last=False)
        result = bpy.ops.xrwb.commit_edit(transaction_id=self.identifier)
        if "FINISHED" not in result:
            _COMMIT_PAYLOADS.pop(self.identifier, None)
            return EditResult(Decision.REJECTED, "Blender rejected the undoable commit")
        try:
            undo_result = push_undo(message="XR Workbench Edit")
        except RuntimeError:
            self._restore_original(context)
            _COMMIT_PAYLOADS.pop(self.identifier, None)
            return EditResult(Decision.REJECTED, "No valid Blender undo context")
        if "FINISHED" not in undo_result:
            self._restore_original(context)
            _COMMIT_PAYLOADS.pop(self.identifier, None)
            return EditResult(
                Decision.REJECTED, "Blender rejected the undo history entry"
            )
        self.capture.finish(CapturePhase.COMMITTED)
        _ACTIVE_TRANSACTION = None
        return EditResult(Decision.APPLY, "Committed as one Blender undo step")


class ObjectSelectionTransaction:
    """Preview one frozen ray target and commit it as Blender selection state."""

    def __init__(
        self,
        context: Any,
        request: SelectionRequest,
        target: Any,
        toggle: bool = False,
    ):
        _require_main_thread()
        if str(context.mode) != "OBJECT":
            raise EditRejected(
                "Select currently supports object mode only; mesh component selection is pending"
            )
        self.identifier = uuid.uuid4().hex
        self.request = request
        self.capture = SelectionClickState(request)
        self.target = target
        self.target_pointer = _safe_pointer(target)
        self.scene_pointer = _safe_pointer(context.scene)
        self.view_layer_pointer = _safe_pointer(context.view_layer)
        self.original_selection = _selection_snapshot(context)
        if toggle:
            # Multi-select: the click adds the target, or removes it if selected.
            selected = tuple(self.original_selection.selected_objects)
            if target in selected:
                remaining = tuple(obj for obj in selected if obj is not target)
                active = self.original_selection.active_object
                if active is target:
                    active = remaining[-1] if remaining else None
                self.preview_selection = _SelectionSnapshot(remaining, active)
            else:
                self.preview_selection = _SelectionSnapshot((*selected, target), target)
        else:
            self.preview_selection = _SelectionSnapshot((target,), target)
        valid, reason = self._target_valid(context, require_preview=False)
        if not valid:
            raise EditRejected(reason)

    @property
    def finished(self) -> bool:
        return self.capture.finished

    def _target_valid(
        self,
        context: Any,
        *,
        require_preview: bool = True,
    ) -> tuple[bool, str]:
        if str(context.mode) != "OBJECT":
            return False, "Blender left object mode during selection"
        if (
            _safe_pointer(context.scene) != self.scene_pointer
            or _safe_pointer(context.view_layer) != self.view_layer_pointer
        ):
            return False, "Selection scene or view layer changed"
        if _safe_pointer(self.target) != self.target_pointer:
            return False, "Selection target disappeared"
        if self.target_pointer not in {
            _safe_pointer(obj) for obj in context.view_layer.objects
        }:
            return False, "Selection target is not in the active view layer"
        try:
            if self.target.hide_select or not self.target.visible_get(
                view_layer=context.view_layer
            ):
                return False, "Selection target is hidden or not selectable"
        except (ReferenceError, RuntimeError, TypeError):
            return False, "Selection target visibility is unavailable"
        if require_preview:
            selected = {_safe_pointer(obj) for obj in context.selected_objects}
            if selected != {_safe_pointer(obj) for obj in self.preview_selection.selected_objects}:
                return False, "Object selection changed during captured click"
            if context.view_layer.objects.active is not self.preview_selection.active_object:
                return False, "Active object changed during captured click"
        return True, ""

    def preview(self, context: Any) -> None:
        _require_main_thread()
        _restore_object_selection(context, self.preview_selection)

    def update(self, context: Any, sample: InputSample) -> EditResult:
        _require_main_thread()
        if _ACTIVE_TRANSACTION is not self:
            return EditResult(Decision.REJECTED, "Transaction does not own capture")
        valid, reason = self._target_valid(context)
        if not valid:
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, reason)
        result = self.capture.evaluate(sample)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        return result

    def _is_noop(self) -> bool:
        before, after = self.original_selection, self.preview_selection
        return ({_safe_pointer(obj) for obj in before.selected_objects}
                == {_safe_pointer(obj) for obj in after.selected_objects}
                and before.active_object is after.active_object)

    def cancel(
        self,
        context: Any,
        owner: CaptureOwner | None = None,
    ) -> EditResult:
        global _ACTIVE_TRANSACTION
        _require_main_thread()
        if self.finished:
            return EditResult(Decision.REJECTED, "Selection transaction is finished")
        if owner is not None and owner != self.request.owner:
            return EditResult(
                Decision.REJECTED, "Selection is owned by another capture"
            )
        _restore_object_selection(context, self.original_selection)
        self.capture.finish(CapturePhase.CANCELLED)
        if _ACTIVE_TRANSACTION is self:
            _ACTIVE_TRANSACTION = None
        return EditResult(Decision.CANCEL_REQUIRED, "Object selection restored")

    def commit(self, context: Any, owner: CaptureOwner) -> EditResult:
        global _ACTIVE_TRANSACTION
        _require_main_thread()
        if _ACTIVE_TRANSACTION is not self:
            return EditResult(Decision.REJECTED, "Transaction does not own capture")
        if not undo_available(context):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Enable Global Undo and at least one undo step")
        allowed = self.capture.can_finish(owner)
        if allowed.decision is not Decision.APPLY:
            return allowed
        valid, reason = self._target_valid(context)
        if not valid:
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, reason)
        if self._is_noop():
            self.capture.finish(CapturePhase.COMMITTED)
            _ACTIVE_TRANSACTION = None
            return EditResult(
                Decision.APPLY,
                "Selection unchanged; undo history unchanged",
            )

        _restore_object_selection(context, self.original_selection)
        _COMMIT_PAYLOADS[self.identifier] = _SelectionCommitPayload(
            self.preview_selection
        )
        _COMMIT_PAYLOADS.move_to_end(self.identifier)
        while len(_COMMIT_PAYLOADS) > _MAX_COMMIT_PAYLOADS:
            _COMMIT_PAYLOADS.popitem(last=False)
        result = bpy.ops.xrwb.commit_edit(transaction_id=self.identifier)
        if "FINISHED" not in result:
            _COMMIT_PAYLOADS.pop(self.identifier, None)
            return EditResult(Decision.REJECTED, "Blender rejected object selection")
        try:
            undo_result = push_undo(message="XR Workbench Select Object")
        except RuntimeError:
            _restore_object_selection(context, self.original_selection)
            _COMMIT_PAYLOADS.pop(self.identifier, None)
            return EditResult(Decision.REJECTED, "No valid Blender undo context")
        if "FINISHED" not in undo_result:
            _restore_object_selection(context, self.original_selection)
            _COMMIT_PAYLOADS.pop(self.identifier, None)
            return EditResult(Decision.REJECTED, "Blender rejected selection history")
        self.capture.finish(CapturePhase.COMMITTED)
        _ACTIVE_TRANSACTION = None
        return EditResult(Decision.APPLY, "Object selection committed as one undo step")


class XRWB_OT_commit_edit(bpy.types.Operator):
    """Internal operator that makes a prepared transaction one undo step."""

    bl_idname = "xrwb.commit_edit"
    bl_label = "XR Workbench Edit"
    bl_options = {"INTERNAL", "REGISTER"}

    transaction_id: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context: Any) -> set[str]:
        payload = _COMMIT_PAYLOADS.get(self.transaction_id)
        if payload is None:
            self.report({"ERROR"}, "XR edit payload expired")
            return {"CANCELLED"}
        try:
            payload.apply(context)
        except (EditRejected, ReferenceError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (XRWB_OT_commit_edit,)


def begin(context: Any, request: EditRequest) -> EditTransaction:
    """Capture the current editable targets or raise :class:`EditRejected`."""

    global _ACTIVE_TRANSACTION
    _require_main_thread()
    if not undo_available(context):
        raise EditRejected("Enable Global Undo and at least one undo step")
    if _ACTIVE_TRANSACTION is not None and not _ACTIVE_TRANSACTION.finished:
        raise EditRejected("Another edit transaction already owns capture")
    transaction = EditTransaction(context, request)
    _ACTIVE_TRANSACTION = transaction
    return transaction


def begin_object_selection(
    context: Any,
    request: SelectionRequest,
    target: Any,
    toggle: bool = False,
) -> ObjectSelectionTransaction:
    """Freeze and preview one evaluated-ray object target."""

    global _ACTIVE_TRANSACTION
    _require_main_thread()
    if not undo_available(context):
        raise EditRejected("Enable Global Undo and at least one undo step")
    if _ACTIVE_TRANSACTION is not None and not _ACTIVE_TRANSACTION.finished:
        raise EditRejected("Another edit transaction already owns capture")
    transaction = ObjectSelectionTransaction(context, request, target, toggle)
    _ACTIVE_TRANSACTION = transaction
    try:
        transaction.preview(context)
    except Exception:
        _restore_object_selection(context, transaction.original_selection)
        _ACTIVE_TRANSACTION = None
        raise
    return transaction


def active_transaction() -> EditTransaction | ObjectSelectionTransaction | None:
    return _ACTIVE_TRANSACTION


def push_undo(message):
    """Every XR undo step goes through Live Cage so no step records hidden modifiers."""
    from . import live_cage
    return live_cage.push_undo(message)


def cancel_active(context: Any) -> EditResult:
    if _ACTIVE_TRANSACTION is None:
        return EditResult(Decision.REJECTED, "No active edit transaction")
    return _ACTIVE_TRANSACTION.cancel(context)


def clear_commit_payloads() -> None:
    """Drop redo payload references, intended for explicit file/session teardown."""

    _COMMIT_PAYLOADS.clear()
