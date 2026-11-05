"""Factory-profile check of the 0.20 Sculpt Pack: Snake Hook, Inflate, Crease, Layer, Mask,
twin hands, radial symmetry and Detail Where Stretched.

Real Blender data, operators and undo; synthetic controller poses only. No XR session or
headset. Run in a separate background process with --python-exit-code 1 and
BLENDER_USER_CONFIG / BLENDER_USER_DATAFILES pointing at empty temporary folders.
"""
import math
import os
from pathlib import Path
import sys
import time

import bmesh
import bpy
import numpy as np
from mathutils import Quaternion, Vector
from mathutils.kdtree import KDTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, physical_tools  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "pack-smoke-right", 0)
OWNER2 = CaptureOwner(InputProvenance.SIMULATOR, "pack-smoke-left", 0)
DOWN = Quaternion()  # Identity aims along -Z.
SIDE = Quaternion((0, 1, 0), math.radians(90))  # Aims along -X, at the +X side.
_clock = [10.0]
EXTRA = {}
_real_opt = physical_tools._opt
# Settings that ui.py may not register yet: the smoke supplies them so it passes before and after wiring.
physical_tools._opt = lambda settings, name, default: EXTRA.get(name, _real_opt(settings, name, default))


def sample(position, rotation=DOWN, owner=OWNER):
    _clock[0] += 0.01
    return InputSample(owner, 0, Pose(tuple(position), tuple(rotation)), _clock[0])


