"""Modifier Tower: an object's real modifier stack as blocks stacked in front of you.

The first modifier is the bottom block and the last the top, the order
Blender evaluates them in. Each block carries its modifier's name and main
value; a hidden modifier's block goes dark.

* Grip a block and drop it higher or lower: the modifier moves to that place
  in the stack.
* Trigger a block (any tool): show or hide that modifier.
* Point at a block and push the right stick up or down: its main value turns
  like a dial (Subdivision levels, Bevel width, Array count...).
* The Modifier Tower menu adds modifiers from the shelf (they land on top),
  applies or removes them, and opens a Geometry Nodes tree in the Node Garden.

Every change is one undo step on the real stack. The blocks are only a view,
owned by one collection; putting the tower away deletes just those objects.
"""
from __future__ import annotations

import math

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector

from . import editing, node_garden

COLLECTION = "XR Modifier Tower"
ROOT_KEY = "xrwb_tower"
BLOCK_KEY = "xrwb_tower_block"
WIDTH_M, HEIGHT_M, DEPTH_M, GAP_M = 0.2, 0.04, 0.05, 0.012
PITCH_M = HEIGHT_M + GAP_M
TARGET_TYPES = {"MESH", "CURVE", "FONT", "SURFACE"}
ON_COLOUR, OFF_COLOUR = (0.12, 0.36, 0.55), (0.05, 0.05, 0.06)
_resnap = False  # Set by undo/redo: rebuild from the stack, never read block moves as edits.


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("modifiers", message, context, level=level)


