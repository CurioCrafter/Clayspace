"""Contact engine: the prop in your hand touches, rests on and presses into meshes.

Every update finds, per hand, the nearest surface to the prop's collider (a
tip sphere plus an optional capsule shaft), how deep the tip is, how fast it
slides, and how far the drawn prop must move so it never passes through.

Surfaces come from one cached local-space BVH tree per object, rebuilt only
when its geometry changed and a hand is near (section 1.3 of spec 0.21). A
stroke that is changing a mesh registers a live probe so contact follows the
clay as it moves without rebuilding the tree.

The pure helpers at the top use numpy only. bpy and mathutils are imported
inside functions so tests can load this file by path with plain Python.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from time import perf_counter  # Not perf_counter(): 4.5 bundles Python 3.11, where it ticks at 15.6 ms on Windows.

import numpy as np

REACH_M = 0.03          # A tip within 3 cm (physical) of a surface is "touching" for contact mode.
PRESS_FULL_M = 0.02     # Pressing 2 cm past the surface = pressure 1.0.
TAP_SPEED_MPS = 0.15    # Normal speed into the surface that counts as a tap/impact.
MAX_CANDIDATES = 4      # Objects tested per hand per frame (nearest AABBs first).
MAX_SAMPLES = 6
MAX_PUSH_M = 0.10       # A bigger push (hand deep inside a big object) is dropped.
REBUILD_GAP_S = 0.1
DEFAULT_COLLIDER = (0.006, 0.0, "Z", "HARD")
SINK_M = {"HARD": 0.0, "CLAY": 0.006, "SOFT": 0.004, "CUT": 0.0}


@dataclass
class Contact:          # Plain values only (safe to read in XR draw).
    hand: str
    obj_name: str = ""  # "" when not touching
    touching: bool = False
    pressed: bool = False
    point: tuple = (0.0, 0.0, 0.0)
    normal: tuple = (0.0, 0.0, 1.0)
    depth_m: float = -1.0
    pressure: float = 0.0
    speed_mps: float = 0.0
    normal_speed_mps: float = 0.0
    face_index: int = -1
    entered: bool = False
    left: bool = False
    pushed_offset: tuple = (0.0, 0.0, 0.0)
    push_dropped: bool = False   # Hand deep inside: prop drawn at the hand, WARNING brackets.
    feel: str = "HARD"
    radius: float = 0.006        # Collider tip radius, world units.


# ---- pure helpers (no bpy) ----------------------------------------------------

def capsule_samples(tip, axis, shaft, radius):
    """The tip plus points about every ``radius`` back along ``axis`` over ``shaft``, at most 6 (N x 3)."""
    tip = np.asarray(tip, dtype=np.float64)
    if shaft <= 0.0 or radius <= 0.0:
        return tip[None, :]
    count = min(MAX_SAMPLES, 1 + int(math.ceil(shaft / radius - 1e-9)))
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / max(np.linalg.norm(axis), 1e-12)
    return tip + np.linspace(0.0, shaft, count)[:, None] * axis


def depth_pressure(depth_m):
    return min(1.0, max(0.0, float(depth_m) / PRESS_FULL_M))


def tangential_speed(prev, cur, dt, normal, units=1.0):
    """(sliding speed, speed into the surface) in m/s, or None when dt is outside 1..100 ms."""
    if not 0.001 <= dt <= 0.1:
        return None
    v = (np.asarray(cur, dtype=np.float64) - np.asarray(prev, dtype=np.float64)) / dt / units
    n = np.asarray(normal, dtype=np.float64)
    along = float(v @ n)
    return float(np.linalg.norm(v - along * n)), -along


def resolve_push(tip, point, normal, radius, sink_max, cut_through, max_push=MAX_PUSH_M):
    """World vector that puts the sample sphere on the surface, sunk at most ``sink_max``.

    ``tip`` is the deepest collider sample. All lengths are world units (physical
    metres times units). Returns None when the push is over ``max_push`` (dropped).
    """
    n = np.asarray(normal, dtype=np.float64)
    depth = radius - float((np.asarray(tip, dtype=np.float64) - np.asarray(point, dtype=np.float64)) @ n)
    if cut_through or depth <= 0.0:
        return np.zeros(3)
    push = depth - min(depth, sink_max)
    if push > max_push:
        return None
    return n * push


def quat_axis(quat, local=(0.0, 0.0, 1.0)):
    """Rotate a local vector by a (w, x, y, z) quaternion (plain floats: np.cross is slow on 3-vectors)."""
    w, x, y, z = map(float, quat)
    vx, vy, vz = local
    tx, ty, tz = 2.0 * (y * vz - z * vy), 2.0 * (z * vx - x * vz), 2.0 * (x * vy - y * vx)
    return np.array((vx + w * tx + y * tz - z * ty, vy + w * ty + z * tx - x * tz, vz + w * tz + x * ty - y * tx))


def box_gap(lo, hi, point):
    """Distance from a point to each AABB (N), 0 inside."""
    return np.linalg.norm(np.maximum(np.maximum(lo - point, point - hi), 0.0), axis=1)


# ---- state --------------------------------------------------------------------

_trees: dict = {}        # name -> dict(tree, pointer, world, inverse, normal_matrix, polygons, dirty, closed)
_probes: dict = {}       # name -> probe(tip_world, radius) -> (point, normal, signed_distance) | None
_aabb = {"dirty": True, "names": [], "lo": np.empty((0, 3)), "hi": np.empty((0, 3))}
_contacts: dict = {}     # hand -> Contact
_prev: dict = {}         # hand -> (tip np, time)
_sides: dict = {}        # (hand, name) -> +1 / -1 for open meshes
_last_rebuild = [-1.0]
stats = {"builds": 0}


def clear():
    _colliders.clear()
    _trees.clear()
    _probes.clear()
    _contacts.clear()
    _prev.clear()
    _sides.clear()
    _aabb.update(dirty=True, names=[], lo=np.empty((0, 3)), hi=np.empty((0, 3)))
    _last_rebuild[0] = -1.0


def current(hand):
    return _contacts.get(hand)


def surface_for(hand, obj):
    contact = _contacts.get(hand)
    if contact is None or not contact.touching or contact.obj_name != obj.name:
        return None
    return np.array(contact.point), np.array(contact.normal)


def set_live_probe(obj_name, probe):
    _probes[obj_name] = probe


def clear_live_probe(obj_name):
    _probes.pop(obj_name, None)


def on_depsgraph_update(scene, depsgraph):
    """depsgraph_update_post: geometry marks the tree dirty; a transform only refreshes matrices."""
    import bpy
    _aabb["dirty"] = True
    for update in depsgraph.updates:
        obj = update.id
        if not isinstance(obj, bpy.types.Object):
            continue
        entry = _trees.get(obj.name)
        if entry is None:
            continue
        if update.is_updated_geometry:
            entry["dirty"] = True
        if update.is_updated_transform:
            _set_matrices(entry, obj.original.matrix_world)


def _set_matrices(entry, matrix_world):
    world = matrix_world.copy()
    entry["world"] = world
    entry["inverse"] = world.inverted_safe()
    entry["normal_matrix"] = entry["inverse"].transposed().to_3x3()
    # Local find_nearest distance for one world unit (non-uniform scale safe).
    entry["local_per_world"] = max(entry["inverse"].to_3x3().to_scale()) or 1.0


def _closed(mesh):
    """True when every edge has two faces (a watertight mesh: the face normal decides the side)."""
    count = len(mesh.loops)
    if count == 0:
        return False
    edges = np.empty(count, dtype=np.int64)
    mesh.loops.foreach_get("edge_index", edges)
    return bool(np.all(np.bincount(edges, minlength=len(mesh.edges)) == 2))


def _build(context, obj):
    from mathutils.bvhtree import BVHTree
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        closed = _closed(mesh)
        polygons = len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()
    entry = {"tree": BVHTree.FromObject(obj, depsgraph), "pointer": obj.data.as_pointer(),
             "polygons": polygons, "dirty": False, "closed": closed}
    _set_matrices(entry, obj.matrix_world)
    _trees[obj.name] = entry
    _last_rebuild[0] = perf_counter()
    stats["builds"] += 1
    return entry


def _may_rebuild(obj, entry, now):
    from . import editing
    if obj.name in _probes or now - _last_rebuild[0] < REBUILD_GAP_S:
        return False
    # Never mid-stroke: paint and spray strokes have no .obj but rewrite their mesh every sample, which
    # would rebuild its tree every 100 ms. The stroke's own tree is fetched fresh by bvh_for at its start.
    return editing.active_transaction() is None


def _entry(context, obj):
    """The cached tree entry, rebuilt by the rules in spec 1.3. Missing trees build at once."""
    entry = _trees.get(obj.name)
    if entry is None or entry["pointer"] != obj.data.as_pointer():
        return _build(context, obj)
    if entry["dirty"] and _may_rebuild(obj, entry, perf_counter()):
        return _build(context, obj)
    return entry


def bvh_for(context, obj):
    """The cached local-space BVHTree for a stroke's start: rebuilt here if its mesh changed since."""
    entry = _trees.get(obj.name)
    if entry is None or entry["dirty"] or entry["pointer"] != obj.data.as_pointer():
        entry = _build(context, obj)  # A stale tree would put the stroke on the last stroke's surface.
    return entry["tree"]