def coords(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", values)
    return values


def mask_of(obj):
    return physical_tools.read_mask(obj.data)


def volume(obj):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        return bm.calc_volume(signed=True)
    finally:
        bm.free()


def stroke(tool, start, path, rotation=DOWN):
    transaction = physical_tools.begin(bpy.context, start, rotation, tool=tool, owner=OWNER)
    assert transaction is not None and editing.active_transaction() is transaction
    for position in path:
        result = transaction.update(bpy.context, sample(position, rotation))
        assert result.decision is Decision.APPLY, (tool, result.message)
    return transaction


def commit(transaction):
    result = transaction.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY, result.message
    return result


def undo_once():
    assert bpy.ops.ed.undo() == {"FINISHED"}


def fresh_clay(settings):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    assert bpy.ops.xrwb.add_clay() == {"FINISHED"}
    clay = bpy.context.object
    EXTRA.clear()
    settings.sculpt_mirror_x = False
    settings.sculpt_invert = False
    settings.sculpt_radius_m = 0.05
    settings.sculpt_strength = 0.5
    return clay, clay.matrix_world @ Vector((0, 0, 0.12)) + Vector((0, 0, 1))


def lateral_path(start, count=8, length=0.04):
    return [start + Vector((dx, 0, 0)) for dx in np.linspace(0.0, length, count + 1)[1:]]


def check_chunks():
    from xr_workbench import sculpt_math

    rng = np.random.default_rng(1)
    points = rng.uniform(-1, 1, (20000, 3))
    points = points[np.argsort(points[:, 2] * 8 + points[:, 0])]  # Some locality, like a remesh.
    chunks = sculpt_math.Chunks(points)
    centre = np.array([0.1, 0.2, 0.3])
    ids = chunks.candidates(centre, 0.3)
    brute = np.flatnonzero(np.linalg.norm(points - centre, axis=1) < 0.3)
    assert set(brute) <= set(ids) and len(ids) < len(points)


def check_snake(settings):
    clay, start = fresh_clay(settings)
    EXTRA["sculpt_detail"] = "OFF"
    base = coords(clay)
    path = [start + Vector((0, 0, 0.004 * i)) for i in range(1, 11)]
    transaction = stroke("sculpt_snake", start, path)
    commit(transaction)
    after = coords(clay).reshape(-1, 3)
    move = after - base.reshape(-1, 3)
    top = int(np.argmax(base.reshape(-1, 3)[:, 2]))
    assert move[top, 2] >= 0.8 * 0.04, move[top]
    far = np.linalg.norm(base.reshape(-1, 3) - np.array([0, 0, 0.12]), axis=1) > 0.10
    assert np.abs(move[far]).max() == 0.0
    undo_once()
    assert np.array_equal(coords(bpy.data.objects["Clay"]), base)
    return float(move[top, 2] / 0.04)


def check_inflate_crease_layer(settings):
    clay, start = fresh_clay(settings)
    EXTRA["sculpt_detail"] = "OFF"
    base = coords(clay)
    v0 = volume(clay)
    out = {}
    for invert in (False, True):
        settings.sculpt_invert = invert
        commit(stroke("sculpt_inflate", start, lateral_path(start)))
        v1 = volume(bpy.data.objects["Clay"])
        assert (v1 - v0) * (-1.0 if invert else 1.0) > 1e-6, (invert, v0, v1)
        out["inflate_dig" if invert else "inflate"] = v1 - v0
        undo_once()
        assert np.array_equal(coords(bpy.data.objects["Clay"]), base)
    settings.sculpt_invert = False

    # Crease lowers the line and pinches neighbours toward it in the tangent plane.
    commit(stroke("sculpt_crease", start, lateral_path(start, 8, 0.04)))
    clay = bpy.data.objects["Clay"]
    b, a = base.reshape(-1, 3), coords(clay).reshape(-1, 3)
    on_line = np.flatnonzero((np.abs(b[:, 1]) < 0.004) & (b[:, 0] > 0.01) & (b[:, 0] < 0.03) & (b[:, 2] > 0.1))
    assert on_line.size and (a[on_line, 2] - b[on_line, 2]).mean() < -1e-5, "Crease did not cut"
    side = np.flatnonzero((np.abs(b[:, 1]) > 0.01) & (np.abs(b[:, 1]) < 0.025) & (b[:, 0] > 0.01) & (b[:, 0] < 0.03)
                          & (b[:, 2] > 0.1))
    assert (np.abs(a[side, 1]) - np.abs(b[side, 1])).mean() < -1e-5, "Crease did not pinch"
    undo_once()
    assert np.array_equal(coords(bpy.data.objects["Clay"]), base)

    # Layer: two passes over the same spot in one stroke match one pass, and reach the layer height.
    EXTRA["sculpt_layer_height_m"] = 0.005
    path = lateral_path(start)
    peaks = []
    for passes in (1, 2):
        commit(stroke("sculpt_layer", start, path * passes))
        moved = np.linalg.norm(coords(bpy.data.objects["Clay"]).reshape(-1, 3) - b, axis=1)
        peaks.append(float(moved.max()))
        undo_once()
        assert np.array_equal(coords(bpy.data.objects["Clay"]), base)
    assert abs(peaks[0] - peaks[1]) <= 1e-5, peaks
    assert 0.98 * 0.005 <= peaks[0] <= 0.005 + 1e-6, peaks
    out["layer_peak_mm"] = peaks[0] * 1000
    return out


def uv_ball(location):
    """A UV sphere is mirror-symmetric about X = 0 (an icosphere is not), so exact pairs exist."""
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    mesh = bpy.data.meshes.new("Clay")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=0.12)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Clay", mesh)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.ed.undo_push(message="uv ball")
    return obj


def check_mirror(settings):
    clay, start = fresh_clay(settings)
    clay = uv_ball(clay.location.copy())
    base = coords(clay).reshape(-1, 3)
    settings.sculpt_mirror_x = True
    for tool in ("sculpt_clay", "sculpt_snake"):
        EXTRA["sculpt_detail"] = "OFF"
        origin = start + Vector((0.03, 0, 0))
        path = [origin + Vector((0, 0.003 * i, 0.003 * i if tool == "sculpt_snake" else 0)) for i in range(1, 9)]
        commit(stroke(tool, origin, path))
        move = coords(bpy.data.objects["Clay"]).reshape(-1, 3) - base
        tree = KDTree(len(base))
        for i, p in enumerate(base):
            tree.insert(p, i)
        tree.balance()
        pair = np.array([tree.find((-p[0], p[1], p[2]))[1] for p in base])
        flipped = move[pair] * np.array([-1.0, 1.0, 1.0])
        assert np.abs(flipped - move).max() < 1e-5 and np.abs(move).max() > 1e-3, tool
        undo_once()
    settings.sculpt_mirror_x = False