def _undo(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def collection(context=None):
    found = bpy.data.collections.get(COLLECTION)
    scene = getattr(context, "scene", None) or bpy.context.scene
    if found is not None and scene.collection.children.get(found.name) is None:
        return None
    return found


def root(context=None):
    found = collection(context)
    return next((obj for obj in found.objects if ROOT_KEY in obj.keys()), None) if found else None


def is_out(context=None) -> bool:
    return root(context) is not None


def object_of(tower):
    return bpy.data.objects.get(tower[ROOT_KEY]) if tower is not None else None


def is_overlay(obj) -> bool:
    """Tower, garden and shelf objects are views, never modifier targets."""
    return any(key.startswith(("xrwb_tower", "xrwb_garden", "xrwb_swatch")) for key in obj.keys())


def target_object(context):
    """The object modifier tools act on: the tower's object while one of its blocks is active."""
    obj = context.active_object
    if obj is not None and BLOCK_KEY in obj.keys():
        return object_of(root(context))
    return obj


def main_property(modifier):
    """(path, RNA property) of the number a block's dial turns, or None."""
    from . import studio
    for path in studio.MODIFIER_PROPS.get(modifier.type, ()):
        prop = modifier.bl_rna.properties.get(path)
        if prop is not None and prop.type in {"INT", "FLOAT"} and not getattr(prop, "is_array", False):
            return path, prop
    return None


def value_text(modifier) -> str:
    found = main_property(modifier)
    if found is None:
        return ""
    path, prop = found
    value = getattr(modifier, path)
    if prop.type == "INT":
        return f"{prop.name} {value}"
    if prop.subtype == "ANGLE":
        return f"{prop.name} {math.degrees(value):.0f}°"
    if prop.subtype == "DISTANCE":
        from . import polish
        return f"{prop.name} {polish.length(value)}"
    return f"{prop.name} {value:.3g}"


def dialled(value, prop, notches: int):
    """The value after turning a dial ``notches`` clicks; ints step by one, floats by 10%."""
    if prop.type == "INT":
        new = value + notches
    else:
        new = value
        for _ in range(abs(notches)):
            new += math.copysign(abs(new) * 0.1 if abs(new) > 1.0e-3 else 0.01, notches)
    return type(value)(min(max(new, prop.soft_min), prop.soft_max))


def order_from_heights(heights) -> list[str]:
    """Modifier names bottom to top from (name, block height) pairs."""
    return [name for name, _z in sorted(heights, key=lambda item: item[1])]


def signature(obj) -> str:
    return "|".join(f"{m.name}:{m.type}:{int(m.show_viewport)}:{value_text(m)}" for m in obj.modifiers)


def blocks(tower):
    return [child for child in tower.children if BLOCK_KEY in child.keys()]


def _material(on):
    name = "XR Tower On" if on else "XR Tower Off"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True  # Blender 4.5 starts without nodes; 5.0+ ignores this
        colour = (*(ON_COLOUR if on else OFF_COLOUR), 1.0)
        material.diffuse_color = colour
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colour
    return material


def build(context, obj, placement=None):
    """Stack a block per modifier of ``obj``; ``placement`` keeps an old tower's transform."""
    global _resnap
    _resnap = False  # Built from the stack as it is now.
    if obj is None or obj.type not in TARGET_TYPES or is_overlay(obj):
        raise editing.EditRejected("Select a mesh or curve to see its modifiers")
    if collection(context) is not None:
        raise editing.EditRejected("The modifier tower is already out")
    owner = bpy.data.collections.new(COLLECTION)
    owner.hide_render = True
    context.scene.collection.children.link(owner)
    try:
        tower = bpy.data.objects.new("XR Modifier Tower", None)
        tower.empty_display_type = "PLAIN_AXES"
        tower.empty_display_size = 0.02
        tower[ROOT_KEY] = obj.name
        tower["xrwb_signature"] = signature(obj)
        if placement is not None:
            tower.matrix_world = placement
        else:
            from . import materials_lab
            location, rotation, units = materials_lab.viewer_frame_flat(context)
            # Ahead and to the right, base at waist height: blocks climb towards your eyes.
            tower.location = (location + rotation @ Vector((0.3, 0.55, 0.0)) * units
                              - Vector((0.0, 0.0, 0.45 * units)))
            tower.rotation_euler = rotation.to_euler()
            tower.scale = (units,) * 3  # Children are in metres.
        tower.hide_select = True
        owner.objects.link(tower)
        for index, modifier in enumerate(obj.modifiers):
            mesh = node_garden._box_mesh(f"XR Tower {modifier.name}", WIDTH_M, HEIGHT_M, DEPTH_M)
            mesh.materials.append(_material(modifier.show_viewport))
            block = bpy.data.objects.new(f"XR Tower {modifier.name}", mesh)
            block[BLOCK_KEY] = modifier.name
            block.hide_render = True
            block.parent = tower
            block.location = slot(index)
            owner.objects.link(block)
            text = f"{modifier.name}  {value_text(modifier)}".strip()
            node_garden._label(owner, block, text[:30], (0.008, -DEPTH_M / 2.0 - 0.001, -HEIGHT_M * 0.7), 0.014)
    except Exception:
        remove(context)
        raise
    return tower


def slot(index):
    """Block origin (its top-left corner, like a node garden block) for stack position ``index``."""
    return (-WIDTH_M / 2.0, 0.0, index * PITCH_M + HEIGHT_M)


def remove(context) -> int:
    found = bpy.data.collections.get(COLLECTION)
    if found is None:
        return 0
    owned = sorted(found.objects, key=lambda obj: ROOT_KEY in obj.keys())  # Children before the root.
    for obj in owned:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.curves).remove(data)
    for material in [m for m in bpy.data.materials
                     if m.name.startswith(("XR Tower ", "XR Garden Label")) and m.users == 0]:
        bpy.data.materials.remove(material)
    if not found.objects and not found.children:
        bpy.data.collections.remove(found)
    return len(owned)


def rebuild(context, obj=None) -> None:
    tower = root(context)
    placement = tower.matrix_world.copy()
    obj = obj or object_of(tower)
    remove(context)
    build(context, obj, placement)


