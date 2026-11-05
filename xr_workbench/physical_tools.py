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

from . import editing, sculpt_math, viewer_scale
from .interaction import (
    CaptureOwner, CapturePhase, CaptureState, Decision, EditRequest, EditResult,
    InputProvenance, Pose, TransformOptions, TransformTool,
)

SCULPT_TOOLS = {"sculpt_grab", "sculpt_clay", "sculpt_smooth", "sculpt_flatten", "sculpt_snake",
                "sculpt_inflate", "sculpt_crease", "sculpt_layer", "sculpt_mask"}
TOOLS = SCULPT_TOOLS | {"spray_material", "scrub_time", "place_object", "measure", "eyedropper", "uv_island",
                        "draw_curve", "paint_vertex", "place_prefab", "sdf_blob", "lab_dial", "grow_seed",
                        "retopo_loom"}
# Off-hand trigger in the air with one of these opens the Brush Dial instead of being refused.
DIAL_TOOLS = SCULPT_TOOLS | {"spray_material", "retopo_loom"}
DIAL_REACH_M = 2.0  # Physical: a surface farther than this along the ray counts as "in the air".
DIAL_DOUBLING_M = 0.10  # Brush size doubles every 10 cm of hand rise.
DIAL_FACES_STEP_M = 0.02  # Retopo target: one 500-face step per 2 cm.
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


_falloff = sculpt_math.falloff


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
        try:
            self._before_push(context)
        except (RuntimeError, ValueError, ReferenceError) as exc:
            self._restore(context)
            self._finish(CapturePhase.CANCELLED)
            return EditResult(Decision.REJECTED, f"{self.label} restored: {exc}")
        if self.needs_undo:
            try:
                if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(
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

    def _before_push(self, context):
        """Hook: release-time work (flush writes, add detail) that belongs in the same undo step."""


def get_positions(mesh, out):
    """Vertex coordinates into a flat float32 array. The position attribute is 10-20x faster than
    mesh.vertices.foreach_get on dense meshes (90 ms vs 0.2 ms to write 164k vertices on 5.1)."""
    mesh.attributes["position"].data.foreach_get("vector", out)


def set_positions(mesh, values):
    mesh.attributes["position"].data.foreach_set("vector", values)


MASK_NAME = ".sculpt_mask"  # Blender's real sculpt mask: float POINT attribute (4.1+), verified on 4.5.15 and 5.1.2.
DETAIL_CAP = 20_000
DENSE_RAY_VERTICES = 50_000
DETAIL_AUTO_TOOLS = {"sculpt_snake", "sculpt_inflate"}
LABELS = {"sculpt_grab": "Sculpt Grab", "sculpt_clay": "Sculpt Clay", "sculpt_smooth": "Sculpt Smooth",
          "sculpt_flatten": "Sculpt Flatten", "sculpt_snake": "Sculpt Snake Hook",
          "sculpt_inflate": "Sculpt Inflate", "sculpt_crease": "Sculpt Crease",
          "sculpt_layer": "Sculpt Layer", "sculpt_mask": "Sculpt Mask"}


def _opt(settings, name, default):
    return getattr(settings, name, default)


def read_mask(mesh):
    """The sculpt mask as float32 (one per vertex), or None when the mesh has none."""
    attr = mesh.attributes.get(MASK_NAME)
    if attr is None or attr.domain != "POINT" or attr.data_type != "FLOAT":
        return None
    values = np.empty(len(mesh.vertices), dtype=np.float32)
    attr.data.foreach_get("value", values)
    return values


def write_mask(mesh, values):
    attr = mesh.attributes.get(MASK_NAME)
    if attr is None:
        attr = mesh.attributes.new(MASK_NAME, "FLOAT", "POINT")
    attr.data.foreach_set("value", np.asarray(values, dtype=np.float32))
    mesh.update()


def masked_points(obj, cap=20_000):
    """World positions of masked vertices (m > 0.5), subsampled to cap, for the overlay."""
    mask = read_mask(obj.data)
    if mask is None:
        return np.empty((0, 3))
    flat = np.empty(len(mask) * 3, dtype=np.float32)
    get_positions(obj.data, flat)
    ids = np.flatnonzero(mask > 0.5)
    if len(ids) > cap:
        ids = ids[np.linspace(0, len(ids) - 1, cap).astype(np.int64)]
    world = _matrix(obj)
    return flat.reshape(-1, 3)[ids].astype(np.float64) @ world[:3, :3].T + world[:3, 3]


def refine_mesh(mesh, touched, target, cap=DETAIL_CAP, backup=False):
    """Split edges longer than 1.5 * target whose ends were both touched (up to 3 passes).

    Returns (vertices added, capped, backup bmesh or None). The caller owns the backup.
    Subdivided verts take the average of the mask/UVs of the edge ends from bmesh
    (ponytail: spec asks for max of the ends for the mask; average is what bmesh
    interpolates. Upgrade: fix up new verts from their edge ends after the pass).
    """
    import bmesh

    limit = 1.5 * target
    count = len(mesh.vertices)
    flat = np.empty(count * 3, dtype=np.float32)
    get_positions(mesh, flat)
    points = flat.reshape(-1, 3)
    pairs = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", pairs)
    pairs = pairs.reshape(-1, 2)
    lengths = np.linalg.norm(points[pairs[:, 0]] - points[pairs[:, 1]], axis=1)
    first = np.flatnonzero(touched[pairs[:, 0]] & touched[pairs[:, 1]] & (lengths > limit))
    if first.size == 0:
        return 0, False, None
    bm = bmesh.new()
    saved = None
    try:
        bm.from_mesh(mesh)
        if backup:
            saved = bm.copy()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        for vert, flag in zip(bm.verts, touched):
            vert.tag = bool(flag)
        edges = [bm.edges[int(i)] for i in first]
        added, capped = 0, False
        for _ in range(3):
            if added + len(edges) > cap:
                edges, capped = edges[:max(0, cap - added)], True
            if not edges:
                break
            result = bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=True)
            fresh = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
            for vert in fresh:
                vert.tag = True
            added += len(fresh)
            faces = list({f for vert in fresh for f in vert.link_faces})
            bmesh.ops.triangulate(bm, faces=faces)
            faces = list({f for vert in fresh if vert.is_valid for f in vert.link_faces})
            bmesh.ops.beautify_fill(bm, faces=faces, edges=list({e for f in faces for e in f.edges}))
            edges = list({e for f in faces if f.is_valid for e in f.edges
                          if e.verts[0].tag and e.verts[1].tag and e.calc_length() > limit})
        bm.to_mesh(mesh)
        mesh.update()
    except Exception:
        if saved is not None:
            saved.free()
        raise
    finally:
        bm.free()
    return added, capped, saved