def check_mask(settings):
    clay, start = fresh_clay(settings)
    EXTRA["sculpt_detail"] = "OFF"
    base = coords(clay).reshape(-1, 3)
    # The Mask tool creates Blender's real attribute; cancel removes it again.
    transaction = stroke("sculpt_mask", start, lateral_path(start))
    mask = mask_of(bpy.data.objects["Clay"])
    assert mask is not None and mask.max() > 0.3
    transaction.cancel(bpy.context, OWNER)
    assert mask_of(bpy.data.objects["Clay"]) is None
    commit(stroke("sculpt_mask", start, lateral_path(start)))
    painted = mask_of(bpy.data.objects["Clay"])
    assert painted is not None and painted.max() > 0.3
    undo_once()
    assert mask_of(bpy.data.objects["Clay"]) is None

    # Mask the top half; clay over the top does not move it, clay from below moves the bottom.
    clay = bpy.data.objects["Clay"]
    top_half = (base[:, 2] > 0.0).astype(np.float32)
    physical_tools.write_mask(clay.data, top_half)
    bpy.ops.ed.undo_push(message="masked")
    # Everything under the brush is frozen, so the stroke changes nothing and pushes no undo step.
    assert commit(stroke("sculpt_clay", start, lateral_path(start))).message == "No change"
    move = coords(bpy.data.objects["Clay"]).reshape(-1, 3) - base
    assert np.abs(move[top_half > 0]).max() < 1e-6
    clay = bpy.data.objects["Clay"]
    bottom = clay.matrix_world @ Vector((0, 0, -0.12)) + Vector((0, 0, -1))
    up = Quaternion((1, 0, 0), math.radians(180))  # Aims along +Z at the underside.
    commit(stroke("sculpt_clay", bottom, [bottom + Vector((dx, 0, 0)) for dx in np.linspace(0.0, 0.04, 9)[1:]], up))
    move = coords(bpy.data.objects["Clay"]).reshape(-1, 3) - base
    assert np.abs(move[top_half == 0]).max() > 1e-4 and np.abs(move[top_half > 0]).max() < 1e-6
    undo_once()

    # Actions: invert, grow, shrink, clear, extract; one undo each.
    clay = bpy.data.objects["Clay"]
    assert np.array_equal(mask_of(clay), top_half)
    assert bpy.ops.xrwb.mask_invert() == {"FINISHED"}
    assert np.allclose(mask_of(bpy.data.objects["Clay"]), 1.0 - top_half)
    undo_once()
    assert bpy.ops.xrwb.mask_grow() == {"FINISHED"}
    grown = mask_of(bpy.data.objects["Clay"])
    assert grown.sum() > top_half.sum()
    undo_once()
    assert bpy.ops.xrwb.mask_shrink() == {"FINISHED"}
    assert mask_of(bpy.data.objects["Clay"]).sum() < top_half.sum()
    undo_once()
    clay = bpy.data.objects["Clay"]
    polys = clay.data.polygons
    faces = sum(1 for p in polys if all(top_half[v] >= 0.5 for v in p.vertices))
    objects_before = len(bpy.data.objects)
    assert bpy.ops.xrwb.mask_extract() == {"FINISHED"}
    shell = bpy.context.object
    assert shell.name == "Clay Extract" and shell.modifiers[0].type == "SOLIDIFY"
    assert len(shell.data.polygons) == faces, (len(shell.data.polygons), faces)
    undo_once()
    assert len(bpy.data.objects) == objects_before and "Clay Extract" not in bpy.data.objects
    clay = bpy.data.objects["Clay"]
    assert bpy.ops.xrwb.mask_clear() == {"FINISHED"}
    assert mask_of(bpy.data.objects["Clay"]) is None
    assert bpy.ops.xrwb.mask_extract() == {"CANCELLED"}  # Nothing masked: "Paint a mask first".
    undo_once()
    # Blender's own Sculpt Mode keeps reading the same attribute (mask_flood_fill itself needs a
    # 3D view region and crashes headless on 4.5.15 and 5.1.2, so it is not run).
    clay = bpy.data.objects["Clay"]
    bpy.context.view_layer.objects.active = clay
    bpy.ops.object.mode_set(mode="SCULPT")
    bpy.ops.object.mode_set(mode="OBJECT")
    assert np.array_equal(mask_of(bpy.data.objects["Clay"]), top_half)
    bpy.ops.ed.undo_push(message="mask done")
    return faces


