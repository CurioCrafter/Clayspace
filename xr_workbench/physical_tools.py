"""Hold-drag-release physical tools: clay sculpting, material spray, time scrub.

Each stroke is one capture owned by one controller. Cancelling (left B / Y,
tracking loss or a changed target) restores the exact original data; release
commits one undo step. Brushes are sized in physical metres, multiplied by the
viewer's world scale, so shrinking yourself makes every tool finer.
Only our code and Blender's native mesh/material/animation data are used.
"""
from __future__ import annotations

import math

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import editing, viewer_scale
from .interaction import (
    CaptureOwner, CapturePhase, CaptureState, Decision, EditRequest, EditResult,
    InputProvenance, Pose, TransformOptions, TransformTool,
)

SCULPT_TOOLS = {"sculpt_grab", "sculpt_clay", "sculpt_smooth", "sculpt_flatten"}
TOOLS = SCULPT_TOOLS | {"spray_material", "scrub_time", "place_object", "measure", "eyedropper", "uv_island",
                        "draw_curve", "paint_vertex", "place_prefab", "sdf_blob"}
RESTING_GAP_M = 0.0005
# key: (label, icon, physical size in metres (x, y, z))
PLACE_SHAPES = {
    "CUBE": ("Cube", "MESH_CUBE", (0.2, 0.2, 0.2)),
    "SPHERE": ("Sphere", "MESH_UVSPHERE", (0.2, 0.2, 0.2)),
    "CYLINDER": ("Cylinder", "MESH_CYLINDER", (0.2, 0.2, 0.2)),
    "CONE": ("Cone", "MESH_CONE", (0.2, 0.2, 0.2)),
    "PLANE": ("Plane", "MESH_PLANE", (0.3, 0.3, 0.0)),
    "TORUS": ("Torus", "MESH_TORUS", (0.24, 0.24, 0.07)),
}
SWATCH_KEY = "xrwb_swatch"
MIN_SCULPT_VERTICES = 50
MAX_SCULPT_VERTICES = 2_000_000


def _falloff(distance, radius):
    """Smooth brush falloff: 1 at the centre, 0 at the radius."""
    t = np.clip(distance / radius, 0.0, 1.0)
    return (1.0 - t * t) ** 2


def _matrix(obj):
    return np.array(obj.matrix_world, dtype=np.float64)


def _pose_ray(pose):
    rotation = Quaternion(pose.rotation).normalized()
    return Vector(pose.position), rotation @ Vector((0.0, 0.0, -1.0)), rotation


def swatch_of(obj):
    """The material block for a block or its floating label, else None."""
    if obj is not None and "xrwb_swatch_label" in obj.keys():
        obj = obj.parent
    return obj if obj is not None and SWATCH_KEY in obj.keys() else None


def is_swatch(obj) -> bool:
    return swatch_of(obj) is not None


def require_mesh_target(context, obj, purpose):
    if context.mode != "OBJECT" or obj is None or obj.type != "MESH":
        raise editing.EditRejected(f"{purpose} needs a mesh in Object mode")
    mesh = obj.data
    if obj.hide_select or not obj.visible_get(view_layer=context.view_layer):
        raise editing.EditRejected(f"{purpose} target must be visible and selectable")
    if (not obj.is_editable or not mesh.is_editable or obj.library or mesh.library
            or obj.override_library or mesh.override_library):
        raise editing.EditRejected("Linked, overridden or read-only meshes are unsupported")
    if mesh.users != 1:
        raise editing.EditRejected(f"{purpose} needs single-user mesh data")
    if (not all(math.isfinite(value) for row in obj.matrix_world for value in row)
            or abs(obj.matrix_world.determinant()) < 1.0e-12):
        raise editing.EditRejected("Object transform is singular")