class _Source:
    """One brush hand: where its last sample was and its own Grab weights / Snake centre."""

    def __init__(self, owner, position, hit):
        self.owner = owner
        self.last = np.array(position, dtype=np.float64)
        self.anchor = self.last.copy()
        self.centre = np.array(hit, dtype=np.float64)
        self.sets = []  # Grab: [ids, weights, rotation 3x3, previous offset]


class SculptStroke(_Stroke):
    """Grab/Snake Hook pull; Clay, Inflate, Layer build; Crease cuts; Smooth/Flatten relax; Mask paints.

    Brushes run in world space over chunked bounding boxes. Mirror X and radial
    symmetry repeat every brush through `copies`; a second hand can join (twin).
    """

    def __init__(self, context, obj, hit, normal, pose, tool, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        self.label = LABELS[tool]
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
        get_positions(self.mesh, flat)
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
        self.normal = np.array(normal, dtype=np.float64)
        # Trigger pressure (0.15..1) from the controller; 1 without analogue data.
        self.pressure = 1.0
        mirror = bool(getattr(settings, "sculpt_mirror_x", False))
        self.radial = int(_opt(settings, "sculpt_radial", 0) or 0)
        self.copies = sculpt_math.brush_copies(self.world, self.inverse, mirror, self.radial)
        self.mirror = self.copies[1] if mirror and len(self.copies) > 1 else None
        edges = np.empty(len(self.mesh.edges) * 2, dtype=np.int64)
        self.mesh.edges.foreach_get("vertices", edges)
        self.edges = edges.reshape(-1, 2)
        self.chunks = sculpt_math.Chunks(self.current)
        self.touched = np.zeros(count, dtype=bool)
        self.frame = 0
        self.bvh = None
        self.dirty_ids = []
        self.local = flat.copy()  # What the mesh holds now (local float32); only touched rows are recomputed.
        self.secondary = {}
        self.primary_released = False
        self.backup = None
        self.refined = 0
        # Mask: read for every tool (frozen vertices); the Mask tool edits it.
        self.mask = read_mask(self.mesh)
        self.mask_created = False
        self.mask_original = None if self.mask is None else self.mask.copy()
        if tool == "sculpt_mask" and self.mask is None:
            self.mask = np.zeros(count, dtype=np.float32)
            self.mask_created = True
        self.normals = None
        if tool in {"sculpt_inflate", "sculpt_layer"}:
            raw = np.empty(count * 3, dtype=np.float32)
            self.mesh.vertex_normals.foreach_get("vector", raw)
            n0 = raw.reshape(-1, 3).astype(np.float64) @ self.inverse[:3, :3]
            length = np.linalg.norm(n0, axis=1)
            self.normals = n0 / np.maximum(length, 1.0e-12)[:, None]
        if tool == "sculpt_layer":
            self.disp = np.zeros(count)
            self.height = float(_opt(settings, "sculpt_layer_height_m", 0.005)) * units * self.sign
        scale = sum(abs(value) for value in obj.matrix_world.to_scale()) / 3.0
        self.detail_length = float(settings.remesh_detail_m) * units / max(scale, 1.0e-9)
        choice = _opt(settings, "sculpt_detail", "AUTO")
        if choice == "AUTO":
            self.detail = tool in DETAIL_AUTO_TOOLS
        else:
            self.detail = bool(choice) and choice != "OFF"
        self.primary = _Source(owner, pose.position, hit)
        if tool == "sculpt_grab":
            self._grab_sets(self.primary, hit)
            if not any(len(s[0]) for s in self.primary.sets):
                raise editing.EditRejected("No vertices under the brush; shrink yourself or enlarge the brush")

    # -- helpers ---------------------------------------------------------
    @property
    def weights(self):
        """Grab: primary-brush weight per vertex (identity copy), for readouts and tests."""
        out = np.zeros(len(self.current))
        if self.primary.sets:
            ids, w, _r, _p = self.primary.sets[0]
            out[ids] = w
        return out

    @property
    def twin(self):
        return bool(self.secondary)

    def _mirror_point(self, point):
        return (self.mirror @ np.append(point, 1.0))[:3]

    def _mirror_vector(self, vector):
        return self.mirror[:3, :3] @ vector

    def _near(self, centre, mask_frozen=True, lateral=None):
        """Vertices under the brush and their weights. `lateral` (a unit normal) measures
        distance on the stroke-start surface across that normal, so raising it never moves the brush."""
        ids = self.chunks.candidates(centre, self.radius)
        if ids.size == 0:
            return ids, np.empty(0)
        if lateral is None:
            distance = np.linalg.norm(self.current[ids] - centre, axis=1)
            keep = distance < self.radius
        else:
            offset = self.start_world[ids] - centre
            along = offset @ lateral
            distance = np.linalg.norm(offset - along[:, None] * lateral, axis=1)
            keep = (distance < self.radius) & (np.abs(along) < self.radius)
        ids, distance = ids[keep], distance[keep]
        weights = sculpt_math.falloff(distance, self.radius)
        if mask_frozen and self.mask is not None:
            weights = weights * (1.0 - self.mask[ids])
        return ids, weights

    def _grab_sets(self, source, hit):
        for transform in self.copies:
            centre = (transform @ np.append(hit, 1.0))[:3]
            ids, weights = self._near(centre)
            keep = weights > 0.0
            ids, weights = ids[keep], weights[keep]
            self.touched[ids] = True
            source.sets.append([ids, weights, transform[:3, :3], np.zeros((len(ids), 3))])

    def _valid(self, context):
        try:
            return (super()._valid(context) and self.obj.name in context.view_layer.objects
                    and self.obj.data == self.mesh
                    and (self.refined or len(self.mesh.vertices) * 3 == len(self.original))
                    and tuple(tuple(row) for row in self.obj.matrix_world) == self.matrix_pointer)
        except ReferenceError:
            return False

    @property
    def dirty(self):
        return bool(self.dirty_ids)

    def _write(self):
        if self.dirty_ids:
            ids = np.concatenate(self.dirty_ids)
            rows = self.local.reshape(-1, 3)
            rows[ids] = ((self.current[ids] - self.world[:3, 3]) @ self.inverse[:3, :3].T).astype(np.float32)
            self.dirty_ids = []
        set_positions(self.mesh, self.local)
        self.mesh.update()

    def _write_due(self, ids):
        """Dense meshes and many brush copies write at 45 or 30 Hz (every 2nd / 3rd sample)."""
        count = len(self.current)
        every = 3 if count > 1_000_000 else 2 if (count > 500_000 or len(self.copies) > 8) else 1
        self.frame += 1
        self.dirty_ids.append(ids)
        if every == 1 or self.frame % every == 0:
            self._write()

    @property
    def rate_note(self):
        count = len(self.current)
        if count > 1_000_000:
            return " (dense mesh, 30 Hz)"
        return " (dense mesh, 45 Hz)" if count > 500_000 or len(self.copies) > 8 else ""

    def _restore(self, context):
        if self.backup is not None:
            self.backup.to_mesh(self.mesh)
        else:
            set_positions(self.mesh, self.original)
        if self.tool == "sculpt_mask" and self.backup is None:
            if self.mask_created:
                attr = self.mesh.attributes.get(MASK_NAME)
                if attr is not None:
                    self.mesh.attributes.remove(attr)
            else:
                write_mask(self.mesh, self.mask_original)
        self.mesh.update()

    def _finish(self, phase):
        if self.backup is not None:
            self.backup.free()
            self.backup = None
        super()._finish(phase)

    def _before_push(self, context):
        if self.dirty:
            self._write()
        if self.detail and self.tool != "sculpt_mask" and np.any(self.touched):
            added, capped, self.backup = refine_mesh(self.mesh, self.touched, self.detail_length, backup=True)
            if added:
                self.refined = added
                _log(context, f"Detail added {added:,} vertices" + ("; capped: Remesh for Detail" if capped else ""))
            elif self.backup is not None:
                self.backup.free()
                self.backup = None

    def _surface_hit(self, context, pose):
        origin, direction, _rotation = _pose_ray(pose)
        inverse = self.obj.matrix_world.inverted()
        if len(self.current) > DENSE_RAY_VERTICES:
            # ponytail: dense meshes ray cast against the surface as it was when the stroke began
            # (one BVH, microseconds per sample); a live ray cast re-evaluates the whole mesh
            # (~130 ms at 164k vertices). Small meshes keep the live surface. Upgrade: refit the BVH.
            if self.bvh is None:
                from mathutils.bvhtree import BVHTree
                context.view_layer.update()
                self.bvh = BVHTree.FromObject(self.obj, context.evaluated_depsgraph_get())
            location, normal, _index, _distance = self.bvh.ray_cast(
                inverse @ origin, (inverse.to_3x3() @ direction).normalized())
            hit = location is not None
        else:
            context.view_layer.update()
            hit, location, normal, _index = self.obj.ray_cast(
                inverse @ origin, (inverse.to_3x3() @ direction).normalized(),
                depsgraph=context.evaluated_depsgraph_get())
        if not hit:
            return None
        world = self.obj.matrix_world
        return (np.array(world @ location), np.array((inverse.transposed().to_3x3() @ normal).normalized()))

    # -- input -----------------------------------------------------------
    def _apply(self, context, pose):
        return self._run(context, self.primary, pose)

    def update_secondary(self, context, sample):
        """The other hand's trigger joins the stroke as a second brush (same transaction)."""
        editing._require_main_thread()
        owner = sample.owner
        key = (owner.provenance, owner.identifier)
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, f"{self.label} does not own capture")
        if key == (self.request.owner.provenance, self.request.owner.identifier):
            return EditResult(Decision.REJECTED, "Input belongs to the first brush")
        if self.primary_released:
            return EditResult(Decision.REJECTED, "First brush already released")
        if not self._valid(context) or not editing.undo_available(context):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, f"{self.label} target or undo state changed")
        if not sample.tracking_active:
            return EditResult(Decision.PAUSED, "Second brush tracking lost")
        position = np.array(Vector(sample.pose.position))
        if not np.all(np.isfinite(position)):
            return EditResult(Decision.REJECTED, "Controller position is outside Blender's finite range")
        source = self.secondary.get(key)
        if source is None:
            surface = self._surface_hit(context, sample.pose)
            if surface is None:
                return EditResult(Decision.REJECTED, "Aim the second brush at the surface")
            source = _Source(owner, position, surface[0])
            if self.tool == "sculpt_grab":
                self._grab_sets(source, surface[0])
            self.secondary[key] = source
            return EditResult(Decision.APPLY, "Second brush joined")
        return EditResult(Decision.APPLY, self._run(context, source, sample.pose))

    def release(self, context, owner):
        """A trigger was let go: a twin brush stops; the stroke commits when both are released."""
        key = (owner.provenance, owner.identifier)
        if key in self.secondary:
            del self.secondary[key]
            if self.primary_released:
                return self.commit(context)
            return EditResult(Decision.APPLY, "Second brush released")
        if self.secondary:
            self.primary_released = True
            return EditResult(Decision.APPLY, "Waiting for the second brush to release")
        return self.commit(context, owner)

    def cancel(self, context, owner=None):
        if owner is not None and (owner.provenance, owner.identifier) in self.secondary:
            owner = None  # Either hand cancels the whole stroke.
        return super().cancel(context, owner)

    # -- brushes ---------------------------------------------------------
    def _grab(self, source, position, primary):
        anchor = np.array(self.anchor) if primary else source.anchor
        delta = position - anchor
        moved = []
        for entry in source.sets:
            ids, weights, rotation, previous = entry
            offset = weights[:, None] * (rotation @ delta)[None, :]
            self.current[ids] += offset - previous
            entry[3] = offset
            moved.append(ids)
        if moved:
            ids = np.concatenate(moved)
            self.chunks.refresh(self.current, ids)
            self._write_due(ids)
        if np.any(np.abs(delta) > 1.0e-9):
            self.changed = True
        return f"Grab {np.linalg.norm(delta) / self.units * 100:.1f} cm{self.rate_note}"

    def _run(self, context, source, pose):
        primary = source is self.primary
        position = np.array(Vector(pose.position))
        tool = self.tool
        if tool == "sculpt_grab":
            return self._grab(source, position, primary)
        last = np.array(self.last) if primary else source.last
        delta = position - last
        if not primary:
            source.last = position
        step = float(np.linalg.norm(delta))
        if step <= 1.0e-9 and tool != "sculpt_layer":
            return "Move the hand to sculpt"
        normal = self.normal
        if tool == "sculpt_snake":
            delta = sculpt_math.clamp_delta(delta, self.radius)
            step = float(np.linalg.norm(delta))
            centre = source.centre
        else:
            surface = self._surface_hit(context, pose)
            if surface is None:
                return "Brush is off the surface"
            centre, normal = surface
        force = self.strength * self.pressure
        moved, pending = [], []
        everything = slice(None)
        for transform in self.copies:
            rot = transform[:3, :3]
            c = (transform @ np.append(centre, 1.0))[:3]
            n = rot @ normal
            ids, w = self._near(c, mask_frozen=(tool != "sculpt_mask"),
                                lateral=n if tool == "sculpt_layer" else None)
            keep = w > 0.0
            ids, w = ids[keep], w[keep]
            if not ids.size:
                continue
            self.touched[ids] = True
            moved.append(ids)
            # Every copy reads the positions from before this sample, so mirror and radial
            # copies stay exactly symmetric even where their brushes overlap.
            pts = self.current[ids]
            if tool == "sculpt_clay":
                # Half the hand travel is added as height at full strength and pressure.
                pts += (w * (step * 0.5 * force * self.sign))[:, None] * n
            elif tool == "sculpt_flatten":
                # Pull bumps and dents toward the average plane under the brush
                # (like Blender's Flatten): the peak moves, not just the sides.
                factor = min(1.0, 2.0 * step / self.radius) * force
                plane = np.average(pts, axis=0, weights=w)
                pts -= (w * factor * ((pts - plane) @ n))[:, None] * n
            elif tool == "sculpt_smooth":
                average = sculpt_math.smooth_average(self.current, ids, self.edges)
                pts += (w * (min(1.0, 2.0 * step / self.radius) * force))[:, None] * (average - pts)
            elif tool == "sculpt_snake":
                sculpt_math.snake(pts, everything, w, c, rot @ delta, force, self.radius)
            elif tool == "sculpt_inflate":
                pts += (w * (step * 0.5 * force * self.sign))[:, None] * self.normals[ids]
            elif tool == "sculpt_crease":
                sculpt_math.crease(pts, everything, w, c, n, step, force, self.sign, self.radius)
            elif tool == "sculpt_layer":
                self.disp[ids] = (np.maximum if self.height >= 0 else np.minimum)(self.disp[ids], w * self.height)
                continue
            elif tool == "sculpt_mask":
                amount = w * (min(1.0, 2.0 * step / self.radius) * 2.0 * force * self.sign)
                self.mask[ids] = np.clip(self.mask[ids] + amount, 0.0, 1.0)
                continue
            pending.append((ids, pts - self.current[ids]))
        for ids, change in pending:
            self.current[ids] += change
        if tool == "sculpt_layer":
            for ids in moved:
                self.current[ids] = self.start_world[ids] + self.disp[ids, None] * self.normals[ids]
        if tool == "sculpt_snake":
            source.centre = source.centre + delta
        if not moved:
            return "No vertices under the brush"
        if tool == "sculpt_mask":
            write_mask(self.mesh, self.mask)
        else:
            ids = np.concatenate(moved)
            self.chunks.refresh(self.current, ids)
            self._write_due(ids)
        self.changed = True
        return f"{self.label}: {int(sum(len(i) for i in moved))} vertices{self.rate_note}"

    def commit_message(self):
        extra = f", detail +{self.refined:,} vertices" if self.refined else ""
        return f"{self.label} committed as one undo step{extra}"


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