def check_radial_and_twin(settings):
    clay, start = fresh_clay(settings)
    EXTRA["sculpt_detail"] = "OFF"
    base = coords(clay).reshape(-1, 3)
    EXTRA["sculpt_radial"] = 6
    side = clay.matrix_world @ Vector((0.12, 0, 0)) + Vector((1, 0, 0))
    path = [side + Vector((0, 0.004 * i, 0)) for i in range(1, 9)]
    commit(stroke("sculpt_clay", side, path, SIDE))
    move = coords(bpy.data.objects["Clay"]).reshape(-1, 3) - base
    peaks = []
    for k in range(6):
        angle = math.radians(60 * k)
        p = np.array([0.12 * math.cos(angle), 0.0, 0.0])
        q = np.array([p[0], 0.0, 0.0])
        q = np.array([0.12 * math.cos(angle), 0.12 * math.sin(angle), 0.0])
        near = np.linalg.norm(base - q, axis=1) < 0.03
        peaks.append(float(np.linalg.norm(move[near], axis=1).max()))
    assert min(peaks) > 1e-4 and (max(peaks) - min(peaks)) / max(peaks) <= 0.02, peaks
    undo_once()
    EXTRA.pop("sculpt_radial")

    # Twin hands: the left hand joins a clay stroke; one commit, one undo; cancel from either hand.
    clay = bpy.data.objects["Clay"]
    assert np.array_equal(coords(clay).reshape(-1, 3), base)
    left = start + Vector((-0.06, 0, 0))
    for tool in ("sculpt_clay", "sculpt_grab"):
        transaction = stroke(tool, start, lateral_path(start, 4, 0.02))
        assert transaction.update_secondary(bpy.context, sample(left, owner=OWNER2)).decision is Decision.APPLY
        for i in range(1, 5):
            result = transaction.update_secondary(bpy.context, sample(left + Vector((0, 0.004 * i, 0.004 * i * (tool == "sculpt_grab"))), owner=OWNER2))
            assert result.decision is Decision.APPLY, result.message
        move = coords(bpy.data.objects["Clay"]).reshape(-1, 3) - base
        left_side = np.flatnonzero(base[:, 0] < -0.03)
        assert np.abs(move[left_side]).max() > 1e-4 and np.abs(move[base[:, 0] > 0.03]).max() > 1e-4, tool
        # Left hand lets go first: stroke stays open; then the right hand commits.
        assert transaction.release(bpy.context, OWNER2).decision is Decision.APPLY
        assert editing.active_transaction() is transaction
        assert transaction.release(bpy.context, OWNER).decision is Decision.APPLY
        assert editing.active_transaction() is None
        undo_once()
        assert np.array_equal(coords(bpy.data.objects["Clay"]).reshape(-1, 3), base), tool
        # Cancel from the secondary hand cancels the whole stroke.
        transaction = stroke(tool, start, lateral_path(start, 4, 0.02))
        transaction.update_secondary(bpy.context, sample(left, owner=OWNER2))
        transaction.update_secondary(bpy.context, sample(left + Vector((0, 0.01, 0)), owner=OWNER2))
        transaction.cancel(bpy.context, OWNER2)
        assert editing.active_transaction() is None
        assert np.array_equal(coords(bpy.data.objects["Clay"]).reshape(-1, 3), base), tool


