"""Factory-profile check of the 0.21 contact tools (spec section 4): knife, mallet, chisel, rake,
sandpaper, rolling pin, drill and clay gun, each on a real mesh with one undo step, plus one timed
chisel / sandpaper sample on a 200k-vertex grid.

Contact data is stubbed (no contact.py needed, no XR session, no headset). Run in a separate
background process with --python-exit-code 1 and BLENDER_USER_CONFIG / BLENDER_USER_SCRIPTS pointing
at empty temporary folders.
"""
import math
from pathlib import Path
import sys
import time

import bmesh
import bpy
import numpy as np
from mathutils import Quaternion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import contact_tools as ct, editing, physical_tools  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "contact-smoke-right", 0)
IDENT = Quaternion()
RADIUS = 0.2
_clock = [10.0]
_tick = [100.0]
STATE = {"c": None}
EXTRA = {}
_real_opt = ct._opt
ct._opt = lambda settings, name, default: EXTRA.get(name, _real_opt(settings, name, default))
ct._clock = lambda: _tick[0]


class Contact:
    def __init__(self, obj, point, normal, pressed=True, entered=False, speed=0.0, nspeed=0.0, touching=True):
        self.obj_name, self.touching, self.pressed, self.entered = obj.name, touching, pressed, entered
        self.point, self.normal, self.speed_mps, self.normal_speed_mps = tuple(point), tuple(normal), speed, nspeed
        self.pressure, self.depth_m, self.face_index = 1.0, 0.005, -1


ct.current_contact = lambda hand: STATE["c"]
ct.surface = lambda hand, obj: (None if STATE["c"] is None or not STATE["c"].touching
                                else (np.array(STATE["c"].point), np.array(STATE["c"].normal)))


def sample(position, owner=OWNER):
    _clock[0] += 0.01
    return InputSample(owner, 0, Pose(tuple(position), tuple(IDENT)), _clock[0])


def coords(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", values)
    return values.reshape(-1, 3)


LAST = {}


def get():
    return bpy.data.objects[LAST["name"]]


def undo_once():
    assert bpy.ops.ed.undo() == {"FINISHED"}


def arc(y):
    """Point and normal on the top of the test sphere at arc offset y."""
    point = np.array((0.0, y, math.sqrt(RADIUS ** 2 - y * y)))
    return point, point / RADIUS


def fresh(settings, noise=0.0, grid=False):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        bpy.data.collections.remove(collection)
    EXTRA.clear()
    if grid:
        mesh = bpy.data.meshes.new("Grid")
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=447, y_segments=447, size=1.0)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new("Grid", mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
    else:
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=6, radius=RADIUS, location=(0, 0, 0))
        obj = bpy.context.object
    if noise:
        rng = np.random.default_rng(4)
        values = coords(obj) + rng.normal(0, noise, (len(obj.data.vertices), 3)).astype(np.float32)
        obj.data.vertices.foreach_set("co", values.ravel())
        obj.data.update()
    settings.sculpt_radius_m = 0.03
    settings.sculpt_strength = 1.0
    settings.sculpt_invert = False
    settings.sculpt_mirror_x = False
    if "sculpt_detail" in settings.bl_rna.properties:
        settings.sculpt_detail = "OFF"
    LAST["name"] = obj.name
    bpy.ops.ed.undo_push(message="contact baseline")
    return obj


def begin(tool, obj, point, normal, settings, **kw):
    STATE["c"] = Contact(obj, point, normal, **kw)
    transaction = ct.begin(bpy.context, Pose(tuple(point), tuple(IDENT)), tool, OWNER, 0, 1.0, settings)
    assert transaction is not None and editing.active_transaction() is transaction
    return transaction


def step(transaction, point, normal, obj, **kw):
    STATE["c"] = Contact(obj, point, normal, **kw)
    _tick[0] += 0.1
    result = transaction.update(bpy.context, sample(point))
    assert result.decision is Decision.APPLY, result.message
    return result


