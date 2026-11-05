"""Hold-drag-release physical tools: clay sculpting, material spray, time scrub.

Each stroke is one capture owned by one controller. Cancelling (left B / Y,
tracking loss or a changed target) restores the exact original data; release
commits one undo step. Brushes are sized in physical metres, multiplied by the
viewer's world scale, so shrinking yourself makes every tool finer.
Only our code and Blender's native mesh/material/animation data are used.
"""
from __future__ import annotations

import importlib
import math
import time

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
                        "retopo_loom", "mask_extract"}
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


# ---- 0.21 touch tools: optional contact / feel engine ----------------------------------------
# contact.py, feel.py and contact_tools.py are built beside this file. Every call below is guarded:
# without them (or with `touch_tools` off) each tool works along the ray exactly as in 0.20.
_MODS = {}
REACH_M = 0.03  # Same as contact.REACH_M: a tip this close (physical) to a surface counts as touching.
SPOT_FRACTION = 0.15  # Spray: each ray paints faces within this fraction of the cone's cross-section.
SPRAY_RAYS = 12
SPRAY_MIN_DISTANCE_M = 0.03  # A nozzle touching the surface still paints a small patch.
SPRAY_NOZZLE_M = 0.15  # The cone's half-angle is atan(spray radius / 15 cm).
SNAP_M = 0.01  # Measure snaps to a vertex of the touched face within 1 cm.
STAMP_DEPTH_M = 0.005  # Stamp presses 5 mm at full pressure and strength.
STAMP_KINDS = {"CUBE": "SQUARE", "PLANE": "SQUARE", "SPHERE": "CIRCLE", "CYLINDER": "CIRCLE", "CONE": "CIRCLE",
               "TORUS": "RING"}


def optional(name):
    """A sibling module that may not be there (contact, feel, contact_tools), else None."""
    if name not in _MODS:
        try:
            _MODS[name] = importlib.import_module("." + name, __package__)
        except Exception:  # ImportError, or a half-built module: tools must keep working.
            _MODS[name] = None
    return _MODS[name]


def touch_enabled() -> bool:
    try:
        from . import onboarding
        return bool(onboarding.prefs().get("touch_tools", True))
    except Exception:
        return True


def current_contact(hand):
    """The hand's Contact when contact mode is on and its tip touches a mesh, else None."""
    contact = optional("contact")
    if contact is None or not touch_enabled():
        return None
    try:
        found = contact.current(hand)
    except Exception:
        return None
    return found if found is not None and found.touching and found.obj_name else None


def feel_event(hand, kind, strength=1.0, at=None, normal=None):
    feel = optional("feel")
    if feel is not None:
        try:
            feel.event(bpy.context, hand, kind, strength, at, normal)
        except Exception:
            pass


def feel_call(name, *args):
    """feel.preview / feel.chips / feel.flash, never raising."""
    feel = optional("feel")
    if feel is not None:
        try:
            getattr(feel, name)(*args)
        except Exception:
            pass


def object_colour(obj):
    """Crumb colour: the object's active material colour, else the theme's muted colour."""
    try:
        return tuple(obj.active_material.diffuse_color)
    except (AttributeError, ReferenceError):
        from . import polish
        return tuple(polish.MUTED)


def bracket_lines(obj):
    """LINES points of short corner brackets around an object (polish.corner_brackets), for feel.flash."""
    from . import polish
    return polish.corner_brackets(obj.matrix_world, [tuple(corner) for corner in obj.bound_box])


def ray_object(context, obj, origin, direction, distance):
    """(world location, world normal, face index) where a ray meets obj, else None.

    Goes through the contact engine's cached BVH; without it, falls back to Object.ray_cast on the evaluated
    mesh (no view_layer.update, so it never sees a mesh that changed since the last redraw).
    """
    contact = optional("contact")
    if contact is not None:
        try:
            found = contact.ray_cast(obj, origin, direction, distance)
        except Exception:
            found = False
        if found is not False:
            return None if found is None else (Vector(found[0]), Vector(found[1]), int(found[2]))
    inverse = obj.matrix_world.inverted()
    hit, location, normal, index = obj.ray_cast(
        inverse @ Vector(origin), (inverse.to_3x3() @ Vector(direction)).normalized(),
        depsgraph=context.evaluated_depsgraph_get())
    if not hit:
        return None
    point = obj.matrix_world @ location
    if (point - Vector(origin)).length > distance:
        return None
    return point, (inverse.transposed().to_3x3() @ normal).normalized(), index


