"""Check of the Tool Props: load the .npz, build every prop's batch data in every
theme and, where a GPU context exists, draw each prop and wheel icon offscreen.

Headless: blender -b --factory-startup --python scripts/xr_tool_props_smoke.py
  Background Blender has no GPU drawing ("GPU functions for drawing are not
  available in background mode"), so this checks the exact arrays the batches
  are built from (positions, lit colours, indices) and the motion matrices.
With a window (no -b) the same script also builds the real gpu batches, draws
them into an offscreen buffer and quits.
Use BLENDER_USER_CONFIG pointing at an empty temporary folder.
"""
from pathlib import Path
import sys

import bpy
from mathutils import Matrix
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xr_workbench import polish, tool_props  # noqa: E402

props = tool_props.prop_ids()
for theme in polish.THEMES:
    polish.apply_theme(theme)
    accent = tool_props._accent(None)
    assert accent == polish.THEMES[theme][2]["ACCENT"]
    for prop in props:
        data = tool_props._load()[prop]
        pos, colours = tool_props.shade(prop, (0.2, 0.9, 0.3), (0.0, -1.0, 0.0), accent, squeeze=1.0)
        assert pos.shape == data["pos"].shape and colours.shape == (len(pos), 4), prop
        assert np.isfinite(pos).all() and 0.0 <= colours.min() and colours.max() <= 1.0, prop
        assert int(data["idx"].max()) < len(pos), prop
assert tool_props.prop_for("select") is None and tool_props.prop_for("sculpt_snake") == "snake_hook"
spin = tool_props._motion("subdiv_lab", 100.0, "smoke") @ tool_props._motion("subdiv_lab", 100.5, "smoke").inverted()
assert abs(spin.to_euler().z) > 0.1, "The helix wand spins"
mode = "cpu"

if not bpy.app.background:
    import gpu
    offscreen = gpu.types.GPUOffScreen(256, 256)
    with offscreen.bind():
        gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
        gpu.matrix.load_matrix(Matrix.Translation((0.0, 0.0, -0.4)))  # 40 cm in front of the eye.
        gpu.matrix.load_projection_matrix(Matrix.Diagonal((4.0, 4.0, -1.0, 1.0)))
        for prop in props:
            assert tool_props.draw(None, prop, Matrix.Identity(4), squeeze=0.5, hand="smoke")
            assert tool_props.wheel_icon(prop, Matrix.Identity(4), 0.05)
        assert not tool_props.draw(None, "select", Matrix.Identity(4))
        assert tool_props.draw(None, "sculpt_snake", Matrix.Identity(4), theme="OCEAN")
        pixels = np.array(offscreen.texture_color.read().to_list(), dtype=np.float32)
    offscreen.free()
    assert pixels.max() > 0.0, "Props drew nothing"
    mode = f"gpu batches={len(tool_props._batches)}"

print(f"XR_TOOL_PROPS_SMOKE_PASS props={len(props)} mode={mode}", flush=True)
if not bpy.app.background:
    tool_props.clear()  # Free the batches while the GPU context is still alive.
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.1)