class _Stroke:
    """Shared capture, validation, cancel and commit rules."""

    label = "Stroke"
    needs_undo = True

    def __init__(self, context, pose, owner, generation, units):
        self.request = EditRequest(owner, TransformTool.MOVE, generation, pose, TransformOptions())
        self.capture = CaptureState(self.request)
        self.scene_pointer = editing._safe_pointer(context.scene)
        self.units = units
        self.anchor = Vector(pose.position)
        self.last = Vector(pose.position)
        self.changed = False

    @property
    def finished(self):
        return self.capture.finished

    def _valid(self, context):
        return (editing._safe_pointer(context.scene) == self.scene_pointer
                and (not self.needs_undo or context.mode == "OBJECT"))

    def _restore(self, context):
        raise NotImplementedError

    def _apply(self, context, pose):
        raise NotImplementedError

    def _finish(self, phase):
        self.capture.finish(phase)
        if editing._ACTIVE_TRANSACTION is self:
            editing._ACTIVE_TRANSACTION = None

    def cancel(self, context, owner=None):
        editing._require_main_thread()
        if self.finished:
            return EditResult(Decision.REJECTED, f"{self.label} is already finished")
        if owner is not None and owner != self.request.owner:
            return EditResult(Decision.REJECTED, "Input belongs to another capture")
        try:
            self._restore(context)
            message = f"{self.label} cancelled; original restored"
        except (ReferenceError, RuntimeError, AttributeError, ValueError) as exc:
            message = f"{self.label} cancelled; target changed externally ({exc})"
        self._finish(CapturePhase.CANCELLED)
        return EditResult(Decision.CANCEL_REQUIRED, message)

    def update(self, context, sample):
        editing._require_main_thread()
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, f"{self.label} does not own capture")
        if not self._valid(context) or (self.needs_undo and not editing.undo_available(context)):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, f"{self.label} target or undo state changed")
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
            # Resume from the current hand position instead of jumping.
            self.anchor += position - self.last
            self.last = position
            return result
        message = self._apply(context, sample.pose)
        self.last = position
        return EditResult(Decision.APPLY, message)

    def tick(self, context, timestamp_seconds):
        result = self.capture.tick(timestamp_seconds)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        return result

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, f"{self.label} does not own capture")
        result = self.capture.can_finish(owner or self.request.owner)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        if result.decision is not Decision.APPLY:
            return result
        if not self._valid(context):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, f"{self.label} target changed")
        if not self.changed:
            self._restore(context)  # Drops anything prepared but never used, e.g. a spray slot.
            self._finish(CapturePhase.COMMITTED)
            return EditResult(Decision.APPLY, "No change")
        if self.needs_undo:
            try:
                if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in bpy.ops.ed.undo_push(
                        message=f"XR {self.label}"):
                    raise RuntimeError("Blender rejected the undo entry")
            except RuntimeError as exc:
                self._restore(context)
                self._finish(CapturePhase.CANCELLED)
                return EditResult(Decision.REJECTED, f"{self.label} restored: {exc}")
        self._finish(CapturePhase.COMMITTED)
        return EditResult(Decision.APPLY, self.commit_message())

    def commit_message(self):
        return f"{self.label} committed as one undo step"


