"""Combine selected objects with native, still-editable Boolean modifiers.

The active object is the target; every other selected mesh becomes a cutter
shown as wire and hidden from renders. Moving a cutter later changes the live
result. Apply bakes only our "XR " Boolean modifiers and hides (never deletes)
their cutters. Each button is one undo step.
"""
from __future__ import annotations

import bpy

from . import editing

OPERATIONS = (("UNION", "Union", "Join the selected objects into the active one"),
              ("DIFFERENCE", "Subtract", "Cut the other selected objects out of the active one"),
              ("INTERSECT", "Intersect", "Keep only where the active object overlaps the others"))
CUTTER_KEY = "xrwb_cutter"


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("boolean", message, context, level=level)


def _undo_push(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in bpy.ops.ed.undo_push(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def _target_and_cutters(context):
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode to combine objects")
    if not editing.undo_available(context) or editing.active_transaction() is not None:
        raise editing.EditRejected("Release the current edit and enable Global Undo first")
    target = context.active_object
    if (target is None or target.type != "MESH" or target not in context.selected_editable_objects
            or target.library or target.override_library or "xrwb_swatch" in target.keys()):
        raise editing.EditRejected("Make the object to keep active (select it last)")
    cutters = [obj for obj in context.selected_editable_objects
               if obj is not target and obj.type == "MESH" and "xrwb_swatch" not in obj.keys()]
    if not cutters:
        raise editing.EditRejected("Select the cutter objects too, then the target last")
    return target, cutters


def combine(context, operation):
    target, cutters = _target_and_cutters(context)
    label = next(item[1] for item in OPERATIONS if item[0] == operation)
    for cutter in cutters:
        modifier = target.modifiers.new(f"XR {label} {cutter.name}"[:63], "BOOLEAN")
        modifier.operation = operation
        modifier.solver = "EXACT"
        modifier.object = cutter
        cutter.display_type = "WIRE"
        cutter.hide_render = True
        cutter[CUTTER_KEY] = target.name
        cutter.select_set(False)
    target.select_set(True)
    context.view_layer.objects.active = target
    context.view_layer.update()
    evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
    empty = len(evaluated.data.vertices) == 0
    return target, len(cutters), empty


def apply(context):
    if context.mode != "OBJECT" or editing.active_transaction() is not None:
        raise editing.EditRejected("Switch to Object Mode and release the current edit")
    target = context.active_object
    if target is None or target.type != "MESH":
        raise editing.EditRejected("Make the combined object active")
    if target.data.users != 1 or target.data.library:
        raise editing.EditRejected("Apply needs single-user, local mesh data")
    names = [m.name for m in target.modifiers if m.type == "BOOLEAN" and m.name.startswith("XR ")]
    if not names:
        raise editing.EditRejected("No XR Boolean modifiers on the active object")
    cutters = {target.modifiers[name].object for name in names} - {None}
    with context.temp_override(object=target, active_object=target, selected_objects=[target]):
        for name in names:
            if "FINISHED" not in bpy.ops.object.modifier_apply(modifier=name):
                raise editing.EditRejected(f"Blender could not apply {name}")
    still_used = {m.object for obj in context.scene.objects for m in obj.modifiers
                  if m.type == "BOOLEAN" and m.object is not None}
    hidden = 0
    for cutter in cutters - still_used:
        cutter.hide_set(True)
        hidden += 1
    return len(names), hidden


class XRWB_OT_boolean_selected(bpy.types.Operator):
    bl_idname = "xrwb.boolean_selected"
    bl_label = "Combine Selected"
    bl_description = "Native Boolean of the selected objects into the active one; cutters stay editable"
    operation: bpy.props.EnumProperty(items=OPERATIONS, options={"SKIP_SAVE"})

    def execute(self, context):
        try:
            target, count, empty = combine(context, self.operation)
            _undo_push(f"XR Combine {self.operation.title()}")
        except (editing.EditRejected, RuntimeError, TypeError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        label = next(item[1] for item in OPERATIONS if item[0] == self.operation)
        if empty:
            _log(context, f"{label}: result is empty. Move a cutter so the objects overlap.", "WARNING")
        else:
            _log(context, f"{label}: {count} cutter(s) on {target.name}. Move a cutter to adjust.")
        return {"FINISHED"}


class XRWB_OT_apply_booleans(bpy.types.Operator):
    bl_idname = "xrwb.apply_booleans"
    bl_label = "Apply Booleans"
    bl_description = "Bake the active object's XR Boolean results and hide their unused cutters"

    def execute(self, context):
        try:
            count, hidden = apply(context)
            _undo_push("XR Apply Booleans")
        except (editing.EditRejected, RuntimeError, TypeError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        _log(context, f"Applied {count} Boolean(s); {hidden} cutter(s) hidden, not deleted.")
        return {"FINISHED"}


CLASSES = (XRWB_OT_boolean_selected, XRWB_OT_apply_booleans)
