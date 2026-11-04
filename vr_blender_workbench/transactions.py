"""Undo-friendly VR transform and experimental sculpt transactions."""

from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree

from .model import quantize, uniform_scale_from_distances


_ACTIVE_TRANSACTIONS: list[object] = []


def _register_active(transaction) -> bool:
    if _ACTIVE_TRANSACTIONS:
        return False
    _ACTIVE_TRANSACTIONS.append(transaction)
    return True


def _unregister_active(transaction):
    try:
        _ACTIVE_TRANSACTIONS.remove(transaction)
    except ValueError:
        pass


def cancel_all_transactions():
    for transaction in list(_ACTIVE_TRANSACTIONS):
        try:
            transaction.cancel()
        except Exception as exc:
            print(f"VR Workbench: transaction cleanup failed: {exc}")
    _ACTIVE_TRANSACTIONS.clear()


def has_active_transactions() -> bool:
    return bool(_ACTIVE_TRANSACTIONS)


class TransformTransaction:
    """Preview a controller-driven transform from immutable source data."""

    def __init__(self, context, tool: str, location: Vector, rotation: Quaternion):
        self.context = context
        self.tool = tool
        self.start_location = location.copy()
        self.start_rotation = rotation.copy()
        self.object_matrices: list[tuple[bpy.types.Object, Matrix]] = []
        self.edit_object = None
        self.edit_coords: dict[int, Vector] = {}
        self.pivot = Vector((0.0, 0.0, 0.0))
        self.error = ""
        self.finished = False

        if has_active_transactions():
            self.error = "Finish or cancel the active VR gesture first"
            return

        if context.mode == "OBJECT":
            objects = [obj for obj in context.selected_objects if obj.library is None]
            if not objects:
                self.error = "Select at least one editable object"
                return
            self.object_matrices = [(obj, obj.matrix_world.copy()) for obj in objects]
            self.pivot = sum(
                (matrix.translation for _obj, matrix in self.object_matrices), Vector()
            ) / len(objects)
        elif context.mode == "EDIT_MESH" and context.edit_object is not None:
            self.edit_object = context.edit_object
            bm = bmesh.from_edit_mesh(self.edit_object.data)
            bm.verts.ensure_lookup_table()
            self.edit_coords = {
                vert.index: vert.co.copy()
                for vert in bm.verts
                if vert.select and not vert.hide
            }
            if not self.edit_coords:
                self.error = "Select mesh vertices before transforming"
                return
            world_points = [
                self.edit_object.matrix_world @ co for co in self.edit_coords.values()
            ]
            self.pivot = sum(world_points, Vector()) / len(world_points)
        else:
            self.error = "VR transforms currently support Object and Mesh Edit modes"
            return

        self.start_distance = max((self.start_location - self.pivot).length, 1.0e-6)
        if not _register_active(self):
            self.error = "Finish or cancel the active VR gesture first"

    @property
    def valid(self) -> bool:
        return not self.error and not self.finished

    def _delta_matrix(self, location: Vector, rotation: Quaternion, settings) -> Matrix:
        if self.tool == "MOVE":
            delta = location - self.start_location
            if settings.snap_translation:
                delta = Vector(
                    tuple(quantize(value, settings.translation_step) for value in delta)
                )
            return Matrix.Translation(delta)

        if self.tool == "ROTATE":
            delta_rotation = rotation @ self.start_rotation.inverted()
            delta_rotation.normalize()
            if settings.snap_rotation:
                axis, angle = delta_rotation.to_axis_angle()
                # Blender stores ANGLE properties in radians even though the UI
                # displays degrees.
                angle = quantize(angle, settings.rotation_step)
                delta_rotation = Quaternion(axis, angle)
            return (
                Matrix.Translation(self.pivot)
                @ delta_rotation.to_matrix().to_4x4()
                @ Matrix.Translation(-self.pivot)
            )

        current_distance = max((location - self.pivot).length, 1.0e-6)
        scale = uniform_scale_from_distances(self.start_distance, current_distance)
        if settings.snap_scale:
            scale = 1.0 + quantize(scale - 1.0, settings.scale_step)
            scale = max(0.01, scale)
        return (
            Matrix.Translation(self.pivot)
            @ Matrix.Diagonal((scale, scale, scale, 1.0))
            @ Matrix.Translation(-self.pivot)
        )

    def apply(self, location: Vector, rotation: Quaternion, settings):
        if not self.valid:
            return
        delta_matrix = self._delta_matrix(location, rotation, settings)
        if self.object_matrices:
            for obj, original in self.object_matrices:
                if obj.name in bpy.data.objects:
                    obj.matrix_world = delta_matrix @ original
            return

        obj = self.edit_object
        if obj is None or obj.name not in bpy.data.objects:
            self.cancel()
            return
        if obj.mode != "EDIT":
            self.cancel()
            return
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        world_to_local = obj.matrix_world.inverted_safe()
        for index, original_co in self.edit_coords.items():
            if index < len(bm.verts):
                world = obj.matrix_world @ original_co
                bm.verts[index].co = world_to_local @ (delta_matrix @ world)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    def cancel(self):
        if self.finished:
            return
        try:
            if self.object_matrices:
                for obj, original in self.object_matrices:
                    if obj.name in bpy.data.objects:
                        obj.matrix_world = original
            elif (
                self.edit_object is not None
                and self.edit_object.name in bpy.data.objects
            ):
                if self.edit_object.mode == "EDIT":
                    bm = bmesh.from_edit_mesh(self.edit_object.data)
                    bm.verts.ensure_lookup_table()
                    for index, original_co in self.edit_coords.items():
                        if index < len(bm.verts):
                            bm.verts[index].co = original_co
                    bmesh.update_edit_mesh(
                        self.edit_object.data,
                        loop_triangles=False,
                        destructive=False,
                    )
                else:
                    for index, original_co in self.edit_coords.items():
                        if index < len(self.edit_object.data.vertices):
                            self.edit_object.data.vertices[index].co = original_co
                    self.edit_object.data.update()
        finally:
            self.finished = True
            _unregister_active(self)

    def commit(self):
        if self.finished:
            return
        self.finished = True
        _unregister_active(self)


