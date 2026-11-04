"""Run with the candidate Blender --background --factory-startup --python.

This checks registration and safe no-session behavior, not headset rendering.
"""
import bpy

assert bpy.app.version[:3] == (5, 1, 2), bpy.app.version
assert bpy.app.build_options.xr_openxr
assert not bpy.types.XrSessionState.controller_aim_active_get(bpy.context, 0)
assert not bpy.types.XrSessionState.controller_aim_active_get(bpy.context, 255)
for name in ("xr_editor_surface", "xr_editor_surface_event", "xr_editor_surface_capture",
             "xr_editor_surface_transform", "xr_hand_menu"):
    operator = getattr(bpy.ops.wm, name)
    assert operator.get_rna_type().identifier
    assert not operator.poll(), name
state = tuple(bpy.types.XrSessionState.editor_surface_info(bpy.context))
assert state == (0, 0, 0, -1, 0, 0), state
assert tuple(bpy.types.XrSessionState.hand_menu_info(bpy.context)) == state
assert tuple(bpy.types.XrSessionState.hand_menu_pose(bpy.context)) == (0,) * 9
pose = tuple(bpy.types.XrSessionState.editor_surface_pose(bpy.context))
assert pose == (0,) * 9, pose
hit = tuple(bpy.types.XrSessionState.editor_surface_hit(
    bpy.context, ray_origin=(0, 0, 1), ray_direction=(0, 0, -1)))
assert hit == (0, -1, 0, 0, 0), hit
transform_props = bpy.ops.wm.xr_editor_surface_transform.get_rna_type().properties
assert transform_props["position"].array_length == 3
assert transform_props["rotation"].array_length == 4
assert transform_props["width"].default == 0
surface_props = bpy.ops.wm.xr_editor_surface.get_rna_type().properties
assert "separated" in surface_props
assert surface_props["separated"].default is False
props = bpy.ops.wm.xr_editor_surface_event.get_rna_type().properties
for name in ("type", "value", "use_ray", "ray_origin", "ray_direction", "u", "v",
             "pointer_id", "unicode", "shift", "ctrl", "alt", "target"):
    assert name in props, name
assert {item.identifier for item in props['target'].enum_items} == {'AUTO', 'EDITOR', 'MENU'}
print("XR_EDITOR_API_PASS registered=5 no_session_safe=1 hand_menu=1 rig_transform=1 hit_query=1 camera_requested=0", flush=True)
