"""Headless check of the contact engine and tool feedback on real meshes (spec 0.21 sections 1-2).

A closed icosphere and an open 200k-vertex grid; synthetic hand poses, no XR
session or headset. Run in a separate background process with
BLENDER_USER_CONFIG set to an empty temporary folder and --python-exit-code 1.
"""
import os
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import contact, feel  # noqa: E402

R = "/user/hand/right"
L = "/user/hand/left"
ID = (1.0, 0.0, 0.0, 0.0)


def scene():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0, location=(0, 0, 0))
    ball = bpy.context.object
    ball.name = "Ball"
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=447, y_subdivisions=447, size=2.0, location=(5, 0, 0))
    grid = bpy.context.object
    grid.name = "Grid"
    bpy.context.view_layer.update()
    assert len(grid.data.vertices) >= 199_000, len(grid.data.vertices)
    return ball, grid


def at(tip, tool="sculpt_flatten", now=0.0, hand=R, held=False):
    return contact.update(bpy.context, {hand: (tuple(tip), ID)}, {hand: tool}, now, {hand: held})[hand]


def distance_to_ball(point, ball):
    tree = contact.bvh_for(bpy.context, ball)
    from mathutils import Vector
    local = ball.matrix_world.inverted() @ Vector(point)
    found = tree.find_nearest(local)
    return (ball.matrix_world @ found[0] - Vector(point)).length


def check_sphere(ball):
    # Outside: 5 cm above the pole, beyond the 3 cm reach.
    c = at((0, 0, 1.05), now=1.0)
    assert not c.touching and c.obj_name == "", c
    assert contact.stats["builds"] == 0  # Out of reach of the AABB: no tree yet.
    # Touching: 2 cm above, inside reach, not pressed.
    c = at((0, 0, 1.02), now=1.011)
    assert c.touching and not c.pressed and c.obj_name == "Ball", c
    assert contact.stats["builds"] == 1  # Built on first approach.
    r = c.radius
    assert abs(c.depth_m - (r - 0.02)) < 0.002 and c.normal[2] > 0.98, c
    # 1 cm inside: depth 1 cm, pressure 0.5, entered once.
    tip = np.array((0.0, 0.0, 1.0 + r - 0.01))
    c = at(tip, now=1.022)
    assert c.pressed and c.entered and abs(c.depth_m - 0.01) < 0.0015 and abs(c.pressure - 0.5) < 0.08, c
    assert c.normal_speed_mps > 0.5, c.normal_speed_mps  # 2.4 cm into the surface in 11 ms.
    # HARD push puts the sphere on the surface.
    gap = distance_to_ball(tip + np.array(c.pushed_offset), ball)
    assert abs(gap - r) < 0.0015, gap
    # CLAY sinks 6 mm.
    c = at(tip, tool="sculpt_clay", now=1.033)
    gap = distance_to_ball(tip + np.array(c.pushed_offset), ball)
    assert abs(gap - (r - 0.006)) < 0.0015 and not c.entered, (gap, c)
    # CUT with the trigger held passes through; released it rests on the surface.
    assert np.allclose(at(tip, tool="sculpt_crease", now=1.044, held=True).pushed_offset, 0)
    assert np.linalg.norm(at(tip, tool="sculpt_crease", now=1.055).pushed_offset) > 0.009
    # Sliding: 3.3 mm sideways in 11 ms along the surface = 0.3 m/s, no speed into it.
    t, slid = 1.066, []
    for step in range(1, 6):
        a = step * 0.0033
        p = (1.0 + r - 0.01) * np.array((np.sin(a), 0.0, np.cos(a)))
        c = at(p, now=t + step * 0.011)
        slid.append(c.speed_mps)
        assert abs(distance_to_ball(p + np.array(c.pushed_offset), ball) - r) < 0.0015, step
    assert all(abs(s - 0.3) < 0.03 for s in slid[1:]), slid
    assert abs(c.normal_speed_mps) < 0.05, c.normal_speed_mps
    # Leaving: back out past reach.
    c = at((0, 0, 1.2), now=t + 0.1)
    assert c.left and not c.touching, c
    # Sinking from a touch keeps the prop pushed out past the 3 cm reach, then drops the push past 10 cm.
    at((0, 0, 1.02), now=t + 0.2)
    deep = np.array((0.0, 0.0, 0.95))
    c = at(deep, now=t + 0.3)
    assert c.touching and not c.push_dropped, c
    assert abs(distance_to_ball(deep + np.array(c.pushed_offset), ball) - c.radius) < 0.0015, c
    c = at((0, 0, 0.85), now=t + 0.4)
    assert c.touching and c.push_dropped, c
    assert not at((0, 0, 1.2), now=t + 0.5).touching
    # Cached ray cast.
    hit = contact.ray_cast(ball, (0, 0, 5), (0, 0, -1), 10.0)
    assert hit is not None and abs(hit[0].z - 1.0) < 0.002 and hit[2] >= 0 and abs(hit[3] - 4.0) < 0.002, hit
    return slid