def commit(transaction):
    result = transaction.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY, result.message
    return result


def radial(obj):
    return np.linalg.norm(coords(obj), axis=1)


def sweep(tool, settings, ys, pressure=1.0, **kw):
    obj = fresh(settings, noise=kw.pop("noise", 0.0))
    base = coords(obj)
    point, normal = arc(ys[0])
    transaction = begin(tool, obj, point, normal, settings)
    transaction.pressure = pressure
    for y in ys[1:]:
        point, normal = arc(y)
        step(transaction, point, normal, obj, **kw)
    return obj, base, transaction


def check_refusals(settings):
    obj = fresh(settings)
    STATE["c"] = None
    for tool in ("knife", "mallet", "drill"):
        try:
            ct.begin(bpy.context, Pose((0, 0, 1), tuple(IDENT)), tool, OWNER, 0, 1.0, settings)
        except editing.EditRejected as exc:
            assert "Touch the mesh" in str(exc), exc
        else:
            raise AssertionError(f"{tool} started without contact")
    assert editing.active_transaction() is None
    dial = ct.begin(bpy.context, Pose((0, 0, 1), tuple(IDENT)), "mallet",
                    CaptureOwner(InputProvenance.SIMULATOR, physical_tools.off_hand(), 0), 0, 1.0, settings)
    assert isinstance(dial, physical_tools.BrushDialStroke)
    dial.cancel(bpy.context)
    return obj


def check_chisel(settings):
    ys = np.arange(0.0, 0.0451, 0.005)
    obj, base, transaction = sweep("chisel", settings, ys)
    assert transaction.label == "Chisel"
    commit(transaction)
    after = coords(get())
    shift = np.linalg.norm(after - base, axis=1)
    moved = shift > 1.0e-7
    assert moved.sum() > 20, "chisel carved nothing"
    path = np.array([arc(y)[0] for y in np.linspace(0, 0.045, 60)])
    far = np.min(np.linalg.norm(base[moved][:, None] - path[None], axis=2), axis=1).max()
    assert far < 0.5 * 0.03 + 0.012, f"vertices moved away from the path: {far:.3f}"
    cut = (np.linalg.norm(base, axis=1) - np.linalg.norm(after, axis=1)).max()
    assert 0.003 <= cut <= 0.0065, cut  # 6 mm x strength 1 x pressure 1
    undo_once()
    assert np.allclose(coords(get()), base)
    return int(moved.sum()), cut * 1000.0


def check_rake(settings):
    ys = np.arange(0.0, 0.0451, 0.005)
    obj, base, transaction = sweep("rake", settings, ys)
    commit(transaction)
    after = coords(get())
    moved = np.linalg.norm(after - base, axis=1) > 1.0e-7
    assert moved.sum() > 20, "rake combed nothing"
    x = base[moved][:, 0]
    assert np.abs(x).max() < 0.021, "rake wider than its head"
    band = (np.abs(base[:, 0]) < 0.02) & (base[:, 1] > 0.0) & (base[:, 1] < 0.045) & (base[:, 2] > 0.1)
    assert moved[band].mean() < 0.8, "rake carved a solid band, not tines"
    undo_once()
    assert np.allclose(coords(get()), base)
    return int(moved.sum())


def rough(pts, cap=0.05):
    near = (pts[:, 2] > 0.17) & (np.abs(pts[:, 1]) < cap) & (np.abs(pts[:, 0]) < 0.04)
    return float(np.linalg.norm(pts[near], axis=1).std())