def sync(context) -> str:
    """Keep the blocks and the stack together; "" when nothing changed.

    A block gripped to a new height moves its modifier there (one undo step);
    a stack changed anywhere else (desktop, menus, undo) rebuilds the tower.
    """
    global _resnap
    tower = root(context)
    if tower is None:
        _resnap = False
        return ""
    obj = object_of(tower)
    if obj is None:
        remove(context)
        return "removed"
    if _resnap or tower.get("xrwb_signature") != signature(obj):
        _resnap = False
        rebuild(context)
        return "rebuilt"
    names = [modifier.name for modifier in obj.modifiers]
    found = blocks(tower)
    moved = [block for block in found if block[BLOCK_KEY] in names
             and (block.location - Vector(slot(names.index(block[BLOCK_KEY])))).length > 1.0e-4]
    if not moved:
        return ""
    order = order_from_heights([(block[BLOCK_KEY], block.location.z) for block in found])
    name = moved[0][BLOCK_KEY]
    old, new = names.index(name), order.index(name)
    if old != new:
        obj.modifiers.move(old, new)
    rebuild(context)
    if old == new:
        return "snapped"
    _undo(f"XR Move {name}")
    _log(context, f"{name} moved to {'the top' if new == len(names) - 1 else 'the bottom' if new == 0 else f'place {new + 1}'}"
                  " of the stack.")
    return "reordered"


def block_at(context, origin, direction):
    """The tower block (or its label's block) a ray hits first, or None."""
    from . import viewer_scale
    if not is_out(context):
        return None
    depsgraph = context.evaluated_depsgraph_get()
    hit, _location, _normal, _face, obj, _matrix = context.scene.ray_cast(
        depsgraph, Vector(origin), Vector(direction), distance=50.0 * viewer_scale.world_units_per_metre(context))
    obj = obj.original if hit and obj is not None else None
    if obj is not None and BLOCK_KEY not in obj.keys() and obj.parent is not None:
        obj = obj.parent
    return obj if obj is not None and BLOCK_KEY in obj.keys() else None


def _modifier(context, name):
    obj = object_of(root(context))
    modifier = obj.modifiers.get(name) if obj is not None else None
    if modifier is None:
        raise editing.EditRejected("That modifier is gone")
    if obj.library or obj.override_library:
        raise editing.EditRejected("Linked objects cannot change modifiers")
    return modifier


def switch(context, name) -> str:
    """Show or hide a modifier (one undo step)."""
    modifier = _modifier(context, name)
    modifier.show_viewport = not modifier.show_viewport
    _undo(f"XR {'Show' if modifier.show_viewport else 'Hide'} {name}")
    sync(context)
    return f"{name} {'on' if modifier.show_viewport else 'off'}"


def dial(context, name, notches) -> str:
    """Turn a modifier's main value by ``notches`` clicks (one undo step)."""
    modifier = _modifier(context, name)
    found = main_property(modifier)
    if found is None:
        raise editing.EditRejected(f"{name} has no dial value; its settings are in the Modifier Tower menu")
    path, prop = found
    before = getattr(modifier, path)
    setattr(modifier, path, dialled(before, prop, notches))
    if getattr(modifier, path) == before:
        raise editing.EditRejected(f"{name}: {prop.name} is at its limit")
    _undo(f"XR Dial {name}")
    sync(context)
    return f"{name}: {value_text(modifier)}"


@persistent
def _after_undo(*_args):
    global _resnap
    _resnap = True


def register_handlers():
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _after_undo not in handlers:
            handlers.append(_after_undo)


def unregister_handlers():
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _after_undo in handlers:
            handlers.remove(_after_undo)