def check_cache(ball):
    builds = contact.stats["builds"]
    ball.location.x = 0.5
    bpy.context.view_layer.update()
    c = at((0.5, 0, 1.0 + 0.006 - 0.01), now=3.0)
    assert c.pressed and abs(c.depth_m - 0.01) < 0.0015, c
    assert contact.stats["builds"] == builds, "A transform-only move must not rebuild the tree"
    # A geometry change marks it dirty: one rebuild, then none.
    ball.data.vertices[0].co.z += 0.0
    ball.data.update()
    bpy.context.view_layer.update()
    contact._last_rebuild[0] = -1.0  # the 100 ms rebuild gap runs on the real clock
    at((0.5, 0, 1.01), now=4.0)
    at((0.5, 0, 1.01), now=4.2)
    assert contact.stats["builds"] == builds + 1, contact.stats["builds"]
    # A live probe replaces the tree (no rebuild while a stroke owns the mesh).
    ball.data.update()
    bpy.context.view_layer.update()
    contact.set_live_probe("Ball", lambda tip, radius: ((0.5, 0, 1.0), (0, 0, 1), float(tip[2] - 1.0)))
    c = at((0.5, 0, 0.99), now=5.0)
    assert c.pressed and abs(c.depth_m - 0.016) < 1e-6 and c.face_index == -1, c
    assert contact.stats["builds"] == builds + 1
    # A stroke starting on the changed mesh still gets a fresh tree from bvh_for, not the stale one.
    assert contact._trees["Ball"]["dirty"]
    contact.bvh_for(bpy.context, ball)
    assert contact.stats["builds"] == builds + 2 and not contact._trees["Ball"]["dirty"]
    contact.clear_live_probe("Ball")
    ball.location.x = 0.0
    bpy.context.view_layer.update()
    return builds


def check_grid_and_timing(grid):
    # Open mesh: a hand below the grid is on the back side, not "inside".
    c = at((5.0, 0.1, -0.02), now=6.0)
    assert c.touching and not c.pressed and c.normal[2] < -0.99, c
    c = at((5.0, 0.1, 0.004), now=6.011)  # Crossed up through the grid from below: pressed.
    assert c.pressed and abs(c.depth_m - (c.radius + 0.004)) < 0.001, c
    at((5.0, 0.1, 1.0), now=6.1)  # away, forgets the side
    c = at((5.0, 0.1, 0.01), now=6.2)  # fresh from above: just over the surface
    assert c.touching and not c.pressed, c
    poses = {R: ((5.1, 0.2, 0.002), ID), L: ((4.8, -0.3, 0.01), ID)}
    tools = {R: "sculpt_clay", L: "measure"}
    for k in range(20):  # warm-up
        contact.update(bpy.context, poses, tools, 7.0 + k * 0.011)
    runs = 200
    began = perf_counter()
    for k in range(runs):
        poses[R] = ((5.1 + k * 1e-4, 0.2, 0.002), ID)
        contacts = contact.update(bpy.context, poses, tools, 8.0 + k * 0.011)
    per_ms = (perf_counter() - began) / runs * 1000.0
    assert contacts[R].touching and contacts[L].touching
    assert per_ms < 0.3, f"contact.update for 2 hands took {per_ms:.3f} ms"
    return per_ms, contacts, tools


