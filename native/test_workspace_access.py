"""Native Blender checks for editor access; no OpenXR or camera is started."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xr_workbench  # noqa: E402

xr_workbench.register()
xr_workbench.ui.configure_steamvr_session(bpy.context)
xr = bpy.context.window_manager.xr_session_settings
assert xr.use_positional_tracking and xr.use_absolute_tracking
assert xr.base_pose_type == "CUSTOM" and tuple(xr.base_pose_location) == (0, 0, 0)
assert xr.base_scale == xr.view_scale == 1.0
assert xr.show_controllers and xr.controller_draw_style == "LIGHT_RAY"
assert not xr.show_passthrough
assert bpy.context.preferences.inputs.xr_navigation.snap_turn
assert bpy.context.window_manager.xrwb_settings.controller_fallback
bpy.context.preferences.inputs.xr_navigation.turn_amount = 0.75
xr_workbench.ui.configure_steamvr_session(bpy.context)
assert bpy.context.preferences.inputs.xr_navigation.turn_amount == 0.75
window = bpy.context.window
assert window is not None
viewport = next(a for a in window.screen.areas if a.type == "VIEW_3D")
region = next(r for r in viewport.regions if r.type == "WINDOW")
operator = bpy.ops.xrwb.workspace_editor
with bpy.context.temp_override(window=window, area=viewport, region=region):
    for item in operator.get_rna_type().properties["editor"].enum_items:
        assert operator(editor=item.identifier) == {"FINISHED"}, item.identifier
        assert any(a.ui_type == item.identifier for a in window.screen.areas)
        assert viewport.type == "VIEW_3D"
    for kind in ("OBJECT", "MODIFIER", "DATA", "MATERIAL", "RENDER", "OUTPUT", "SCENE", "WORLD"):
        assert operator(editor="PROPERTIES", property_context=kind) == {"FINISHED"}, kind
    # Registered enum identifiers, not arbitrary icon names that fail only at draw time.
    icons = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items
    for name in ("WINDOW", "WORKSPACE", "PROPERTIES", "PREFERENCES", "INFO", "ORIENTATION_GIMBAL", "LOOP_BACK", "LOOP_FORWARDS"):
        assert name in icons, name
assert bpy.ops.wm.xr_editor_surface.get_rna_type().properties.get("separated")
xr_workbench.unregister()
print("XR_WORKSPACE_ACCESS_PASS editors=11 property_contexts=8 viewport_preserved=1 steamvr_settings=1 camera=0", flush=True)