class XRWB_OT_modifier_tower(bpy.types.Operator):
    bl_idname = "xrwb.modifier_tower"
    bl_label = "Modifier Tower"
    bl_description = "The modifier stack as blocks: grip to reorder, trigger to show/hide, stick to dial"
    action: bpy.props.EnumProperty(
        items=(("TOGGLE", "Bring Out / Put Away", ""), ("BRING", "Bring Here", "Put the tower in front of you"),
               ("RETARGET", "Show Selected", "Show the active object's stack instead"),
               ("SWITCH", "Show / Hide", ""), ("DIAL", "Dial", "")),
        default="TOGGLE", options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(options={"SKIP_SAVE"})
    notches: bpy.props.IntProperty(default=1, options={"SKIP_SAVE"})

    def execute(self, context):
        if context.mode != "OBJECT" or editing.active_transaction() is not None:
            _log(context, "Switch to Object Mode and release the current edit", "WARNING")
            return {"CANCELLED"}
        try:
            if self.action == "SWITCH":
                message = switch(context, self.name)
            elif self.action == "DIAL":
                message = dial(context, self.name, self.notches)
            elif self.action == "TOGGLE" and is_out(context):
                count = remove(context)
                _undo("XR Put Away Modifier Tower")
                message = f"Modifier tower put away ({count} objects)."
            else:
                if self.action == "TOGGLE":
                    obj = target_object(context)
                elif not is_out(context):
                    raise editing.EditRejected("Bring out the modifier tower first")
                else:
                    obj = context.active_object if self.action == "RETARGET" else object_of(root(context))
                if self.action != "TOGGLE":
                    remove(context)
                tower = build(context, obj)
                _undo("XR Modifier Tower")
                count = len(blocks(tower))
                message = (f"{obj.name}: {count} modifier blocks, first at the bottom. Grip to reorder, "
                           "trigger to show/hide, point and stick to dial." if count else
                           f"{obj.name} has no modifiers yet: add one from the Modifier Tower menu.")
        except (editing.EditRejected, RuntimeError, ReferenceError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        _log(context, message)
        return {"FINISHED"}


class XRWB_MT_modifier_tower(bpy.types.Menu):
    bl_idname = "XRWB_MT_modifier_tower"
    bl_label = "Modifier Tower"

    def draw(self, context):
        layout = self.layout
        obj = object_of(root(context))
        layout.label(text=f"MODIFIER TOWER: {obj.name}" if obj else "MODIFIER TOWER", icon="MODIFIER")
        layout.operator("xrwb.modifier_tower", text="Put Away" if obj else "Bring Out",
                        icon="X" if obj else "MODIFIER").action = "TOGGLE"
        if obj is None:
            layout.label(text="Select a mesh, then Bring Out.")
            return
        active = context.active_object
        if active is not None and active != obj and active.type in TARGET_TYPES and not is_overlay(active):
            layout.operator("xrwb.modifier_tower", text=f"Show {active.name}", icon="RESTRICT_SELECT_OFF"
                            ).action = "RETARGET"
        layout.operator("xrwb.open_hand_menu", text="Add Modifier  ›", icon="ADD").menu = "XRWB_MT_modifier_add"
        layout.operator("xrwb.modifier_tower", text="Bring Here", icon="VIEW_PAN").action = "BRING"
        layout.label(text="Grip a block up/down: reorder. Trigger: on/off. Point + stick: dial.")
        for modifier in reversed(obj.modifiers):  # Top of the tower first, as you see it.
            layout.separator()
            row = layout.row(align=True)
            row.label(text=modifier.name, icon="MODIFIER_ON" if modifier.show_viewport else "MODIFIER_OFF")
            switch_op = row.operator("xrwb.modifier_tower", text="", icon="HIDE_OFF" if modifier.show_viewport
                                     else "HIDE_ON")
            switch_op.action = "SWITCH"
            switch_op.name = modifier.name
            for action, icon in (("APPLY", "CHECKMARK"), ("REMOVE", "X")):
                operator = row.operator("xrwb.modifier", text="", icon=icon)
                operator.action = action
                operator.name = modifier.name
            found = main_property(modifier)
            if found is not None:
                layout.prop(modifier, found[0])
            if modifier.type == "NODES" and modifier.node_group is not None:
                garden = layout.operator("xrwb.node_garden", text="Rewire in Node Garden", icon="NODETREE")
                garden.action = "GEOMETRY"
                garden.modifier = modifier.name


CLASSES = (XRWB_OT_modifier_tower, XRWB_MT_modifier_tower)
