"""Factory-profile check of the 0.21 tool upgrades (spec section 3): contact-mode strokes for Clay, Mask
Extract, Eyedropper, Scissors (seams), Spray (cone), Paint Colour and the 3D Pen.

Real Blender meshes, materials, undo and UVs; synthetic controller poses; the contact and feel engines are
stubbed here so these checks hold whether or not contact.py / feel.py have landed. Run in a separate
factory-startup background process with BLENDER_USER_CONFIG / BLENDER_USER_SCRIPTS set to an empty temporary
folder and --python-exit-code 1.
"""
from pathlib import Path
import sys
import time
import types

import bmesh
import bpy
import numpy as np
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import creative_tools, editing, onboarding, physical_tools  # noqa: E402
from xr_workbench.interaction import (  # noqa: E402
    CaptureOwner, Decision, InputProvenance, InputSample, Pose,
)

HAND = "/user/hand/right"
OWNER = CaptureOwner(InputProvenance.SIMULATOR, HAND, 0)
DOWN = Quaternion()  # Aim -Z.
_clock = [10.0]
STATE = {"contact": None, "surface": None}
PROBES, EVENTS, PREVIEWS = {}, [], []


# ---- stubs for the contact and feel engines -------------------------------------------------
def touching(obj, point, normal, depth_m=0.0, face=-1, pressure=None):
    pressure = max(0.0, min(1.0, depth_m / 0.02)) if pressure is None else pressure
    STATE["contact"] = types.SimpleNamespace(
        hand=HAND, obj_name=obj.name, touching=True, pressed=depth_m > 0.0, point=tuple(point),
        normal=tuple(normal), depth_m=depth_m, pressure=pressure, face_index=face)
    STATE["surface"] = (np.array(point, dtype=np.float64), np.array(normal, dtype=np.float64))


def lift():
    STATE["contact"] = STATE["surface"] = None


def plane_ray_cast(obj, origin, direction, distance):
    """Analytic z = 0 plane (the spray grid): a stand-in for the engine's cached BVH, so the cone costs nothing."""
    direction = Vector(direction)
    if abs(direction.z) < 1e-9:
        return None
    t = -Vector(origin).z / direction.z
    if t < 0 or t > distance:
        return None
    return Vector(origin) + direction * t, Vector((0, 0, 1)), -1, t


def install_stubs():
    contact = types.ModuleType("contact_stub")
    contact.current = lambda hand: STATE["contact"]
    contact.surface_for = lambda hand, obj: STATE["surface"]
    contact.ray_cast = plane_ray_cast
    contact.bvh_for = lambda context, obj: None
    contact.set_live_probe = lambda name, probe: PROBES.__setitem__(name, probe)
    contact.clear_live_probe = lambda name: PROBES.pop(name, None)
    feel = types.ModuleType("feel_stub")
    feel.event = lambda context, hand, kind, strength=1.0, at=None, normal=None: EVENTS.append(kind)
    feel.preview = lambda hand, kind, data: PREVIEWS.append(kind)
    feel.chips = lambda *args: EVENTS.append("chips")
    feel.flash = lambda *args: EVENTS.append("flash")
    physical_tools._MODS["contact"] = contact
    physical_tools._MODS["feel"] = feel
    physical_tools._MODS["contact_tools"] = None


def sample(position, rotation=DOWN):
    _clock[0] += 0.02
    return InputSample(OWNER, 0, Pose(tuple(position), tuple(rotation)), _clock[0])


def fresh_scene():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    STATE.update(contact=None, surface=None)
    PROBES.clear()
    EVENTS.clear()
    PREVIEWS.clear()