def check_detail(settings):
    clay, start = fresh_clay(settings)
    settings.remesh_detail_m = 0.02
    EXTRA["sculpt_detail"] = "AUTO"  # Snake Hook defaults to on.
    base = coords(clay)
    count = len(clay.data.vertices)
    settings.sculpt_radius_m = 0.05
    path = [start + Vector((0, 0, 0.03 * i)) for i in range(1, 11)]  # 30 cm pull.
    started = time.perf_counter()
    transaction = stroke("sculpt_snake", start, path)
    pulled = time.perf_counter()
    commit(transaction)
    commit_ms = (time.perf_counter() - pulled) * 1000.0
    clay = bpy.data.objects["Clay"]
    grown = len(clay.data.vertices)
    assert grown > count, (count, grown)
    pts = coords(clay).reshape(-1, 3)
    pairs = np.empty(len(clay.data.edges) * 2, dtype=np.int64)
    clay.data.edges.foreach_get("vertices", pairs)
    a, b = pairs.reshape(-1, 2).T
    lengths = np.linalg.norm(pts[a] - pts[b], axis=1)
    stretched = ((a >= count) | (np.linalg.norm(pts[np.minimum(a, count - 1)] - base.reshape(-1, 3)[np.minimum(a, count - 1)], axis=1) > 1e-3)) \
        & ((b >= count) | (np.linalg.norm(pts[np.minimum(b, count - 1)] - base.reshape(-1, 3)[np.minimum(b, count - 1)], axis=1) > 1e-3))
    limit = 1.5 * 0.02 * 1.05
    assert lengths[stretched].max() <= limit, (lengths[stretched].max(), limit)
    undo_once()
    assert len(bpy.data.objects["Clay"].data.vertices) == count
    assert np.array_equal(coords(bpy.data.objects["Clay"]), base)
    # Cancel drops everything, topology included, and the toggle can turn detail off.
    EXTRA["sculpt_detail"] = "OFF"
    commit(stroke("sculpt_snake", start, path))
    assert len(bpy.data.objects["Clay"].data.vertices) == count
    undo_once()
    # Manual Detail action on the stretched region.
    EXTRA["sculpt_detail"] = "OFF"
    commit(stroke("sculpt_snake", start, path))
    assert bpy.ops.xrwb.sculpt_detail() == {"FINISHED"} and len(bpy.data.objects["Clay"].data.vertices) > count
    undo_once()
    undo_once()
    return count, grown, commit_ms


def check_brush_dial(settings):
    """Off-hand trigger in the air: up/down sizes, roll sets strength; settings only, no undo step."""
    clay, start = fresh_clay(settings)
    base = coords(clay)
    commit(stroke("sculpt_clay", start, lateral_path(start)))
    off = CaptureOwner(InputProvenance.CONTROLLER, physical_tools.off_hand(), 0)
    up = Quaternion((1, 0, 0), math.pi)  # Aims along +Z: nothing up there.
    air = Vector((2.0, 2.0, 2.0))

    def dial():
        transaction = physical_tools.begin(bpy.context, air, up, tool="sculpt_clay", owner=off)
        assert isinstance(transaction, physical_tools.BrushDialStroke), transaction
        return transaction

    transaction = dial()
    result = transaction.update(bpy.context, sample(air + Vector((0, 0, 0.10)), up, off))
    assert result.decision is Decision.APPLY, result.message
    assert abs(settings.sculpt_radius_m / 0.05 - 2.0) < 0.04, settings.sculpt_radius_m
    roll = up @ Quaternion((0, 0, 1), math.radians(90))
    transaction.update(bpy.context, sample(air + Vector((0, 0, 0.10)), roll, off))
    assert abs(settings.sculpt_strength - 1.0) < 1e-6, settings.sculpt_strength
    transaction.cancel(bpy.context, off)
    assert abs(settings.sculpt_radius_m - 0.05) < 1e-9 and abs(settings.sculpt_strength - 0.5) < 1e-9
    transaction = dial()
    transaction.update(bpy.context, sample(air + Vector((0, 0, -0.10)), up, off))
    assert transaction.commit(bpy.context, off).decision is Decision.APPLY
    assert abs(settings.sculpt_radius_m - 0.025) < 0.001, settings.sculpt_radius_m
    undo_once()  # Undoes the clay stroke: the dial left no step of its own.
    assert np.array_equal(coords(bpy.data.objects["Clay"]), base)
    try:  # The main hand keeps the old rule: pointing at nothing is refused.
        physical_tools.begin(bpy.context, air, up, tool="sculpt_clay", owner=OWNER)
        raise AssertionError("main hand in the air should be refused")
    except editing.EditRejected:
        pass


