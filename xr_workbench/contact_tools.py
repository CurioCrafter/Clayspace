"""Hands-on tools that need the tip to touch the mesh: knife, mallet, chisel, rake, sandpaper,
rolling pin, drill and clay gun (spec 0.21 section 4).

Every tool starts only in contact mode (a tip touching a mesh); the Clay Gun also draws in the air.
The vertex tools (mallet, chisel, rake, sandpaper, rolling pin) subclass SculptStroke and work only on
`Chunks.candidates`. Knife, drill and clay gun change topology or add objects once, on release,
inside the same undo step. Contact data comes from `contact.current` / `contact.surface_for`
(contact.py); until that module exists every tool refuses with "Touch the mesh".

This module imports bpy only through physical_tools, so the pure helpers load in plain Python.
"""
from __future__ import annotations

import math
import time

import numpy as np

try:
    from . import editing, physical_tools, sculpt_math
    from .interaction import CapturePhase, Decision, EditResult
    from .physical_tools import SculptStroke as _SculptBase, _Stroke as _StrokeBase
except ImportError:  # plain Python (unit tests): the pure helpers below still work
    editing = physical_tools = sculpt_math = None
    _SculptBase = _StrokeBase = object

TOOLS = {"knife", "mallet", "chisel", "rake", "sandpaper", "rolling_pin", "drill", "clay_gun"}
LABELS = {"knife": "Knife", "mallet": "Mallet", "chisel": "Chisel", "rake": "Rake",
          "sandpaper": "Sandpaper", "rolling_pin": "Rolling Pin", "drill": "Drill", "clay_gun": "Clay Gun"}
HELP = {
    "knife": "Hold the trigger and swipe the blade through the mesh to slice it",
    "mallet": "Hold the trigger and tap the mesh: harder taps dent deeper",
    "chisel": "Press and drag to carve a groove",
    "rake": "Press and drag to comb parallel grooves",
    "sandpaper": "Press and rub to smooth: faster rubbing sands more",
    "rolling_pin": "Press and roll to flatten the bumps under the pin",
    "drill": "Press the bit and hold the trigger: a hole is bored (non-destructive Boolean)",
    "clay_gun": "Hold the trigger to extrude a coil of clay on a surface or in the air",
}
DIAL_TOOLS = {"mallet", "chisel", "rake", "sandpaper", "rolling_pin", "drill"}

TAP_SPEED_MPS = 0.15        # Same value as contact.TAP_SPEED_MPS (kept here so this file stands alone).
MALLET_HEAD_M = 0.035       # Striking-face radius.
MALLET_GAP_S = 0.12
MALLET_MAX_SPEED_MPS = 5.0  # Faster than this is a tracking glitch.
CHISEL_DEPTH_M = 0.006
RAKE_DEPTH_M = 0.003
RAKE_SPACING_M = 0.008
RAKE_TINES = 5
PIN_HALF_M = 0.125
PIN_RADIUS_M = 0.025
DRILL_RATE_MPS = 0.02
DRILL_MIN_M = 0.001
DRILL_SPIN = 30.0           # rad/s while boring (props read `stroke.spin`)
GUN_SPACING_M = 0.004
KNIFE_BLADE_M = 0.16
KNIFE_PAD_M = 0.01
WORKING_VERTICES = 50_000
CUTTERS = "XR Cutters"

_clock = time.monotonic     # Tests replace this to step time.


# -- pure maths (no bpy) --------------------------------------------------------------------

def groove_profile(t, shape="U"):
    """1 at the centre (t = 0), 0 at the edge (|t| >= 1). U is round-bottomed, V is a wedge."""
    t = np.clip(np.abs(np.asarray(t, dtype=np.float64)), 0.0, 1.0)
    return np.sqrt(1.0 - t * t) if shape == "U" else 1.0 - t


def rake_profile(x, spacing, tines=RAKE_TINES):
    """Sharp tines across the head: peaks every `spacing`, `tines` of them, 0 outside the head."""
    x = np.asarray(x, dtype=np.float64)
    inside = np.abs(x) < tines * spacing / 2.0
    return (0.5 * (1.0 + np.cos(2.0 * math.pi * x / spacing))) ** 4 * inside


def mallet_depth(speed):
    """Physical metres of dent for an impact speed in m/s (before strength and scale)."""
    return float(np.clip(0.004 * speed, 0.0005, 0.02))


def drill_step(depth, pressure, dt, limit):
    """Bored depth after dt seconds: 2 cm/s at full pressure, never past `limit`."""
    return min(limit, depth + DRILL_RATE_MPS * max(0.0, pressure) * max(0.0, dt))


