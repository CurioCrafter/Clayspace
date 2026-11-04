"""Run in an isolated factory-startup Blender; writes only a temporary USD file."""

import tempfile
from pathlib import Path

import bpy


required = (
    "cycles", "cycles_osl", "usd", "openvdb", "libmv", "alembic",
    "fluid", "mod_oceansim", "mod_remesh", "io_fbx", "xr_openxr",
)
missing = [name for name in required if not getattr(bpy.app.build_options, name, False)]
assert not missing, f"Missing expected release features: {missing}"
assert all(hasattr(bpy.ops.wm, name) for name in (
    "xr_task_card", "xr_task_card_event", "xr_editor_surface", "xr_editor_surface_event",
))
assert all(hasattr(bpy.types.XrSessionState, name) for name in (
    "task_card_info", "task_card_pose", "hand_tracking_info", "hand_joint_sample",
    "hand_tracking_world_transform", "camera_gesture_observation",
))
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 1
scene.cycles.use_denoising = False
scene.render.resolution_x = scene.render.resolution_y = 16
scene.render.resolution_percentage = 100
assert "FINISHED" in bpy.ops.render.render()
assert bpy.data.images.get("Render Result") is not None
with tempfile.TemporaryDirectory(prefix="xrwb-feature-smoke-") as folder:
    usd_path = Path(folder) / "factory-scene.usda"
    assert "FINISHED" in bpy.ops.wm.usd_export(filepath=str(usd_path))
    assert usd_path.stat().st_size > 100
print("XR_FEATURE_BUILD_PASS cpu_cycles_render=1 usd_export=1 release_flags=1 native_xr_api=1 gpu_cycles_verified=0")
