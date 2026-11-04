"""Background API registration/no-session guard checks. Does not start XR."""
import sys
from importlib import import_module
from pathlib import Path
import bpy

assert hasattr(bpy.ops.wm, "xr_task_card")
assert hasattr(bpy.ops.wm, "xr_task_card_event")
assert not bpy.ops.wm.xr_task_card.poll()
assert not bpy.ops.wm.xr_task_card_event.poll()
info = tuple(bpy.types.XrSessionState.task_card_info(bpy.context))
assert info == (0, 0, 0, -1, 0, 0), info
assert not any(bpy.types.XrSessionState.task_card_pose(bpy.context))
show = bpy.ops.wm.xr_task_card.get_rna_type().properties
assert not show["allow_simulated_input"].default
event = bpy.ops.wm.xr_task_card_event.get_rna_type().properties
assert tuple(event["phase"].enum_items.keys()) == ("MOVE", "PRESS", "RELEASE", "CANCEL")
assert {"generation", "pointer_id", "ray_origin", "ray_direction"} <= set(event.keys())
assert not event["cancel_only"].default
assert not event["image_local"].default
assert {"image_x", "image_y"} <= set(event.keys())
assert event["pointer_id"].hard_max == 2
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
xr_workbench = import_module("xr_workbench")
xr_workbench.register()
assert hasattr(bpy.ops.xrwb, "set_edit_tool")
assert not bpy.ops.xrwb.set_edit_tool.poll()
tools = bpy.ops.xrwb.set_edit_tool.get_rna_type().properties["tool"].enum_items
assert tuple(tools.keys()) == ("select", "move", "rotate", "scale")
assert hasattr(bpy.ops.xrwb, "add_cube") and not bpy.ops.xrwb.add_cube.poll()
assert hasattr(bpy.ops.xrwb, "history_undo") and not bpy.ops.xrwb.history_undo.poll()
assert hasattr(bpy.ops.xrwb, "history_redo") and not bpy.ops.xrwb.history_redo.poll()
assert hasattr(bpy.ops.xrwb, "cancel_scene_edit") and not bpy.ops.xrwb.cancel_scene_edit.poll()
print("XR_TASK_API_PASS registered=7 no_session_safe=1 simulator_default_off=1 tool_bridge=1 add_bridge=1 history_bridge=1 cancel_bridge=1 image_pointer=1")