def ray_cast(obj, origin, direction, distance=1.0e6):
    """World ray cast through the cached tree: (location, normal, face_index, distance) or None."""
    import bpy
    from mathutils import Vector
    entry = _entry(bpy.context, obj)
    inverse = entry["inverse"]
    local_origin = inverse @ Vector(origin)
    local_dir = inverse.to_3x3() @ Vector(direction)
    scale = local_dir.length
    if scale < 1e-12:
        return None
    location, normal, index, local_distance = entry["tree"].ray_cast(
        local_origin, local_dir / scale, distance * scale)
    if location is None:
        return None
    return (entry["world"] @ location, (entry["normal_matrix"] @ normal).normalized(), index,
            local_distance / scale)


def _refresh_aabbs(context):
    from . import ray_feedback
    names, corners = [], []
    for obj in context.view_layer.objects:
        if obj.type != "MESH" or obj.hide_select or not obj.visible_get() or ray_feedback._is_overlay_object(obj):
            continue
        world = np.array(obj.matrix_world)
        local = np.array(obj.bound_box)
        corners.append(local @ world[:3, :3].T + world[:3, 3])
        names.append(obj.name)
    # ponytail: rebuilt on every depsgraph update, O(objects); fine to a few hundred objects.
    if corners:
        stack = np.array(corners)
        _aabb.update(names=names, lo=stack.min(axis=1), hi=stack.max(axis=1))
    else:
        _aabb.update(names=[], lo=np.empty((0, 3)), hi=np.empty((0, 3)))
    _aabb["dirty"] = False