def aim_hit(context, pose, hand, touching, last=None, distance=None):
    """(object, world point, world normal, face index) under the tip (contact) or the ray, else None.

    Order: the hand's contact; the last object again through the cached BVH when it is dense (no
    view_layer.update per sample); the scene ray cast.
    """
    units = viewer_scale.world_units_per_metre(context)
    origin, direction, _rotation = _pose_ray(pose)
    distance = (50.0 if distance is None else distance) * units
    if touching:
        found = current_contact(hand)
        obj = bpy.data.objects.get(found.obj_name) if found is not None else None
        if obj is not None:
            return obj, Vector(found.point), Vector(found.normal), int(found.face_index)
    if last is not None and last.type == "MESH" and len(last.data.vertices) > DENSE_RAY_VERTICES:
        # ponytail: a dense target stays the target while the ray still meets it; aiming past it to a nearer
        # object paints/picks the dense one. Upgrade: compare against a cached scene ray cast every 100 ms.
        try:
            found = ray_object(context, last, origin, direction, distance)
        except ReferenceError:
            found = None
        if found is not None:
            return last, found[0], found[1], found[2]
    context.view_layer.update()
    hit, location, normal, index, evaluated, _matrix = context.scene.ray_cast(
        context.evaluated_depsgraph_get(), origin, direction, distance=distance)
    if not hit or evaluated is None:
        return None
    return evaluated.original, Vector(location), Vector(normal), index


def contact_frame(point, normal, dimensions, yaw_angle, units, align=True):
    """Where a placed shape rests on a contact: (location, rotation).

    align: the shape's local Z follows the surface normal (yaw kept); else it stays upright and only
    its yaw is used (it is lifted out by its biggest half extent, as `placement` does on a wall).
    """
    n = Vector(normal).normalized()
    half = Vector(dimensions) * 0.5 + Vector((1.0, 1.0, 1.0)) * RESTING_GAP_M * units
    yaw = Quaternion((0.0, 0.0, 1.0), yaw_angle)
    if align:
        return Vector(point) + n * half.z, Vector((0.0, 0.0, 1.0)).rotation_difference(n) @ yaw
    return Vector(point) + n * max(half.x, half.y, half.z), yaw


def hand_yaw(pose):
    _origin, direction, _rotation = _pose_ray(pose)
    return math.atan2(-direction.x, direction.y)


