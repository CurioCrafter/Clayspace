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