def check_sandpaper(settings):
    ys = np.arange(-0.03, 0.0301, 0.005)
    obj, base, transaction = sweep("sandpaper", settings, ys, noise=0.002, speed=0.5)
    before = rough(base)
    commit(transaction)
    after = rough(coords(get()))
    assert after < 0.8 * before, (before, after)
    undo_once()
    assert np.allclose(coords(get()), base)
    # No rubbing speed = no effect and no undo step.
    obj, base, transaction = sweep("sandpaper", settings, ys, noise=0.002, speed=0.0)
    assert np.allclose(coords(obj), base)
    assert commit(transaction).message == "No change"
    return before * 1000.0, after * 1000.0


def check_rolling_pin(settings):
    obj = fresh(settings)
    pts = coords(obj)
    bump = (pts[:, 2] > 0.19) & (np.abs(pts[:, 0]) < 0.02) & (np.abs(pts[:, 1]) < 0.03)
    pts[bump] *= 1.0 + 0.005 / RADIUS * 1.0  # 5 mm proud of the sphere
    obj.data.vertices.foreach_set("co", pts.ravel())
    obj.data.update()
    bpy.ops.ed.undo_push(message="bumped")
    base = coords(obj)
    top = float(np.linalg.norm(base[bump], axis=1).max())
    point, normal = arc(-0.04)
    transaction = begin("rolling_pin", obj, point, normal, settings)
    transaction.pressure = 1.0
    for y in np.arange(-0.035, 0.0401, 0.005):
        point, normal = arc(y)
        step(transaction, point, normal, obj)
    commit(transaction)
    after = coords(get())
    flat = float(np.linalg.norm(after[bump], axis=1).max())
    assert top - flat > 0.002, (top, flat)
    assert not (np.linalg.norm(after[~bump] - base[~bump], axis=1) > 0.0061).any()
    undo_once()
    assert np.allclose(coords(get()), base)
    return (top - flat) * 1000.0


def mallet_dent(settings, speed):
    obj = fresh(settings)
    base = coords(obj)
    point, normal = arc(0.0)
    transaction = begin("mallet", obj, point, normal, settings, pressed=False)
    _tick[0] += 1.0
    step(transaction, point, normal, obj, pressed=False)
    step(transaction, point, normal, obj, pressed=True, entered=True, nspeed=speed)
    step(transaction, point, normal, obj, pressed=True, entered=True, nspeed=speed)  # 100 ms: too soon
    commit(transaction)
    dent = float((np.linalg.norm(base, axis=1) - np.linalg.norm(coords(get()), axis=1)).max())
    undo_once()
    assert np.allclose(coords(get()), base)
    return dent


def check_mallet(settings):
    soft, hard = mallet_dent(settings, 0.3), mallet_dent(settings, 1.5)
    assert hard > 2.0 * soft > 0.0, (soft, hard)
    assert abs(hard - ct.mallet_depth(1.5)) < 0.0015, hard
    # Dig mode flattens the area to the impact plane instead of denting.
    settings.sculpt_invert = True
    obj = fresh(settings)
    settings.sculpt_invert = True
    base = coords(obj)
    point, normal = arc(0.0)
    transaction = begin("mallet", obj, point, normal, settings, pressed=False)
    step(transaction, point, normal, obj, pressed=True, entered=True, nspeed=1.0)
    commit(transaction)
    flat = coords(get())
    xy = np.linalg.norm(base[:, :2], axis=1)
    ring = (xy > 0.012) & (xy < 0.025) & (base[:, 2] > 0.1)
    gap_before, gap_after = (RADIUS - base[ring, 2]).mean(), (RADIUS - flat[ring, 2]).mean()
    assert ring.any() and gap_after < 0.6 * gap_before, (gap_before, gap_after)
    undo_once()
    settings.sculpt_invert = False
    return soft * 1000.0, hard * 1000.0


