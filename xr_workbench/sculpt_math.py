"""Pure numpy maths for the controller sculpt brushes (no bpy, unit-testable).

Chunked bounding boxes, brush copies for mirror/radial symmetry, and the
Snake Hook / Crease / smooth helpers used by physical_tools.SculptStroke.
"""
from __future__ import annotations

import math

import numpy as np

CHUNK = 4096
MAX_COPIES = 24
MAX_RADIAL = 12


def falloff(distance, radius):
    """Smooth brush falloff: 1 at the centre, 0 at the radius."""
    t = np.clip(distance / radius, 0.0, 1.0)
    return (1.0 - t * t) ** 2


class Chunks:
    """Axis-aligned boxes over runs of CHUNK consecutive vertices.

    ponytail: relies on vertex order locality (icospheres and voxel remesh have it);
    a mesh with random vertex order scans most chunks, which costs what an
    unchunked scan costs. Upgrade: sort vertex order once by Morton code on remesh.
    """

    def __init__(self, points, size=CHUNK):
        self.size = size
        self.count = len(points)
        self.chunks = max(1, -(-self.count // size))
        self.lo = np.empty((self.chunks, 3))
        self.hi = np.empty((self.chunks, 3))
        self.refresh(points, None)

    def refresh(self, points, ids):
        """Rebuild all boxes (ids None) or grow the boxes of chunks that ids moved into.

        Growing only never shrinks a box within a stroke: candidates stay a safe superset and
        the cost follows the number of moved vertices, not the chunk size.
        """
        if ids is None:
            for k in range(self.chunks):
                block = points[k * self.size:(k + 1) * self.size]
                self.lo[k] = block.min(axis=0)
                self.hi[k] = block.max(axis=0)
            return
        ids = np.sort(np.asarray(ids))
        if ids.size == 0:
            return
        chunk = ids // self.size
        starts = np.flatnonzero(np.r_[True, chunk[1:] != chunk[:-1]])
        moved = points[ids]
        keys = chunk[starts]
        self.lo[keys] = np.minimum(self.lo[keys], np.minimum.reduceat(moved, starts, axis=0))
        self.hi[keys] = np.maximum(self.hi[keys], np.maximum.reduceat(moved, starts, axis=0))

    def candidates(self, centre, radius):
        """Sorted vertex indices from chunks whose padded box touches the brush sphere."""
        hit = np.flatnonzero(np.all((self.lo - radius <= centre) & (centre <= self.hi + radius), axis=1))
        if hit.size == 0:
            return np.empty(0, dtype=np.int64)
        ids = (hit[:, None] * self.size + np.arange(self.size)).ravel()
        return ids[ids < self.count]


def _spans(self, centre, radius):
    """(start, stop) index runs of the chunks the sphere touches, adjacent chunks merged.

    Cheaper than `candidates` when the caller can work on contiguous slices (no index array, no gather).
    """
    hit = np.flatnonzero(np.all((self.lo - radius <= centre) & (centre <= self.hi + radius), axis=1))
    spans = []
    for k in hit:
        start, stop = int(k) * self.size, min(self.count, (int(k) + 1) * self.size)
        if spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], stop)
        else:
            spans.append((start, stop))
    return spans


Chunks.spans = _spans


def brush_copies(world, inverse, mirror_x, radial):
    """World-space 4x4 transforms of every brush copy (first is the identity).

    Radial rotates around the object's local Z through its origin; mirror is the
    local X = 0 plane. Both together give 2N copies, capped at MAX_COPIES.
    """
    n = max(1, min(MAX_RADIAL, int(radial or 0)))
    flip = world @ np.diag((-1.0, 1.0, 1.0, 1.0)) @ inverse if mirror_x else None
    copies = []
    for k in range(n):
        a = 2.0 * math.pi * k / n
        rot = np.eye(4)
        rot[:2, :2] = ((math.cos(a), -math.sin(a)), (math.sin(a), math.cos(a)))
        turn = world @ rot @ inverse
        copies.append(turn)
        if flip is not None:
            copies.append(turn @ flip)
    return copies[:MAX_COPIES]


def snake(cur, ids, w, centre, delta, strength, radius):
    """Snake Hook: drag candidates with the brush, pinch them toward its axis."""
    cur[ids] += w[:, None] * delta
    length = float(np.linalg.norm(delta))
    if length > 1.0e-12:
        axis = delta / length
        p = centre - cur[ids]
        p -= (p @ axis)[:, None] * axis
        cur[ids] += (w * 0.3 * strength * min(1.0, length / radius))[:, None] * p


def clamp_delta(delta, radius):
    """A hand jump over 3 radii in one frame (tracking glitch) is limited to 1 radius."""
    length = float(np.linalg.norm(delta))
    return delta * (radius / length) if length > 3.0 * radius else delta