_colliders: dict = {}


def collider_for(tool):
    """(radius_m, shaft_m, axis, feel) of a tool's prop (spec 1.1), with the feel table as override. Cached."""
    cached = _colliders.get(tool)
    if cached is None:
        cached = _colliders[tool] = _collider(tool)
    return cached


def _collider(tool):
    collider = DEFAULT_COLLIDER
    try:
        from . import tool_props
        prop = tool_props.prop_for(tool)
        if prop is not None and hasattr(tool_props, "collider"):
            value = tuple(tool_props.collider(prop))
            collider = value if len(value) == 4 else (value[0], value[1], "Z", value[2])
    except (ImportError, KeyError, OSError, ValueError):
        pass
    try:
        from . import feel
        return collider[:3] + (feel.TOOL_FEEL.get(tool, collider[3]),)
    except ImportError:
        return collider


def _nearest(entry, sample, reach):
    """(world point, world normal, signed raw distance, face) from the cached tree, or None."""
    from mathutils import Vector
    location, normal, index, _d = entry["tree"].find_nearest(
        entry["inverse"] @ Vector(sample), reach * entry["local_per_world"])
    if location is None:
        return None
    point = np.array(entry["world"] @ location)
    n = np.array((entry["normal_matrix"] @ normal).normalized())
    return point, n, float((sample - point) @ n), index