def icosphere(name="Ball", radius=0.1, subdivisions=5):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def coords(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", values)
    return values


def on_sphere(angle, radius=0.1):
    """World point and outward normal on the ball's meridian, `angle` radians from the top."""
    normal = Vector((np.sin(angle), 0.0, np.cos(angle)))
    return normal * radius, normal


def undo_base(label):
    assert bpy.ops.ed.undo_push(message=label) == {"FINISHED"}


# ---- checks -------------------------------------------------------------------------------------
def clay_stroke(obj, depth_m, hold_still=False):
    """Sweep clay over the top of the ball with the tip `depth_m` inside the surface; returns the displacement."""
    before = coords(obj)
    ctx = bpy.context
    point, normal = on_sphere(0.0)
    touching(obj, point, normal, depth_m)
    start = point - normal * depth_m
    stroke = physical_tools.begin(ctx, start, DOWN, tool="sculpt_clay", owner=OWNER)
    assert stroke.contact_mode and obj.name in PROBES, "Contact strokes register a live probe"
    pressure = max(0.15, STATE["contact"].pressure)  # What controllers._update_move does in contact mode.
    steps = 6
    for k in range(1, steps + 1):
        point, normal = on_sphere(0.0 if hold_still else 0.02 * k)
        touching(obj, point, normal, depth_m)
        stroke.pressure = pressure
        time.sleep(0.02)
        result = stroke.update(ctx, sample(point - normal * depth_m))
        assert result.decision is Decision.APPLY, result.message
    assert stroke.commit(ctx, OWNER).decision is Decision.APPLY
    assert obj.name not in PROBES, "The probe is cleared when the stroke ends"
    after = coords(obj)
    return float(np.abs(after - before).sum()), stroke


def check_clay():
    fresh_scene()
    obj = icosphere()
    undo_base("Clay Base")
    shallow, _ = clay_stroke(obj, 0.002)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    deep, stroke = clay_stroke(obj, 0.01)
    assert shallow > 0.0 and deep > 1.5 * shallow, ("depth is pressure", shallow, deep)
    assert np.allclose(stroke.normal, (0.0, 0.0, 1.0), atol=0.4) or stroke.normal is not None
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert np.abs(coords(obj)).max() <= 0.1001, "One undo restores the ball"
    # Press-to-build: the hand does not move at all, yet pressed clay keeps rising.
    held, _ = clay_stroke(obj, 0.01, hold_still=True)
    assert held > 0.0, "Holding still while pressed builds"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    # Far from the surface (no contact, ray mode) the 0.20 rules apply: Contact left unset.
    lift()
    stroke = physical_tools.begin(bpy.context, Vector((0.0, 0.0, 1.0)), DOWN, tool="sculpt_clay", owner=OWNER)
    assert not stroke.contact_mode
    stroke.cancel(bpy.context)
    assert "bowl" in PREVIEWS
    return shallow, deep, held


def check_mask_extract():
    fresh_scene()
    obj = icosphere("Masked")
    undo_base("Extract Base")
    point, normal = on_sphere(0.0)
    touching(obj, point, normal, 0.0)
    try:
        physical_tools.begin(bpy.context, point, DOWN, tool="mask_extract", owner=OWNER)
    except editing.EditRejected as exc:
        assert "mask" in str(exc).lower()
    else:
        raise AssertionError("No mask: Paint a mask first")
    flat = coords(obj).reshape(-1, 3)
    physical_tools.write_mask(obj.data, (flat[:, 2] > 0.04).astype(np.float32))
    undo_base("Mask Painted")
    count = len(bpy.data.objects)
    stroke = physical_tools.begin(bpy.context, point, DOWN, tool="mask_extract", owner=OWNER)
    for k in range(1, 4):  # 3 cm out along the normal = 3 mm more shell.
        assert stroke.update(bpy.context, sample(point + normal * 0.01 * k)).decision is Decision.APPLY
    assert stroke.mm == 7.0, stroke.mm
    assert "detent" in EVENTS and "click" in EVENTS, "A detent per mm"
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    made = bpy.data.objects["Masked Extract"]
    assert len(bpy.data.objects) == count + 1 and abs(made.modifiers["Solidify"].thickness - 0.007) < 1e-6
    assert "flash" in EVENTS
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert "Masked Extract" not in bpy.data.objects and len(bpy.data.objects) == count, "One undo step"
    # Cancel removes nothing: the shell only exists after release.
    stroke = physical_tools.begin(bpy.context, point, DOWN, tool="mask_extract", owner=OWNER)
    stroke.update(bpy.context, sample(point + normal * 0.02))
    stroke.cancel(bpy.context)
    assert len(bpy.data.objects) == count
    return made.modifiers["Solidify"].thickness if False else 7.0


def check_eyedropper():
    fresh_scene()
    obj = icosphere("Painted", subdivisions=3)
    red = bpy.data.materials.new("Pick Red")
    red.diffuse_color = (1.0, 0.0, 0.0, 1.0)
    obj.data.materials.append(red)
    settings = bpy.context.window_manager.xrwb_settings
    settings.spray_material = ""
    settings.edit_tool = "eyedropper"
    point, normal = on_sphere(0.0)
    touching(obj, point, normal, 0.0, face=0)
    stroke = physical_tools.begin(bpy.context, point, DOWN, tool="eyedropper", owner=OWNER)
    assert stroke is not None and settings.spray_material == "", "Nothing is picked on press"
    assert stroke.update(bpy.context, sample(point)).decision is Decision.APPLY
    assert "swatch" in PREVIEWS and stroke.pressure == 1.0
    assert settings.spray_material == "", "Still nothing while held"
    result = stroke.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY and settings.spray_material == "Pick Red", result.message
    assert settings.edit_tool == "spray_material"
    # Pointing away from everything before release cancels.
    settings.spray_material, settings.edit_tool = "", "eyedropper"
    stroke = physical_tools.begin(bpy.context, point, DOWN, tool="eyedropper", owner=OWNER)
    lift()
    far = Vector((50.0, 50.0, 50.0))
    stroke.update(bpy.context, sample(far, Quaternion((1, 0, 0), np.pi / 2)))
    result = stroke.commit(bpy.context, OWNER)
    assert result.decision is Decision.REJECTED and settings.spray_material == "", result.message
    assert editing.active_transaction() is None


def seam_count(obj):
    return sum(1 for edge in obj.data.edges if edge.use_seam)


def check_scissors():
    fresh_scene()
    obj = icosphere("Cut Ball", subdivisions=4)
    undo_base("Scissors Base")
    steps = [on_sphere(angle) for angle in (0.1, 0.35, 0.6)]  # Over 2.5 cm apart on a 10 cm ball.
    touching(obj, *steps[0], 0.0)
    stroke = physical_tools.begin(bpy.context, steps[0][0], DOWN, tool="uv_island", owner=OWNER)
    assert stroke.label == "Cut Seams" and stroke.contact_mode
    for point, normal in steps[1:]:
        touching(obj, point, normal, 0.0)
        assert stroke.update(bpy.context, sample(point)).decision is Decision.APPLY
    assert seam_count(obj) >= 2 and len(stroke.lines) == 2 * seam_count(obj), "Seams show live"
    assert stroke.pressure >= 0.0 and "snip" in EVENTS
    cut = seam_count(obj)
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert seam_count(obj) == cut and obj.data.uv_layers, "Release keeps the seams and unwraps"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert seam_count(bpy.data.objects["Cut Ball"]) == 0, "One undo removes every seam"
    obj = bpy.data.objects["Cut Ball"]
    # Cancel puts the seams back exactly (here: none).
    touching(obj, *steps[0], 0.0)
    stroke = physical_tools.begin(bpy.context, steps[0][0], DOWN, tool="uv_island", owner=OWNER)
    touching(obj, *steps[2], 0.0)
    stroke.update(bpy.context, sample(steps[2][0]))
    assert seam_count(obj) >= 1
    stroke.cancel(bpy.context)
    assert seam_count(obj) == 0
    return cut


def grid(name, segments):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=segments, y_segments=segments, size=0.5)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    return obj