class SculptStroke(_Stroke):
    """Grab pulls a fixed region; Clay builds up/digs and Smooth relaxes along the path."""

    def __init__(self, context, obj, hit, normal, pose, tool, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        self.label = {"sculpt_grab": "Sculpt Grab", "sculpt_clay": "Sculpt Clay",
                      "sculpt_smooth": "Sculpt Smooth", "sculpt_flatten": "Sculpt Flatten"}[tool]
        self.tool = tool
        self.obj = obj
        self.mesh = obj.data
        count = len(self.mesh.vertices)
        if count < MIN_SCULPT_VERTICES:
            raise editing.EditRejected("Too few vertices to sculpt: add a Clay Ball or use Remesh for Detail")
        if count > MAX_SCULPT_VERTICES:
            raise editing.EditRejected("Mesh is too dense for controller sculpting")
        if self.mesh.shape_keys:
            raise editing.EditRejected("Shape keys are not supported by clay tools yet")
        flat = np.empty(count * 3, dtype=np.float32)
        self.mesh.vertices.foreach_get("co", flat)
        self.original = flat
        self.world = _matrix(obj)
        self.matrix_pointer = tuple(tuple(row) for row in obj.matrix_world)
        self.inverse = np.linalg.inv(self.world)
        local = flat.reshape(count, 3).astype(np.float64)
        self.start_world = local @ self.world[:3, :3].T + self.world[:3, 3]
        self.current = self.start_world.copy()
        self.radius = max(1.0e-6, float(settings.sculpt_radius_m) * units)
        self.strength = float(settings.sculpt_strength)
        self.sign = -1.0 if settings.sculpt_invert else 1.0
        self.hit = np.array(hit, dtype=np.float64)
        self.normal = np.array(normal, dtype=np.float64)
        # Trigger pressure (0.15..1) from the controller; 1 without analogue data.
        self.pressure = 1.0
        # World-space mirror across the object's local X = 0 plane.
        self.mirror = None
        if getattr(settings, "sculpt_mirror_x", False):
            self.mirror = self.world @ np.diag((-1.0, 1.0, 1.0, 1.0)) @ self.inverse
        edges = np.empty(len(self.mesh.edges) * 2, dtype=np.int64)
        self.mesh.edges.foreach_get("vertices", edges)
        self.edges = edges.reshape(-1, 2)
        if tool == "sculpt_grab":
            self.weights = _falloff(np.linalg.norm(self.start_world - self.hit, axis=1), self.radius)
            self.mirror_weights = (
                _falloff(np.linalg.norm(self.start_world - self._mirror_point(self.hit), axis=1), self.radius)
                if self.mirror is not None else None)
            if not np.any(self.weights > 0.0):
                raise editing.EditRejected("No vertices under the brush; shrink yourself or enlarge the brush")

    def _mirror_point(self, point):
        return (self.mirror @ np.append(point, 1.0))[:3]

    def _mirror_vector(self, vector):
        return self.mirror[:3, :3] @ vector

    def _valid(self, context):
        try:
            return (super()._valid(context) and self.obj.name in context.view_layer.objects
                    and self.obj.data == self.mesh and len(self.mesh.vertices) * 3 == len(self.original)
                    and tuple(tuple(row) for row in self.obj.matrix_world) == self.matrix_pointer)
        except ReferenceError:
            return False

    def _write(self):
        local = (self.current - self.world[:3, 3]) @ self.inverse[:3, :3].T
        self.mesh.vertices.foreach_set("co", local.astype(np.float32).ravel())
        self.mesh.update()

    def _restore(self, context):
        self.mesh.vertices.foreach_set("co", self.original)
        self.mesh.update()

    def _surface_hit(self, context, pose):
        origin, direction, _rotation = _pose_ray(pose)
        context.view_layer.update()
        inverse = self.obj.matrix_world.inverted()
        hit, location, normal, _index = self.obj.ray_cast(
            inverse @ origin, (inverse.to_3x3() @ direction).normalized(),
            depsgraph=context.evaluated_depsgraph_get())
        if not hit:
            return None
        world = self.obj.matrix_world
        return (np.array(world @ location), np.array((inverse.transposed().to_3x3() @ normal).normalized()))

    def _apply(self, context, pose):
        position = Vector(pose.position)
        if self.tool == "sculpt_grab":
            delta = np.array(position - self.anchor)
            self.current = self.start_world + self.weights[:, None] * delta[None, :]
            if self.mirror_weights is not None:
                self.current += self.mirror_weights[:, None] * self._mirror_vector(delta)[None, :]
            self._write()
            self.changed = bool(np.any(np.abs(delta) > 1.0e-9))
            return f"Grab {np.linalg.norm(delta) / self.units * 100:.1f} cm"
        step = (position - self.last).length
        if step <= 1.0e-9:
            return "Move the hand to sculpt"
        surface = self._surface_hit(context, pose)
        if surface is None:
            return "Brush is off the surface"
        centre, normal = surface
        brushes = [(centre, normal)]
        if self.mirror is not None:
            brushes.append((self._mirror_point(centre), self._mirror_vector(normal)))
        touched = np.zeros(len(self.current), dtype=bool)
        for centre, normal in brushes:
            weights = _falloff(np.linalg.norm(self.current - centre, axis=1), self.radius)
            mask = weights > 0.0
            if not np.any(mask):
                continue
            touched |= mask
            if self.tool == "sculpt_clay":
                # Half the hand travel is added as height at full strength and pressure.
                amount = step * 0.5 * self.strength * self.pressure * self.sign
                self.current[mask] += weights[mask, None] * amount * normal[None, :]
            elif self.tool == "sculpt_flatten":
                # Pull bumps and dents toward the average plane under the brush
                # (like Blender's Flatten): the peak moves, not just the sides.
                factor = min(1.0, 2.0 * step / self.radius) * self.strength * self.pressure
                plane = np.average(self.current[mask], axis=0, weights=weights[mask])
                height = (self.current[mask] - plane) @ normal
                self.current[mask] -= (weights[mask] * factor * height)[:, None] * normal[None, :]
            else:
                total = np.zeros_like(self.current)
                counts = np.zeros(len(self.current))
                a, b = self.edges[:, 0], self.edges[:, 1]
                np.add.at(total, a, self.current[b])
                np.add.at(total, b, self.current[a])
                np.add.at(counts, a, 1.0)
                np.add.at(counts, b, 1.0)
                safe = counts > 0
                average = self.current.copy()
                average[safe] = total[safe] / counts[safe, None]
                factor = min(1.0, 2.0 * step / self.radius) * self.strength * self.pressure
                self.current[mask] += (weights[mask, None] * factor) * (average[mask] - self.current[mask])
        mask = touched
        if not np.any(mask):
            return "No vertices under the brush"
        self._write()
        self.changed = True
        return f"{self.label}: {int(mask.sum())} vertices"


class SprayStroke(_Stroke):
    """Paint the chosen material onto faces under the spray cone."""

    label = "Spray Material"

    def __init__(self, context, pose, material, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        self.material = material
        self.radius = max(1.0e-6, float(settings.spray_radius_m) * units)
        self.pressure = 1.0  # A harder squeeze widens the spray (50-100% of Spray Size).
        self.targets = {}

    def _record(self, context, obj):
        key = editing._safe_pointer(obj)
        if key in self.targets:
            return self.targets[key]
        try:
            require_mesh_target(context, obj, "Spray")
        except editing.EditRejected:
            self.targets[key] = None
            return None
        mesh = obj.data
        if any(slot.link != "DATA" for slot in obj.material_slots):
            self.targets[key] = None
            return None
        indices = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", indices)
        centres = np.empty(len(mesh.polygons) * 3, dtype=np.float32)
        mesh.polygons.foreach_get("center", centres)
        world = _matrix(obj)
        centres = centres.reshape(-1, 3).astype(np.float64) @ world[:3, :3].T + world[:3, 3]
        record = {"obj": obj, "mesh": mesh, "original": indices, "current": indices.copy(),
                  "centres": centres, "slots": len(mesh.materials), "slot": None,
                  "exact_faces": not any(m.show_viewport for m in obj.modifiers)}
        self.targets[key] = record
        return record

    def _slot(self, record):
        if record["slot"] is None:
            mesh = record["mesh"]
            names = [m.name if m else "" for m in mesh.materials]
            if self.material.name in names:
                record["slot"] = names.index(self.material.name)
            else:
                if not names:
                    # Keep the unpainted faces as they looked: an empty base slot.
                    mesh.materials.append(None)
                mesh.materials.append(self.material)
                record["slot"] = len(mesh.materials) - 1
        return record["slot"]

    def _valid(self, context):
        if not super()._valid(context):
            return False
        try:
            for record in self.targets.values():
                if record and len(record["mesh"].polygons) != len(record["original"]):
                    return False
            return self.material.name in bpy.data.materials
        except ReferenceError:
            return False

    def _restore(self, context):
        for record in self.targets.values():
            if not record:
                continue
            mesh = record["mesh"]
            mesh.polygons.foreach_set("material_index", record["original"])
            while len(mesh.materials) > record["slots"]:
                mesh.materials.pop()
            mesh.update()

    def _apply(self, context, pose):
        origin, direction, _rotation = _pose_ray(pose)
        context.view_layer.update()
        hit, location, _normal, index, obj, _matrix_hit = context.scene.ray_cast(
            context.evaluated_depsgraph_get(), origin, direction)
        if not hit or obj is None:
            return "Aim the spray at a mesh"
        obj = obj.original
        if is_swatch(obj):
            return "Spray passes over material blocks"
        record = self._record(context, obj)
        if record is None:
            return f"{obj.name} cannot be sprayed (linked, shared or object-linked materials)"
        distance = np.linalg.norm(record["centres"] - np.array(location), axis=1)
        faces = distance <= self.radius * (0.5 + 0.5 * self.pressure)
        if record["exact_faces"] and 0 <= index < len(faces):
            faces[index] = True
        slot = self._slot(record)
        if not np.any(record["current"][faces] != slot):
            return f"Spraying {self.material.name}"
        record["current"][faces] = slot
        record["mesh"].polygons.foreach_set("material_index", record["current"])
        record["mesh"].update()
        self.changed = True
        return f"Sprayed {self.material.name} on {obj.name}"

    def commit_message(self):
        count = sum(1 for record in self.targets.values() if record)
        return f"Sprayed {self.material.name} on {count} object(s) as one undo step"


class ScrubStroke(_Stroke):
    """Drag sideways to move through time; frames per metre are physical."""

    label = "Scrub Time"
    needs_undo = False

    def __init__(self, context, pose, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        self.scene = context.scene
        self.start_frame = context.scene.frame_current
        self.axis = Quaternion(pose.rotation).normalized() @ Vector((1.0, 0.0, 0.0))
        self.frames_per_metre = float(settings.scrub_frames_per_m)

    def _restore(self, context):
        self.scene.frame_set(self.start_frame)

    def _apply(self, context, pose):
        metres = (Vector(pose.position) - self.anchor).dot(self.axis) / self.units
        frame = max(0, self.start_frame + int(round(metres * self.frames_per_metre)))
        if frame != self.scene.frame_current:
            self.scene.frame_set(frame)
        self.changed = frame != self.start_frame
        return f"Frame {frame}"

    def commit_message(self):
        return f"Frame {self.scene.frame_current}"


def build_shape_mesh(kind, dimensions, name):
    """Native mesh for a place shape, centred on its origin, at real size."""
    import bmesh

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        if kind == "SPHERE":
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.5)
        elif kind in {"CYLINDER", "CONE"}:
            bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32, radius1=0.5,
                                  radius2=0.5 if kind == "CYLINDER" else 0.0, depth=1.0)
        elif kind == "PLANE":
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
        elif kind == "TORUS":
            major, minor, rings, sides = 0.5 - 0.5 * dimensions[2] / dimensions[0], 0.5 * dimensions[2] / dimensions[0], 32, 12
            verts = [[bm.verts.new(((major + minor * math.cos(b)) * math.cos(a),
                                    (major + minor * math.cos(b)) * math.sin(a), minor * math.sin(b)))
                      for b in (2 * math.pi * j / sides for j in range(sides))]
                     for a in (2 * math.pi * i / rings for i in range(rings))]
            for i in range(rings):
                for j in range(sides):
                    bm.faces.new((verts[i][j], verts[(i + 1) % rings][j],
                                  verts[(i + 1) % rings][(j + 1) % sides], verts[i][(j + 1) % sides]))
            dimensions = (dimensions[0], dimensions[1], dimensions[0])  # Proportions already built in.
        else:
            bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector((dimensions[0], dimensions[1], dimensions[2] or 1.0)), verts=bm.verts)
        if kind in {"SPHERE", "TORUS"}:
            for face in bm.faces:
                face.smooth = True
        bm.normal_update()
        bm.to_mesh(mesh)
    finally:
        bm.free()
    return mesh