def off_hand() -> str:
    from . import onboarding
    return "/user/hand/right" if onboarding.prefs()["left_handed"] else "/user/hand/left"


class BrushDialStroke(_Stroke):
    """Off-hand trigger in the air: up/down sizes the brush (x2 per 10 cm), wrist roll sets strength.

    Settings only: no undo step; cancel puts both values back.
    """

    label = "Brush Dial"
    needs_undo = False

    def __init__(self, context, pose, owner, generation, units, settings, tool):
        super().__init__(context, pose, owner, generation, units)
        self.settings = settings
        self.size_name = {"spray_material": "spray_radius_m", "retopo_loom": "retopo_faces"}.get(
            tool, "sculpt_radius_m")
        self.use_strength = self.size_name == "sculpt_radius_m"
        self.start = (getattr(settings, self.size_name), float(settings.sculpt_strength))
        self.start_rotation = Quaternion(pose.rotation).normalized()
        self.size, self.strength = self.start

    def _restore(self, context):
        setattr(self.settings, self.size_name, self.start[0])
        self.settings.sculpt_strength = self.start[1]
        self.size, self.strength = self.start

    def _apply(self, context, pose):
        rise = (pose.position[2] - self.anchor.z) / self.units
        if self.size_name == "retopo_faces":
            from . import retopo
            self.size = retopo.step_target(self.start[0], round(rise / DIAL_FACES_STEP_M))
        else:
            prop = self.settings.bl_rna.properties[self.size_name]
            self.size = min(prop.hard_max, max(prop.hard_min, self.start[0] * 2.0 ** (rise / DIAL_DOUBLING_M)))
        setattr(self.settings, self.size_name, self.size)
        if self.use_strength:
            relative = self.start_rotation.inverted() @ Quaternion(pose.rotation).normalized()
            roll = (math.degrees(2.0 * math.atan2(relative.z, relative.w)) + 180.0) % 360.0 - 180.0
            t, s0 = max(-1.0, min(1.0, roll / 90.0)), self.start[1]
            # Start value at no roll; +90 degrees reaches 1.0, -90 degrees reaches 0.05.
            self.strength = max(0.05, min(1.0, s0 + t * ((1.0 - s0) if t > 0 else (s0 - 0.05))))
            self.settings.sculpt_strength = self.strength
        self.changed = True
        return self.readout()

    def readout(self):
        if self.size_name == "retopo_faces":
            from . import retopo
            return f"Retopo {retopo.quads_label(self.size)}"
        text = f"Brush {self.size * 100.0:.1f} cm"
        return f"{text} · {round(self.strength * 100)} %" if self.use_strength else text

    def detent(self):
        if self.size_name == "retopo_faces":
            return int(self.size)
        return (math.floor(self.size / 0.005 + 1e-9), math.floor(self.strength * 10.0 + 1e-9))

    def commit_message(self):
        return self.readout()