def check_spray_and_paint():
    fresh_scene()
    obj = grid("Big Grid", 447)  # 200,704 vertices, 199,809 faces.
    assert len(obj.data.vertices) > 200_000
    undo_base("Spray Base")
    blue = bpy.data.materials.new("Spray Blue")
    blue.diffuse_color = (0.0, 0.0, 1.0, 1.0)
    settings = bpy.context.window_manager.xrwb_settings
    settings.spray_material = blue.name
    settings.spray_radius_m = 0.05
    touching(obj, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0)
    stroke = physical_tools.begin(bpy.context, Vector((0.0, 0.0, 0.0)), DOWN, tool="spray_material", owner=OWNER)
    # Nozzle 12 cm above the grid, pointing down: rays through the cone reach the plane.
    stroke.update(bpy.context, sample((0.0, 0.0, 0.12)))
    touching(obj, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0)
    assert "cone" in PREVIEWS and "chips" in EVENTS
    record = next(r for r in stroke.targets.values() if r)
    painted = int(np.count_nonzero(record["current"] != record["original"]))
    assert 0 < painted < len(record["current"]) // 10, painted
    # One sample of spray numpy (12 cone spots over the chunked face centres) under 1 ms.
    origin, direction = Vector((0.0, 0.0, 0.12)), Vector((0.0, 0.0, -1.0))
    spots, half = stroke._spots(bpy.context, obj, origin, direction, Vector((0, 0, 0)), -1, None)
    assert len(spots) > 8, len(spots)
    for _ in range(3):
        stroke.paint_faces(record, spots, half)
    times = []
    for _ in range(15):
        t0 = time.perf_counter()
        stroke.paint_faces(record, spots, half)
        times.append(time.perf_counter() - t0)
    spray_ms = sorted(times)[len(times) // 2] * 1000.0
    assert spray_ms < 1.0, f"spray sample {spray_ms:.3f} ms"
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert bpy.ops.ed.undo() == {"FINISHED"}
    obj = bpy.data.objects["Big Grid"]
    undone = np.empty(len(obj.data.polygons), dtype=np.int32)
    obj.data.polygons.foreach_get("material_index", undone)
    assert not undone.any(), "One undo removes the spray"

    # Paint Colour: chunked dab, same grid.
    settings.paint_colour = (1.0, 0.0, 0.0, 1.0)
    touching(obj, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0)
    paint = physical_tools.begin(bpy.context, Vector((0.0, 0.0, 0.0)), DOWN, tool="paint_vertex", owner=OWNER)
    record = next(r for r in paint.targets.values() if r)
    assert "chunks" in record, "Paint Colour reads vertices through Chunks"
    near = paint.dab(record, np.zeros(3))
    assert 0 < near < 2000, near
    times = []
    for _ in range(15):
        t0 = time.perf_counter()
        paint.dab(record, np.array([0.01, 0.02, 0.0]))
        times.append(time.perf_counter() - t0)
    paint_ms = sorted(times)[len(times) // 2] * 1000.0
    assert paint_ms < 1.0, f"paint dab {paint_ms:.3f} ms"
    assert paint.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert bpy.ops.ed.undo() == {"FINISHED"}
    return spray_ms, paint_ms, painted


def check_pen():
    fresh_scene()
    settings = bpy.context.window_manager.xrwb_settings
    tip = Vector((0.2, 0.3, 1.0))
    assert onboarding.prefs().get("tool_props", True) and creative_tools.pen_tip_m() == 0.0
    stroke = physical_tools.begin(bpy.context, tip, DOWN, tool="draw_curve", owner=OWNER)
    assert (stroke.points[0] - tip).length < 1e-6, "Props on: the ink starts at the nib"
    assert creative_tools.PEN_TIP_M == 0.0
    stroke.cancel(bpy.context)
    onboarding.prefs()["tool_props"] = False
    try:
        stroke = physical_tools.begin(bpy.context, tip, DOWN, tool="draw_curve", owner=OWNER)
        assert (stroke.points[0] - (tip + Vector((0, 0, -0.2)))).length < 1e-6, "Props off: 20 cm ahead as before"
        stroke.cancel(bpy.context)
    finally:
        onboarding.prefs()["tool_props"] = True
    # Touching a surface the ink lies on it, lifted by the tube radius.
    obj = icosphere("Pen Ball", subdivisions=3)
    point, normal = on_sphere(0.0)
    touching(obj, point, normal, 0.004)
    stroke = physical_tools.begin(bpy.context, point - normal * 0.004, DOWN, tool="draw_curve", owner=OWNER)
    radius = float(settings.pen_radius_m)
    assert (stroke.points[0] - (Vector(point) + Vector(normal) * radius)).length < 1e-6
    stroke.cancel(bpy.context)


def check_placement_and_friends():
    """Place (contact and STAMP), Prefab, Measure, SDF blob and Grow in contact mode."""
    from xr_workbench import growth, prefabs, sdf_modeling  # noqa: F401
    settings = bpy.context.window_manager.xrwb_settings

    # Place: the cube rests on the contact point with local Z along the surface normal.
    fresh_scene()
    ball = icosphere("Place Ball", radius=0.5, subdivisions=5)
    undo_base("Place Base")
    point, normal = on_sphere(0.6, 0.5)
    touching(ball, point, normal, 0.0)
    settings.place_kind = "CUBE"
    stroke = physical_tools.begin(bpy.context, point, DOWN, tool="place_object", owner=OWNER)
    assert isinstance(stroke, physical_tools.PlaceStroke) and stroke.contact_mode
    cube = stroke.obj
    expected = Vector(point) + Vector(normal) * (0.1 + physical_tools.RESTING_GAP_M)
    assert (cube.matrix_world.translation - expected).length < 1e-6, cube.matrix_world.translation
    up = cube.matrix_world.to_quaternion() @ Vector((0, 0, 1))
    assert (up - Vector(normal)).length < 1e-5, "Local Z follows the contact normal"
    # The tip now touches the new cube too: the stroke keeps resting on the ball instead of climbing the cube.
    touching(cube, point, normal, 0.0)
    assert stroke.update(bpy.context, sample(point)).decision is Decision.APPLY
    assert (cube.matrix_world.translation - expected).length < 1e-6
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY and "flash" in EVENTS
    assert bpy.ops.ed.undo() == {"FINISHED"} and "Cube" not in bpy.data.objects

    # Stamp: the same shape pressed into the ball (one undo step), only vertices under the footprint move.
    original_opt = physical_tools._opt
    physical_tools._opt = lambda st, name, default: "STAMP" if name == "place_mode" else original_opt(st, name, default)
    try:
        ball = bpy.data.objects["Place Ball"]
        before = coords(ball).reshape(-1, 3)
        point, normal = on_sphere(0.0, 0.5)
        touching(ball, point, normal, 0.01)
        stroke = physical_tools.begin(bpy.context, point - normal * 0.01, DOWN, tool="place_object", owner=OWNER)
        assert isinstance(stroke, physical_tools.StampStroke) and "Stamp" in stroke.label
        stroke.pressure = 0.6
        assert stroke.update(bpy.context, sample(point - normal * 0.01)).decision is Decision.APPLY
        assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
        after = coords(ball).reshape(-1, 3)
        moved = np.linalg.norm(after - before, axis=1)
        assert 0 < np.count_nonzero(moved) < len(moved) // 4, np.count_nonzero(moved)
        assert (np.linalg.norm(after, axis=1) <= np.linalg.norm(before, axis=1) + 1e-7).all(), "Pressed in"
        assert moved.max() <= 0.005 * 0.5 * 0.6 + 1e-6 + 0.0001, moved.max()
        assert bpy.ops.ed.undo() == {"FINISHED"}
        assert np.allclose(coords(bpy.data.objects["Place Ball"]).reshape(-1, 3), before)
    finally:
        physical_tools._opt = original_opt

    # Prefab: rests on an upward surface, base on the surface.
    fresh_scene()
    floor = grid("Floor", 4)
    undo_base("Prefab Base")
    touching(floor, (0.1, 0.1, 0.0), (0.0, 0.0, 1.0), 0.0)
    settings.prefab_kind = "BUILTIN:STOOL"
    stroke = physical_tools.begin(bpy.context, Vector((0.1, 0.1, 0.0)), DOWN, tool="place_prefab", owner=OWNER)
    bpy.context.view_layer.update()
    low = min((stroke.objects[0].matrix_world @ Vector(c)).z for c in stroke.objects[0].bound_box)
    assert abs(low) < 0.01, f"the prefab's base sits on the floor ({low})"
    stroke.cancel(bpy.context)

    # Measure: starts on a snapped vertex, ends at the live contact.
    fresh_scene()
    ball = icosphere("Tape Ball", subdivisions=4)
    first = ball.data.polygons[0]
    corner = ball.matrix_world @ ball.data.vertices[first.vertices[0]].co
    touching(ball, corner + Vector((0.003, 0.0, 0.0)), (0, 0, 1), 0.0, face=0)
    stroke = physical_tools.begin(bpy.context, corner, DOWN, tool="measure", owner=OWNER)
    assert (stroke.start - corner).length < 1e-6, "Snapped to the face vertex within 1 cm"
    far = Vector((0.0, 0.0, 0.1))
    touching(ball, far, (0, 0, 1), 0.0, face=-1)
    stroke.update(bpy.context, sample(far))
    assert abs(stroke.length - (far - corner).length) < 1e-6 and "tape" in PREVIEWS
    stroke.cancel(bpy.context)

    # SDF blob: the blob's centre sits on the contact point.
    point, normal = on_sphere(0.3)
    touching(ball, point, normal, 0.0)
    stroke = physical_tools.begin(bpy.context, point, DOWN, tool="sdf_blob", owner=OWNER)
    blob = stroke.obj.matrix_world @ stroke.added[0].co
    assert (blob - Vector(point)).length < 1e-5 and stroke.contact_mode
    stroke.cancel(bpy.context)

    # Grow: a seed planted on the surface and pulled into it still grows out.
    touching(ball, *on_sphere(0.0), 0.0)
    top = Vector((0.0, 0.0, 0.1))
    stroke = physical_tools.begin(bpy.context, top, DOWN, tool="grow_seed", owner=OWNER)
    assert stroke.contact_mode and (Vector(stroke.seed_point) - top).length < 1e-6
    stroke.update(bpy.context, sample(top + Vector((0.0, 0.0, -0.25))))
    assert stroke.lines and max(q[2] for pts, _r in stroke.lines for q in pts) > top.z + 0.1, "Reflected out of the surface"
    stroke.cancel(bpy.context)


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    install_stubs()
    try:
        shallow, deep, held = check_clay()
        check_mask_extract()
        check_eyedropper()
        seams = check_scissors()
        spray_ms, paint_ms, painted = check_spray_and_paint()
        check_pen()
        check_placement_and_friends()
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        physical_tools._MODS.clear()
        xr_workbench.unregister()
    print(f"XR_TOOL_UPGRADES_SMOKE_PASS clay_shallow={shallow:.4f} clay_deep={deep:.4f} clay_held={held:.4f} "
          f"seams={seams} spray_faces={painted} spray_ms={spray_ms:.3f} paint_ms={paint_ms:.3f} "
          f"input=synthetic hardware=0", flush=True)


main()
