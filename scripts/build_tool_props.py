"""Build the hand-held Tool Props into xr_workbench/assets/tool_props.npz.

Run in headless Blender (modelling only, no rendering):

    blender -b --factory-startup --python scripts/build_tool_props.py
    blender -b --factory-startup --python scripts/build_tool_props.py -- --sheet docs/media/tool-props.png

Every prop is real modelling (lathe profiles, swept tubes, bevel, solidify,
wireframe and boolean modifiers applied), triangulated, with smooth-by-angle
normals and a part index per vertex: 0 handle, 1 body, 2 accent (drawn in the
theme's ACCENT). A part may carry a squeeze transform; its per-vertex offset
is stored so the trigger can close a claw or squash a bulb.

Modelling frame: +Y forward (the working end), +Z up, the hand's grip near the
origin. Export frame (prop-local, matches the controller aim pose): -Z
forward, +Y up, so (x, y, z) -> (x, z, -y).

``--sheet`` renders one 512 px Eevee still per prop from the written .npz (a
quick local check) and composes them into one PNG.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "xr_workbench" / "assets" / "tool_props.npz"
MAX_PROP_TRIS = 2000
MAX_TOTAL_TRIS = 40000
SMOOTH_DEG = 40.0

WOOD = (0.56, 0.33, 0.16)
WOOD_DARK = (0.34, 0.19, 0.09)
STEEL = (0.74, 0.76, 0.80)
IRON = (0.30, 0.32, 0.36)
RUBBER = (0.11, 0.11, 0.13)
CLAY = (0.76, 0.40, 0.25)
STONE = (0.58, 0.63, 0.60)
GLASS = (0.82, 0.92, 0.96)
CREAM = (0.93, 0.91, 0.85)
RED = (0.80, 0.18, 0.15)
GOLD = (0.86, 0.68, 0.30)
BEECH = (0.80, 0.60, 0.38)
CORK = (0.72, 0.53, 0.33)
GRIT = (0.24, 0.21, 0.19)

_parts: list = []  # (object, part, squeeze matrix or None)
_spinning: set = set()  # Objects that turn with the prop's spin (the drill's chuck and bit).


# ---- Modelling helpers ---------------------------------------------------------

def _obj(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def add(obj, part, squeeze=None):
    _parts.append((obj, part, squeeze))
    return obj


def spinning(obj):
    _spinning.add(obj.name)
    return obj


def bevel(obj, width, segments=2, angle=30.0):
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(angle)
    mod.use_clamp_overlap = True
    return obj


def lathe(name, profile, segments=16, part=0, axis="Y", centre=(0.0, 0.0, 0.0), squeeze=None, scale=(1, 1, 1),
          smooth=0):
    """Revolve [(radius, along)] around an axis; radius 0 makes a closed pole.

    smooth > 0 runs the profile through a Catmull-Rom curve first (organic shapes).
    """
    if smooth:
        profile = [(max(0.0, p.x), p.y) for p in smooth_path([(r, a, 0.0) for r, a in profile], smooth)]
    bm = bmesh.new()
    rings = []
    for radius, along in profile:
        if radius <= 1e-6:
            rings.append([bm.verts.new((0.0, along, 0.0))])
            continue
        rings.append([bm.verts.new((math.cos(a) * radius, along, math.sin(a) * radius))
                      for a in (i * math.tau / segments for i in range(segments))])
    for ring_a, ring_b in zip(rings, rings[1:]):
        if len(ring_a) == 1 and len(ring_b) == 1:
            continue
        for i in range(segments):
            j = (i + 1) % segments
            if len(ring_a) == 1:
                bm.faces.new((ring_a[0], ring_b[j], ring_b[i]))
            elif len(ring_b) == 1:
                bm.faces.new((ring_a[i], ring_a[j], ring_b[0]))
            else:
                bm.faces.new((ring_a[i], ring_a[j], ring_b[j], ring_b[i]))
    for ring in (rings[0], rings[-1]):  # Flat caps on open ends.
        if len(ring) > 1:
            bm.faces.new(ring)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _obj(name, bm)
    obj.scale = scale
    if axis == "Z":
        obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)  # Model +Y becomes +Z.
    elif axis == "X":
        obj.rotation_euler = (0.0, 0.0, math.radians(-90.0))  # Model +Y becomes +X.
    obj.location = centre
    return add(obj, part, squeeze)


def box(name, size, centre, part, bevel_w=0.002, segments=2, squeeze=None, rotation=(0, 0, 0)):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    obj = _obj(name, bm)
    obj.location = centre
    obj.rotation_euler = rotation
    if bevel_w:
        bevel(obj, bevel_w, segments)
    return add(obj, part, squeeze)


def sphere(name, radius, centre, part, scale=(1, 1, 1), segments=16, rings=8, squeeze=None):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    obj = _obj(name, bm)
    obj.location = centre
    obj.scale = scale
    return add(obj, part, squeeze)


def slab(name, outline, thickness, z, part, bevel_w=0.0008, squeeze=None):
    """A flat plate from an XY outline, `thickness` tall, bottom at z."""
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y, z)) for x, y in outline])
    extruded = bmesh.ops.extrude_face_region(bm, geom=[face])
    bmesh.ops.translate(bm, vec=(0.0, 0.0, thickness),
                        verts=[g for g in extruded["geom"] if isinstance(g, bmesh.types.BMVert)])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _obj(name, bm)
    if bevel_w:
        bevel(obj, bevel_w, 2)
    return add(obj, part, squeeze)


def smooth_path(points, per_segment=6, closed=False):
    """Catmull-Rom through the control points."""
    p = [Vector(c) for c in points]
    n = len(p)
    out = []
    count = n if closed else n - 1
    for i in range(count):
        p0 = p[(i - 1) % n] if closed else p[max(i - 1, 0)]
        p1, p2 = p[i], p[(i + 1) % n]
        p3 = p[(i + 2) % n] if closed else p[min(i + 2, n - 1)]
        for s in range(per_segment):
            t = s / per_segment
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t))
    if not closed:
        out.append(p[-1])
    return out


def sweep(name, path, radius, part, up=(0.0, 0.0, 1.0), sides=10, closed=False, profile=None, squeeze=None):
    """Tube (or any 2D profile) along a path; radius may be a list per point."""
    pts = [Vector(c) for c in path]
    n = len(pts)
    radii = radius if isinstance(radius, (list, tuple)) else [radius] * n
    shape = profile or [(math.cos(a), math.sin(a)) for a in (i * math.tau / sides for i in range(sides))]
    up = Vector(up)
    bm = bmesh.new()
    rings = []
    for i, point in enumerate(pts):
        if closed:
            tangent = pts[(i + 1) % n] - pts[i - 1]
        else:
            tangent = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        tangent.normalize()
        normal = (up - tangent * up.dot(tangent)).normalized()
        binormal = tangent.cross(normal)
        rings.append([bm.verts.new(point + (normal * u + binormal * v) * radii[i]) for u, v in shape])
    m = len(shape)
    for a in range(n if closed else n - 1):
        ra, rb = rings[a], rings[(a + 1) % n]
        for i in range(m):
            j = (i + 1) % m
            bm.faces.new((ra[i], ra[j], rb[j], rb[i]))
    if not closed:
        bm.faces.new(rings[0])
        bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return add(_obj(name, bm), part, squeeze)


def rounded_outline(x0, y0, x1, y1, radius, steps=3):
    corners = ((x1 - radius, y1 - radius, 0.0), (x0 + radius, y1 - radius, 0.5 * math.pi),
               (x0 + radius, y0 + radius, math.pi), (x1 - radius, y0 + radius, 1.5 * math.pi))
    return [(cx + math.cos(start + 0.5 * math.pi * s / steps) * radius,
             cy + math.sin(start + 0.5 * math.pi * s / steps) * radius)
            for cx, cy, start in corners for s in range(steps + 1)]


def about(pivot, matrix):
    pivot = Vector(pivot)
    return Matrix.Translation(pivot) @ matrix @ Matrix.Translation(-pivot)


def tilted(pivot, degrees, build):
    """Build some parts, then lean all of them back about X (faces read from behind and the side)."""
    before = set(bpy.data.objects)
    build()
    bpy.context.view_layer.update()  # matrix_world is stale until the depsgraph runs.
    matrix = about(pivot, Matrix.Rotation(math.radians(degrees), 4, "X"))
    for obj in set(bpy.data.objects) - before:
        obj.matrix_world = matrix @ obj.matrix_world
    return matrix


def wood_handle(back=-0.105, front=0.0, radius=0.0145, part=0, segments=14):
    """A turned file-style handle: rounded butt, a waist, a bulge under the palm."""
    length = front - back
    profile = [(0.0, back), (radius * 0.55, back + 0.002), (radius * 0.85, back + 0.008),
               (radius, back + length * 0.22), (radius * 0.9, back + length * 0.55),
               (radius * 0.96, back + length * 0.85), (radius * 0.8, front)]
    return lathe("Handle", profile, segments, part)


def ferrule(front, back=None, radius=0.0135, segments=16):
    back = front - 0.016 if back is None else back
    profile = [(radius * 0.8, back), (radius, back + 0.002), (radius, front - 0.003),
               (radius * 0.85, front), (radius * 0.55, front)]
    return lathe("Ferrule", profile, segments, 2)


# ---- The props (modelling frame: +Y forward, +Z up, grip at the origin) ------------

def p_clay():
    wood_handle(-0.11, -0.012)
    ferrule(0.012, -0.014)
    loop = smooth_path([(0.006, 0.012, 0), (0.02, 0.035, 0), (0.024, 0.062, 0), (0.0, 0.088, 0),
                        (-0.024, 0.062, 0), (-0.02, 0.035, 0), (-0.006, 0.012, 0)], 5)
    ribbon = [(-1.0, -0.17), (1.0, -0.17), (1.0, 0.17), (-1.0, 0.17)]  # 12 mm tall, 2 mm thick.
    flex = about((0.0, 0.012, 0.0), Matrix.Diagonal((1.05, 1.0 - 0.003 / 0.0765, 1.0, 1.0)))  # 3 mm in.
    sweep("Loop", loop, 0.006, 1, up=(0, 0, 1), profile=ribbon, squeeze=flex)
    return (0.0, 0.0885, 0.0), (WOOD, STEEL)


def p_smooth():
    lathe("Grip", [(0.0, -0.08), (0.009, -0.079), (0.0145, -0.07), (0.016, -0.045), (0.013, -0.02),
                   (0.0115, -0.004)], 14, 0)
    ferrule(0.008, -0.006, 0.0145)
    press = about((0.0, 0.008, 0.0), Matrix.Diagonal((1.04, 1.0 - 0.003 / 0.064, 1.04, 1.0)))  # 3 mm in.
    sphere("Stone", 0.03, (0.0, 0.04, 0.0), 1, scale=(1.3, 1.08, 0.62), segments=18, rings=10, squeeze=press)
    return (0.0, 0.0723, 0.0), (RUBBER, STONE)


def p_flatten():
    wood_handle(-0.115, -0.012)
    ferrule(0.008, -0.014)
    sweep("Tang", smooth_path([(0, 0.004, 0), (0, 0.02, -0.004), (0, 0.03, -0.016), (0, 0.04, -0.021)], 3),
          0.0042, 1, up=(1, 0, 0))
    blade = [(-0.03, 0.03), (0.03, 0.03), (0.038, 0.048), (0.022, 0.09), (0.0, 0.135),
             (-0.022, 0.09), (-0.038, 0.048)]
    slab("Blade", blade, 0.0035, -0.025, 1)
    sweep("Rib", [(0, 0.035, -0.0215), (0, 0.075, -0.0215)], 0.0028, 2, up=(1, 0, 0), sides=6)
    return (0.0, 0.135, -0.023), (WOOD, STEEL)


def p_grab():
    lathe("Grip", [(0.0, -0.095), (0.01, -0.094), (0.015, -0.085), (0.0165, -0.05), (0.0145, -0.012),
                   (0.012, 0.0)], 14, 0)
    box("Hub", (0.034, 0.034, 0.042), (0.0, 0.016, 0.0), 1, 0.006)
    for sign in (1.0, -1.0):
        pivot = (0.0, 0.03, 0.012 * sign)
        squeeze = about(pivot, Matrix.Rotation(math.radians(-16.0 * sign), 4, "X"))
        path = smooth_path([(0, 0.026, 0.012 * sign), (0, 0.06, 0.032 * sign), (0, 0.092, 0.026 * sign),
                            (0, 0.106, 0.009 * sign)], 4)
        sweep("Finger", path, [0.0072 - 0.0025 * i / (len(path) - 1) for i in range(len(path))], 2,
              up=(1, 0, 0), sides=10, squeeze=squeeze)
        sphere("Pad", 0.0058, (0, 0.106, 0.009 * sign), 0, segments=10, rings=6, squeeze=squeeze)
    return (0.0, 0.108, 0.0), (RUBBER, IRON)


def p_snake_hook():
    wood_handle(-0.12, -0.01)
    ferrule(0.012, -0.012, 0.012)
    sweep("Shaft", [(0, 0.01, 0), (0, 0.075, 0)], 0.0052, 1, up=(1, 0, 0), sides=10)
    crook = smooth_path([(0, 0.07, 0), (0, 0.095, 0.002), (0, 0.118, 0.014), (0, 0.124, 0.036),
                         (0, 0.11, 0.056), (0, 0.088, 0.058), (0, 0.078, 0.044)], 4)
    sweep("Crook", crook, 0.0058, 2, up=(1, 0, 0), sides=10)
    sphere("Knob", 0.0075, (0, 0.078, 0.044), 2, segments=10, rings=6)
    return (0.0, 0.1298, 0.034), (WOOD, STEEL)


def p_inflate():
    squash = about((0, -0.02, 0), Matrix.Diagonal((1.12, 0.86, 0.72, 1.0)))
    lathe("Bulb", [(0.0, -0.065), (0.016, -0.062), (0.028, -0.048), (0.033, -0.025), (0.028, -0.002),
                   (0.016, 0.012), (0.011, 0.02)], 20, 2, squeeze=squash, smooth=3)
    lathe("Neck", [(0.0, 0.016), (0.0115, 0.016), (0.0125, 0.02), (0.0125, 0.03), (0.009, 0.034),
                   (0.0, 0.034)], 14, 0)
    lathe("Nozzle", [(0.0, 0.032), (0.0072, 0.032), (0.0066, 0.05), (0.0034, 0.076), (0.0022, 0.08),
                     (0.0, 0.08)], 12, 1)
    return (0.0, 0.08, 0.0), (RUBBER, STEEL)


def p_crease():
    lathe("Handle", [(0.0, -0.118), (0.009, -0.116), (0.0155, -0.106), (0.017, -0.06), (0.0145, -0.026),
                     (0.0125, -0.014)], 8, 0)
    ferrule(0.004, -0.016, 0.0125)
    bm = bmesh.new()
    w, t = 0.0095, 0.0036
    for x in (-w, w):  # A wedge: flat underneath, the top ground down to a sharp front edge.
        for y, z in ((0.004, -t), (0.004, t), (0.062, t), (0.092, -t), (0.092, -t + 0.0008)):
            bm.verts.new((x, y, z))
    bmesh.ops.convex_hull(bm, input=bm.verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _obj("Blade", bm)
    bevel(obj, 0.0007, 2)
    add(obj, 1)
    return (0.0, 0.092, -0.003), (WOOD_DARK, STEEL)


def p_layer():
    wood_handle(-0.11, -0.012)
    ferrule(0.008, -0.014)
    sweep("Shank", [(0, 0.004, 0), (0, 0.022, -0.006)], 0.004, 1, up=(1, 0, 0))
    for index, (length, part) in enumerate(((0.108, 1), (0.084, 2), (0.06, 1))):
        z = -0.0085 + index * 0.0042
        slab("Step", rounded_outline(-0.022, 0.018, 0.022, 0.018 + length, 0.007), 0.0036, z, part)
    return (0.0, 0.126, -0.0068), (WOOD, STEEL)


def p_mask_paint():
    lathe("Grip", [(0.0, -0.1), (0.009, -0.099), (0.0145, -0.09), (0.0155, -0.05), (0.0135, -0.015),
                   (0.008, -0.004), (0.006, 0.0)], 14, 0)
    frame = smooth_path([(0, -0.004, 0), (0, 0.035, 0), (0.024, 0.052, 0), (0.05, 0.066, 0),
                         (0.05, 0.092, 0), (0.036, 0.092, 0)], 4)
    sweep("Frame", frame, 0.0034, 1, up=(0, 0, 1))
    lathe("Roller", [(0.0, -0.04), (0.017, -0.04), (0.0205, -0.037), (0.0205, 0.037), (0.017, 0.04),
                     (0.0, 0.04)], 18, 2, axis="X", centre=(0.0, 0.092, 0.0))
    for x in (-0.042, 0.042):
        lathe("Cap", [(0.0, -0.003), (0.012, -0.003), (0.012, 0.003), (0.0, 0.003)], 14, 0, axis="X",
              centre=(x, 0.092, 0.0))
    return (0.0, 0.1125, 0.0), (RUBBER, STEEL)


def p_mask_extract():
    wood_handle(-0.105, -0.012)
    ferrule(0.008, -0.014, 0.013)
    sweep("Stem", [(0, 0.004, 0), (0, 0.028, 0)], 0.005, 1, up=(1, 0, 0), sides=10)
    star = []
    for i in range(10):
        a = math.pi / 2 + i * math.tau / 10
        r = 0.036 if i % 2 == 0 else 0.019
        star.append(Vector((math.cos(a) * r, 0.0, math.sin(a) * r)))
    tilt = Matrix.Rotation(math.radians(-40.0), 3, "X")  # Lean back: the star faces you and the target.
    normal = tilt @ Vector((0.0, 1.0, 0.0))
    star = [tilt @ p + Vector((0.0, 0.04, 0.01)) for p in star]
    rim = [(-1.0, -0.13), (1.0, -0.13), (1.0, 0.13), (-1.0, 0.13)]  # 16 mm deep, 2 mm wall.
    sweep("Cutter", smooth_path(star, 2, closed=True), 0.008, 2, up=normal, closed=True, profile=rim)
    # The ejector plate rests at the back of the cutter; the trigger pushes it 8 mm out.
    centre = Vector((0.0, 0.04, 0.01))
    plate = [centre + (q - centre) * 0.78 - normal * 0.007 for q in smooth_path(star, 2, closed=True)]
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new(p) for p in plate])
    out = bmesh.ops.extrude_face_region(bm, geom=[face])
    bmesh.ops.translate(bm, vec=normal * 0.002, verts=[g for g in out["geom"] if isinstance(g, bmesh.types.BMVert)])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    add(_obj("Ejector", bm), 1, Matrix.Translation(normal * 0.008))
    lathe("Boss", [(0.0, 0.024), (0.009, 0.024), (0.009, 0.036), (0.0, 0.036)], 12, 1)
    return (0.0, 0.044, 0.01), (WOOD, STEEL)


def p_spray():
    lathe("Can", [(0.0, -0.08), (0.022, -0.08), (0.026, -0.076), (0.026, 0.024), (0.0235, 0.03)], 20, 2,
          axis="Z", centre=(0, 0, -0.012))
    lathe("Label", [(0.0262, -0.06), (0.0268, -0.058), (0.0268, -0.012), (0.0262, -0.01)], 20, 0, axis="Z",
          centre=(0, 0, -0.012))
    lathe("Shoulder", [(0.024, 0.028), (0.0255, 0.03), (0.022, 0.042), (0.013, 0.05), (0.012, 0.052),
                       (0.0, 0.052)], 20, 1, axis="Z", centre=(0, 0, -0.012))
    press = Matrix.Translation((0.0, 0.0, -0.005))
    lathe("Button", [(0.0, 0.038), (0.0105, 0.038), (0.0105, 0.05), (0.008, 0.054), (0.0, 0.054)], 14, 1,
          axis="Z", centre=(0, 0, 0.0), squeeze=press)
    lathe("Nozzle", [(0.0, 0.006), (0.0034, 0.006), (0.0034, 0.0145), (0.0, 0.0145)], 10, 0, axis="Y",
          centre=(0, 0, 0.047), squeeze=press)
    return (0.0, 0.0145, 0.047), (RUBBER, CREAM)


def p_scrub():
    lathe("Stick", [(0.0, -0.09), (0.008, -0.089), (0.0125, -0.08), (0.0135, -0.04), (0.0115, -0.01),
                    (0.008, 0.012)], 14, 0)

    def reel():
        flanges = []
        for y0 in (0.012, 0.042):
            flanges.append(lathe("Flange", [(0.0, y0), (0.04, y0), (0.042, y0 + 0.0015), (0.042, y0 + 0.0045),
                                            (0.04, y0 + 0.006), (0.0, y0 + 0.006)], 28, 1))
        for flange in flanges:
            for i in range(5):
                a = math.pi / 2 + i * math.tau / 5
                bm = bmesh.new()
                bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0085, radius2=0.0085, depth=0.03)
                cutter = _obj("Hole", bm)
                cutter.rotation_euler = (math.radians(90.0), 0.0, 0.0)
                cutter.location = (math.cos(a) * 0.024, 0.03, math.sin(a) * 0.024)
                cutter.hide_render = True
                mod = flange.modifiers.new("Hole", "BOOLEAN")
                mod.operation = "DIFFERENCE"
                mod.object = cutter
                mod.solver = "EXACT"
        lathe("Film", [(0.0, 0.018), (0.033, 0.018), (0.033, 0.042), (0.0, 0.042)], 28, 0)
        lathe("Hub", [(0.0, 0.017), (0.011, 0.017), (0.011, 0.05), (0.0085, 0.053), (0.0, 0.053)], 14, 2)
        box("Tail", (0.02, 0.0016, 0.032), (0.0, 0.03, -0.045), 0, 0.0004)
        box("Pointer", (0.008, 0.006, 0.012), (0.0, 0.051, 0.041), 2, 0.0015)
    first = len(_parts)
    lean = tilted((0.0, 0.012, 0.0), -40.0, reel)
    turn = lean @ Matrix.Rotation(math.radians(36.0), 4, "Y") @ lean.inverted()  # Squeeze turns the reel.
    for i in range(first, len(_parts)):
        obj, part, _squeeze = _parts[i]
        if obj.name.split(".")[0] in {"Flange", "Film", "Hub"}:
            _parts[i] = (obj, part, turn)
    return tuple(lean @ Vector((0.0, 0.053, 0.0))), (RUBBER, STEEL)


def _stamp(emblem):
    sphere("Knob", 0.021, (0, -0.075, 0), 0, scale=(1, 0.9, 1), segments=16, rings=8)
    lathe("Neck", [(0.009, -0.064), (0.0075, -0.05), (0.009, -0.03), (0.015, -0.022), (0.015, -0.02)], 14, 0)
    box("Block", (0.056, 0.03, 0.056), (0, -0.006, 0), 0, 0.004)
    box("Pad", (0.054, 0.008, 0.054), (0, 0.012, 0), 1, 0.0025)
    emblem()


def p_place_object():
    # ponytail: one cube emblem for every Place shape; add a prop per PLACE_SHAPES key if the stamp
    # should show the selected shape (sphere, cylinder...).
    def cube():
        box("Emblem", (0.024, 0.024, 0.024), (0, 0.024, 0), 2, 0.0025,
            rotation=(math.radians(35.0), 0.0, math.radians(45.0)))
        box("Index", (0.026, 0.026, 0.026), (0, -0.006, 0.03), 2, 0.003,
            rotation=(math.radians(35.0), 0.0, math.radians(45.0)))
    _stamp(cube)
    return (0.0, 0.0405, 0.0), (WOOD, RED)


def p_place_prefab():
    def house():
        box("Walls", (0.026, 0.01, 0.018), (0, 0.021, -0.008), 2, 0.0012)
        bm = bmesh.new()
        tri = [(-0.018, -0.005), (0.018, -0.005), (0.0, 0.014)]
        face = bm.faces.new([bm.verts.new((x, 0.016, z + 0.006)) for x, z in tri])
        out = bmesh.ops.extrude_face_region(bm, geom=[face])
        bmesh.ops.translate(bm, vec=(0, 0.01, 0),
                            verts=[g for g in out["geom"] if isinstance(g, bmesh.types.BMVert)])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        add(bevel(_obj("Roof", bm), 0.0012, 2), 2)
        box("Index", (0.03, 0.022, 0.012), (0, -0.006, 0.032), 2, 0.002)  # A house on top as well.
        bm = bmesh.new()
        tri = [(-0.02, 0.0), (0.02, 0.0), (0.0, 0.016)]
        face = bm.faces.new([bm.verts.new((x, -0.017, 0.038 + z)) for x, z in tri])
        out = bmesh.ops.extrude_face_region(bm, geom=[face])
        bmesh.ops.translate(bm, vec=(0, 0.022, 0),
                            verts=[g for g in out["geom"] if isinstance(g, bmesh.types.BMVert)])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        add(bevel(_obj("IndexRoof", bm), 0.0012, 2), 2)
    _stamp(house)
    return (0.0, 0.026, 0.0), (WOOD, RED)


def p_measure():
    box("Case", (0.06, 0.04, 0.064), (0, 0, 0), 2, 0.012, 3)
    for x in (-0.031, 0.031):
        lathe("Hub", [(0.0, -0.002), (0.017, -0.002), (0.019, 0.0), (0.017, 0.002), (0.0, 0.002)], 16, 0,
              axis="X", centre=(x, 0.0, 0.002))
    box("Clip", (0.012, 0.05, 0.06), (-0.0, -0.02, 0.0), 0, 0.004)
    box("Mouth", (0.03, 0.006, 0.012), (0, 0.02, -0.022), 0, 0.002)
    tape = [(-0.0085, 0.022), (0.0085, 0.022), (0.0085, 0.064), (-0.0085, 0.064)]
    slab("Tape", tape, 0.0014, -0.0235, 1, 0.0003)
    box("Hook", (0.02, 0.0025, 0.012), (0, 0.065, -0.0275), 1, 0.0006)
    box("Lock", (0.012, 0.016, 0.006), (0, 0.004, 0.033), 0, 0.0018,
        squeeze=Matrix.Translation((0.0, 0.0, -0.002)))  # The lock button, 2 mm in.
    return (0.0, 0.0663, -0.0225), (RUBBER, STEEL)


def p_eyedropper():
    squash = about((0, -0.062, 0), Matrix.Diagonal((0.72, 1.12, 0.72, 1.0)))
    lathe("Bulb", [(0.0, -0.092), (0.009, -0.09), (0.016, -0.08), (0.0175, -0.062), (0.014, -0.044),
                   (0.0095, -0.036)], 16, 2, squeeze=squash, smooth=3)
    lathe("Collar", [(0.0, -0.04), (0.012, -0.04), (0.0125, -0.034), (0.012, -0.03), (0.0, -0.03)], 16, 0)
    lathe("Tube", [(0.0, -0.032), (0.0095, -0.032), (0.0095, 0.03), (0.0088, 0.04), (0.003, 0.074),
                   (0.0018, 0.078), (0.0, 0.078)], 16, 1)
    return (0.0, 0.078, 0.0), (RUBBER, GLASS)


def p_uv_island():
    pivot = (0.0, 0.028, 0.0)
    for sign, z in ((1.0, 0.002), (-1.0, -0.002)):
        open_angle = math.radians(11.0 * sign)
        squeeze = about(pivot, Matrix.Rotation(-open_angle, 4, "Z"))
        rest = about(pivot, Matrix.Rotation(open_angle, 4, "Z"))
        outline = [(-0.002 * sign, 0.13), (0.008 * sign, 0.1), (0.013 * sign, 0.05), (0.012 * sign, 0.018),
                   (0.008 * sign, -0.006), (-0.005 * sign, -0.006), (-0.008 * sign, 0.02), (-0.005 * sign, 0.07)]
        if sign < 0:
            outline.reverse()
        blade = slab("Blade", outline, 0.0036, z - 0.0018, 1, 0.0007, squeeze=squeeze)
        blade.matrix_world = rest
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.004, radius2=0.004, depth=0.018)
        shank = _obj("Shank", bm)
        shank.matrix_world = rest @ Matrix.Translation((0.008 * sign, -0.012, z)) @ \
            Matrix.Rotation(math.radians(90 - 30 * sign), 4, "X") @ Matrix.Rotation(math.radians(30 * sign), 4, "Y")
        add(shank, 0, squeeze)
        major = 0.015 if sign > 0 else 0.019
        ring = smooth_path([(math.cos(a) * major + 0.018 * sign, -0.034 + math.sin(a) * major * 1.15, z)
                            for a in (i * math.tau / 10 for i in range(10))], 2, closed=True)
        loop = sweep("Loop", ring, 0.0055, 0, up=(0, 0, 1), closed=True, sides=8, squeeze=squeeze)
        loop.matrix_world = rest
    lathe("Screw", [(0.0, -0.0045), (0.0055, -0.0045), (0.0065, -0.003), (0.0065, 0.003), (0.0055, 0.0045),
                    (0.0, 0.0045)], 12, 2, axis="Z", centre=pivot)
    return (0.0, 0.127, 0.0), (RUBBER, STEEL)  # Where the open blade tips meet when closed.


def p_draw_curve():
    lathe("Barrel", [(0.0, -0.105), (0.006, -0.104), (0.0092, -0.098), (0.0098, -0.07), (0.0098, -0.024),
                     (0.009, -0.004), (0.0078, 0.0)], 16, 0)
    for y in (-0.093, -0.03):
        lathe("Band", [(0.0, y), (0.0103, y), (0.0106, y + 0.003), (0.0103, y + 0.006), (0.0, y + 0.006)], 16, 2)
    sweep("Clip", [(0, -0.096, 0.0102), (0, -0.07, 0.0118), (0, -0.05, 0.0112)], 0.0024, 2, up=(0, 0, 1),
          profile=[(-0.5, -1.0), (0.5, -1.0), (0.5, 1.0), (-0.5, 1.0)])
    lathe("Section", [(0.0, -0.002), (0.0078, -0.002), (0.0072, 0.016), (0.0062, 0.022), (0.0, 0.022)], 16, 0)
    lathe("Nib", [(0.0, 0.016), (0.006, 0.016), (0.0055, 0.03), (0.0032, 0.045), (0.0008, 0.056),
                  (0.0, 0.0565)], 14, 1, scale=(1.0, 1.0, 0.42))
    return (0.0, 0.0565, 0.0), (RUBBER, GOLD)


def p_paint_vertex():
    lathe("Handle", [(0.0, -0.13), (0.004, -0.129), (0.0062, -0.12), (0.0088, -0.06), (0.0098, -0.03),
                     (0.009, -0.012)], 14, 0)
    lathe("Ferrule", [(0.0, -0.014), (0.0096, -0.014), (0.0096, -0.004), (0.0088, 0.0), (0.0092, 0.006),
                      (0.0088, 0.022), (0.0, 0.022)], 16, 1)
    splay = about((0.0, 0.018, 0.0), Matrix.Diagonal((1.14, 1.0 - 0.003 / 0.059, 1.14, 1.0)))  # 3 mm in.
    lathe("Bristles", [(0.0, 0.018), (0.0088, 0.018), (0.0098, 0.03), (0.009, 0.046), (0.006, 0.062),
                       (0.002, 0.074), (0.0, 0.077)], 16, 2, squeeze=splay)
    return (0.0, 0.077, 0.0), (WOOD, STEEL)


def p_sdf_blob():
    lathe("Stick", [(0.0, -0.11), (0.005, -0.109), (0.0065, -0.1), (0.0065, 0.03), (0.0, 0.03)], 10, 0)
    lathe("Collar", [(0.0, 0.012), (0.0095, 0.012), (0.011, 0.018), (0.0095, 0.026), (0.0, 0.026)], 14, 2)
    ball = sphere("Clay", 0.03, (0.0, 0.055, 0.0), 1, segments=20, rings=12)
    texture = bpy.data.textures.new("Lumps", "CLOUDS")
    texture.noise_scale = 0.6
    mod = ball.modifiers.new("Lumps", "DISPLACE")
    mod.texture = texture
    mod.strength = 0.004
    mod.mid_level = 0.5
    return (0.0, 0.0845, 0.0), (WOOD, CLAY)


def p_live_cage():
    lathe("Cuff", [(0.0, -0.082), (0.03, -0.082), (0.032, -0.078), (0.032, -0.056), (0.03, -0.052),
                   (0.0, -0.052)], 16, 0, scale=(1.0, 1.0, 0.62))
    box("Palm", (0.072, 0.078, 0.026), (0.0, -0.01, 0.0), 1, 0.009, 2)
    cage = box("PalmCage", (0.082, 0.088, 0.036), (0.0, -0.01, 0.0), 2, 0)
    cages = [cage]
    fingers = [(-0.027, 0.06), (-0.009, 0.07), (0.009, 0.066), (0.027, 0.052)]
    for x, length in fingers:  # Fingers curl down a little, like a relaxed hand.
        path = [(x, 0.026, 0.0), (x, 0.026 + length * 0.55, -0.002), (x, 0.026 + length, -0.012)]
        sweep("Finger", smooth_path(path, 3), 0.0082, 1, up=(1, 0, 0), sides=10)
        for seg in range(2):
            y0 = 0.03 + seg * length * 0.5
            cages.append(box("Knuckle", (0.021, length * 0.46, 0.022),
                             (x, y0 + length * 0.23, -0.002 - 0.006 * seg), 2, 0))
    thumb = [(-0.03, -0.02, -0.004), (-0.05, 0.0, -0.006), (-0.06, 0.022, -0.01)]
    sweep("Thumb", smooth_path(thumb, 3), 0.0092, 1, up=(0, 0, 1), sides=10)
    cages.append(box("ThumbCage", (0.024, 0.046, 0.024), (-0.05, 0.002, -0.006), 2, 0,
                     rotation=(0, 0, math.radians(40.0))))
    for cage in cages:  # The lattice: just the cage edges, as bars.
        mod = cage.modifiers.new("Wire", "WIREFRAME")
        mod.thickness = 0.0028
        mod.use_even_offset = True
    return (0.0, 0.0, 0.0), (RUBBER, IRON)


def p_subdiv_lab():
    lathe("Grip", [(0.0, -0.11), (0.009, -0.109), (0.0145, -0.1), (0.0155, -0.06), (0.0125, -0.02),
                   (0.0095, 0.0), (0.006, 0.006)], 14, 0)
    lathe("Shaft", [(0.0, 0.0), (0.0048, 0.0), (0.0048, 0.105), (0.0, 0.105)], 10, 1)
    turns, points = 3.0, 36
    helix = []
    for i in range(points + 1):
        t = i / points
        a = t * turns * math.tau
        r = 0.006 + 0.016 * math.sin(math.pi * min(1.0, t * 1.15)) ** 0.7
        helix.append((math.cos(a) * r, 0.036 + t * 0.074, math.sin(a) * r))
    sweep("Helix", helix, 0.0036, 2, up=(0, 1, 0), sides=10)
    sphere("Orb", 0.0105, (0.0, 0.116, 0.0), 2, segments=14, rings=8)
    return (0.0, 0.1265, 0.0), (RUBBER, STEEL)


def p_grow_seed():
    wood_handle(-0.11, -0.012, 0.014)
    lathe("Planter", [(0.0, -0.014), (0.0145, -0.014), (0.0155, -0.008), (0.012, 0.02), (0.006, 0.05),
                      (0.0015, 0.068), (0.0, 0.07)], 16, 1)
    cone = ((-0.008, 0.0155), (0.02, 0.012), (0.05, 0.006), (0.068, 0.0015))
    control = []
    for i in range(9):  # A vine that winds up the planter, then leaps off the tip and curls.
        y = -0.004 + i * 0.006
        r = float(np.interp(y, [c[0] for c in cone], [c[1] for c in cone])) + 0.0026
        a = math.radians(-60.0 + i * 52.0)
        control.append((math.cos(a) * r, y, math.sin(a) * r))
    control += [(0.004, 0.058, 0.02), (-0.004, 0.068, 0.036), (0.004, 0.082, 0.046), (0.014, 0.076, 0.05),
                (0.01, 0.068, 0.046)]
    vine = smooth_path(control, 3)
    sweep("Vine", vine, [0.0036 - 0.0024 * i / (len(vine) - 1) for i in range(len(vine))], 2,
          up=(0, 1, 0), sides=8)
    for at, side in ((len(vine) * 2 // 5, 1.0), (len(vine) * 3 // 4, -1.0)):
        point, ahead = Vector(vine[at]), Vector(vine[at + 1]) - Vector(vine[at - 1])
        out = ahead.cross(Vector((0.0, 0.0, side))).normalized()
        leaf = sphere("Leaf", 0.016, point + out * 0.013, 2, scale=(0.5, 1.0, 0.14), segments=12, rings=6)
        leaf.rotation_euler = out.to_track_quat("Y", "Z").to_euler()
    return (0.0, 0.07, 0.0), (WOOD, CLAY)


def p_retopo_loom():
    wood_handle(-0.11, -0.03, 0.0135)
    sweep("Neck", [(0, -0.032, 0), (0, -0.0, 0)], 0.006, 0, up=(1, 0, 0), sides=10)
    half, bar = 0.04, 0.009

    def loom():
        for x, z, sx, sz in ((0, half, 2 * half + bar, bar), (0, -half, 2 * half + bar, bar),
                             (half, 0, bar, 2 * half - bar), (-half, 0, bar, 2 * half - bar)):
            box("Frame", (sx, 0.009, sz), (x, 0.01, z + half), 0, 0.002)
        for x in (-half, half):
            for z in (-half, half):
                lathe("Peg", [(0.0, 0.014), (0.0055, 0.014), (0.0055, 0.019), (0.0, 0.021)], 10, 1,
                      centre=(x, 0.0, z + half))
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=4, y_segments=4, size=half - bar / 2.0)
        net = _obj("Net", bm)
        net.rotation_euler = (math.radians(90.0), 0.0, 0.0)
        net.location = (0.0, 0.01, half)
        mod = net.modifiers.new("Wire", "WIREFRAME")
        mod.thickness = 0.0024
        add(net, 2)
    lean = tilted((0.0, 0.01, 0.0), -40.0, loom)
    return tuple(lean @ Vector((0.0, 0.0115, half))), (WOOD, STEEL)


def _hull(name, points, part, bevel_w=0.0):
    bm = bmesh.new()
    for co in points:
        bm.verts.new(co)
    bmesh.ops.convex_hull(bm, input=bm.verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _obj(name, bm)
    if bevel_w:
        bevel(obj, bevel_w, 2)
    return add(obj, part)


def p_knife():
    lathe("Handle", [(0.0, -0.1), (0.008, -0.099), (0.0125, -0.092), (0.0135, -0.062), (0.012, -0.032),
                     (0.0135, -0.01), (0.0125, -0.002)], 14, 0, scale=(0.56, 1.0, 1.0))
    lathe("Bolster", [(0.0, -0.004), (0.0125, -0.004), (0.0132, 0.002), (0.011, 0.01), (0.0, 0.01)], 14, 1,
          scale=(0.6, 1.0, 1.0))
    for y in (-0.082, -0.054, -0.026):
        lathe("Rivet", [(0.0, -0.0082), (0.0028, -0.0082), (0.0028, 0.0082), (0.0, 0.0082)], 10, 2, axis="X",
              centre=(0.0, y, 0.0))
    spine = ((0.006, 0.011), (0.12, 0.009), (0.16, 0.0), (0.183, -0.011))  # (y, z): straight, then drops.
    edge = ((0.006, -0.033), (0.06, -0.035), (0.12, -0.033), (0.16, -0.025), (0.184, -0.016))
    _hull("Blade", [(x, y, z) for y, z in spine for x in (-0.0011, 0.0011)]
          + [(x, y, z) for y, z in edge for x in (-0.0002, 0.0002)] + [(0.0, 0.19, -0.014)], 1)
    return (0.0, 0.19, -0.014), (WOOD_DARK, STEEL)


def p_mallet():
    wood_handle(-0.12, -0.012, 0.0135)
    lathe("Shaft", [(0.0, -0.014), (0.0108, -0.014), (0.0096, 0.03), (0.0, 0.03)], 12, 0)
    head = (0.0, 0.03, 0.0)
    lathe("Head", [(0.0, -0.045), (0.035, -0.045), (0.038, -0.042), (0.039, -0.03), (0.039, 0.03),
                   (0.038, 0.042), (0.035, 0.045), (0.0, 0.045)], 24, 1, axis="Z", centre=head)
    for z in (-0.031, 0.031):
        lathe("Band", [(0.0, z - 0.0032), (0.0393, z - 0.0032), (0.0398, z), (0.0393, z + 0.0032),
                       (0.0, z + 0.0032)], 24, 2, axis="Z", centre=head)
    return (0.0, 0.03, -0.045), (WOOD_DARK, BEECH)  # The bottom striking face: swing it like a hammer.


def p_chisel():
    wood_handle(-0.115, -0.014, 0.0145)
    lathe("Cap", [(0.0, -0.121), (0.0085, -0.121), (0.0105, -0.116), (0.0105, -0.112), (0.0, -0.112)], 14, 2)
    ferrule(0.008, -0.016, 0.0135)
    lathe("Neck", [(0.0, 0.004), (0.0058, 0.004), (0.0042, 0.02), (0.0, 0.02)], 10, 1)
    w, t = 0.006, 0.0017
    _hull("Blade", [(x, y, z) for x in (-w, w) for y, z in ((0.016, -t), (0.016, t), (0.066, t), (0.082, -t),
                                                              (0.082, -t + 0.0006))], 1, 0.0004)
    return (0.0, 0.082, -0.0014), (WOOD, STEEL)


def p_rake():
    wood_handle(-0.11, -0.012, 0.0135)
    ferrule(0.008, -0.014, 0.0125)
    sweep("Stem", smooth_path([(0, 0.004, 0), (0, 0.03, 0.003), (0, 0.05, 0.0)], 3), 0.0036, 1,
          up=(1, 0, 0), sides=8)
    sweep("Bar", [(-0.021, 0.05, 0.0), (0.021, 0.05, 0.0)], 0.0042, 2, up=(0, 0, 1), sides=10)
    for i in range(5):  # 8 mm apart, curling down like claws.
        x = (i - 2) * 0.008
        tine = smooth_path([(x, 0.05, 0.0), (x, 0.066, -0.002), (x, 0.078, -0.012), (x, 0.081, -0.025)], 3)
        sweep("Tine", tine, [0.0019 - 0.0011 * k / (len(tine) - 1) for k in range(len(tine))], 1,
              up=(1, 0, 0), sides=6)
    return (0.0, 0.081, -0.0258), (WOOD, STEEL)


def p_sandpaper():
    box("Block", (0.064, 0.026, 0.046), (0.0, 0.016, 0.0), 1, 0.004)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=16, y_segments=12, size=0.5)
    for v in bm.verts:
        v.co = Vector((v.co.x * 0.06, v.co.y * 0.042, 0.0))
    grit = _obj("Grit", bm)
    grit.rotation_euler = (math.radians(-90.0), 0.0, 0.0)  # Faces forward (+Y).
    grit.location = (0.0, 0.0295, 0.0)
    texture = bpy.data.textures.new("Grit", "CLOUDS")
    texture.noise_scale = 0.003
    mod = grit.modifiers.new("Grit", "DISPLACE")
    mod.texture = texture
    mod.strength = 0.0008
    mod.mid_level = 0.0
    add(grit, 0, Matrix.Translation((0.0, -0.001, 0.0)))  # The grit face gives 1 mm.
    strap = smooth_path([(-0.029, 0.006, 0), (-0.024, -0.016, 0), (0, -0.03, 0), (0.024, -0.016, 0),
                         (0.029, 0.006, 0)], 4)
    sweep("Strap", strap, 0.008, 2, up=(0, 0, 1), profile=[(-1.0, -0.16), (1.0, -0.16), (1.0, 0.16), (-1.0, 0.16)])
    return (0.0, 0.0305, 0.0), (GRIT, CORK)


def p_rolling_pin():
    pin = (0.0, 0.035, 0.0)
    lathe("Pin", [(0.0, -0.125), (0.021, -0.125), (0.0238, -0.122), (0.025, -0.112), (0.025, 0.112),
                  (0.0238, 0.122), (0.021, 0.125), (0.0, 0.125)], 24, 1, axis="X", centre=pin)
    for side in (-1.0, 1.0):
        lathe("Handle", [(0.0, 0.124 * side), (0.0078, 0.124 * side), (0.0066, 0.131 * side),
                         (0.0098, 0.142 * side), (0.0108, 0.151 * side), (0.0082, 0.158 * side),
                         (0.0, 0.159 * side)], 14, 0, axis="X", centre=pin)
        lathe("Band", [(0.0, 0.103 * side), (0.0254, 0.103 * side), (0.0254, 0.108 * side),
                       (0.0, 0.108 * side)], 24, 2, axis="X", centre=pin)
    return pin, (WOOD_DARK, BEECH)  # Tip on the axis: the collider radius is the pin's.


def p_drill():
    lathe("Housing", [(0.0, -0.072), (0.013, -0.071), (0.02, -0.064), (0.023, -0.046), (0.023, 0.008),
                      (0.0195, 0.02), (0.0, 0.02)], 18, 0)
    grip = smooth_path([(0, -0.032, -0.012), (0, -0.04, -0.05), (0, -0.05, -0.084)], 3)
    sweep("Grip", grip, 0.0135, 0, up=(0, 1, 0),
          profile=[(math.cos(a), 0.72 * math.sin(a)) for a in (i * math.tau / 12 for i in range(12))])
    box("Battery", (0.036, 0.062, 0.022), (0.0, -0.048, -0.094), 0, 0.005)
    box("Trigger", (0.0075, 0.009, 0.017), (0.0, -0.014, -0.028), 2, 0.0025,
        squeeze=Matrix.Translation((0.0, -0.004, 0.0)))
    spinning(lathe("Chuck", [(0.0, 0.018), (0.0128, 0.018), (0.0128, 0.034), (0.0108, 0.042),
                             (0.0062, 0.05), (0.0, 0.05)], 16, 2))
    spinning(lathe("Bit", [(0.0, 0.048), (0.0015, 0.048), (0.0015, 0.095), (0.0, 0.1)], 8, 1))
    helix = [(math.cos(t * 4.5 * math.tau) * 0.0016, 0.05 + t * 0.043, math.sin(t * 4.5 * math.tau) * 0.0016)
             for t in (i / 36 for i in range(37))]
    spinning(sweep("Flute", helix, 0.0007, 1, up=(0, 1, 0), sides=5))
    return (0.0, 0.1, 0.0), (IRON, STEEL)


def p_clay_gun():
    lathe("Barrel", [(0.0, -0.062), (0.0145, -0.062), (0.0145, 0.042), (0.0, 0.042)], 18, 1)
    lathe("Cap", [(0.0, 0.04), (0.0158, 0.04), (0.0158, 0.052), (0.012, 0.057), (0.0, 0.057)], 18, 0)
    lathe("Nozzle", [(0.0, 0.055), (0.0082, 0.055), (0.006, 0.066), (0.0042, 0.076), (0.0, 0.076)], 12, 1)
    box("Flange", (0.062, 0.006, 0.018), (0.0, -0.056, 0.0), 0, 0.0028)
    press = Matrix.Translation((0.0, 0.015, 0.0))  # The plunger, 15 mm forward.
    lathe("Rod", [(0.0, -0.106), (0.0036, -0.106), (0.0036, -0.058), (0.0, -0.058)], 10, 1, squeeze=press)
    box("Plunger", (0.05, 0.009, 0.013), (0.0, -0.109, 0.0), 2, 0.0035, squeeze=press)
    return (0.0, 0.076, 0.0), (RUBBER, STEEL)


PROPS = {
    "sculpt_clay": p_clay, "sculpt_smooth": p_smooth, "sculpt_flatten": p_flatten, "sculpt_grab": p_grab,
    "snake_hook": p_snake_hook, "inflate": p_inflate, "crease": p_crease, "layer": p_layer,
    "mask_paint": p_mask_paint, "mask_extract": p_mask_extract, "spray_material": p_spray,
    "scrub_time": p_scrub, "place_object": p_place_object, "place_prefab": p_place_prefab,
    "measure": p_measure, "eyedropper": p_eyedropper, "uv_island": p_uv_island, "draw_curve": p_draw_curve,
    "paint_vertex": p_paint_vertex, "sdf_blob": p_sdf_blob, "live_cage": p_live_cage,
    "subdiv_lab": p_subdiv_lab, "grow_seed": p_grow_seed, "retopo_loom": p_retopo_loom,
    "knife": p_knife, "mallet": p_mallet, "chisel": p_chisel, "rake": p_rake, "sandpaper": p_sandpaper,
    "rolling_pin": p_rolling_pin, "drill": p_drill, "clay_gun": p_clay_gun,
}

# Contact collider (radius_m, shaft_m, axis, feel), spec 0.21 sections 1.1, 2.1 and 5. Sculpt brushes
# use radius ~0.6 x the width that touches at the tip.
# ponytail: hand-set estimates, not measured from the meshes; tune in the headset.
# 0.21 tuning: the mallet and sandpaper tips sit ON their flat faces, so a face-sized sphere centred on
# the tip held the face 2.5-3.5 cm off the surface. A few-mm sphere lets the face rest on it; the dent and
# sanding size still come from the brush radius, not the collider.
COLLIDERS = {
    "sculpt_clay": (0.007, 0.0, "Z", "CLAY"), "sculpt_smooth": (0.012, 0.0, "Z", "SOFT"),
    "sculpt_flatten": (0.006, 0.0, "Z", "HARD"), "sculpt_grab": (0.018, 0.0, "Z", "CLAY"),
    "snake_hook": (0.007, 0.0, "Z", "CLAY"), "inflate": (0.003, 0.0, "Z", "CLAY"),
    "crease": (0.011, 0.0, "Z", "CUT"), "layer": (0.026, 0.0, "Z", "CLAY"),
    "mask_paint": (0.024, 0.0, "Z", "SOFT"), "mask_extract": (0.02, 0.0, "Z", "HARD"),
    "spray_material": (0.004, 0.0, "Z", "SOFT"), "scrub_time": (0.006, 0.0, "Z", "HARD"),
    "place_object": (0.02, 0.0, "Z", "HARD"), "place_prefab": (0.02, 0.0, "Z", "HARD"),
    "measure": (0.004, 0.0, "Z", "HARD"), "eyedropper": (0.002, 0.0, "Z", "HARD"),
    "uv_island": (0.002, 0.05, "Z", "CUT"), "draw_curve": (0.002, 0.0, "Z", "SOFT"),
    "paint_vertex": (0.002, 0.0, "Z", "SOFT"), "sdf_blob": (0.01, 0.0, "Z", "CLAY"),
    "live_cage": (0.03, 0.0, "Z", "HARD"), "subdiv_lab": (0.01, 0.0, "Z", "HARD"),
    "grow_seed": (0.003, 0.0, "Z", "HARD"), "retopo_loom": (0.006, 0.0, "Z", "HARD"),
    "knife": (0.002, 0.16, "Z", "CUT"), "mallet": (0.004, 0.0, "Z", "HARD"),
    "chisel": (0.006, 0.03, "Z", "CUT"), "rake": (0.004, 0.0, "Z", "CUT"),
    "sandpaper": (0.003, 0.0, "Z", "SOFT"), "rolling_pin": (0.025, 0.25, "X", "CLAY"),
    "drill": (0.002, 0.04, "Z", "CUT"), "clay_gun": (0.004, 0.0, "Z", "CLAY"),
}
assert set(COLLIDERS) == set(PROPS), set(PROPS) ^ set(COLLIDERS)


# ---- Export ---------------------------------------------------------------------

TO_LOCAL = np.array(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))  # (x, y, z) -> (x, z, -y)


def _smooth_normals(co, tris):
    """Per-corner normals averaged over faces within SMOOTH_DEG of each other."""
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    face = np.cross(b - a, c - a)
    unit = face / np.maximum(np.linalg.norm(face, axis=1, keepdims=True), 1e-20)
    limit = math.cos(math.radians(SMOOTH_DEG))
    around: dict = {}
    for t, tri in enumerate(tris):
        for v in tri:
            around.setdefault(int(v), []).append(t)
    normals = np.zeros((len(tris), 3, 3))
    for t, tri in enumerate(tris):
        for k, v in enumerate(tri):
            near = [s for s in around[int(v)] if unit[s] @ unit[t] > limit]
            n = face[near].sum(axis=0)
            normals[t, k] = n / max(np.linalg.norm(n), 1e-20)
    return normals


def export_prop():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pos, nrm, part, sqz, idx, spin = [], [], [], [], [], []
    base = 0
    for obj, part_index, squeeze in _parts:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        evaluated.to_mesh_clear()
        bmesh.ops.triangulate(bm, faces=bm.faces)
        world = obj.matrix_world
        co = np.array([tuple(world @ v.co) for v in bm.verts])
        tris = np.array([[v.index for v in f.verts] for f in bm.faces], dtype=np.int64)
        bm.free()
        if not len(tris):
            continue
        if squeeze is None:
            delta = np.zeros_like(co)
        else:
            delta = np.array([tuple(squeeze @ Vector(p)) for p in co]) - co
        normals = _smooth_normals(co, tris)
        # One vertex per (position, rounded normal) so shared smooth corners stay shared.
        keys = {}
        for t, tri in enumerate(tris):
            out = []
            for k, v in enumerate(tri):
                n = normals[t, k]
                key = (int(v), *np.round(n * 200).astype(int))
                if key not in keys:
                    keys[key] = len(keys)
                    pos.append(co[v])
                    nrm.append(n)
                    sqz.append(delta[v])
                    part.append(part_index)
                    spin.append(obj.name in _spinning)
                out.append(base + keys[key])
            idx.append(out)
        base += len(keys)
    pos = np.array(pos) @ TO_LOCAL.T
    nrm = np.array(nrm) @ TO_LOCAL.T
    sqz = np.array(sqz) @ TO_LOCAL.T
    return pos, nrm, np.array(part, dtype=np.uint8), sqz, np.array(idx, dtype=np.uint16), np.array(spin, bool)


def clear_scene():
    _parts.clear()
    _spinning.clear()
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in tuple(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def build():
    arrays = {}
    total = 0
    for prop_id, make in PROPS.items():
        clear_scene()
        tip, (handle_colour, body_colour) = make()
        bpy.context.view_layer.update()
        pos, nrm, part, sqz, idx, spin = export_prop()
        tris = len(idx)
        total += tris
        print(f"  {prop_id:15s} {tris:5d} tris  {len(pos):5d} verts")
        assert tris <= MAX_PROP_TRIS, (prop_id, tris)
        arrays[f"{prop_id}__pos"] = pos.astype(np.float32)
        arrays[f"{prop_id}__nrm"] = nrm.astype(np.float16)
        arrays[f"{prop_id}__part"] = part
        arrays[f"{prop_id}__sqz"] = sqz.astype(np.float16)
        arrays[f"{prop_id}__idx"] = idx
        arrays[f"{prop_id}__tip"] = (np.array(tip) @ TO_LOCAL.T).astype(np.float32)
        arrays[f"{prop_id}__colours"] = np.array((handle_colour, body_colour), dtype=np.float32)
        radius, shaft, axis, feel = COLLIDERS[prop_id]
        arrays[f"{prop_id}__collider"] = np.array((radius, shaft), dtype=np.float32)
        arrays[f"{prop_id}__axis"] = np.array(axis)
        arrays[f"{prop_id}__feel"] = np.array(feel)
        if spin.any():
            arrays[f"{prop_id}__spin"] = spin.astype(np.uint8)
    assert total <= MAX_TOTAL_TRIS, total
    arrays["ids"] = np.array(list(PROPS))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, **arrays)
    print(f"TOOL_PROPS_BUILT {len(PROPS)} props, {total} triangles -> {OUT}")


# ---- Contact sheet (quick local check: Eevee, 512 px stills) -------------------------

def _linear(c):
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


SHEET_ROLL = {"live_cage": math.radians(70.0)}  # Show the back of the glove, not its edge.


def contact_sheet(path):
    import importlib.util
    import tempfile
    spec = importlib.util.spec_from_file_location("props_polish", ROOT / "xr_workbench" / "polish.py")
    polish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(polish)
    accent = polish.THEMES["DEFAULT"][2]["ACCENT"]
    data = np.load(OUT)
    ids = [str(i) for i in data["ids"]]
    clear_scene()
    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    try:
        scene.eevee.taa_render_samples = 16
    except AttributeError:
        pass
    world = bpy.data.worlds.new("Sheet")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.35, 0.37, 0.42, 1.0)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    scene.world = world
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.2
    sun.rotation_euler = (math.radians(40), math.radians(-25), math.radians(-30))
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.data.type = "ORTHO"
    scene.collection.objects.link(cam)
    scene.camera = cam
    text_mat = bpy.data.materials.new("Label")
    text_mat.use_nodes = True
    text_mat.node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (0.9, 0.92, 0.96, 1)
    text_mat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 1.0
    text_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0, 0, 0, 1)
    cell, cols = 256, 8
    rows = math.ceil(len(ids) / cols)
    sheet = np.zeros((rows * cell, cols * cell, 4), dtype=np.float32)
    plate = (0.085, 0.095, 0.12)
    sheet[..., :3] = plate
    sheet[..., 3] = 1.0
    tmp = Path(tempfile.mkdtemp())
    view_dir = Vector((1.0, -0.42, 0.8)).normalized()  # Side view from in front and above.
    for n, prop_id in enumerate(ids):
        pos = data[f"{prop_id}__pos"].astype(np.float64) @ TO_LOCAL  # Back to the modelling frame.
        nrm = data[f"{prop_id}__nrm"].astype(np.float64) @ TO_LOCAL
        idx = data[f"{prop_id}__idx"].astype(np.int64)
        part = data[f"{prop_id}__part"]
        mesh = bpy.data.meshes.new(prop_id)
        mesh.from_pydata([tuple(p) for p in pos], [], [tuple(t) for t in idx])
        mesh.update()
        mesh.polygons.foreach_set("material_index", part[idx[:, 0]].astype(np.int32))
        mesh.polygons.foreach_set("use_smooth", np.ones(len(idx), dtype=bool))
        mesh.normals_split_custom_set([tuple(nrm[v]) for t in idx for v in t])
        colours = list(data[f"{prop_id}__colours"]) + [accent]
        for k, colour in enumerate(colours):
            mat = bpy.data.materials.new(f"{prop_id}{k}")
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes["Principled BSDF"]
            bsdf.inputs["Base Color"].default_value = (*_linear(colour), 1.0)
            bsdf.inputs["Roughness"].default_value = 0.42 if k != 1 else 0.3
            bsdf.inputs["Metallic"].default_value = 0.0
            mesh.materials.append(mat)
        obj = bpy.data.objects.new(prop_id, mesh)
        obj.rotation_euler = (math.radians(28.0), SHEET_ROLL.get(prop_id, 0.0), 0.0)  # Tip up and right.
        scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        world_pts = np.array([tuple(obj.matrix_world @ Vector(p)) for p in pos])
        centre = Vector(((world_pts.min(0) + world_pts.max(0)) / 2.0).tolist())
        cam.rotation_euler = (-view_dir).to_track_quat("-Z", "Y").to_euler()
        cam.location = centre + view_dir * 1.0
        bpy.context.view_layer.update()
        inv = cam.matrix_world.inverted()
        local = np.array([tuple(inv @ Vector(p)) for p in world_pts])
        cam.data.ortho_scale = max(np.ptp(local[:, 0]), np.ptp(local[:, 1])) * 1.32
        mid = (local.min(0) + local.max(0)) / 2.0
        cam.location += cam.matrix_world.to_3x3() @ Vector((mid[0], mid[1] - cam.data.ortho_scale * 0.03, 0.0))
        bpy.context.view_layer.update()
        curve = bpy.data.curves.new("Label", "FONT")
        curve.body = prop_id
        curve.size = cam.data.ortho_scale * 0.075
        label = bpy.data.objects.new("Label", curve)
        label.data.materials.append(text_mat)
        label.parent = cam
        label.location = (-cam.data.ortho_scale * 0.46, -cam.data.ortho_scale * 0.47, -0.5)
        scene.collection.objects.link(label)
        out = tmp / f"{prop_id}.png"
        scene.render.filepath = str(out)
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(str(out))
        pixels = np.array(image.pixels[:], dtype=np.float32).reshape(512, 512, 4)
        bpy.data.images.remove(image)
        small = pixels.reshape(cell, 2, cell, 2, 4).mean(axis=(1, 3))
        alpha = small[..., 3:4]
        row, col = rows - 1 - n // cols, n % cols  # Image rows run bottom-up.
        block = sheet[row * cell:(row + 1) * cell, col * cell:(col + 1) * cell]
        block[..., :3] = small[..., :3] * alpha + block[..., :3] * (1.0 - alpha)  # PNG alpha is straight.
        for o in (obj, label):
            bpy.data.objects.remove(o, do_unlink=True)
        print(f"  rendered {prop_id}")
    out_image = bpy.data.images.new("ToolProps", cols * cell, rows * cell, alpha=False)
    out_image.pixels.foreach_set(sheet.ravel())
    out_image.filepath_raw = str(path)
    out_image.file_format = "PNG"
    out_image.save()
    print(f"TOOL_PROPS_SHEET {path}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--sheet-only" not in argv:
        build()
    if "--sheet" in argv or "--sheet-only" in argv:
        flag = "--sheet" if "--sheet" in argv else "--sheet-only"
        contact_sheet(Path(argv[argv.index(flag) + 1]).resolve())


main()