def crease(cur, ids, w, centre, normal, step, strength, sign, radius):
    """Crease: pinch toward the stroke line, then cut (Dig: raise) along the normal."""
    t = centre - cur[ids]
    t -= (t @ normal)[:, None] * normal
    cur[ids] += (w * min(1.0, 2.0 * step / radius) * 0.5 * strength)[:, None] * t
    cur[ids] -= (w * w * step * 0.3 * strength * sign)[:, None] * normal


def smooth_average(points, ids, edges):
    """Mean of edge neighbours for the vertices in ids (others untouched, no full-mesh pass)."""
    n = len(points)
    local = np.full(n, -1, dtype=np.int64)
    local[ids] = np.arange(len(ids))
    a, b = edges[:, 0], edges[:, 1]
    average = points[ids].copy()
    total = np.zeros((len(ids), 3))
    count = np.zeros(len(ids))
    for src, dst in ((a, b), (b, a)):
        pick = local[src] >= 0
        slot = local[src[pick]]
        count += np.bincount(slot, minlength=len(ids))
        for axis in range(3):
            total[:, axis] += np.bincount(slot, weights=points[dst[pick], axis], minlength=len(ids))
    has = count > 0
    average[has] = total[has] / count[has, None]
    return average


# ---- 0.21 touch tools: pure helpers -------------------------------------------------------

PRESS_BUILD_MPS = 0.02  # Clay/Inflate pressed and held still build at 2 cm/s (physical) at full pressure and strength.


def press_floor(dt, units):
    """The `step` floor for Clay/Inflate when the hand holds still while pressed (world units)."""
    return max(0.0, float(dt)) * PRESS_BUILD_MPS * units


def smooth_speed_factor(speed, radius_per_s):
    """Smooth rubs harder the faster you rub: 0.5 at rest up to 1.0 at `radius_per_s` (units per second)."""
    return 0.5 + 0.5 * min(1.0, max(0.0, speed) / max(radius_per_s, 1.0e-12))


def cone_directions(direction, half_angle, count=12, phase=0.0):
    """`count` unit rays spread over a disc inside a cone around `direction` (sunflower pattern).

    `phase` rotates the pattern, so successive frames cover the gaps between rays.
    """
    d = np.asarray(direction, dtype=np.float64)
    d = d / max(np.linalg.norm(d), 1.0e-12)
    helper = np.array((0.0, 0.0, 1.0)) if abs(d[2]) < 0.9 else np.array((1.0, 0.0, 0.0))
    u = np.cross(d, helper)
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    k = np.arange(count)
    angle = half_angle * np.sqrt((k + 0.5) / count)
    turn = k * 2.399963229728653 + phase  # Golden angle.
    side = np.cos(turn)[:, None] * u + np.sin(turn)[:, None] * v
    return np.cos(angle)[:, None] * d + np.sin(angle)[:, None] * side


def spray_spot(distance, half_angle, min_distance):
    """Radius of the cone's cross-section at `distance` (never smaller than at `min_distance`)."""
    return max(distance, min_distance) * math.tan(half_angle)


def edge_soft(t):
    """Smoothstep 0..1: a stamp's wall softens over the first tenth of its size."""
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def stamp_footprint(kind, xy, half, inner=0.0):
    """Signed distance to a stamp footprint's edge, positive inside. `xy` is (n, 2) in the stamp's frame.

    kind: SQUARE (half = (hx, hy)), CIRCLE (radius half[0]) or RING (outer half[0], inner = fraction of it).
    """
    xy = np.asarray(xy, dtype=np.float64)
    if kind == "SQUARE":
        return np.minimum(half[0] - np.abs(xy[:, 0]), half[1] - np.abs(xy[:, 1]))
    radius = np.hypot(xy[:, 0], xy[:, 1])
    if kind == "RING":
        return np.minimum(half[0] - radius, radius - half[0] * inner)
    return half[0] - radius


def extract_thickness_mm(start_mm, along_m):
    """Mask Extract: 1 cm of hand away from the surface adds 1 mm of shell; whole mm, 0.5..50."""
    mm = round(start_mm + along_m * 100.0)
    return max(0.5, min(50.0, float(mm)))


def shortest_path(neighbours, start, goal, max_edges=64, max_visits=6000):
    """Dijkstra over a mesh graph. `neighbours(v)` yields (other vertex, edge id, length).

    Returns the edge ids from start to goal, or None when goal is more than `max_edges` edges away or
    the search ran past `max_visits` vertices (a long jump across the mesh).
    """
    import heapq

    if start == goal:
        return []
    best = {start: 0.0}
    came = {}
    heap = [(0.0, 0, start)]
    visits = 0
    while heap:
        cost, hops, v = heapq.heappop(heap)
        if v == goal:
            path = []
            while v != start:
                v, edge = came[v]
                path.append(edge)
            return path[::-1]
        if cost > best.get(v, float("inf")) or hops >= max_edges:
            continue
        visits += 1
        if visits > max_visits:
            return None
        for other, edge, length in neighbours(v):
            total = cost + length
            if total < best.get(other, float("inf")):
                best[other] = total
                came[other] = (v, edge)
                heapq.heappush(heap, (total, hops + 1, other))
    return None