def _retopo_pull(context, obj, hand):
    """Retopo Loom trigger: show the running card now, run CurioMesh on the next main-thread tick."""
    from . import interaction_log, retopo
    if obj is None or obj.type != "MESH":
        raise editing.EditRejected("Point the Retopo Loom at a mesh")
    settings = context.window_manager.xrwb_settings
    name, target, quality = obj.name, int(settings.retopo_faces), str(settings.retopo_quality)
    interaction_log.emit("retopo", " ".join(retopo.card_lines({"state": "running"})), context, hand=hand)

    def run():
        from . import controllers, ui
        with ui._view3d_override(bpy.context):
            ctx = bpy.context
            source = bpy.data.objects.get(name)
            card = (retopo.run(ctx, source, target, quality) if source is not None
                    else {"ok": False, "reason": "The mesh is gone"})
            interaction_log.emit("retopo", " · ".join(retopo.card_lines(card)), ctx, hand=hand,
                                 level="INFO" if card.get("ok") else "WARNING")
            controllers.pulse(ctx, hand, "commit" if card.get("ok") else "reject")
        return None

    bpy.app.timers.register(run, first_interval=0.05)  # One frame for the card to show.
    return None


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
    if tool == "lab_dial":
        from . import subdiv_lab
        return subdiv_lab.begin(context, pose, owner, target_generation)
    if tool == "grow_seed":
        from . import growth
        return growth.begin(context, pose, owner, target_generation)
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
        if (tool in DIAL_TOOLS and owner.identifier == off_hand()
                and (obj is None or (Vector(location) - origin).length > DIAL_REACH_M * units)):
            transaction = BrushDialStroke(context, pose, owner, target_generation, units, settings, tool)
            editing._ACTIVE_TRANSACTION = transaction
            return transaction
        if tool == "retopo_loom":
            return _retopo_pull(context, obj, owner.identifier)
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
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
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
            had_mask = read_mask(obj.data) is not None
            obj.data.remesh_voxel_size = voxel
            with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                if "FINISHED" not in bpy.ops.object.voxel_remesh():
                    raise editing.EditRejected("Native voxel remesh failed; the mesh may be open")
            _undo_push("XR Remesh for Detail")
        except (editing.EditRejected, RuntimeError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        if had_mask and read_mask(obj.data) is None:
            _log(context, "Remesh cleared the mask.", "WARNING")
        _log(context, f"Remeshed to {len(obj.data.vertices):,} vertices.")
        return {"FINISHED"}


class _MaskAction(bpy.types.Operator):
    """Base for instant mask actions on the active mesh: one undo step each."""

    undo_name = "Mask"
    needs_mask = True

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        reason = _guard(context) or ("" if obj is not None and obj.type == "MESH" else "Select a mesh")
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def change(self, context, obj, mask):
        raise NotImplementedError

    def execute(self, context):
        obj = context.active_object
        try:
            require_mesh_target(context, obj, "Mask")
            mask = read_mask(obj.data)
            if mask is None and self.needs_mask:
                raise editing.EditRejected("Paint a mask first")
            message = self.change(context, obj, mask)
            _undo_push(f"XR {self.undo_name}")
        except (editing.EditRejected, RuntimeError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        _log(context, message)
        return {"FINISHED"}


class XRWB_OT_mask_invert(_MaskAction):
    bl_idname = "xrwb.mask_invert"
    bl_label = "Invert Mask"
    bl_description = "Swap masked and unmasked vertices"
    undo_name = "Mask Invert"
    needs_mask = False

    def change(self, context, obj, mask):
        write_mask(obj.data, 1.0 - (np.zeros(len(obj.data.vertices), np.float32) if mask is None else mask))
        return "Mask inverted."


class XRWB_OT_mask_clear(_MaskAction):
    bl_idname = "xrwb.mask_clear"
    bl_label = "Clear Mask"
    bl_description = "Remove the sculpt mask"
    undo_name = "Mask Clear"

    def change(self, context, obj, mask):
        obj.data.attributes.remove(obj.data.attributes[MASK_NAME])
        obj.data.update()
        return "Mask cleared."


class _MaskSpread(_MaskAction):
    spread = np.maximum
    verb = "grown"

    def change(self, context, obj, mask):
        edges = np.empty(len(obj.data.edges) * 2, dtype=np.int64)
        obj.data.edges.foreach_get("vertices", edges)
        a, b = edges.reshape(-1, 2).T
        out = mask.copy()
        self.spread.at(out, a, mask[b])
        self.spread.at(out, b, mask[a])
        write_mask(obj.data, out)
        return f"Mask {self.verb} one step."


class XRWB_OT_mask_grow(_MaskSpread):
    bl_idname = "xrwb.mask_grow"
    bl_label = "Grow Mask"
    bl_description = "Spread the mask one edge outward"
    undo_name = "Mask Grow"


class XRWB_OT_mask_shrink(_MaskSpread):
    bl_idname = "xrwb.mask_shrink"
    bl_label = "Shrink Mask"
    bl_description = "Pull the mask one edge inward"
    undo_name = "Mask Shrink"
    spread = np.minimum
    verb = "shrunk"


def extract_masked(context, obj, thickness_m=None):
    """New object from the fully masked faces, boundary smoothed, with a Solidify modifier."""
    import bmesh

    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        layer = bm.verts.layers.float.get(MASK_NAME)
        if layer is None or not any(v[layer] >= 0.5 for v in bm.verts):
            raise editing.EditRejected("Paint a mask first")
        drop = [f for f in bm.faces if not all(v[layer] >= 0.5 for v in f.verts)]
        if len(drop) == len(bm.faces):
            raise editing.EditRejected("No face is fully masked: paint a bigger area")
        bmesh.ops.delete(bm, geom=drop, context="FACES")
        bm.verts.layers.float.remove(layer)
        edge_verts = list({v for e in bm.edges if e.is_boundary for v in e.verts})
        for _ in range(3):
            bmesh.ops.smooth_vert(bm, verts=edge_verts, factor=0.5, use_axis_x=True, use_axis_y=True,
                                  use_axis_z=True)
        new_mesh = bpy.data.meshes.new(f"{obj.name} Extract")
        bm.to_mesh(new_mesh)
    finally:
        bm.free()
    for material in mesh.materials:
        new_mesh.materials.append(material)
    new_obj = bpy.data.objects.new(f"{obj.name} Extract", new_mesh)
    new_obj.matrix_world = obj.matrix_world
    (obj.users_collection[0] if obj.users_collection else context.collection).objects.link(new_obj)
    units = viewer_scale.world_units_per_metre(context)
    scale = sum(abs(value) for value in obj.matrix_world.to_scale()) / 3.0
    if thickness_m is None:
        thickness_m = float(_opt(context.window_manager.xrwb_settings, "mask_extract_m", 0.004))
    solid = new_obj.modifiers.new("Solidify", "SOLIDIFY")
    solid.thickness = thickness_m * units / max(scale, 1.0e-9)
    solid.offset = 1.0
    for other in context.selected_objects:
        other.select_set(False)
    new_obj.select_set(True)
    context.view_layer.objects.active = new_obj
    return new_obj


class XRWB_OT_mask_extract(_MaskAction):
    bl_idname = "xrwb.mask_extract"
    bl_label = "Extract Mask"
    bl_description = "Copy the masked area into a new editable shell object (Solidify modifier)"
    undo_name = "Mask Extract"

    def change(self, context, obj, mask):
        new_obj = extract_masked(context, obj)
        return f"Extracted {len(new_obj.data.polygons):,} faces into {new_obj.name}."


class XRWB_OT_sculpt_detail(bpy.types.Operator):
    bl_idname = "xrwb.sculpt_detail"
    bl_label = "Detail"
    bl_description = "Add triangles where the surface is stretched, near the brush (or everywhere without a ray)"

    origin: bpy.props.FloatVectorProperty(size=3, options={"SKIP_SAVE"})
    direction: bpy.props.FloatVectorProperty(size=3, options={"SKIP_SAVE"})

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
            require_mesh_target(context, obj, "Detail")
            settings = context.window_manager.xrwb_settings
            units = viewer_scale.world_units_per_metre(context)
            scale = sum(abs(value) for value in obj.matrix_world.to_scale()) / 3.0
            target = float(settings.remesh_detail_m) * units / max(scale, 1.0e-9)
            count = len(obj.data.vertices)
            touched = np.ones(count, dtype=bool)
            if Vector(self.direction).length > 0.0:
                inverse = obj.matrix_world.inverted()
                hit, location, _n, _i = obj.ray_cast(
                    inverse @ Vector(self.origin), (inverse.to_3x3() @ Vector(self.direction)).normalized(),
                    depsgraph=context.evaluated_depsgraph_get())
                if not hit:
                    raise editing.EditRejected("Aim at the surface to add detail")
                flat = np.empty(count * 3, dtype=np.float32)
                get_positions(obj.data, flat)
                radius = float(settings.sculpt_radius_m) * units / max(scale, 1.0e-9)
                touched = np.linalg.norm(flat.reshape(-1, 3) - np.array(location), axis=1) < radius
            added, capped, _saved = refine_mesh(obj.data, touched, target)
            if not added:
                raise editing.EditRejected("Nothing is stretched enough here")
            _undo_push("XR Sculpt Detail")
        except (editing.EditRejected, RuntimeError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        _log(context, f"Detail added {added:,} vertices" + ("; capped: Remesh for Detail" if capped else "."))
        return {"FINISHED"}


CLASSES = (XRWB_OT_add_clay, XRWB_OT_remesh_detail, XRWB_OT_mask_invert, XRWB_OT_mask_clear,
           XRWB_OT_mask_grow, XRWB_OT_mask_shrink, XRWB_OT_mask_extract, XRWB_OT_sculpt_detail)