def check_drill(settings):
    obj = fresh(settings)
    base_count = len(obj.data.vertices)
    point, normal = arc(0.0)
    # Cancel leaves nothing behind.
    transaction = begin("drill", obj, point, normal, settings)
    transaction.pressure = 1.0
    for _ in range(3):
        step(transaction, point, normal, obj)
    transaction.cancel(bpy.context, OWNER)
    assert "XR Drill Bit" not in bpy.data.objects and not obj.modifiers
    # A hole under 1 mm is "No hole" and pushes nothing.
    transaction = begin("drill", obj, point, normal, settings)
    transaction.pressure = 1.0
    step(transaction, point, normal, obj, pressed=False)
    assert commit(transaction).message == "No hole"
    transaction = begin("drill", obj, point, normal, settings)
    transaction.pressure = 1.0
    for _ in range(6):
        step(transaction, point, normal, obj)
    assert transaction.spin == ct.DRILL_SPIN
    commit(transaction)
    obj = get()
    assert [m.type for m in obj.modifiers] == ["BOOLEAN"] and obj.modifiers[0].operation == "DIFFERENCE"
    cutter = bpy.data.objects["XR Drill Bit"]
    assert cutter.display_type == "WIRE" and cutter.hide_render and cutter.users_collection[0].name == ct.CUTTERS
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert len(evaluated.data.vertices) != base_count, "the Boolean cut no hole"
    assert len(obj.data.vertices) == base_count, "drilling must be non-destructive"
    hole = len(evaluated.data.vertices) - base_count
    undo_once()
    obj = get()
    assert "XR Drill Bit" not in bpy.data.objects and not obj.modifiers
    return hole


def volume(obj):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        return bm.calc_volume(signed=True)
    finally:
        bm.free()


def knife_stroke(settings, split):
    obj = fresh(settings)
    EXTRA["knife_split"] = split
    base = coords(obj)
    full = volume(obj)
    transaction = begin("knife", obj, (0, -0.25, 0.25), (0, 0, 1), settings)
    for y, z in zip(np.linspace(-0.25, 0.25, 13), np.linspace(0.25, -0.25, 13)):
        step(transaction, (0.0, float(y), float(z)), (0, 0, 1), obj)
    return obj, base, full, transaction


def check_knife(settings):
    obj, base, full, transaction = knife_stroke(settings, False)
    plane = transaction.plane()
    assert plane is not None and abs(plane[1][0]) > 0.99, plane
    commit(transaction)
    obj = get()
    assert len(obj.data.vertices) > len(base) and len([o for o in bpy.data.objects if o.type == "MESH"]) == 1
    assert abs(volume(obj) - full) < 1.0e-6
    undo_once()
    assert np.allclose(coords(get()), base)
    obj, base, full, transaction = knife_stroke(settings, True)
    commit(transaction)
    pieces = [o for o in bpy.data.objects if o.type == "MESH"]
    assert len(pieces) == 2 and any(o.name.endswith(" Cut") for o in pieces), [o.name for o in pieces]
    volumes = sorted(volume(o) for o in pieces)
    assert volumes[0] > 0.3 * full and abs(sum(volumes) - full) < 0.01 * full, (volumes, full)
    undo_once()
    assert len([o for o in bpy.data.objects if o.type == "MESH"]) == 1
    assert np.allclose(coords(get()), base)
    # A swipe that never enters the mesh changes nothing.
    obj = fresh(settings)
    transaction = begin("knife", obj, (0, 0, 0.5), (0, 0, 1), settings)
    step(transaction, (0, 0, 0.5), (0, 0, 1), obj, pressed=False)
    assert commit(transaction).message == "No cut"
    return len(pieces)


def check_clay_gun(settings):
    obj = fresh(settings)
    STATE["c"] = None
    before = len(bpy.data.objects)
    transaction = ct.begin(bpy.context, Pose((0, 0, 0.5), tuple(IDENT)), "clay_gun", OWNER, 0, 1.0, settings)
    transaction.pressure = 0.5
    for i in range(1, 12):
        point = (0.01 * i, 0.0, 0.5 + 0.005 * math.sin(i))
        _tick[0] += 0.1
        assert transaction.update(bpy.context, sample(point)).decision is Decision.APPLY
    commit(transaction)
    coil = bpy.data.objects["Clay Coil"]
    assert coil.type == "CURVE" and len(coil.data.splines[0].bezier_points) >= 10
    assert len(bpy.data.objects) == before + 1
    undo_once()
    assert "Clay Coil" not in bpy.data.objects
    transaction = ct.begin(bpy.context, Pose((0, 0, 0.5), tuple(IDENT)), "clay_gun", OWNER, 0, 1.0, settings)
    assert commit(transaction).message == "No coil"