def check_feel(contacts, tools):
    began = perf_counter()
    started = feel.start(bpy.context)  # never raises; False with no audio device
    start_ms = (perf_counter() - began) * 1000.0
    play_ms = 0.0
    if started:
        began = perf_counter()
        for kind in feel.LENGTH_S:
            feel.play(kind, 0.01)
        play_ms = (perf_counter() - began) * 1000.0
        assert play_ms < 5.0, play_ms  # aud plays asynchronously: nothing waits
    feel.update(bpy.context, contacts, tools, None, 8.9)
    began = perf_counter()
    for k in range(100):
        feel.update(bpy.context, contacts, tools, None, 8.9 + k * 0.001)
    update_ms = (perf_counter() - began) / 100 * 1000.0
    assert update_ms < 0.5, f"feel.update for 2 hands took {update_ms:.3f} ms"
    feel.update(bpy.context, contacts, tools, None, 9.0)
    contacts[R].entered = True
    feel.update(bpy.context, contacts, tools, None, 9.011)
    if started and contacts[R].pressed:
        assert feel._loops.get(R) is not None  # Pressed: the scrape loop runs.
        contacts[R].pressed = False
        feel.update(bpy.context, contacts, tools, None, 9.015)
        assert R not in feel._loops, "A lifted tip still in reach must stop the scrape loop"
        contacts[R].pressed = True
    for name in ("touch", "impact", "snip", "commit", "reject", "click", "thunk", "detent", "chip", "cancel"):
        feel.event(bpy.context, R, name, 0.5)
    feel.chips(contacts[R].point, contacts[R].normal, 10, (0.8, 0.5, 0.3), now=9.0)
    feel.flash(points=np.random.default_rng(1).random((3000, 3)), now=9.0)
    feel.preview(R, "bowl", {"point": (0, 0, 1), "normal": (0, 0, 1), "radius": 0.05, "step": 0.004}, now=9.0)
    feel.preview(L, "cone", {"nozzle": (0, 0, 1.2), "point": (0, 0, 1), "normal": (0, 0, 1), "radius": 0.05},
                 now=9.0)
    lines, fills, tris = feel.draw_data(9.05)
    assert lines and fills and len(tris) == 10, (len(lines), len(fills), len(tris))
    for points, colour, alpha in lines + fills + tris:
        assert isinstance(points, list) and isinstance(points[0], tuple) and len(colour) == 3 and 0 <= alpha <= 1
    assert feel.squash_for(R) is not None and feel.squash_for(R)[1] > 0, feel.squash_for(R)
    later = feel.draw_data(9.5)
    assert not later[2], "crumbs must expire after 0.4 s"
    feel.stop()
    # No audio device: start() reports False, logs once, and haptics/visuals still run.
    import aud
    real = aud.Device
    aud.Device = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no device"))
    try:
        assert feel.start(bpy.context) is False and feel.play("tap") is None
        feel.update(bpy.context, contacts, tools, None, 10.0)
        feel.event(bpy.context, R, "commit")
        assert feel.draw_data(10.0)[0]
    finally:
        aud.Device = real
        feel.stop()
    return started, start_ms, play_ms, update_ms


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and bpy.app.background
    assert os.environ.get("BLENDER_USER_CONFIG"), "Set BLENDER_USER_CONFIG to an empty temporary folder"
    xr_workbench.register()
    bpy.app.handlers.depsgraph_update_post.append(contact.on_depsgraph_update)
    try:
        contact.clear()
        ball, grid = scene()
        slid = check_sphere(ball)
        check_cache(ball)
        per_ms, contacts, tools = check_grid_and_timing(grid)
        started, start_ms, play_ms, update_ms = check_feel(contacts, tools)
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(contact.on_depsgraph_update)
        contact.clear()
        xr_workbench.unregister()
    print(f"XR_CONTACT_SMOKE_PASS blender={bpy.app.version_string} depth=1 push=1 clay_sink=1 cut_through=1 "
          f"slide_mps={np.mean(slid[1:]):.3f} transform_no_rebuild=1 geometry_rebuild=1 live_probe=1 open_mesh_side=1 "
          f"update_2_hands_200k_ms={per_ms:.3f} synth_ms={feel.stats['synth_ms']:.1f} audio={int(started)} "
          f"feel_start_ms={start_ms:.1f} play_6_ms={play_ms:.2f} feel_update_ms={update_ms:.3f} no_device_fallback=1 draw_data=1 hardware=0", flush=True)


if __name__ == "__main__":
    main()
