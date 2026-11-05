"""Factory-profile check of the 0.20 wiring: every new tool registers, has a label, a
colour and a prop; the Falloff Ring radii; the held-prop cache; the Tool Belt; and the
Retopo Loom trigger (task card now, CurioMesh on the next tick).

No XR session or headset: poses are synthetic and nothing is drawn (background
Blender has no GPU drawing). Run with BLENDER_USER_CONFIG at an empty temp folder.
"""
from pathlib import Path
import math
import sys

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (controllers, editing, interaction_log, onboarding, physical_tools, polish,  # noqa: E402
                          ray_feedback, tool_props, tool_wheel, ui)
from xr_workbench.interaction import CaptureOwner, InputProvenance  # noqa: E402

assert bpy.app.background and not bpy.data.filepath
bpy.context.preferences.edit.use_global_undo = True
bpy.context.preferences.edit.undo_steps = 32
xr_workbench.register()
try:
    settings = bpy.context.window_manager.xrwb_settings
    assert xr_workbench.bl_info["version"] >= (0, 20, 0)
    labels = {tool for tool, _l, _d in ui.ALL_TOOLS}
    for tool in physical_tools.TOOLS:
        assert tool in labels and tool in ray_feedback.TOOL_COLOURS, tool
        assert tool_props.prop_for(tool) is not None, tool
    for name in ("xrwb.menu_section", "xrwb.live_cage_mode", "xrwb.retopo_analyze", "xrwb.lab_add",
                 "xrwb.path_array", "xrwb.grow_kind", "xrwb.mask_extract"):
        family, action = name.split(".")
        assert getattr(getattr(bpy.ops, family), action).get_rna_type(), name
    assert hasattr(bpy.ops.curiomesh, "remesh") and hasattr(bpy.types.Scene, "curiomesh_settings")

    # Falloff Ring: three rings + dot; the Mask ring is dashed (half the segments).
    settings.sculpt_strength = 0.5
    info = ray_feedback._falloff_info(settings, "sculpt_clay")
    rings = ray_feedback.falloff_rings((0, 0, 0), (0, 0, 1), 0.1, info)
    assert len(rings) == 4 and [alpha for _p, alpha in rings[:3]] == [a * 0.95 for a in polish.FALLOFF_ALPHAS]
    dashed = ray_feedback.falloff_rings((0, 0, 0), (0, 0, 1), 0.1, ray_feedback._falloff_info(settings, "sculpt_mask"))
    assert len(dashed[0][0]) * 2 == len(rings[0][0])
    assert ray_feedback._falloff_info(settings, "select") is None

    # Held props: the tool on the main hand, the lattice glove on the off hand while Live Cage is on.
    poses = {controllers.RIGHT_HAND: (Vector((0.2, 0, 1)), Quaternion(), Vector((0, 0, -1))),
             controllers.LEFT_HAND: (Vector((-0.2, 0, 1)), Quaternion(), Vector((0, 0, -1)))}
    held = ray_feedback._held_props(bpy.context, settings, "sculpt_snake", poses, 1.0)
    assert [(h[0], h[4]) for h in held] == [("sculpt_snake", controllers.RIGHT_HAND),
                                            ("live_cage", controllers.LEFT_HAND)], held
    settings.tool_props = False
    assert ray_feedback._held_props(bpy.context, settings, "sculpt_snake", poses, 1.0) == []
    settings.tool_props = True

    # Tool Belt: off by default; on, six slots at hip height in front; a grip near one picks it.
    tool_wheel.update_belt(settings, (0, 0, 1.7), tuple(Quaternion((1, 0, 0), math.pi / 2)), 1.0)
    assert not tool_wheel._belt
    settings.tool_belt = True
    settings.workflow = "SCULPT"
    tool_wheel.update_belt(settings, (0, 0, 1.7), tuple(Quaternion((1, 0, 0), math.pi / 2)), 1.0)
    assert len(tool_wheel._belt) == 6, tool_wheel._belt
    tool, _label, slot, _yaw, _units = tool_wheel._belt[2]
    assert abs(slot[2] - 1.1) < 1e-6 and slot[1] > 0.25, slot  # 60 cm down, 30 cm in front (+Y).
    assert tool_wheel.belt_pick(Vector(slot) + Vector((0.02, 0, 0)), 1.0)[0] == tool
    assert tool_wheel.belt_pick((0, 0, 0), 1.0) is None
    settings.tool_belt = False
    tool_wheel._belt.clear()

    # Retopo Loom: the trigger shows the card at once and leaves the run to a timer; no stroke owns capture.
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(location=(0, 0, 0))
    bpy.ops.ed.undo_push(message="integration baseline")
    owner = CaptureOwner(InputProvenance.CONTROLLER, controllers.RIGHT_HAND, 0)
    result = physical_tools.begin(bpy.context, (0, 0, 3), tuple(Quaternion()), tool="retopo_loom", owner=owner)
    assert result is None and editing.active_transaction() is None
    assert interaction_log.last_message().startswith("Retopologising"), interaction_log.last_message()
    # Off hand in the air with the Retopo Loom: the dial sets the target in 500-face steps.
    off = CaptureOwner(InputProvenance.CONTROLLER, physical_tools.off_hand(), 0)
    dial = physical_tools.begin(bpy.context, (3, 3, 3), tuple(Quaternion((1, 0, 0), math.pi)),
                                tool="retopo_loom", owner=off)
    assert isinstance(dial, physical_tools.BrushDialStroke) and dial.readout() == "Retopo ~5,000 quads"
    dial._apply(bpy.context, type("P", (), {"position": (3, 3, 3.041), "rotation": tuple(Quaternion())})())
    assert settings.retopo_faces == 6000 and polish.detent_index(dial) == 6000, settings.retopo_faces
    dial.cancel(bpy.context, off)
    assert settings.retopo_faces == 5000
    settings.edit_tool = "retopo_loom"
    ok, message = controllers._adjust_brush(bpy.context, 1)
    assert ok and settings.retopo_faces == 5500, message
finally:
    editing.cancel_active(bpy.context)
    xr_workbench.unregister()
print("XR_INTEGRATION_020_SMOKE_PASS tools=1 falloff=1 props=1 belt=1 retopo_trigger=1 retopo_dial=1 "
      "pixels=0 hardware=0", flush=True)