def knife_plane(points, blade_dir=None, swipe_dir=None):
    """Best cut plane (centre, unit normal) through swept blade points, or None.

    PCA: the normal is the smallest-variance axis. A nearly collinear sweep (2nd eigenvalue under
    1e-4 of the 1st) uses blade x swipe instead.
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(pts) < 2:
        return None
    centre = pts.mean(axis=0)
    d = pts - centre
    values, vectors = np.linalg.eigh(d.T @ d)
    if values[2] <= 1.0e-18:
        return None
    if values[1] < 1.0e-4 * values[2]:
        if blade_dir is None or swipe_dir is None:
            return None
        normal = np.cross(blade_dir, swipe_dir)
        length = np.linalg.norm(normal)
        if length < 1.0e-9:
            return None
        return centre, normal / length
    return centre, vectors[:, 0]


# -- contact access (stubbed by the smoke; safe before contact.py exists) --------------------

def current_contact(hand):
    try:
        from . import contact
        return contact.current(hand)
    except (ImportError, AttributeError):
        return None


def surface(hand, obj):
    """(point, normal) as numpy arrays on `obj` for this hand, or None."""
    try:
        from . import contact
        found = contact.surface_for(hand, obj)
    except (ImportError, AttributeError):
        return None
    if found is None:
        return None
    return np.asarray(found[0], dtype=np.float64), np.asarray(found[1], dtype=np.float64)


def _event(context, hand, kind, strength=1.0):
    """Haptic + sound through feel.py when it exists. Never raises: feedback must not break a tool."""
    try:
        from . import feel
        feel.event(context, hand, kind, strength)
    except Exception:  # noqa: BLE001 ponytail: feedback is optional, any failure means silence
        pass


def _chips(point, normal, count):
    try:
        from . import feel
        feel.chips(tuple(point), tuple(normal), int(count), None)
    except Exception:  # noqa: BLE001
        pass


def _log(context, message, level="INFO"):
    try:
        from . import interaction_log
        interaction_log.emit("sculpt", message, context, level=level)
    except Exception:  # noqa: BLE001
        pass


def _opt(settings, name, default):
    return getattr(settings, name, default)


def _hand(owner):
    return owner.identifier


def _touching(contact, obj_name=None):
    return bool(contact is not None and getattr(contact, "touching", False)
                and (obj_name is None or contact.obj_name == obj_name))


class _Plain:
    """Result relabel: a stroke that changed nothing says why ("No cut", "No hole", "No coil")."""

    none_message = "No change"

    def commit(self, context, owner=None):
        result = super().commit(context, owner)
        if result.decision is Decision.APPLY and result.message == "No change":
            return EditResult(Decision.APPLY, self.none_message)
        return result


# -- vertex tools ---------------------------------------------------------------------------

class ContactSculptStroke(_Plain, _SculptBase):
    """Mallet, chisel, rake, sandpaper and rolling pin on SculptStroke's capture, chunks, symmetry,
    mask, write throttle, restore and single undo push."""

    def __init__(self, context, obj, contact, pose, tool, owner, generation, units, settings):
        # ponytail: the base class looks its label up in LABELS by tool id, so it is built as
        # Smooth (no tool-specific setup) and renamed here; once SculptStroke takes label=, pass it.
        super().__init__(context, obj, tuple(contact.point), tuple(contact.normal), pose,
                         "sculpt_smooth", owner, generation, units, settings)
        self.tool = tool
        self.label = LABELS[tool]
        self.contact_mode = True
        self.warned = False
        self._adj = None
        count = len(self.current)
        if tool in {"chisel", "rake"}:
            choice = _opt(settings, "sculpt_detail", "AUTO")
            self.detail = choice == "AUTO" or (bool(choice) and choice != "OFF")
            raw = np.empty(count * 3, dtype=np.float32)
            self.mesh.vertex_normals.foreach_get("vector", raw)
            n0 = raw.reshape(-1, 3).astype(np.float64) @ self.inverse[:3, :3]
            self.normals = n0 / np.maximum(np.linalg.norm(n0, axis=1), 1.0e-12)[:, None]
            self.disp = np.zeros(count)
        self.primary.ct_last = np.array(contact.point, dtype=np.float64)
        self.primary.ct_pressed = bool(getattr(contact, "pressed", False))
        self.primary.ct_time = -1.0

    # -- helpers
    def _within(self, centre, radius):
        ids = self.chunks.candidates(centre, radius)
        if ids.size == 0:
            return ids, np.empty(0)
        offset = self.current[ids] - centre
        near = np.einsum("ij,ij->i", offset, offset)
        keep = near < radius * radius
        ids, distance = ids[keep], np.sqrt(near[keep])
        weights = sculpt_math.falloff(distance, radius)
        if self.mask is not None:
            weights = weights * (1.0 - self.mask[ids])
        return ids, weights

    def _frozen(self, ids):
        return 1.0 if self.mask is None else 1.0 - self.mask[ids]

    def _adjacency(self):
        # ponytail: CSR built once at the first sandpaper sample (~100 ms at 600k edges);
        # upgrade: build per chunk on demand.
        if self._adj is None:
            a, b = self.edges[:, 0], self.edges[:, 1]
            src, dst = np.concatenate([a, b]), np.concatenate([b, a])
            order = np.argsort(src, kind="stable")
            degree = np.bincount(src, minlength=len(self.current))
            start = np.concatenate([[0], np.cumsum(degree)[:-1]])
            self._adj = (start, degree, dst[order])
        return self._adj

    def _neighbour_mean(self, ids):
        start, degree, dst = self._adjacency()
        count = degree[ids]
        total = int(count.sum())
        mean = self.current[ids].copy()
        if total == 0:
            return mean
        row = np.repeat(np.arange(len(ids)), count)
        offset = np.arange(total) - np.repeat(np.cumsum(count) - count, count)
        near = dst[np.repeat(start[ids], count) + offset]
        sums = np.stack([np.bincount(row, weights=self.current[near, k], minlength=len(ids))
                         for k in range(3)], axis=1)
        has = count > 0
        mean[has] = sums[has] / count[has, None]
        return mean

    def _warn_detail(self, context, ids, width):
        """Once per stroke: grooves narrower than twice the local edge length come out ragged."""
        self.warned = True
        inside = np.zeros(len(self.current), dtype=bool)
        inside[ids] = True
        pick = inside[self.edges[:, 0]] & inside[self.edges[:, 1]]
        if pick.any():
            a, b = self.edges[pick, 0], self.edges[pick, 1]
            mean = float(np.linalg.norm(self.current[a] - self.current[b], axis=1).mean())
            if mean > width / 2.0:
                _log(context, "Low detail: Remesh for Detail", "WARNING")

    def _boxed(self, ids, positions, centre, half):
        """Cheap cube prefilter of the chunk candidates (they are whole 4096-vertex runs)."""
        if not ids.size:
            return ids
        return ids[(np.abs(positions[ids] - centre) < half).all(axis=1)]

    # -- per-tool brush maths: each returns (ids, new world positions) or None
    def _carve(self, ids, height):
        keep = height > 0.0
        ids, height = ids[keep], height[keep]
        if not ids.size:
            return None
        self.disp[ids] = np.maximum(self.disp[ids], height * self._frozen(ids))
        return ids, self.start_world[ids] - (self.sign * self.disp[ids])[:, None] * self.normals[ids]

    def _chisel(self, context, a, b, n, rot):
        width = 0.5 * self.radius
        depth = self.pressure * CHISEL_DEPTH_M * self.strength * self.units
        length = float(np.linalg.norm(b - a))
        reach = length / 2.0 + width + 2.0 * depth
        mid = (a + b) / 2.0
        ids = self._boxed(self.chunks.candidates(mid, reach), self.start_world, mid, reach)
        if not ids.size:
            return None
        rel = self.start_world[ids] - a
        ab = b - a
        t = np.clip((rel @ ab) / (length * length), 0.0, 1.0)
        d = rel - t[:, None] * ab
        h = d @ n
        lateral = np.linalg.norm(d - h[:, None] * n, axis=1)
        keep = (lateral < width) & (np.abs(h) < width + 2.0 * depth)
        if not self.warned and keep.any():
            self._warn_detail(context, ids[keep], width)
        height = np.where(keep, groove_profile(lateral / width) * depth, 0.0)
        return self._carve(ids, height)

    def _rake(self, context, a, b, n, rot):
        spacing = RAKE_SPACING_M * self.units
        depth = self.pressure * RAKE_DEPTH_M * self.strength * self.units
        half = RAKE_TINES * spacing / 2.0
        ab = b - a
        ab -= (ab @ n) * n
        length = float(np.linalg.norm(ab))
        if length < 1.0e-12:
            return None
        along_dir = ab / length
        xaxis = rot @ np.array((1.0, 0.0, 0.0))
        xaxis = xaxis - (xaxis @ n) * n
        xaxis -= (xaxis @ along_dir) * along_dir
        norm = float(np.linalg.norm(xaxis))
        if norm < 0.3:
            return None  # Dragged along its own tines: no comb.
        xaxis /= norm
        mid = (a + b) / 2.0
        reach = math.hypot(length / 2.0 + spacing, half) + 2.0 * depth + spacing
        ids = self._boxed(self.chunks.candidates(mid, reach), self.start_world, mid, reach)
        if not ids.size:
            return None
        rel = self.start_world[ids] - a
        along, x, h = rel @ along_dir, rel @ xaxis, rel @ n
        profile = rake_profile(x, spacing)
        keep = (along > -spacing / 2.0) & (along < length + spacing / 2.0) & (np.abs(h) < 2.0 * depth + spacing)
        if not self.warned and keep.any():
            self._warn_detail(context, ids[keep], spacing / 2.0)
        return self._carve(ids, np.where(keep, profile * depth, 0.0))

    def _sand(self, context, c, n, speed):
        factor = min(1.0, max(0.0, speed / 0.5)) * self.pressure * self.strength
        if factor < 1.0e-4:
            return None
        ids, w = self._within(c, self.radius)
        keep = w > 0.0
        ids, w = ids[keep], w[keep]
        if not ids.size:
            return None
        pts = self.current[ids]
        pts = pts + (w * factor)[:, None] * (self._neighbour_mean(ids) - pts)
        pts -= (w * 0.2 * factor * ((pts - c) @ n))[:, None] * n
        return ids, pts

    def _roll(self, context, c, n, rot):
        axis = rot @ np.array((1.0, 0.0, 0.0))
        axis = axis - (axis @ n) * n
        norm = float(np.linalg.norm(axis))
        if norm < 0.3:
            return None  # Pin standing on end.
        axis /= norm
        half, radius = PIN_HALF_M * self.units, PIN_RADIUS_M * self.units
        reach = math.hypot(half, 4.0 * radius)
        ids = self._boxed(self.chunks.candidates(c, reach), self.current, c, reach)
        if not ids.size:
            return None
        rel = self.current[ids] - c
        along, h = rel @ axis, rel @ n
        lateral = np.linalg.norm(rel - along[:, None] * axis - h[:, None] * n, axis=1)
        keep = (np.abs(along) < half) & (lateral < radius) & (h > 0.0) & (h < 4.0 * radius)
        ids, h, lateral = ids[keep], h[keep], lateral[keep]
        if not ids.size:
            return None
        w = (1.0 - (lateral / radius) ** 2) * self._frozen(ids)
        return ids, self.current[ids] - (w * h * self.pressure)[:, None] * n

    def _hit(self, context, source, c, n, speed):
        """Mallet impact: a dent along -n, or (Dig) a flatten to the impact plane."""
        radius = MALLET_HEAD_M * self.units * (1.0 + 0.3 * min(1.0, speed))
        depth = mallet_depth(speed) * self.strength * self.units
        ids, w = self._within(c, radius)
        keep = w > 0.0
        ids, w = ids[keep], w[keep]
        if not ids.size:
            return None
        pts = self.current[ids]
        if self.sign < 0:
            pts = pts - (w * ((pts - c) @ n))[:, None] * n
        else:
            pts = pts - (w * depth)[:, None] * n
        return ids, pts

    # -- sample
    def _run(self, context, source, pose):
        owner = source.owner
        hand = _hand(owner)
        tool = self.tool
        found = surface(hand, self.obj)
        contact = current_contact(hand)
        if found is None:
            source.ct_pressed = False
            return f"{self.label} is off the surface"
        point, normal = found
        self.normal = normal
        rotation = np.array(physical_tools.Quaternion(pose.rotation).normalized().to_matrix())
        speed = float(getattr(contact, "speed_mps", 0.0) or 0.0)
        if tool == "mallet":
            was, source.ct_pressed = source.ct_pressed, bool(getattr(contact, "pressed", False))
            fast = float(getattr(contact, "normal_speed_mps", 0.0) or 0.0)
            struck = bool(getattr(contact, "entered", False)) or (source.ct_pressed and not was)
            now = _clock()
            if not (source.ct_pressed and struck and fast >= TAP_SPEED_MPS):
                return "Mallet armed: strike the mesh"
            if fast > MALLET_MAX_SPEED_MPS:
                return "Ignored a tracking jump"
            if now - source.ct_time < MALLET_GAP_S:
                return "Mallet armed: strike the mesh"
            source.ct_time = now
            speed = fast
            _event(context, hand, "impact", min(1.0, 0.3 + fast))
        last = source.ct_last
        if tool in {"chisel", "rake"}:
            if np.linalg.norm(point - last) < 1.0e-3 * self.units:
                return f"Drag the {self.label.lower()} to carve"
            source.ct_last = point
        moved, pending = [], []
        for transform in self.copies:
            rot = transform[:3, :3]
            c, n = (transform @ np.append(point, 1.0))[:3], rot @ normal
            if tool == "chisel":
                out = self._chisel(context, (transform @ np.append(last, 1.0))[:3], c, n, rot)
            elif tool == "rake":
                out = self._rake(context, (transform @ np.append(last, 1.0))[:3], c, n, rot @ rotation)
            elif tool == "sandpaper":
                out = self._sand(context, c, n, speed)
            elif tool == "rolling_pin":
                out = self._roll(context, c, n, rot @ rotation)
            else:
                out = self._hit(context, source, c, n, speed)
            if out is not None:
                pending.append(out)
        for ids, pts in pending:
            self.current[ids] = pts
            self.touched[ids] = True
            moved.append(ids)
        if not moved:
            return f"{self.label}: nothing under the tool"
        ids = np.concatenate(moved)
        self.chunks.refresh(self.current, ids)
        self._write_due(ids)
        self.changed = True
        if tool in {"chisel", "rake"}:
            seg = float(np.linalg.norm(point - last))
            _chips(point, normal, round(seg / (0.01 * self.units) * (3 if tool == "chisel" else RAKE_TINES)))
        return f"{self.label}: {len(ids)} vertices{self.rate_note}"


# -- topology tools -------------------------------------------------------------------------

class _ToolStroke(_Plain, _StrokeBase):
    contact_mode = True

    def __init__(self, context, obj, pose, owner, generation, units):
        super().__init__(context, pose, owner, generation, units)
        self.obj = obj
        self.hand_id = _hand(owner)

    def _inside(self, contact):
        return bool(contact is not None and getattr(contact, "pressed", False)
                    and contact.obj_name == self.obj.name)


def _islands(bm):
    """Face-connected islands as lists of faces (iterative flood fill)."""
    bm.faces.index_update()
    seen = bytearray(len(bm.faces))
    out = []
    for face in bm.faces:
        if seen[face.index]:
            continue
        seen[face.index] = 1
        stack, island = [face], []
        while stack:
            f = stack.pop()
            island.append(f)
            for edge in f.edges:
                for g in edge.link_faces:
                    if not seen[g.index]:
                        seen[g.index] = 1
                        stack.append(g)
        out.append(island)
    return out


class KnifeStroke(_ToolStroke):
    label = "Knife"

    def __init__(self, context, obj, pose, owner, generation, units, settings):
        super().__init__(context, obj, pose, owner, generation, units)
        if obj.data.shape_keys:
            raise editing.EditRejected("The knife cannot cut a mesh with shape keys")
        self.none_message = "No cut"
        self.split = bool(_opt(settings, "knife_split", False))
        self.matrix_pointer = tuple(tuple(row) for row in obj.matrix_world)
        self.vertex_count = len(obj.data.vertices)
        self.tips, self.heels = [], []
        self.backup = None
        self.new_obj = None
        self.new_mesh = None
        self.result_text = "Cut"

    def _valid(self, context):
        try:
            return (super()._valid(context) and self.obj.name in context.view_layer.objects
                    and tuple(tuple(row) for row in self.obj.matrix_world) == self.matrix_pointer)
        except ReferenceError:
            return False

    def plane(self):
        """The fitted cut plane so far (centre, normal) for the preview quad, or None."""
        if len(self.tips) < 2:
            return None
        return knife_plane(np.vstack([self.tips, self.heels]), self._blade(), self.tips[-1] - self.tips[0])

    def _blade(self):
        return np.mean(np.asarray(self.heels) - np.asarray(self.tips), axis=0)

    def _apply(self, context, pose):
        contact = current_contact(self.hand_id)
        if not self._inside(contact):
            return "Swipe the blade through the mesh"
        rotation = physical_tools.Quaternion(pose.rotation).normalized()
        tip = np.array(pose.position, dtype=np.float64)
        heel = tip + np.array(rotation @ physical_tools.Vector((0.0, 0.0, 1.0))) * KNIFE_BLADE_M * self.units
        if not self.tips:
            _event(context, self.hand_id, "snip")
        self.tips.append(tip)
        self.heels.append(heel)
        self.changed = len(self.tips) >= 2
        return f"Knife: {len(self.tips)} samples inside"

    def _restore(self, context):
        if self.backup is not None:
            self.backup.to_mesh(self.obj.data)
            self.obj.data.update()
        self._drop_new()

    def _drop_new(self):
        try:
            import bpy
            if self.new_obj is not None:
                bpy.data.objects.remove(self.new_obj, do_unlink=True)
            if self.new_mesh is not None and self.new_mesh.users == 0:
                bpy.data.meshes.remove(self.new_mesh)
        except ReferenceError:
            pass
        self.new_obj = self.new_mesh = None

    def _finish(self, phase):
        if self.backup is not None:
            self.backup.free()
            self.backup = None
        super()._finish(phase)

    def _before_push(self, context):
        import bmesh
        from mathutils import Vector

        obj, mesh = self.obj, self.obj.data
        if len(mesh.vertices) > WORKING_VERTICES:
            _log(context, "Working...")
        plane = knife_plane(np.vstack([self.tips, self.heels]), self._blade(), self.tips[-1] - self.tips[0])
        if plane is None:
            raise RuntimeError("No cut")
        world = np.array(obj.matrix_world, dtype=np.float64)
        inverse = np.linalg.inv(world)
        centre = (inverse @ np.append(plane[0], 1.0))[:3]
        normal = inverse[:3, :3].T @ plane[1]
        normal /= np.linalg.norm(normal)
        swept = np.vstack([self.tips, self.heels])
        local = (inverse @ np.hstack([swept, np.ones((len(swept), 1))]).T).T[:, :3]
        scale = sum(abs(v) for v in obj.matrix_world.to_scale()) / 3.0
        pad = KNIFE_PAD_M * self.units / max(scale, 1.0e-9)
        lo, hi = local.min(axis=0) - pad, local.max(axis=0) + pad
        # Faces whose bounding box meets the swept box (numpy over loops, no per-face Python).
        polys = len(mesh.polygons)
        if polys == 0:
            raise RuntimeError("No cut")
        flat = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        physical_tools.get_positions(mesh, flat)
        loop_vertex = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", loop_vertex)
        starts = np.empty(polys, dtype=np.int64)
        mesh.polygons.foreach_get("loop_start", starts)
        corners = flat.reshape(-1, 3)[loop_vertex]
        fmin, fmax = np.minimum.reduceat(corners, starts, axis=0), np.maximum.reduceat(corners, starts, axis=0)
        hit = np.flatnonzero(np.all((fmax >= lo) & (fmin <= hi), axis=1))
        bm = bmesh.new()
        spare = None
        try:
            bm.from_mesh(mesh)
            bm.faces.ensure_lookup_table()
            faces = [bm.faces[int(i)] for i in hit]
            if not faces:
                raise RuntimeError("No cut")
            geom = faces + list({e for f in faces for e in f.edges}) + list({v for f in faces for v in f.verts})
            plane_co, plane_no = Vector(centre), Vector(normal)
            result = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=plane_co, plane_no=plane_no, dist=1.0e-5)
            cut = [g for g in result["geom_cut"] if isinstance(g, bmesh.types.BMEdge)]
            if not cut:
                raise RuntimeError("No cut")
            self.result_text = f"Knife cut {len(cut)} edges"
            piece = None
            if self.split:
                spare = bm.copy()
                piece = self._separate(bm, cut, centre, normal)
                if piece is None:
                    bm.free()
                    bm, spare = spare, None
                    self.result_text = "Sliced (no separate piece)"
            self.backup = bmesh.new()
            self.backup.from_mesh(mesh)
            bm.to_mesh(mesh)
            mesh.update()
            if piece is not None:
                self._make_piece(context, piece)
                self.result_text = "Knife cut the mesh in two pieces"
        finally:
            bm.free()
            if spare is not None:
                spare.free()

    def _separate(self, bm, cut, centre, normal):
        """Split along the cut, cap both sides, move the +side islands into a new bmesh. None if no
        separate piece comes out (the slit did not go all the way through)."""
        import bmesh
        from mathutils import Vector

        eps = 1.0e-4
        bmesh.ops.split_edges(bm, edges=cut)
        islands = _islands(bm)
        if len(islands) < 2:
            return None
        co, no = np.asarray(centre), np.asarray(normal)

        def dist(v):
            return float((np.array(v.co) - co) @ no)

        side = []  # (island, mean signed distance) for islands that touch the cut plane
        for island in islands:
            verts = {v for f in island for v in f.verts}
            d = [dist(v) for v in verts]
            if min(abs(x) for x in d) < eps:
                side.append((island, sum(d) / len(d)))
        if not any(m > 0 for _i, m in side) or not any(m < 0 for _i, m in side):
            return None
        rim = [e for island, _m in side for f in island for e in f.edges
               if e.is_boundary and all(abs(dist(v)) < eps for v in e.verts)]
        caps = bmesh.ops.holes_fill(bm, edges=list(set(rim)), sides=0)["faces"] if rim else []
        touched = [f for island, _m in side for f in island] + list(caps)
        bmesh.ops.recalc_face_normals(bm, faces=touched)
        bm.faces.index_update()
        plus = set()
        for island, mean in side:
            if mean > 0:
                plus.update(f.index for f in island)
        for cap in caps:  # A cap belongs with the island whose edge it closes.
            for g in cap.edges[0].link_faces:
                if g is not cap and g.index in plus:
                    plus.add(cap.index)
        piece = bm.copy()
        bm.faces.ensure_lookup_table()
        piece.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in plus], context="FACES")
        bmesh.ops.delete(piece, geom=[f for f in piece.faces if f.index not in plus], context="FACES")
        for b in (bm, piece):
            loose = [v for v in b.verts if not v.link_faces]
            if loose:
                bmesh.ops.delete(b, geom=loose, context="VERTS")
        return piece

    def _make_piece(self, context, piece):
        import bpy

        try:
            source = self.obj
            self.new_mesh = bpy.data.meshes.new(f"{source.data.name} Cut")
            for material in source.data.materials:
                self.new_mesh.materials.append(material)
            piece.to_mesh(self.new_mesh)
            self.new_obj = bpy.data.objects.new(f"{source.name} Cut", self.new_mesh)
            for collection in (source.users_collection or (context.collection,)):
                collection.objects.link(self.new_obj)
            self.new_obj.matrix_world = source.matrix_world.copy()
        finally:
            piece.free()

    def commit_message(self):
        return f"{self.result_text}; one undo step"


class DrillStroke(_ToolStroke):
    label = "Drill"

    def __init__(self, context, obj, contact, pose, owner, generation, units, settings):
        super().__init__(context, obj, pose, owner, generation, units)
        self.none_message = "No hole"
        self.start = physical_tools.Vector(contact.point)
        self.axis = -physical_tools.Vector(contact.normal).normalized()
        self.depth_m = 0.0
        self.limit_m = float(np.linalg.norm(tuple(obj.dimensions))) / units
        self.radius = 0.25 * max(1.0e-6, float(settings.sculpt_radius_m)) * units
        self.spin = 0.0
        self.mark = _clock()
        self.cutter = self.modifier = self.cutter_mesh = None

    def _apply(self, context, pose):
        now = _clock()
        dt, self.mark = min(0.1, max(0.0, now - self.mark)), now
        contact = current_contact(self.hand_id)
        if not self._inside(contact):
            self.spin = 0.0
            return f"Drill: press the bit in ({self.depth_m * 100:.1f} cm)"
        self.depth_m = drill_step(self.depth_m, self.pressure, dt, self.limit_m)
        self.spin = DRILL_SPIN
        self.changed = self.depth_m > 0.0
        return f"Drill: {self.depth_m * 100:.1f} cm deep"

    def _restore(self, context):
        import bpy

        try:
            if self.modifier is not None and self.modifier.name in self.obj.modifiers:
                self.obj.modifiers.remove(self.modifier)
            if self.cutter is not None:
                bpy.data.objects.remove(self.cutter, do_unlink=True)
            if self.cutter_mesh is not None and self.cutter_mesh.users == 0:
                bpy.data.meshes.remove(self.cutter_mesh)
        except (ReferenceError, RuntimeError):
            pass
        self.cutter = self.modifier = self.cutter_mesh = None

    def _before_push(self, context):
        import bmesh
        import bpy
        from mathutils import Matrix

        if self.depth_m < DRILL_MIN_M:
            raise RuntimeError("No hole")
        outside = 0.002 * self.units
        length = self.depth_m * self.units + outside
        mesh = bpy.data.meshes.new("XR Drill Bit")
        bm = bmesh.new()
        try:
            bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=16, radius1=self.radius,
                                  radius2=self.radius, depth=length)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        self.cutter_mesh = mesh
        self.cutter = bpy.data.objects.new("XR Drill Bit", mesh)
        middle = self.start - self.axis * outside + self.axis * (length / 2.0)
        self.cutter.matrix_world = (Matrix.Translation(middle)
                                    @ self.axis.to_track_quat("Z", "Y").to_matrix().to_4x4())
        collection = bpy.data.collections.get(CUTTERS)
        if collection is None:
            collection = bpy.data.collections.new(CUTTERS)
            context.scene.collection.children.link(collection)
        collection.objects.link(self.cutter)
        self.cutter.display_type = "WIRE"
        self.cutter.hide_render = True
        self.cutter["xrwb_cutter"] = self.obj.name  # boolean_tools.CUTTER_KEY
        self.modifier = self.obj.modifiers.new("XR Drill", "BOOLEAN")
        self.modifier.operation = "DIFFERENCE"
        self.modifier.solver = "EXACT"
        self.modifier.object = self.cutter
        try:
            self.cutter.hide_set(True)
        except RuntimeError:
            self.cutter.hide_viewport = True

    def commit_message(self):
        return f"Drilled a {self.depth_m * 100:.1f} cm hole (Boolean modifier, still editable); one undo step"


class ClayGunStroke(_ToolStroke):
    label = "Clay Gun"

    def __init__(self, context, pose, owner, generation, units, settings):
        super().__init__(context, None, pose, owner, generation, units)
        collection = context.collection
        if collection is None or not collection.is_editable or collection.library:
            raise editing.EditRejected("Choose a local editable collection for the new object")
        self.none_message = "No coil"
        self.selection = editing._selection_snapshot(context)
        self.radius = max(1.0e-5, float(settings.pen_radius_m)) * units
        self.spacing = GUN_SPACING_M * units
        self.material_name = str(_opt(settings, "spray_material", ""))
        self.points, self.pressures, self.lines = [], [], []
        self.obj = None
        self.pressure = 1.0
        self._add(pose)

    def _inside(self, contact):  # Clay Gun lays coil on a surface it only touches.
        return _touching(contact)

    def _add(self, pose):
        grow = 0.6 + 0.4 * max(0.0, min(1.0, self.pressure))
        contact = current_contact(self.hand_id)
        if _touching(contact):
            normal = physical_tools.Vector(contact.normal).normalized()
            tip = physical_tools.Vector(contact.point) + normal * (self.radius * grow)
        else:
            tip = physical_tools.Vector(pose.position)
        if self.points and (tip - self.points[-1]).length < self.spacing:
            return False
        self.points.append(tip)
        self.pressures.append(grow)
        if len(self.points) > 1:
            self.lines += [tuple(self.points[-2]), tuple(self.points[-1])]
        return True

    def _apply(self, context, pose):
        if self._add(pose) and len(self.points) == 2:
            _event(context, self.hand_id, "click")
        self.changed = len(self.points) >= 2
        return f"Clay Gun: {len(self.points)} points"

    def _restore(self, context):
        import bpy

        try:
            if self.obj is not None:
                curve = self.obj.data
                bpy.data.objects.remove(self.obj, do_unlink=True)
                if curve.users == 0:
                    bpy.data.curves.remove(curve)
        except ReferenceError:
            pass
        self.obj = None
        editing._restore_object_selection(context, self.selection)

    def _before_push(self, context):
        import bpy
        from . import creative_tools

        self.obj = creative_tools.build_curve(context, self.points, self.pressures, self.radius,
                                              bpy.data.materials.get(self.material_name))
        self.obj.name = self.obj.data.name = "Clay Coil"

    def commit_message(self):
        return f"Extruded a clay coil ({len(self.points)} points); one undo step"


# -- entry ----------------------------------------------------------------------------------

def begin(context, pose, tool, owner, generation, units, settings):
    """Start a contact tool. Returns the transaction, already the active one."""
    if tool not in TOOLS:
        raise editing.EditRejected("Unknown contact tool")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    label = LABELS[tool]
    hand = _hand(owner)
    contact = current_contact(hand)
    touching = _touching(contact)
    if tool in DIAL_TOOLS and hand == physical_tools.off_hand() and not touching:
        transaction = physical_tools.BrushDialStroke(context, pose, owner, generation, units, settings, tool)
        editing._ACTIVE_TRANSACTION = transaction
        return transaction
    if tool == "clay_gun":
        transaction = ClayGunStroke(context, pose, owner, generation, units, settings)
        editing._ACTIVE_TRANSACTION = transaction
        return transaction
    import bpy

    obj = bpy.data.objects.get(contact.obj_name) if touching else None
    if obj is None:
        raise editing.EditRejected(f"Touch the mesh with the {label}")
    if physical_tools.is_swatch(obj):
        raise editing.EditRejected("Material blocks cannot be changed")
    physical_tools.require_mesh_target(context, obj, label)
    if tool == "knife":
        transaction = KnifeStroke(context, obj, pose, owner, generation, units, settings)
    elif tool == "drill":
        transaction = DrillStroke(context, obj, contact, pose, owner, generation, units, settings)
    else:
        transaction = ContactSculptStroke(context, obj, contact, pose, tool, owner, generation, units, settings)
    editing._ACTIVE_TRANSACTION = transaction
    return transaction