def placement(context, origin, direction, dimensions, exclude=None):
    """Where a new shape lands: resting on the surface the ray hits, else 70 cm ahead.

    Returns (location, yaw rotation, hit object or None). The shape turns to face
    the viewer around world Z so it stays upright.
    """
    units = viewer_scale.world_units_per_metre(context)
    origin, direction = Vector(origin), Vector(direction).normalized()
    depsgraph = context.evaluated_depsgraph_get()
    start, hit_obj, location, normal = origin, None, None, None
    for _ in range(4):  # Skip the shape's own preview if the ray meets it.
        hit, location, normal, _index, obj, _matrix = context.scene.ray_cast(
            depsgraph, start, direction, distance=50.0 * units)
        if not hit:
            location = None
            break
        obj = obj.original if obj is not None else None
        skip = exclude if isinstance(exclude, (set, frozenset)) else {exclude}
        if obj not in skip:
            hit_obj = obj
            break
        start = location + direction * 1.0e-4 * units
        location = None
    yaw = Quaternion((0.0, 0.0, 1.0), math.atan2(-direction.x, direction.y))
    if location is None:
        return origin + direction * 0.7 * units, yaw, None
    # Rest 0.5 mm clear of the surface: exactly coplanar faces would let the next
    # ray skip the shape's own bottom and pass through a thin floor.
    half = Vector(dimensions) * 0.5 + Vector((1.0, 1.0, 1.0)) * RESTING_GAP_M * units
    if normal.z > 0.5:
        return location + Vector((0.0, 0.0, half.z)), yaw, hit_obj
    return location + Vector(normal).normalized() * max(half.x, half.y, half.z), yaw, hit_obj