def rest_contact(stroke, own_names):
    """(point, normal) a placement stroke rests on, or None to use the ray rule.

    The stroke's own shape sits under the tip and would be touched too: while the contact is that shape,
    the last real surface is kept so the shape does not climb on itself.
    """
    if not stroke.contact_mode:
        return None
    found = current_contact(stroke.hand)
    if found is not None and found.obj_name not in own_names:
        stroke._rest = (Vector(found.point), Vector(found.normal))
        return stroke._rest
    return getattr(stroke, "_rest", None) if found is not None else None


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
    contact_mode = False  # Set by begin(): the stroke started with the tip touching a surface.
    feel_commit = True  # The controller plays feel.event("commit") on release unless a stroke does its own.
    flashed = False  # True once the stroke flashed its result itself (the controller then skips feel.flash).

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

    @property
    def hand(self):
        return self.request.owner.identifier

    def flash(self, *objects):
        """Commit flash: brackets around each result object (contact engine's feel module)."""
        for obj in objects:
            feel_call("flash", None, bracket_lines(obj))
        self.flashed = True

    def _fx(self, kind, strength=1.0, at=None, normal=None):
        feel_event(self.hand, kind, strength, at, normal)

    def _preview(self, kind, **data):
        feel_call("preview", self.hand, kind, data)

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

    def __init__(self, context, obj, hit, normal, pose, tool, owner, generation, units, settings, label=None):
        super().__init__(context, pose, owner, generation, units)
        self.label = LABELS.get(tool) or label or tool
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
        self.clock = time.monotonic()  # Last primary sample, for press-to-build and rub speed.
        self.chip_path = 0.0
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
        self.start_hit = np.array(hit, dtype=np.float64)
        if tool == "sculpt_grab":
            self._grab_sets(self.primary, hit)
            if not any(len(s[0]) for s in self.primary.sets):
                raise editing.EditRejected("No vertices under the brush; shrink yourself or enlarge the brush")
        # The cached BVH is stale while this stroke edits the mesh: the contact engine asks us instead.
        self._probe_name = obj.name
        contact = optional("contact")
        if contact is not None:
            try:
                contact.set_live_probe(self._probe_name, self.probe)
            except Exception:
                self._probe_name = None

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

    def probe(self, tip, radius):
        """Live surface for the contact engine: (point, normal, signed distance) or None.

        ponytail: nearest current vertex and the last surface normal, not the nearest triangle; fine at
        sculpt densities. Upgrade: a triangle test over the candidates' faces.
        """
        ids = self.chunks.candidates(np.asarray(tip, dtype=np.float64), radius + REACH_M * self.units)
        if ids.size == 0:
            return None
        offset = self.current[ids] - tip
        point = self.current[ids[int(np.argmin(np.einsum("ij,ij->i", offset, offset)))]]
        return point, self.normal, float(np.dot(np.asarray(tip) - point, self.normal))

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
        if getattr(self, "_probe_name", None):
            contact = optional("contact")
            if contact is not None:
                try:
                    contact.clear_live_probe(self._probe_name)
                except Exception:
                    pass
            self._probe_name = None
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

    def _surface_hit(self, context, pose, hand=None):
        hand = hand or self.hand
        if self.contact_mode and touch_enabled():
            # Contact mode: the tip's own surface point, no ray cast. A lifted primary brush is off the surface.
            contact = optional("contact")
            try:
                found = contact.surface_for(hand, self.obj) if contact is not None else None
            except Exception:
                found = None
            if found is not None:
                return np.array(found[0], dtype=np.float64), np.array(found[1], dtype=np.float64)
            if hand == self.hand:
                return None
        origin, direction, _rotation = _pose_ray(pose)
        inverse = self.obj.matrix_world.inverted()
        if len(self.current) > DENSE_RAY_VERTICES:
            # ponytail: dense meshes ray cast against the surface as it was when the stroke began
            # (one BVH, microseconds per sample); a live ray cast re-evaluates the whole mesh
            # (~130 ms at 164k vertices). Small meshes keep the live surface. Upgrade: refit the BVH.
            if self.bvh is None:
                contact = optional("contact")
                try:
                    self.bvh = contact.bvh_for(context, self.obj) if contact is not None else None  # Cached, local space.
                except Exception:
                    self.bvh = None
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
            surface = self._surface_hit(context, sample.pose, hand=owner.identifier)
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
        if primary:  # A line from the grab point to the hand shows the tension.
            self._preview("tape", start=tuple(self.start_hit), end=tuple(position))
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
        now = time.monotonic()
        dt = min(0.1, max(1.0e-3, now - self.clock)) if primary else 0.0
        if primary:
            self.clock = now
        touch = current_contact(self.hand) if primary and self.contact_mode else None
        pressed = touch is not None and touch.pressed
        # Press-to-build: Clay and Inflate pushed into the surface and held still keep raising it.
        build = pressed and tool in {"sculpt_clay", "sculpt_inflate"}
        if step <= 1.0e-9 and tool != "sculpt_layer" and not build:
            return "Move the hand to sculpt"
        normal = self.normal
        if tool == "sculpt_snake":
            delta = sculpt_math.clamp_delta(delta, self.radius)
            step = float(np.linalg.norm(delta))
            centre = source.centre
        else:
            surface = self._surface_hit(context, pose, hand=None if primary else source.owner.identifier)
            if surface is None:
                return "Brush is off the surface"
            centre, normal = surface
            self.normal = normal
        if build:
            step = max(step, sculpt_math.press_floor(dt, self.units))
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
                # Pressed in contact mode the paddle's own plane (through the contact point) is the target.
                factor = min(1.0, 2.0 * step / self.radius) * force
                plane = c if pressed else np.average(pts, axis=0, weights=w)
                pts -= (w * factor * ((pts - plane) @ n))[:, None] * n
            elif tool == "sculpt_smooth":
                average = sculpt_math.smooth_average(self.current, ids, self.edges)
                rub = 1.0
                if touch is not None and dt > 0.0:  # Rubbing faster smooths more.
                    rub = sculpt_math.smooth_speed_factor(step / dt, 0.3 * self.units)
                pts += (w * (min(1.0, 2.0 * step / self.radius) * force * rub))[:, None] * (average - pts)
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
        if primary:
            self._feedback(centre, normal, step, force)
        return f"{self.label}: {int(sum(len(i) for i in moved))} vertices{self.rate_note}"

    def _feedback(self, centre, normal, step, force):
        """Live effect preview and crumbs for the contact engine's feel module (no-ops without it)."""
        tool = self.tool
        if tool in {"sculpt_clay", "sculpt_inflate"}:
            self._preview("bowl", point=tuple(centre), normal=tuple(normal), radius=self.radius,
                          step=force * self.radius * 0.25 * self.sign)
        elif tool == "sculpt_layer":
            self._preview("plateau", point=tuple(centre), normal=tuple(normal), radius=self.radius,
                          height=self.height)
        elif tool == "sculpt_snake":
            self._preview("tape", start=tuple(self.start_hit), end=tuple(centre))  # Start to the hook.
        elif tool == "sculpt_crease":
            self.chip_path += step
            if self.chip_path >= 0.01 * self.units:  # 2-4 crumbs per 10 mm of path.
                self.chip_path = 0.0
                feel_call("chips", tuple(centre), tuple(normal), 3, object_colour(self.obj), self.units)

    def commit_message(self):
        extra = f", detail +{self.refined:,} vertices" if self.refined else ""
        return f"{self.label} committed as one undo step{extra}"