class SculptTransaction:
    """Experimental direct mesh deformation driven by controller rays."""

    def __init__(self, context, tool: str):
        self.context = context
        self.tool = tool
        self.obj = context.active_object
        self.original_coords: dict[int, Vector] = {}
        self.finished = False
        self.error = ""
        self.kdtree = None
        self.neighbors: dict[int, set[int]] = {}

        if has_active_transactions():
            self.error = "Finish or cancel the active VR gesture first"
            return

        if self.obj is None or self.obj.type != "MESH":
            self.error = "Select an editable mesh for VR sculpting"
            return
        if self.obj.library is not None:
            self.error = "Linked meshes cannot be sculpted"
            return
        mesh = self.obj.data
        self.kdtree = KDTree(len(mesh.vertices))
        for vertex in mesh.vertices:
            self.kdtree.insert(vertex.co, vertex.index)
        self.kdtree.balance()
        if tool == "SCULPT_SMOOTH":
            self.neighbors = {vertex.index: set() for vertex in mesh.vertices}
            for edge in mesh.edges:
                first, second = edge.vertices
                self.neighbors[first].add(second)
                self.neighbors[second].add(first)
        if not _register_active(self):
            self.error = "Finish or cancel the active VR gesture first"

    @property
    def valid(self) -> bool:
        return not self.error and not self.finished

    def _local_hit(self, origin: Vector, direction: Vector, max_distance: float):
        inverse = self.obj.matrix_world.inverted_safe()
        local_origin = inverse @ origin
        local_direction = (inverse.to_3x3() @ direction).normalized()
        hit, location, normal, _face_index = self.obj.ray_cast(
            local_origin, local_direction, distance=max_distance
        )
        return (location, normal) if hit else None

    def apply(self, origin: Vector, direction: Vector, settings, pressure: float = 1.0):
        if not self.valid:
            return
        hit = self._local_hit(origin, direction, settings.ray_distance)
        if hit is None:
            return
        hit_location, hit_normal = hit
        scales = [abs(value) for value in self.obj.scale if abs(value) > 1.0e-6]
        radius_local = settings.brush_radius / (
            sum(scales) / len(scales) if scales else 1.0
        )
        affected = list(self.kdtree.find_range(hit_location, radius_local))
        if not affected:
            return

        mesh = self.obj.data
        before = {
            index: mesh.vertices[index].co.copy() for _co, index, _distance in affected
        }
        strength = settings.brush_strength * max(0.05, min(1.0, pressure)) * 0.08
        sign = -1.0 if self.tool == "SCULPT_DEFLATE" else 1.0
        for _co, index, distance in affected:
            vertex = mesh.vertices[index]
            self.original_coords.setdefault(index, vertex.co.copy())
            falloff = max(0.0, 1.0 - distance / max(radius_local, 1.0e-6)) ** 2
            if self.tool == "SCULPT_SMOOTH":
                adjacent = self.neighbors.get(index, ())
                if adjacent:
                    average = sum(
                        (
                            before.get(other, mesh.vertices[other].co)
                            for other in adjacent
                        ),
                        Vector(),
                    ) / len(adjacent)
                    vertex.co = vertex.co.lerp(
                        average, min(0.5, strength * falloff * 4.0)
                    )
            elif self.tool == "SCULPT_DRAW":
                vertex.co += hit_normal.normalized() * strength * falloff * radius_local
            else:
                normal = (
                    vertex.normal.normalized()
                    if vertex.normal.length_squared
                    else hit_normal.normalized()
                )
                vertex.co += normal * sign * strength * falloff * radius_local
        mesh.update()

    def cancel(self):
        if self.finished:
            return
        try:
            if self.obj is not None and self.obj.name in bpy.data.objects:
                for index, co in self.original_coords.items():
                    if index < len(self.obj.data.vertices):
                        self.obj.data.vertices[index].co = co
                self.obj.data.update()
        finally:
            self.finished = True
            _unregister_active(self)

    def commit(self):
        if self.finished:
            return
        if self.obj is not None and self.obj.name in bpy.data.objects:
            self.obj.data.update()
        self.finished = True
        _unregister_active(self)