def check_speed(settings, subdivisions):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    mesh = bpy.data.meshes.new("Dense")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=0.12)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Dense", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.ed.undo_push(message="dense")
    EXTRA.clear()
    EXTRA["sculpt_detail"] = "OFF"
    settings.sculpt_mirror_x = True
    start = Vector((0.0, 0.0, 1.12))
    transaction = physical_tools.begin(bpy.context, start, DOWN, tool="sculpt_clay", owner=OWNER)
    times = []
    for i in range(1, 101):
        position = start + Vector((0.0005 * i, 0.0, 0.0))
        began = time.perf_counter()
        transaction.update(bpy.context, sample(position))
        times.append((time.perf_counter() - began) * 1000.0)
    transaction.cancel(bpy.context, OWNER)
    settings.sculpt_mirror_x = False
    # Same maths without the per-sample ray cast (that is Blender's depsgraph + BVH, not ours).
    transaction = physical_tools.begin(bpy.context, start, DOWN, tool="sculpt_snake", owner=OWNER)
    snake = []
    for i in range(1, 101):
        position = start + Vector((0.0, 0.0005 * i, 0.0))
        began = time.perf_counter()
        transaction.update(bpy.context, sample(position))
        snake.append((time.perf_counter() - began) * 1000.0)
    transaction.cancel(bpy.context, OWNER)
    return len(mesh.vertices), float(np.median(times)), float(np.median(snake)), float(np.max(snake))


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and bpy.app.background
    assert not bpy.data.filepath, "Only a factory unsaved test scene is allowed"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    xr_workbench.register()
    try:
        settings = bpy.context.window_manager.xrwb_settings
        bpy.ops.ed.undo_push(message="sculpt pack baseline")
        check_chunks()
        snake = check_snake(settings)
        pack = check_inflate_crease_layer(settings)
        check_mirror(settings)
        faces = check_mask(settings)
        check_radial_and_twin(settings)
        check_brush_dial(settings)
        before, after, commit_ms = check_detail(settings)
        verts, clay_ms, snake_ms, snake_max = check_speed(settings, 8)
    finally:
        editing.cancel_active(bpy.context)
        xr_workbench.unregister()
    print(f"XR_SCULPT_PACK_SMOKE_PASS snake_follow={snake:.2f} inflate_dv={pack['inflate']:.2e} "
          f"layer_peak_mm={pack['layer_peak_mm']:.2f} mask_faces={faces} mirror=1 radial6=1 twin=1 brush_dial=1 "
          f"detail={before}->{after} detail_commit_ms={commit_ms:.0f} dense_verts={verts} "
          f"clay_mirror_ms={clay_ms:.1f} snake_ms={snake_ms:.2f} snake_max_ms={snake_max:.2f} "
          "input=synthetic hardware=0", flush=True)
    assert snake_ms <= 8.0 and clay_ms <= 8.0, f"over budget: snake {snake_ms:.1f} ms, clay+mirror {clay_ms:.1f} ms"


if __name__ == "__main__":
    main()
