"""Own-code native bevel and registration check, with no purchased add-ons."""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xr_workbench  # noqa: E402

assert bpy.app.background and not bpy.data.filepath
assert not any("hardops" in key.lower() or "boxcutter" in key.lower()
               for key in bpy.context.preferences.addons.keys())
xr_workbench.register()
bpy.context.preferences.edit.use_global_undo = True
bpy.context.preferences.edit.undo_steps = 32
settings = bpy.context.window_manager.xrwb_settings
assert len(xr_workbench.ui.ALL_TOOLS) == 31
for tool, _label, _description in xr_workbench.ui.ALL_TOOLS:
    settings.edit_tool = tool
    assert settings.edit_tool == tool
obj = bpy.context.active_object
assert obj.type == "MESH" and obj.name == "Cube"
mesh = obj.data
original = tuple(tuple(v.co) for v in mesh.vertices)
# Keep an existing stack entry: bevel must append, not erase or bake it.
existing = obj.modifiers.new("Existing disabled mirror", "MIRROR")
existing.show_viewport = False
bpy.ops.ed.undo_push(message="Native bevel fixture")
assert bpy.ops.xrwb.bevel_object() == {"FINISHED"}
assert len(obj.modifiers) == 2 and obj.modifiers[-1].type == "BEVEL"
assert abs(obj.modifiers[-1].width - settings.bevel_width_m) < 1e-6
assert obj.modifiers[-1].segments == 3
bpy.context.view_layer.update()
evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
assert len(evaluated.data.vertices) > 8
assert obj.data == mesh and tuple(tuple(v.co) for v in mesh.vertices) == original
with xr_workbench.ui._view3d_override(bpy.context):
    assert bpy.ops.ed.undo() == {"FINISHED"}
    obj = bpy.data.objects["Cube"]
    assert len(obj.modifiers) == 1
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert len(bpy.data.objects["Cube"].modifiers) == 2
obj = bpy.data.objects["Cube"]
obj.scale = (2, 1, 1)
bpy.context.view_layer.update()
assert not bpy.ops.xrwb.bevel_object.poll()
try:
    bpy.ops.xrwb.bevel_object()
    raise AssertionError("Nonuniform bevel was not rejected by poll")
except RuntimeError as exc:
    assert "uniform scale" in str(exc), str(exc)
assert len(obj.modifiers) == 2
obj.scale = (float("nan"), 1, 1)
bpy.context.view_layer.update()
assert not bpy.ops.xrwb.bevel_object.poll()
try:
    bpy.ops.xrwb.bevel_object()
    raise AssertionError("Nonfinite bevel was not rejected by poll")
except RuntimeError as exc:
    assert "uniform scale" in str(exc), str(exc)
assert len(obj.modifiers) == 2
# Same modifier action on a native cutter stack, with no purchased add-ons.
obj.scale = (1, 1, 1)
obj.modifiers.remove(obj.modifiers[-1])
bpy.context.view_layer.update()
from xr_workbench import draw_editing  # noqa: E402
from xr_workbench.interaction import InputSample, Pose  # noqa: E402
transaction = draw_editing.begin(bpy.context, (0, 0, .2), (1, 0, 0, 0), "cut_difference")
sample = InputSample(transaction.request.owner, 0, Pose((1.5, 1.5, 1.7)), 1.0)
assert transaction.update(bpy.context, sample).applied
assert transaction.commit(bpy.context).applied
obj = bpy.data.objects["Cube"]
assert obj.modifiers[-1].type == "BOOLEAN"
context_mesh = obj.data
assert bpy.ops.xrwb.bevel_object() == {"FINISHED"}
assert [m.type for m in obj.modifiers][-2:] == ["BOOLEAN", "BEVEL"]
bpy.context.view_layer.update()
assert len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices) > 8
assert obj.data == context_mesh
with xr_workbench.ui._view3d_override(bpy.context):
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert bpy.data.objects["Cube"].modifiers[-1].type == "BOOLEAN"
xr_workbench.unregister()
print("XR_NATIVE_TOOLS_PASS own_code=1 purchased_addons=0 tools=31 bevel_geometry=1 undo=1 redo=1 preserves_stack=1 invalid_transform_rejected=1 bevel_after_boolean=1", flush=True)