class StampStroke(SculptStroke):
    """Place tool in STAMP mode: press the shape's footprint into a mesh (Dig: emboss it).

    Vertices inside the footprint sink along the first contact normal by pressure x 5 mm x strength, softened
    over the first tenth of the stamp's size at its edge; the deepest push per vertex wins, as Layer does.
    ponytail: ignores mirror and radial symmetry, and a curved surface gets a flat-bottomed press along
    the first normal. Upgrade: press along each vertex's own normal.
    """

    def __init__(self, context, obj, hit, normal, pose, kind, owner, generation, units, settings):
        super().__init__(context, obj, hit, normal, pose, "stamp", owner, generation, units, settings,
                         label=f"Stamp {PLACE_SHAPES[kind][0]}")
        label, _icon, size = PLACE_SHAPES[kind]
        self.footprint = STAMP_KINDS.get(kind, "SQUARE")
        self.half = (0.5 * size[0] * units, 0.5 * size[1] * units)
        # Torus: the ring's inner radius as a share of the outer (major - minor over major + minor).
        self.inner = max(0.0, (size[0] - 2.0 * size[2]) / size[0]) if kind == "TORUS" else 0.0
        self.size_m = max(size[0], size[1]) * units
        self.press_n = np.array(normal, dtype=np.float64)
        self.press_n /= max(np.linalg.norm(self.press_n), 1.0e-12)
        hand_x = np.array(Quaternion(pose.rotation).normalized() @ Vector((1.0, 0.0, 0.0)))
        u = hand_x - (hand_x @ self.press_n) * self.press_n
        if np.linalg.norm(u) < 1.0e-6:
            u = np.cross(self.press_n, (0.0, 0.0, 1.0) if abs(self.press_n[2]) < 0.9 else (1.0, 0.0, 0.0))
        self.axis_u = u / np.linalg.norm(u)
        self.axis_v = np.cross(self.press_n, self.axis_u)
        self.disp = np.zeros(len(self.current))
        self.reach = float(np.hypot(*self.half)) + 0.01 * units

    def _run(self, context, source, pose):
        primary = source is self.primary
        surface = self._surface_hit(context, pose, hand=None if primary else source.owner.identifier)
        if surface is None:
            return "Stamp is off the surface"
        centre, self.normal = surface
        ids = self.chunks.candidates(centre, self.reach)
        if ids.size == 0:
            return "No vertices under the stamp"
        offset = self.start_world[ids] - centre
        xy = np.stack((offset @ self.axis_u, offset @ self.axis_v), axis=1)
        inside = sculpt_math.stamp_footprint(self.footprint, xy, self.half, self.inner)
        keep = (inside >= 0.0) & (np.abs(offset @ self.press_n) < self.reach)
        if not keep.any():
            return "No vertices under the stamp"
        ids, edge = ids[keep], inside[keep]
        weight = sculpt_math.edge_soft(edge / (0.1 * self.size_m))
        if self.mask is not None:
            weight = weight * (1.0 - self.mask[ids])
        # Pressing in moves vertices against the normal; Dig (invert) embosses outward.
        push = -self.sign * weight * STAMP_DEPTH_M * self.units * self.strength * self.pressure
        deeper = np.abs(push) > np.abs(self.disp[ids])
        self.disp[ids[deeper]] = push[deeper]
        self.current[ids] = self.start_world[ids] + self.disp[ids, None] * self.press_n
        self.touched[ids] = True
        self.chunks.refresh(self.current, ids)
        self._write_due(ids)
        self.changed = True
        if primary:
            # ponytail: the footprint shows as a round ring of its outer radius, whatever the shape.
            self._preview("plateau", point=tuple(centre), normal=tuple(self.press_n), radius=max(self.half),
                          height=0.0)
        return f"{self.label}: {len(ids)} vertices{self.rate_note}"