def _hand(context, hand, origin, quat, tool, now, units, held):
    import bpy
    radius_m, shaft_m, axis_name, feel = collider_for(tool)
    radius, shaft = radius_m * units, shaft_m * units
    tip = np.asarray(origin, dtype=np.float64)
    axis = quat_axis(quat, (1.0, 0.0, 0.0) if axis_name == "X" else (0.0, 0.0, 1.0))
    start = tip - axis * shaft * 0.5 if axis_name == "X" else tip
    samples = capsule_samples(start, axis, shaft, max(radius, 1e-9))
    reach = REACH_M * units + radius
    previous = _contacts.get(hand)
    if previous is not None and previous.touching:
        # Hysteresis: keep the surface while the hand sinks deeper, so the prop stays pushed out up to
        # MAX_PUSH_M, then shows at the hand with warning brackets (push_dropped) until twice that deep,
        # instead of popping into the mesh at 3 cm.
        reach = 2.0 * MAX_PUSH_M * units + radius
    contact = Contact(hand=hand, feel=feel, radius=float(radius))
    best = None  # (gap, name, point, normal, face, sample)
    if _aabb["names"]:
        gaps = box_gap(_aabb["lo"], _aabb["hi"], tip)
        near = np.flatnonzero(gaps <= reach + shaft)
        seen = set()
        for k in near[np.argsort(gaps[near])][:MAX_CANDIDATES]:
            name = _aabb["names"][k]
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            seen.add(name)
            probe = _probes.get(name)
            entry = None if probe is not None else _entry(context, obj)
            # ponytail: a live probe (a mesh mid-stroke) is asked about the tip only: ~0.25 ms at 200k
            # vertices against ~1.5 ms for six capsule samples. The shaft can sink into the clay being
            # sculpted. Upgrade: a batched probe that gathers the candidates once for all samples.
            for sample in (tip[None] if probe is not None else samples):
                if probe is not None:
                    found = probe(sample, radius)
                    if found is None:
                        continue
                    point, n, raw = (np.asarray(found[0], dtype=np.float64),
                                     np.asarray(found[1], dtype=np.float64), float(found[2]))
                    face = -1
                    closed = True
                else:
                    found = _nearest(entry, sample, reach)
                    if found is None:
                        continue
                    point, n, raw, face = found
                    closed = entry["closed"]
                if not closed:
                    # ponytail: side memory per hand/object; a tip that tunnels through a thin wall
                    # in one frame reads as the far side.
                    side = _sides.setdefault((hand, name), 1.0 if raw >= 0.0 else -1.0)
                    n, raw = n * side, raw * side
                gap = raw - radius
                if best is None or gap < best[0]:
                    best = (gap, name, point, n, face, sample)
        for key in [key for key in _sides if key[0] == hand and key[1] not in seen]:
            del _sides[key]
    if best is not None and best[0] <= REACH_M * units:
        gap, name, point, n, face, sample = best
        depth_m = -gap / units
        contact.obj_name, contact.touching, contact.pressed = name, True, depth_m > 0.0
        contact.point, contact.normal = tuple(map(float, point)), tuple(map(float, n))
        contact.depth_m, contact.pressure, contact.face_index = float(depth_m), depth_pressure(depth_m), int(face)
        prev = _prev.get(hand)
        if prev is not None:
            speeds = tangential_speed(prev[0], tip, now - prev[1], n, units)
            if speeds is not None:
                contact.speed_mps, contact.normal_speed_mps = speeds
        push = resolve_push(sample, point, n, radius, SINK_M.get(feel, 0.0) * units,
                            feel == "CUT" and held, MAX_PUSH_M * units)
        if push is None:
            contact.push_dropped = True
        else:
            contact.pushed_offset = tuple(map(float, push))
    contact.entered = contact.pressed and not (previous is not None and previous.pressed)
    contact.left = not contact.touching and previous is not None and previous.touching
    _prev[hand] = (tip, now)
    return contact


