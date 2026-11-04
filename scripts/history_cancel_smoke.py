"""Verify nested Undo/Redo survives an UNDO-enabled outer CANCELLED result."""

from __future__ import annotations

import bpy


class VRW_OT_history_cancel_probe(bpy.types.Operator):
    bl_idname = "vrw.history_cancel_probe"
    bl_label = "VRW History Cancel Probe"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, _context):
        result = bpy.ops.ed.undo()
        assert result == {"FINISHED"}, result
        return {"CANCELLED"}


bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.0))
name = bpy.context.object.name
bpy.ops.ed.undo_push(message="VRW initial")
bpy.data.objects[name].location.x = 1.0
bpy.context.view_layer.update()
bpy.ops.ed.undo_push(message="VRW translated")
assert abs(bpy.data.objects[name].location.x - 1.0) < 1.0e-6

bpy.utils.register_class(VRW_OT_history_cancel_probe)
try:
    assert bpy.ops.vrw.history_cancel_probe() == {"CANCELLED"}
    assert abs(bpy.data.objects[name].location.x) < 1.0e-6
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert abs(bpy.data.objects[name].location.x - 1.0) < 1.0e-6
finally:
    bpy.utils.unregister_class(VRW_OT_history_cancel_probe)

print("VRW_HISTORY_CANCEL_REDO_PASS")
