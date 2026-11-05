"""Factory-profile check of the 0.21 wiring: prefs and settings, the Workshop wheel, every new tool id,
the depsgraph handler, the 90 Hz pose timer, the contact-corrected props, the commit flash, and the
per-frame cost of contact + feel + props on a 200k-vertex mesh.

No XR session or headset: poses are synthetic and nothing is drawn (background Blender has no GPU
drawing). Run with BLENDER_USER_CONFIG and BLENDER_USER_SCRIPTS at an empty temp folder.
"""
import os
from pathlib import Path
import sys
from time import perf_counter

import bpy
import numpy as np
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (contact, contact_tools, controllers, feel, onboarding, physical_tools,  # noqa: E402
                          ray_feedback, tool_props, tool_wheel, ui)

R, L = controllers.RIGHT_HAND, controllers.LEFT_HAND
NEW_PREFS = {"touch_tools": True, "tool_sound_volume": 0.6, "tool_sound_mute": False,
             "contact_texture": True, "contact_nudge": False}


def handlers():
    return [h for h in bpy.app.handlers.depsgraph_update_post if h is xr_workbench._depsgraph_post]


def check_wiring(settings):
    assert xr_workbench.bl_info["version"] == (0, 21, 0)
    for key, value in NEW_PREFS.items():
        assert onboarding.DEFAULTS[key] == value and abs(getattr(settings, key) - value) < 1e-6, key
    assert settings.knife_split is False and settings.place_mode == "PLACE" and settings.uv_seam_unwrap is True
    workshop = [value for kind, value, _label in tool_wheel.WHEELS["WORKSHOP"] if kind == "tool"]
    assert set(workshop) == contact_tools.TOOLS and len(workshop) == 8, workshop
    assert ("tool", "mask_extract", "Extract") in tool_wheel.WHEELS["LAB"]
    assert any(key == "WORKSHOP" for key, _l, _d in tool_wheel.WHEEL_SETS)
    labels = {tool for tool, _l, _d in ui.ALL_TOOLS}
    for tool in sorted(contact_tools.TOOLS | physical_tools.TOOLS):
        assert tool in labels and tool in ray_feedback.TOOL_COLOURS, tool
        assert tool_props.prop_for(tool) is not None, tool
        settings.edit_tool = tool  # The enum accepts every id.
        assert settings.edit_tool == tool
    settings.workflow, settings.wheel_set, settings.edit_tool = "SCULPT", "AUTO", "chisel"
    assert tool_wheel.items_for(settings) is tool_wheel.WHEELS["WORKSHOP"]
    settings.edit_tool = "sculpt_clay"
    assert tool_wheel.items_for(settings) is tool_wheel.WHEELS["SCULPT"]
    assert len(handlers()) == 1, "depsgraph handler registered once"
    assert ray_feedback._pose_timer() == 0.25  # No XR session: returns early.


def grid():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=447, y_subdivisions=447, size=2.0, location=(0, 0, 0))
    obj = bpy.context.object
    bpy.context.view_layer.update()
    assert len(obj.data.vertices) >= 199_000, len(obj.data.vertices)
    return obj


def check_frame(settings):
    """Props are drawn at the contact-corrected pose; the cost of one frame for two hands."""
    settings.edit_tool = "sculpt_flatten"  # HARD: rests exactly on the surface.
    down = Quaternion()  # Prop -Z forward; the collider capsule runs back along +Z.
    started = feel.start(bpy.context)
    above = {R: (Vector((0.1, 0.2, 0.02)), down, Vector((0, 0, -1))),
             L: (Vector((-0.3, -0.2, 0.05)), down, Vector((0, 0, -1)))}
    ray_feedback.frame(bpy.context, above, 1.0)  # Approach from above: the grid's open side is remembered.
    inside = {R: (Vector((0.1, 0.2, -0.004)), down, Vector((0, 0, -1))),
              L: (Vector((-0.3, -0.2, 0.01)), down, Vector((0, 0, -1)))}
    contacts = ray_feedback.frame(bpy.context, inside, 1.011)
    c = contacts[R]
    assert c.touching and c.pressed and ray_feedback._touching == {R, L}, (c, ray_feedback._touching)
    prop = next(p for p in ray_feedback._props if p[4] == R)
    assert prop[5] is not None, prop
    tip = np.array((0.1, 0.2, -0.004)) + np.array(prop[5])
    assert abs(tip[2] - c.radius) < 0.0015, f"HARD prop tip should rest on the surface, z={tip[2]:.4f}"
    glove = next(p for p in ray_feedback._props if p[4] == L)
    assert glove[0] == "live_cage" and contacts[L].touching, glove
    # Timing: both hands sliding while pressed (texture, scrape loop, glow rings, props).
    now = 2.0
    for k in range(30):  # warm-up
        now += 1 / 90
        inside[R] = (Vector((0.1 + k * 0.003, 0.2, -0.004)), down, Vector((0, 0, -1)))
        ray_feedback.frame(bpy.context, inside, now)
    runs, spent, draw = 300, [], []
    for k in range(runs):
        now += 1 / 90
        inside[R] = (Vector((0.19 + k * 0.003 * (1 if k % 200 < 100 else -1), 0.2, -0.004)), down,
                     Vector((0, 0, -1)))
        began = perf_counter()
        ray_feedback.frame(bpy.context, inside, now)
        spent.append((perf_counter() - began) * 1000.0)
        began = perf_counter()
        feel.draw_data(now)
        draw.append((perf_counter() - began) * 1000.0)
    frame_ms, frame_p95 = float(np.median(spent)), float(np.percentile(spent, 95))
    draw_ms = float(np.median(draw))
    assert frame_ms < 1.0, f"contact + feel + props took {frame_ms:.3f} ms per frame"
    return started, frame_ms, frame_p95, draw_ms


def check_commit_flash():
    class Stroke:
        feel_commit, flashed = True, False
        current = np.random.default_rng(1).random((5000, 3))
        touched = np.zeros(5000, dtype=bool)
    Stroke.touched[:3000] = True
    feel._flash.clear()
    controllers.feel_commit(bpy.context, R, Stroke())
    assert len(feel._flash) == 1 and len(feel._flash[0][1]) == 2000 * 6, len(feel._flash[0][1])
    Stroke.flashed = True
    feel._flash.clear()
    controllers.feel_commit(bpy.context, R, Stroke())
    assert not feel._flash, "A stroke that flashed itself is not flashed twice"
    controllers.feel_commit(bpy.context, R, object())  # Not a stroke: nothing, no error.


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and bpy.app.background
    assert os.environ.get("BLENDER_USER_CONFIG"), "Set BLENDER_USER_CONFIG to an empty temporary folder"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    xr_workbench.register()
    try:
        settings = bpy.context.window_manager.xrwb_settings
        check_wiring(settings)
        grid()
        started, frame_ms, frame_p95, draw_ms = check_frame(settings)
        check_commit_flash()
    finally:
        xr_workbench.unregister()
    assert not handlers(), "depsgraph handler removed on unregister"
    assert not ray_feedback._props and contact.current(R) is None
    xr_workbench.register()  # Register again: still exactly one handler.
    assert len(handlers()) == 1
    xr_workbench.unregister()
    print(f"XR_INTEGRATION_021_SMOKE_PASS blender={bpy.app.version_string} prefs=5 workshop=8 handler_once=1 "
          f"pose_timer_idle=1 prop_on_surface=1 commit_flash=1 audio={int(bool(started))} "
          f"frame_2_hands_200k_ms={frame_ms:.3f} p95_ms={frame_p95:.3f} feel_draw_data_ms={draw_ms:.3f}",
          flush=True)


main()