def check_speed(settings, radius):
    obj = fresh(settings, grid=True)
    count = len(obj.data.vertices)
    settings.sculpt_radius_m = radius
    out = {}
    for tool, extra in (("chisel", {}), ("sandpaper", {"speed": 0.5})):
        point, normal = (-0.2, 0.0, 0.0), (0.0, 0.0, 1.0)
        transaction = begin(tool, obj, point, normal, settings)
        transaction.pressure = 1.0
        times = []
        for i in range(1, 23):
            point = (-0.2 + 0.005 * i, 0.0, 0.0)
            STATE["c"] = Contact(obj, point, normal, **extra)
            _tick[0] += 0.1
            if i < 3:
                transaction._write_due = lambda ids: None
            began = time.perf_counter()
            transaction.update(bpy.context, sample(point))
            times.append((time.perf_counter() - began) * 1000.0)
        # Brush math alone: the mesh write is the shared SculptStroke throttle.
        transaction._write_due = lambda ids: None
        math_times = []
        for i in range(23, 43):
            point = (-0.2 + 0.005 * i, 0.0, 0.0)
            STATE["c"] = Contact(obj, point, normal, **extra)
            _tick[0] += 0.1
            began = time.perf_counter()
            transaction.update(bpy.context, sample(point))
            math_times.append((time.perf_counter() - began) * 1000.0)
        transaction.cancel(bpy.context, OWNER)
        out[tool] = (float(np.median(times[3:])), float(np.median(math_times)), times[0])
    return count, out


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
        bpy.ops.ed.undo_push(message="contact tools baseline")
        check_refusals(settings)
        chisel_n, chisel_mm = check_chisel(settings)
        rake_n = check_rake(settings)
        rough_before, rough_after = check_sandpaper(settings)
        pin_mm = check_rolling_pin(settings)
        soft, hard = check_mallet(settings)
        hole = check_drill(settings)
        pieces = check_knife(settings)
        check_clay_gun(settings)
        count, speed = check_speed(settings, 0.05)
        _count, wide = check_speed(settings, 0.1)
    finally:
        editing.cancel_active(bpy.context)
        xr_workbench.unregister()
    print(f"XR_CONTACT_TOOLS_SMOKE_PASS chisel_verts={chisel_n} chisel_mm={chisel_mm:.1f} rake_verts={rake_n} "
          f"sand_rough_mm={rough_before:.2f}->{rough_after:.2f} pin_flatten_mm={pin_mm:.1f} "
          f"mallet_mm={soft:.1f}/{hard:.1f} drill_hole_verts={hole} knife_pieces={pieces} clay_gun=1 "
          f"grid_verts={count} chisel_update_ms={speed['chisel'][0]:.2f} chisel_math_ms={speed['chisel'][1]:.2f} "
          f"sand_update_ms={speed['sandpaper'][0]:.2f} sand_math_ms={speed['sandpaper'][1]:.2f} "
          f"sand_first_ms={speed['sandpaper'][2]:.0f} r10cm_chisel_ms={wide['chisel'][1]:.2f} r10cm_sand_ms={wide['sandpaper'][1]:.2f} undo=one_step_each input=synthetic contact=stubbed",
          flush=True)
    assert speed["chisel"][1] < 1.0 and speed["sandpaper"][1] < 1.0, f"over 1 ms budget: {speed}"


if __name__ == "__main__":
    main()