class MaskExtractStroke(_Stroke):
    """Mask Extract as a tool: hold on a masked mesh and pull the hand away along the surface normal to set the
    shell's thickness (1 cm of hand = 1 mm of shell, 0.5-50 mm, a detent per mm). Release makes the shell."""

    label = "Mask Extract"

    def __init__(self, context, pose, obj, normal, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        require_mesh_target(context, obj, "Mask Extract")
        if is_swatch(obj):
            raise editing.EditRejected("Material blocks are not extracted")
        if read_mask(obj.data) is None:
            raise editing.EditRejected("Paint a mask first")
        self.obj = obj
        self.normal = Vector(normal).normalized()
        self.start_mm = float(_opt(settings, "mask_extract_m", 0.004)) * 1000.0
        self.mm = sculpt_math.extract_thickness_mm(self.start_mm, 0.0)
        self.selection = editing._selection_snapshot(context)
        self.new_obj = None
        self.pressure = 1.0

    def _valid(self, context):
        try:
            return super()._valid(context) and self.obj.name in context.view_layer.objects
        except ReferenceError:
            return False

    def _restore(self, context):
        if self.new_obj is None:
            return
        try:
            mesh = self.new_obj.data
            bpy.data.objects.remove(self.new_obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        except ReferenceError:
            pass
        self.new_obj = None
        editing._restore_object_selection(context, self.selection)

    def _apply(self, context, pose):
        along = (Vector(pose.position) - self.anchor).dot(self.normal) / self.units
        mm = sculpt_math.extract_thickness_mm(self.start_mm, along)
        if mm != self.mm:
            self.mm = mm
            self._fx("detent")
            self._fx("click")
        self.changed = True
        self._preview("plateau", point=tuple(self.anchor), normal=tuple(self.normal), radius=0.03 * self.units,
                      height=self.mm / 1000.0 * self.units)  # The ring floats at the shell thickness.
        return f"Shell {self.mm:g} mm: release to extract"

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if (self.changed and editing.active_transaction() is self and not self.finished
                and self.new_obj is None
                and self.capture.can_finish(owner or self.request.owner).decision is Decision.APPLY
                and self._valid(context)):
            try:
                self.new_obj = extract_masked(context, self.obj, self.mm / 1000.0)
            except (editing.EditRejected, RuntimeError, ValueError) as exc:
                self._restore(context)
                self._finish(CapturePhase.CANCELLED)
                return EditResult(Decision.REJECTED, str(exc))
        result = super().commit(context, owner)
        if self.new_obj is not None and self.finished and result.decision is Decision.APPLY:
            self.flash(self.new_obj)
        return result

    def commit_message(self):
        return f"Extracted {len(self.new_obj.data.polygons):,} faces into {self.new_obj.name}"


class SprayStroke(_Stroke):
    """Paint the chosen material onto faces under the spray cone."""

    label = "Spray Material"

    def __init__(self, context, pose, material, owner, generation, units, settings):
        super().__init__(context, pose, owner, generation, units)
        self.material = material
        self.radius = max(1.0e-6, float(settings.spray_radius_m) * units)
        self.pressure = 1.0  # A harder squeeze widens the spray (50-100% of Spray Size).
        self.targets = {}
        self.last_obj = None
        self.sprayed = 0

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
        if not len(mesh.polygons):
            self.targets[key] = None
            return None
        indices = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", indices)
        centres = np.empty(len(mesh.polygons) * 3, dtype=np.float32)
        mesh.polygons.foreach_get("center", centres)
        world = _matrix(obj)
        centres = centres.reshape(-1, 3).astype(np.float64) @ world[:3, :3].T + world[:3, 3]
        record = {"obj": obj, "mesh": mesh, "original": indices, "current": indices.copy(),
                  "centres": centres, "chunks": sculpt_math.Chunks(centres),
                  "slots": len(mesh.materials), "slot": None,
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

    def _spots(self, context, obj, origin, direction, aim_point, aim_index, touch):
        """(world point, face index, distance from the nozzle) for the aim, the contact and the cone's rays."""
        radius = self.radius * (0.5 + 0.5 * self.pressure)
        half = math.atan(radius / (SPRAY_NOZZLE_M * self.units))
        spots = [(aim_point, aim_index, (aim_point - origin).length)]
        if touch is not None and touch.obj_name == obj.name:
            spots.append((Vector(touch.point), int(touch.face_index), max(0.0, -touch.depth_m) * self.units))
        self.frame = getattr(self, "frame", 0) + 1
        far = 50.0 * self.units
        # A pressed nozzle is inside the surface: cast from 3 cm behind it so the cone sees the surface ahead.
        back = REACH_M * self.units if touch is not None else 0.0
        start = origin - Vector(direction) * back
        for ray in sculpt_math.cone_directions(tuple(direction), half, SPRAY_RAYS, self.frame * 0.7):
            found = ray_object(context, obj, start, Vector(ray), far)
            if found is not None:
                spots.append((found[0], found[2], abs((found[0] - start).length - back)))
        return spots, half

    def paint_faces(self, record, spots, half):
        """Face mask for the spray: each spot's own face plus faces whose centres are within a
        fraction of the cone's cross-section there. One chunked gather over the whole cone footprint,
        then a squared-distance test per spot (no sqrt, no full scan)."""
        faces = np.zeros(len(record["centres"]), dtype=bool)
        minimum = SPRAY_MIN_DISTANCE_M * self.units
        points = np.array([tuple(point) for point, _i, _d in spots], dtype=np.float64)
        spot = np.array([SPOT_FRACTION * sculpt_math.spray_spot(distance, half, minimum)
                         for _p, _i, distance in spots])
        if record["exact_faces"]:
            for _point, index, _distance in spots:
                if 0 <= index < len(faces):
                    faces[index] = True
        middle = points.mean(axis=0)
        reach = float((np.linalg.norm(points - middle, axis=1) + spot).max())
        for start, stop in record["chunks"].spans(middle, reach):
            centres = record["centres"][start:stop]  # A contiguous slice: no gather.
            offset = centres - middle  # Chunks are boxes: trim to the footprint disc before the per-spot tests.
            disc = np.flatnonzero(np.einsum("ij,ij->i", offset, offset) <= reach * reach)
            if not disc.size:
                continue
            centres = centres[disc]
            near = np.zeros(len(disc), dtype=bool)
            for at, radius in zip(points, spot):
                offset = centres - at
                near |= np.einsum("ij,ij->i", offset, offset) <= radius * radius
            faces[disc[near] + start] = True
        return faces

    def _apply(self, context, pose):
        origin, direction, _rotation = _pose_ray(pose)
        aim = aim_hit(context, pose, self.hand, self.contact_mode, last=self.last_obj)
        if aim is None:
            return "Aim the spray at a mesh"
        obj, location, normal, index = aim
        if is_swatch(obj):
            return "Spray passes over material blocks"
        record = self._record(context, obj)
        if record is None:
            return f"{obj.name} cannot be sprayed (linked, shared or object-linked materials)"
        self.last_obj = obj
        touch = current_contact(self.hand) if self.contact_mode else None
        spots, half = self._spots(context, obj, origin, direction, location, index, touch)
        faces = self.paint_faces(record, spots, half)
        reach = (location - origin).length
        self._preview("cone", nozzle=tuple(origin), point=tuple(location), normal=tuple(normal),
                      radius=sculpt_math.spray_spot(reach, half, SPRAY_MIN_DISTANCE_M * self.units))
        feel_call("chips", tuple(location), tuple(normal), 1, tuple(self.material.diffuse_color), self.units,
                  None, True)  # One tiny mist crumb per sample.
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
        self.last_tick = 0.0

    def _restore(self, context):
        self.scene.frame_set(self.start_frame)

    def _apply(self, context, pose):
        metres = (Vector(pose.position) - self.anchor).dot(self.axis) / self.units
        frame = max(0, self.start_frame + int(round(metres * self.frames_per_metre)))
        if frame != self.scene.frame_current:
            self.scene.frame_set(frame)
            now = time.monotonic()
            if now - self.last_tick >= 0.03:  # A haptic tick per frame crossed, at most every 30 ms.
                self.last_tick = now
                self._fx("detent")
            if frame % 10 == 0:
                self._fx("click")
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
    feel_event(hand, "click")

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
            if card.get("ok"):  # Thunk and brackets on the new object; otherwise the quiet reject click.
                feel_event(hand, "thunk")
                made = bpy.data.objects.get(card.get("name", ""))
                if made is not None:
                    feel_call("flash", None, bracket_lines(made))
            else:
                feel_event(hand, "reject")
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

    def __init__(self, context, pose, kind, owner, generation, units, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        if contact is not None:
            self._rest = (Vector(contact.point), Vector(contact.normal))
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
        rest = rest_contact(self, {self.obj.name})
        if rest is not None:  # Touching: rest on the contact point, local Z along the surface normal.
            location, rotation = contact_frame(rest[0], rest[1], self.dimensions, hand_yaw(pose), self.units)
            self.obj.matrix_world = Matrix.Translation(location) @ rotation.to_matrix().to_4x4()
            self._preview("plateau", point=tuple(rest[0]), normal=tuple(rest[1]),
                          radius=0.5 * max(self.dimensions), height=0.0)  # Outline of the shape on the surface.
            return
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

    def commit(self, context, owner=None):
        result = super().commit(context, owner)
        if self.capture.finished and result.decision is Decision.APPLY and self.changed:
            self.flash(self.obj)  # The drop's thunk is the commit event the controller plays.
        return result


class MeasureStroke(_Stroke):
    """Tape measure: hold from one point to another; nothing in the scene changes."""

    label = "Measure"
    needs_undo = False

    def __init__(self, context, pose, owner, generation, units, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        self.snap = None
        self.start = self._point(context, pose)
        self.end = self.start.copy()
        self.ticks = 0
        self.changed = True  # A measurement is the result, even at zero length.

    def _snapped(self, found):
        """The touched face's nearest vertex within 1 cm (base mesh only), else the contact point."""
        point = Vector(found.point)
        obj = bpy.data.objects.get(found.obj_name)
        if (obj is None or obj.type != "MESH" or any(m.show_viewport for m in obj.modifiers)
                or not 0 <= found.face_index < len(obj.data.polygons)):
            self.snap = None
            return point
        world, best, best_index = obj.matrix_world, SNAP_M * self.units, None
        for index in obj.data.polygons[found.face_index].vertices:
            corner = world @ obj.data.vertices[index].co
            if (corner - point).length <= best:
                best, best_index, point = (corner - point).length, index, corner
        if best_index != self.snap:
            self.snap = best_index
            if best_index is not None:
                self._fx("touch", 0.5)  # A tap where the tape snaps.
        return point

    def _point(self, context, pose):
        """The touched surface point (snapped to a vertex), else where the ray meets a surface, else 20 cm ahead."""
        found = current_contact(self.hand)
        if found is not None:
            return self._snapped(found)
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
            self._fx("click")
            self.ticks = ticks
        # The tape band: from the hand's tape housing out to the start, with the live end.
        self._preview("tape", start=tuple(self.start), end=tuple(self.end), hand=tuple(pose.position))
        return self.text()

    def commit_message(self):
        return "Measured " + self.text()


def scene_target(context, origin, direction, contact):
    """(object, world point, world normal, face index) the stroke starts on: the touched surface in
    contact mode, else what the ray meets. (None, None, None, -1) when there is nothing."""
    if contact is not None:
        obj = bpy.data.objects.get(contact.obj_name)
        if obj is not None:
            return obj, Vector(contact.point), Vector(contact.normal), int(contact.face_index)
    context.view_layer.update()
    hit, location, normal, index, evaluated, _matrix = context.scene.ray_cast(
        context.evaluated_depsgraph_get(), origin, direction)
    if hit and evaluated is not None:
        return evaluated.original, Vector(location), Vector(normal), index
    return None, None, None, -1


def begin(context, pose_origin, pose_rotation, tool="sculpt_clay", owner=None, target_generation=0):
    """Start a physical stroke, or return None after an instant action (dipping a swatch)."""
    editing._require_main_thread()
    contact_tools = optional("contact_tools")
    new_tool = contact_tools is not None and tool in getattr(contact_tools, "TOOLS", ())
    if tool not in TOOLS and not new_tool:
        raise editing.EditRejected("Unknown physical tool")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    pose = Pose(tuple(pose_origin), tuple(pose_rotation))
    rotation = Quaternion(pose.rotation)
    if (not all(math.isfinite(value) for value in Vector(pose.position))
            or not math.isfinite(rotation.magnitude) or rotation.magnitude < 1.0e-8):
        raise editing.EditRejected("Controller pose is outside Blender's finite range")
    if tool not in {"scrub_time", "measure", "eyedropper"} and (not editing.undo_available(context)
                                                  or not bpy.ops.ed.undo_push.poll()):
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode for physical tools")
    owner = owner or CaptureOwner(InputProvenance.CONTROLLER, "/user/hand/right", 0)
    settings = context.window_manager.xrwb_settings
    units = viewer_scale.world_units_per_metre(context)
    # Contact mode: the tip touches a mesh, so the stroke starts at the tip, not along the ray.
    contact = current_contact(owner.identifier) if tool not in {"scrub_time", "lab_dial"} else None
    if new_tool:
        transaction = contact_tools.begin(context, pose, tool, owner, target_generation, units, settings)
        if transaction is not None and editing.active_transaction() is not transaction:
            editing._ACTIVE_TRANSACTION = transaction
        return transaction
    if tool == "lab_dial":
        from . import subdiv_lab
        touched = current_contact(owner.identifier)
        return subdiv_lab.begin(context, pose, owner, target_generation,
                                obj=bpy.data.objects.get(touched.obj_name) if touched is not None else None)
    if tool == "grow_seed":
        from . import growth
        return _contact_begin(growth.begin(context, pose, owner, target_generation, contact=contact), contact)
    if tool == "scrub_time":
        transaction = ScrubStroke(context, pose, owner, target_generation, units, settings)
    elif tool == "uv_island":
        from . import uv_tools
        transaction = uv_tools.begin(context, pose, owner, target_generation, contact=contact)
    elif tool == "measure":
        transaction = MeasureStroke(context, pose, owner, target_generation, units, contact=contact)
    elif tool == "place_prefab":
        from . import prefabs
        transaction = prefabs.PrefabStroke(context, pose, str(settings.prefab_kind), owner, target_generation, units,
                                           contact=contact)
    elif tool == "sdf_blob":
        from . import sdf_modeling
        return _contact_begin(sdf_modeling.begin(context, pose, owner, target_generation, contact=contact), contact)
    elif tool in {"draw_curve", "paint_vertex"}:
        from . import creative_tools
        transaction = creative_tools.begin(context, tool, pose, owner, target_generation, units, settings,
                                           contact=contact)
        if tool == "paint_vertex":
            return transaction  # Already owns capture and painted the first dab.
    elif tool == "place_object" and str(_opt(settings, "place_mode", "PLACE")) == "STAMP":
        origin, direction, _rotation = _pose_ray(pose)
        obj, location, normal, _index = scene_target(context, origin, direction, contact)
        if obj is None:
            raise editing.EditRejected("Point the stamp at a mesh")
        if is_swatch(obj):
            raise editing.EditRejected("Material blocks are not stampable")
        require_mesh_target(context, obj, "Stamp")
        transaction = StampStroke(context, obj, location, normal, pose, str(settings.place_kind), owner,
                                  target_generation, units, settings)
        transaction.contact_mode = contact is not None
    elif tool == "place_object":
        transaction = PlaceStroke(context, pose, str(settings.place_kind), owner, target_generation, units,
                                  contact=contact)
    else:
        origin, direction, _rotation = _pose_ray(pose)
        obj, location, normal, _index = scene_target(context, origin, direction, contact)
        if (tool in DIAL_TOOLS and owner.identifier == off_hand()
                and (obj is None or (Vector(location) - origin).length > DIAL_REACH_M * units)):
            transaction = BrushDialStroke(context, pose, owner, target_generation, units, settings, tool)
            editing._ACTIVE_TRANSACTION = transaction
            return transaction
        if tool == "retopo_loom":
            return _retopo_pull(context, obj, owner.identifier)
        if tool == "eyedropper":
            if obj is None:
                raise editing.EditRejected("Point at something with a material")
            transaction = EyedropStroke(context, pose, obj, _index, owner, target_generation, units,
                                        contact=contact)
            editing._ACTIVE_TRANSACTION = transaction
            return transaction
        if tool == "mask_extract":
            if obj is None:
                raise editing.EditRejected("Aim at a masked mesh")
            transaction = MaskExtractStroke(context, pose, obj, normal, owner, target_generation, units, settings)
            transaction.contact_mode = contact is not None
            editing._ACTIVE_TRANSACTION = transaction
            return transaction
        if tool == "spray_material":
            if is_swatch(obj):
                from . import materials_lab
                name = materials_lab.dip(context, swatch_of(obj))
                return None if name else _raise("That block has no material")
            material = bpy.data.materials.get(settings.spray_material)
            if material is None:
                raise editing.EditRejected("Choose a spray material: trigger a material block or a Paint button")
            transaction = SprayStroke(context, pose, material, owner, target_generation, units, settings)
            transaction.contact_mode = contact is not None
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
    transaction.contact_mode = contact is not None
    editing._ACTIVE_TRANSACTION = transaction
    return transaction


def _contact_begin(transaction, contact):
    """Mark a stroke a sibling module began (and registered) as a contact-mode stroke."""
    if transaction is not None:
        transaction.contact_mode = contact is not None
    return transaction


def material_at(obj, face_index):
    """The material under a hit: a swatch block's own, else the face's slot, else the object's active one."""
    block = swatch_of(obj)
    if block is not None:
        return bpy.data.materials.get(block.get(SWATCH_KEY, ""))
    material = None
    if obj.type == "MESH" and not any(m.show_viewport for m in obj.modifiers) \
            and 0 <= face_index < len(obj.data.polygons):
        slot = obj.data.polygons[face_index].material_index
        if slot < len(obj.material_slots):
            material = obj.material_slots[slot].material
    return material or obj.active_material


class EyedropStroke(_Stroke):
    """Hold to see what the bulb will pick (a swatch at the tip); the bulb squeezes with the trigger;
    release picks. Pointing at nothing when you let go cancels. Settings only: no undo step."""

    label = "Eyedropper"
    needs_undo = False
    feel_commit = False  # It plays its own click.

    def __init__(self, context, pose, obj, index, owner, generation, units, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        self.pressure = 1.0  # The bulb squeezes with the trigger (ray_feedback reads this).
        self.obj, self.index, self.material = obj, index, material_at(obj, index)
        self.changed = True

    def _restore(self, context):
        pass  # Nothing is written until release.

    def _apply(self, context, pose):
        aim = aim_hit(context, pose, self.hand, self.contact_mode, last=self.obj)
        if aim is None:
            self.obj, self.index, material = None, -1, None
        else:
            self.obj, _point, _normal, self.index = aim
            material = material_at(self.obj, self.index)
        if getattr(material, "name", None) != getattr(self.material, "name", None):
            self._fx("detent")  # A tick when the target material changes.
        self.material = material
        if material is not None:
            _origin, direction, _rotation = _pose_ray(pose)
            self._preview("swatch", colour=tuple(material.diffuse_color), point=tuple(pose.position),
                          normal=tuple(-direction), radius=0.008 * self.units)
        return f"Picking {material.name}" if material is not None else "Nothing to pick here"

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, f"{self.label} does not own capture")
        result = self.capture.can_finish(owner or self.request.owner)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
        if result.decision is not Decision.APPLY:
            return result
        if self.obj is None or self.material is None:
            self._finish(CapturePhase.CANCELLED)
            return EditResult(Decision.REJECTED, "Eyedropper cancelled: nothing with a material under the tip")
        try:
            _eyedrop(context, self.obj, self.index)
        except (editing.EditRejected, ReferenceError) as exc:
            self._finish(CapturePhase.CANCELLED)
            return EditResult(Decision.REJECTED, str(exc))
        self._finish(CapturePhase.COMMITTED)
        self._fx("click")
        return EditResult(Decision.APPLY, f"Picked {self.material.name}; spray is loaded")


def _eyedrop(context, obj, face_index):
    """Load the material under the hit into the spray and switch to Spray."""
    from . import interaction_log, materials_lab

    if obj is None:
        raise editing.EditRejected("Point at something with a material")
    if is_swatch(obj):
        materials_lab.dip(context, swatch_of(obj))
    else:
        material = material_at(obj, face_index)
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