class PlaceStroke(_Stroke):
    """Trigger drops the chosen shape where the ray lands; hold to slide it."""

    label = "Place"

    def __init__(self, context, pose, kind, owner, generation, units):
        super().__init__(context, pose, owner, generation, units)
        if kind not in PLACE_SHAPES:
            raise editing.EditRejected("Unknown shape")
        collection = context.collection
        if collection is None or not collection.is_editable or collection.library:
            raise editing.EditRejected("Choose a local editable collection for the new object")
        label, _icon, size = PLACE_SHAPES[kind]
        self.label = f"Place {label}"
        self.dimensions = tuple(value * units for value in size)
        self.selection = editing._selection_snapshot(context)
        mesh = build_shape_mesh(kind, self.dimensions, label)
        self.obj = bpy.data.objects.new(label, mesh)
        collection.objects.link(self.obj)
        self.obj_name = self.obj.name
        self._move(context, pose)
        for other in tuple(context.selected_objects):
            other.select_set(False)
        self.obj.select_set(True)
        context.view_layer.objects.active = self.obj
        self.changed = True

    def _move(self, context, pose):
        origin, direction, _rotation = _pose_ray(pose)
        location, yaw, _hit = placement(context, origin, direction, self.dimensions, exclude=self.obj)
        self.obj.matrix_world = Matrix.Translation(location) @ yaw.to_matrix().to_4x4()

    def _valid(self, context):
        try:
            return super()._valid(context) and self.obj.name in context.view_layer.objects
        except ReferenceError:
            return False

    def _restore(self, context):
        try:
            mesh = self.obj.data
            bpy.data.objects.remove(self.obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        except ReferenceError:
            pass
        editing._restore_object_selection(context, self.selection)

    def _apply(self, context, pose):
        self._move(context, pose)
        return f"{self.label}: release to keep it here"

    def commit_message(self):
        return f"{self.obj_name} placed as one undo step"


class MeasureStroke(_Stroke):
    """Tape measure: hold from one point to another; nothing in the scene changes."""

    label = "Measure"
    needs_undo = False

    def __init__(self, context, pose, owner, generation, units):
        super().__init__(context, pose, owner, generation, units)
        self.start = self._point(context, pose)
        self.end = self.start.copy()
        self.ticks = 0
        self.changed = True  # A measurement is the result, even at zero length.

    def _point(self, context, pose):
        """Where the ray meets a surface, else 20 cm ahead of the controller tip."""
        origin, direction, _rotation = _pose_ray(pose)
        hit, location, _normal, _index, _obj, _matrix = context.scene.ray_cast(
            context.evaluated_depsgraph_get(), origin, direction, distance=50.0 * self.units)
        return Vector(location) if hit else origin + direction * 0.2 * self.units

    @property
    def length(self):
        return (self.end - self.start).length

    def text(self):
        metres = self.length / self.units
        return f"{self.length:.3f} units (real size {metres * 100:.1f} cm at your scale)"

    def _restore(self, context):
        pass

    def _apply(self, context, pose):
        self.end = self._point(context, pose)
        ticks = int(self.length / self.units / 0.1)
        if ticks != self.ticks:
            from . import controllers
            controllers.pulse(context, self.request.owner.identifier, "tick")  # Every 10 cm.
            self.ticks = ticks
        return self.text()

    def commit_message(self):
        return "Measured " + self.text()


def begin(context, pose_origin, pose_rotation, tool="sculpt_clay", owner=None, target_generation=0):
    """Start a physical stroke, or return None after an instant action (dipping a swatch)."""
    editing._require_main_thread()
    if tool not in TOOLS:
        raise editing.EditRejected("Unknown physical tool")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    pose = Pose(tuple(pose_origin), tuple(pose_rotation))
    rotation = Quaternion(pose.rotation)
    if (not all(math.isfinite(value) for value in Vector(pose.position))
            or not math.isfinite(rotation.magnitude) or rotation.magnitude < 1.0e-8):
        raise editing.EditRejected("Controller pose is outside Blender's finite range")
    if tool not in {"scrub_time", "measure"} and (not editing.undo_available(context)
                                                  or not bpy.ops.ed.undo_push.poll()):
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode for physical tools")
    owner = owner or CaptureOwner(InputProvenance.CONTROLLER, "/user/hand/right", 0)
    settings = context.window_manager.xrwb_settings
    units = viewer_scale.world_units_per_metre(context)
    if tool == "scrub_time":
        transaction = ScrubStroke(context, pose, owner, target_generation, units, settings)
    elif tool == "uv_island":
        from . import uv_tools
        transaction = uv_tools.begin(context, pose, owner, target_generation)
    elif tool == "measure":
        transaction = MeasureStroke(context, pose, owner, target_generation, units)
    elif tool == "place_prefab":
        from . import prefabs
        transaction = prefabs.PrefabStroke(context, pose, str(settings.prefab_kind), owner, target_generation, units)
    elif tool == "sdf_blob":
        from . import sdf_modeling
        return sdf_modeling.begin(context, pose, owner, target_generation)
    elif tool in {"draw_curve", "paint_vertex"}:
        from . import creative_tools
        transaction = creative_tools.begin(context, tool, pose, owner, target_generation, units, settings)
        if tool == "paint_vertex":
            return transaction  # Already owns capture and painted the first dab.
    elif tool == "place_object":
        transaction = PlaceStroke(context, pose, str(settings.place_kind), owner, target_generation, units)
    else:
        origin, direction, _rotation = _pose_ray(pose)
        context.view_layer.update()
        hit, location, normal, _index, evaluated, _matrix_hit = context.scene.ray_cast(
            context.evaluated_depsgraph_get(), origin, direction)
        obj = evaluated.original if hit and evaluated is not None else None
        if tool == "eyedropper":
            return _eyedrop(context, obj, _index)
        if tool == "spray_material":
            if is_swatch(obj):
                from . import materials_lab
                name = materials_lab.dip(context, swatch_of(obj))
                return None if name else _raise("That block has no material")
            material = bpy.data.materials.get(settings.spray_material)
            if material is None:
                raise editing.EditRejected("Choose a spray material: trigger a material block or a Paint button")
            transaction = SprayStroke(context, pose, material, owner, target_generation, units, settings)
            editing._ACTIVE_TRANSACTION = transaction
            # Paint immediately under the nozzle; later samples go through capture rules.
            transaction._apply(context, pose)
            return transaction
        if obj is None:
            raise editing.EditRejected("Point at a mesh surface to sculpt")
        if is_swatch(obj):
            raise editing.EditRejected("Material blocks are not sculptable")
        require_mesh_target(context, obj, "Sculpt")
        transaction = SculptStroke(context, obj, location, normal, pose, tool, owner,
                                   target_generation, units, settings)
    editing._ACTIVE_TRANSACTION = transaction
    return transaction


def _eyedrop(context, obj, face_index):
    """Load the material under the ray into the spray and switch to Spray. Instant."""
    from . import interaction_log, materials_lab

    if obj is None:
        raise editing.EditRejected("Point at something with a material")
    if is_swatch(obj):
        materials_lab.dip(context, swatch_of(obj))
    else:
        material = None
        if obj.type == "MESH" and not any(m.show_viewport for m in obj.modifiers) \
                and 0 <= face_index < len(obj.data.polygons):
            slot = obj.data.polygons[face_index].material_index
            if slot < len(obj.material_slots):
                material = obj.material_slots[slot].material
        material = material or obj.active_material
        if material is None:
            raise editing.EditRejected(f"{obj.name} has no material to pick")
        context.window_manager.xrwb_settings.spray_material = material.name
        interaction_log.emit("material", f"Picked {material.name}; spray is loaded.", context)
    context.window_manager.xrwb_settings.edit_tool = "spray_material"
    return None


def _raise(message):
    raise editing.EditRejected(message)


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("sculpt", message, context, level=level)


def _guard(context):
    if context.mode != "OBJECT":
        return "Switch to Object Mode for clay tools"
    if not editing.undo_available(context):
        return "Enable Global Undo and at least one undo step"
    if editing.active_transaction() is not None:
        return "Release or cancel the current edit first"
    return ""


def _undo_push(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in bpy.ops.ed.undo_push(message=message):
        raise RuntimeError("Blender rejected the undo entry")


class XRWB_OT_add_clay(bpy.types.Operator):
    bl_idname = "xrwb.add_clay"
    bl_label = "Clay Ball"
    bl_description = "Add a dense, smooth sphere within reach, ready for clay tools"

    @classmethod
    def poll(cls, context):
        reason = _guard(context)
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def execute(self, context):
        import bmesh
        from . import materials_lab

        location, rotation, units = viewer_scale.viewer_frame(context)
        location = location + rotation @ Vector((0.0, 0.0, -0.5 * units))
        mesh = bpy.data.meshes.new("Clay")
        bm = bmesh.new()
        try:
            bmesh.ops.create_icosphere(bm, subdivisions=6, radius=0.12 * units)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        mesh.shade_smooth()
        mesh.materials.append(materials_lab.build_preset("CLAY"))
        obj = bpy.data.objects.new("Clay", mesh)
        obj.location = location
        context.collection.objects.link(obj)
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        try:
            _undo_push("XR Add Clay Ball")
        except RuntimeError as exc:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, "Clay ball ready: pick Clay, Grab or Smooth and hold a trigger on it.")
        return {"FINISHED"}


class XRWB_OT_remesh_detail(bpy.types.Operator):
    bl_idname = "xrwb.remesh_detail"
    bl_label = "Remesh for Detail"
    bl_description = ("Native voxel remesh of the active mesh; voxel size is physical, "
                      "so shrinking yourself gives finer detail")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        reason = _guard(context) or ("" if obj is not None and obj.type == "MESH" else "Select a mesh")
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def execute(self, context):
        obj = context.active_object
        try:
            require_mesh_target(context, obj, "Remesh")
            if is_swatch(obj):
                raise editing.EditRejected("Material blocks are not remeshed")
            units = viewer_scale.world_units_per_metre(context)
            scale = sum(abs(value) for value in obj.matrix_world.to_scale()) / 3.0
            voxel = float(context.window_manager.xrwb_settings.remesh_detail_m) * units / scale
            areas = np.empty(len(obj.data.polygons), dtype=np.float32)
            obj.data.polygons.foreach_get("area", areas)
            estimate = float(areas.sum()) / (voxel * voxel)
            if estimate > 1_500_000:
                raise editing.EditRejected(f"About {estimate:,.0f} vertices: grow yourself or raise Detail")
            obj.data.remesh_voxel_size = voxel
            with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                if "FINISHED" not in bpy.ops.object.voxel_remesh():
                    raise editing.EditRejected("Native voxel remesh failed; the mesh may be open")
            _undo_push("XR Remesh for Detail")
        except (editing.EditRejected, RuntimeError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        _log(context, f"Remeshed to {len(obj.data.vertices):,} vertices.")
        return {"FINISHED"}


CLASSES = (XRWB_OT_add_clay, XRWB_OT_remesh_detail)