def update(context, poses, tools, now=None, held=None):
    """{hand: Contact} for this frame. poses {hand: (origin, (w, x, y, z))}, tools {hand: tool id},
    held {hand: trigger held} (CUT tools pass through while held)."""
    from . import viewer_scale
    now = perf_counter() if now is None else now
    if _aabb["dirty"] and poses:  # Touch Tools off passes no poses: skip the O(objects) refresh.
        _refresh_aabbs(context)
    try:
        units = viewer_scale.world_units_per_metre(context)
    except Exception:  # EditRejected and friends: keep the last frame's contacts.
        return dict(_contacts)
    held = held or {}
    for hand in [hand for hand in _contacts if hand not in poses]:
        del _contacts[hand]
        _prev.pop(hand, None)
    for hand, (origin, quat) in poses.items():
        contact = _hand(context, hand, origin, quat, tools.get(hand, ""), now, units, bool(held.get(hand, False)))
        _contacts[hand] = contact
        if contact.entered and contact.normal_speed_mps >= NUDGE_SPEED_MPS:
            _nudge(context, contact, units, now)
    return dict(_contacts)


# ---- optional nudge (pref contact_nudge, off by default) -----------------------

NUDGE_SPEED_MPS = 0.25
NUDGE_MAX_SIDE_M = 0.3
NUDGE_GAP_S = 0.3
_nudged: dict = {}  # name -> time


def nudge_step(normal, normal_speed, units):
    """World translation for a nudge: along -normal, 0.04 s of the impact speed, at most 5 cm."""
    return -np.asarray(normal, dtype=np.float64) * min(0.04 * normal_speed, 0.05) * units


def _nudge(context, contact, units, now):
    # ponytail: translation only, no physics, no resting on the floor.
    import bpy
    from mathutils import Vector
    from . import editing, onboarding
    if not onboarding.prefs().get("contact_nudge", False) or editing.active_transaction() is not None:
        return False
    obj = bpy.data.objects.get(contact.obj_name)
    if obj is None or now - _nudged.get(obj.name, -1.0) < NUDGE_GAP_S:
        return False
    if (obj.parent is not None or obj.constraints or obj.hide_select or obj.library is not None
            or obj.override_library is not None or "xrwb_swatch" in obj.keys()):
        return False
    if obj.animation_data is not None and obj.animation_data.action is not None:
        return False  # ponytail: any action blocks the nudge, not only location curves.
    k = _aabb["names"].index(obj.name) if obj.name in _aabb["names"] else -1
    if k < 0 or float(np.max(_aabb["hi"][k] - _aabb["lo"][k])) > NUDGE_MAX_SIDE_M * units:
        return False
    obj.location += Vector(nudge_step(contact.normal, contact.normal_speed_mps, units))
    editing.push_undo("XR Nudge")
    _nudged[obj.name] = now
    try:
        from . import feel
        feel.event(context, contact.hand, "thunk", min(1.0, contact.normal_speed_mps), contact.point, contact.normal)
        feel.event(context, contact.hand, "impact", contact.normal_speed_mps)
    except ImportError:
        pass
    return True
