"""Materials you can hold: preset blocks, a spray can and your own presets.

Presets are ordinary Blender node materials built here from native shader
nodes, with every node labelled by its role so "Show Logic" teaches the graph.
The shelf is a normal collection of small blocks (hidden from renders); a
trigger on a block loads it into the spray can. Personal presets are written
one material per .blend in Blender's user data folder; the open scene is never
written, and nothing is loaded until you pick a preset.
"""
from __future__ import annotations

import math
from pathlib import Path
import re

import bpy
from mathutils import Euler, Quaternion, Vector

from . import editing, viewer_scale

SHELF_COLLECTION = "XR Material Shelf"
PRESET_KEY = "xrwb_preset"
USER_PRESET_KEY = "xrwb_user_preset"
SWATCH_KEY = "xrwb_swatch"
LABEL_KEY = "xrwb_swatch_label"
BLOCK_SIZE_M = 0.07
BLOCK_GAP_M = 0.03
SHELF_COLUMNS = 4

# key: (label, principled inputs, texture recipe)
PRESETS = {
    "CLAY": ("Clay", {"Base Color": (0.62, 0.45, 0.36, 1), "Roughness": 0.8}, None),
    "PLASTIC": ("Red Plastic", {"Base Color": (0.8, 0.05, 0.04, 1), "Roughness": 0.35}, None),
    "RUBBER": ("Rubber", {"Base Color": (0.03, 0.03, 0.035, 1), "Roughness": 0.9}, None),
    "STEEL": ("Steel", {"Base Color": (0.6, 0.6, 0.62, 1), "Metallic": 1.0, "Roughness": 0.3}, None),
    "GOLD": ("Gold", {"Base Color": (1.0, 0.71, 0.29, 1), "Metallic": 1.0, "Roughness": 0.2}, None),
    "COPPER": ("Copper", {"Base Color": (0.95, 0.64, 0.54, 1), "Metallic": 1.0, "Roughness": 0.25}, None),
    "GLASS": ("Glass", {"Base Color": (0.95, 0.98, 1.0, 1), "Roughness": 0.0, "IOR": 1.45,
                        "Transmission Weight": 1.0}, None),
    "CAR_PAINT": ("Car Paint", {"Base Color": (0.02, 0.1, 0.6, 1), "Metallic": 0.5, "Roughness": 0.3,
                                "Coat Weight": 1.0}, None),
    "NEON": ("Neon", {"Base Color": (0.02, 0.02, 0.02, 1), "Emission Color": (0.1, 0.9, 1.0, 1),
                      "Emission Strength": 8.0}, None),
    "WOOD": ("Wood", {"Roughness": 0.6}, "WOOD"),
    "MARBLE": ("Marble", {"Roughness": 0.15}, "MARBLE"),
    "CHECKER": ("Checker", {"Roughness": 0.5}, "CHECKER"),
    "UV_CHECKER": ("UV Checker", {"Roughness": 0.6}, "UV_CHECKER"),
}
# Working materials offered by their own buttons, not on the shelf or preset grid.
UTILITY_PRESETS = {"UV_CHECKER"}


def _set_input(node, name, value):
    socket = node.inputs.get(name)
    if socket is not None and hasattr(socket, "default_value"):
        socket.default_value = value


def _texture_recipe(tree, recipe, bsdf):
    nodes, links = tree.nodes, tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    uv_space = recipe == "UV_CHECKER"
    coordinates.label = "1. Where: UV coordinates" if uv_space else "1. Where: object coordinates"
    coordinates.location = (-900, 0)
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-350, 0)
    ramp.label = "3. Colour: map pattern to colours"
    elements = ramp.color_ramp.elements
    if recipe == "WOOD":
        pattern = nodes.new("ShaderNodeTexWave")
        pattern.wave_type = "RINGS"
        _set_input(pattern, "Scale", 4.0)
        _set_input(pattern, "Distortion", 6.0)
        _set_input(pattern, "Detail", 3.0)
        elements[0].color = (0.18, 0.08, 0.03, 1)
        elements[1].color = (0.55, 0.3, 0.13, 1)
        output = pattern.outputs["Fac"]
    elif recipe == "MARBLE":
        pattern = nodes.new("ShaderNodeTexNoise")
        _set_input(pattern, "Scale", 4.0)
        _set_input(pattern, "Detail", 8.0)
        _set_input(pattern, "Distortion", 3.0)
        elements[0].color = (0.55, 0.56, 0.6, 1)
        elements[1].color = (0.97, 0.97, 0.95, 1)
        output = pattern.outputs["Fac"]
    else:
        pattern = nodes.new("ShaderNodeTexChecker")
        _set_input(pattern, "Scale", 16.0 if uv_space else 6.0)
        _set_input(pattern, "Color1", (0.9, 0.9, 0.9, 1))
        _set_input(pattern, "Color2", (0.08, 0.08, 0.08, 1))
        output = pattern.outputs["Fac"]
    pattern.location = (-650, 0)
    pattern.label = "2. Pattern: " + recipe.title()
    links.new(coordinates.outputs["UV" if uv_space else "Object"], pattern.inputs["Vector"])
    links.new(output, ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])


def build_preset(key: str) -> bpy.types.Material:
    """Return the scene's preset material, creating it from native nodes once."""
    if key not in PRESETS:
        raise editing.EditRejected("Unknown material preset")
    for material in bpy.data.materials:
        if material.get(PRESET_KEY) == key and not material.library:
            return material
    label, inputs, recipe = PRESETS[key]
    material = bpy.data.materials.new("XR " + label)
    material.use_nodes = True  # Blender 4.5 starts without nodes; 5.0+ ignores this
    material[PRESET_KEY] = key
    tree = material.node_tree
    tree.nodes.clear()
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.label = "4. Surface: how light reacts"
    bsdf.location = (0, 0)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.label = "5. Output: what the renderer uses"
    output.location = (320, 0)
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    for name, value in inputs.items():
        _set_input(bsdf, name, value)
    if recipe:
        _texture_recipe(tree, recipe, bsdf)
    # Solid view shows diffuse_color: give textured presets their typical colour.
    colour = (inputs.get("Base Color") or inputs.get("Emission Color")
              or {"WOOD": (0.45, 0.26, 0.12, 1), "MARBLE": (0.86, 0.86, 0.84, 1)}.get(recipe) or (0.5, 0.5, 0.5, 1))
    material.diffuse_color = colour
    material.metallic = float(inputs.get("Metallic", 0.0))
    material.roughness = float(inputs.get("Roughness", 0.5))
    return material


def user_preset_dir(create=False) -> Path:
    return Path(bpy.utils.user_resource("DATAFILES", path="xr_workbench/material_presets", create=create))


def user_presets() -> list[str]:
    directory = user_preset_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.blend"))


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip()[:48]
    if not cleaned:
        raise editing.EditRejected("Preset needs a name with letters or numbers")
    return cleaned


def save_user_preset(material) -> Path:
    if material is None or material.library:
        raise editing.EditRejected("Select an object with a local material to save it")
    name = _safe_name(material.name.removeprefix("XR "))
    path = user_preset_dir(create=True) / f"{name}.blend"
    # One material (and its node dependencies) per file: the open scene is never written.
    bpy.data.libraries.write(str(path), {material}, fake_user=True, path_remap="ABSOLUTE", compress=True)
    return path


def load_user_preset(name: str) -> bpy.types.Material:
    for material in bpy.data.materials:
        if material.get(USER_PRESET_KEY) == name and not material.library:
            return material
    path = user_preset_dir() / f"{_safe_name(name)}.blend"
    if not path.is_file():
        raise editing.EditRejected(f"Preset file is missing: {name}")
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        if not source.materials:
            raise editing.EditRejected(f"Preset file has no material: {name}")
        target.materials = [source.materials[0]]
    material = target.materials[0]
    if material is None:
        raise editing.EditRejected(f"Preset could not be loaded: {name}")
    material.use_fake_user = False
    material[USER_PRESET_KEY] = name
    return material


def resolve(identifier: str) -> bpy.types.Material:
    """PRESET key, "USER:<name>", or an existing material name."""
    if identifier in PRESETS:
        return build_preset(identifier)
    if identifier.startswith("USER:"):
        return load_user_preset(identifier[5:])
    material = bpy.data.materials.get(identifier)
    if material is None:
        raise editing.EditRejected(f"Material not found: {identifier}")
    return material


def shelf_collection(context):
    collection = bpy.data.collections.get(SHELF_COLLECTION)
    if collection is not None and context.scene.collection.children.get(collection.name) is None:
        return None
    return collection


def _add_label(collection, block, text):
    """Floating name above a block. Parented, so sizes are in block units."""
    curve = bpy.data.curves.new(block.name + " label", "FONT")
    curve.body = text
    curve.size = 0.3
    curve.align_x = "CENTER"
    label = bpy.data.objects.new(block.name + " label", curve)
    label[LABEL_KEY] = True
    label.hide_render = True
    label.hide_select = True
    label.parent = block
    # Stand the text up above the block, facing the viewer (block local -Y).
    label.location = (0.0, 0.0, 0.62)
    label.rotation_euler = Euler((math.pi / 2, 0.0, 0.0))
    collection.objects.link(label)
    return label


def build_shelf(context):
    """Spawn one block per preset (and personal preset) in front of the viewer."""
    if shelf_collection(context) is not None:
        raise editing.EditRejected("The material shelf is already out")
    location, rotation, units = viewer_frame_flat(context)
    entries = [(key, PRESETS[key][0]) for key in PRESETS if key not in UTILITY_PRESETS]
    entries += [(f"USER:{name}", name) for name in user_presets()]
    collection = bpy.data.collections.new(SHELF_COLLECTION)
    collection.hide_render = True
    context.scene.collection.children.link(collection)
    size = BLOCK_SIZE_M * units
    pitch = (BLOCK_SIZE_M + BLOCK_GAP_M) * units
    right = rotation @ Vector((1.0, 0.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    forward = rotation @ Vector((0.0, 1.0, 0.0))
    origin = location + forward * 0.55 * units - up * 0.3 * units
    created = []
    try:
        for index, (identifier, label) in enumerate(entries):
            material = resolve(identifier)
            row, column = divmod(index, SHELF_COLUMNS)
            mesh = bpy.data.meshes.new(f"XR Swatch {label}")
            half = 0.5
            verts = [(x * half, y * half, z * half) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
            faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.materials.append(material)
            block = bpy.data.objects.new(f"XR Swatch {label}", mesh)
            block[SWATCH_KEY] = material.name
            block.hide_render = True
            block.scale = (size, size, size)
            block.location = (origin + right * (column - (SHELF_COLUMNS - 1) / 2) * pitch
                              - up * row * pitch * 1.25)
            block.rotation_euler = rotation.to_euler()
            bevel = block.modifiers.new("Rounded", "BEVEL")
            bevel.width = 0.12
            bevel.segments = 3
            collection.objects.link(block)
            mesh.shade_smooth()
            created.append(block)
            _add_label(collection, block, label)
    except Exception:
        remove_shelf(context)
        raise
    return created


def viewer_frame_flat(context):
    """Viewer position with a yaw-only rotation whose +Y points forward."""
    location, rotation, units = viewer_scale.viewer_frame(context)
    forward = rotation @ Vector((0.0, 0.0, -1.0))
    forward.z = 0.0
    if forward.length < 1.0e-6:
        forward = Vector((0.0, 1.0, 0.0))
    yaw = math.atan2(-forward.x, forward.y)
    return location, Quaternion((0.0, 0.0, 1.0), yaw), units


def remove_shelf(context) -> int:
    collection = bpy.data.collections.get(SHELF_COLLECTION)
    if collection is None:
        return 0
    removed = 0
    # Labels first; never delete something a user moved into this collection.
    owned = sorted((obj for obj in collection.objects if SWATCH_KEY in obj.keys() or LABEL_KEY in obj.keys()),
                   key=lambda obj: SWATCH_KEY in obj.keys())
    for obj in owned:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
        if data is not None and data.users == 0:
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.curves).remove(data)
    if not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)
    return removed


def dip(context, block) -> str:
    """Load a block's material into the spray can."""
    from . import interaction_log

    name = block.get(SWATCH_KEY, "")
    if name not in bpy.data.materials:
        return ""
    context.window_manager.xrwb_settings.spray_material = name
    interaction_log.emit("material", f"Spray loaded with {name}. Aim at a mesh and hold the trigger.", context)
    return name


def _undo_push(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("material", message, context, level=level)


def _guard(context):
    if context.mode != "OBJECT":
        return "Switch to Object Mode for material tools"
    if not editing.undo_available(context):
        return "Enable Global Undo and at least one undo step"
    if editing.active_transaction() is not None:
        return "Release or cancel the current edit first"
    return ""


class XRWB_OT_material_shelf(bpy.types.Operator):
    bl_idname = "xrwb.material_shelf"
    bl_label = "Material Shelf"
    bl_description = "Bring out (or put away) physical blocks for every material preset"

    @classmethod
    def poll(cls, context):
        reason = _guard(context)
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def execute(self, context):
        try:
            if shelf_collection(context) is not None:
                count = remove_shelf(context)
                _undo_push("XR Put Away Material Shelf")
                _log(context, f"Material shelf put away ({count} objects).")
            else:
                blocks = build_shelf(context)
                _undo_push("XR Material Shelf")
                _log(context, f"{len(blocks)} material blocks out. Trigger one with Spray to load it.")
        except (editing.EditRejected, RuntimeError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class XRWB_OT_pick_material(bpy.types.Operator):
    bl_idname = "xrwb.pick_material"
    bl_label = "Load Spray Material"
    bl_description = "Load this material into the spray can and pick up the Spray tool"
    identifier: bpy.props.StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        reason = _guard(context)
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def execute(self, context):
        try:
            material = resolve(self.identifier)
        except editing.EditRejected as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        settings = context.window_manager.xrwb_settings
        settings.spray_material = material.name
        settings.edit_tool = "spray_material"
        _log(context, f"Spray loaded with {material.name}. Close the menu and hold a trigger.")
        return {"FINISHED"}


class XRWB_OT_apply_material(bpy.types.Operator):
    bl_idname = "xrwb.apply_material"
    bl_label = "Apply to Selected"
    bl_description = "Put the spray material on every face of the selected meshes"

    @classmethod
    def poll(cls, context):
        return not _guard(context) and bool(context.selected_editable_objects)

    def execute(self, context):
        material = bpy.data.materials.get(context.window_manager.xrwb_settings.spray_material)
        if material is None:
            _log(context, "Choose a spray material first", "WARNING")
            return {"CANCELLED"}
        targets = [obj for obj in context.selected_editable_objects
                   if obj.type == "MESH" and SWATCH_KEY not in obj.keys() and obj.data.users == 1
                   and not obj.data.library]
        if not targets:
            _log(context, "Select a local single-user mesh", "WARNING")
            return {"CANCELLED"}
        for obj in targets:
            if not obj.material_slots:
                obj.data.materials.append(material)
            else:
                obj.active_material = material
        _undo_push("XR Apply Material")
        _log(context, f"{material.name} applied to {len(targets)} object(s).")
        return {"FINISHED"}


def _frame_nodes_after_redraw(window, area):
    """Frame the whole graph once the editor has drawn at its new size."""
    def frame():
        try:
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None and area.ui_type == "ShaderNodeTree":
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.node.view_all()
        except (ReferenceError, RuntimeError, StopIteration):
            pass  # The editor was closed or changed; nothing to frame.
        return None

    area.tag_redraw()
    bpy.app.timers.register(frame, first_interval=0.2)


class XRWB_OT_show_material_logic(bpy.types.Operator):
    bl_idname = "xrwb.show_material_logic"
    bl_label = "Show Logic"
    bl_description = "Open the real Shader node tree for the spray or active material on the desktop panel"

    def execute(self, context):
        from . import runtime, ui

        obj = context.active_object
        material = bpy.data.materials.get(context.window_manager.xrwb_settings.spray_material)
        if obj is not None and SWATCH_KEY in obj.keys():
            material = bpy.data.materials.get(obj[SWATCH_KEY], material)
        elif material is None and obj is not None:
            material = obj.active_material
        if material is None or material.node_tree is None:
            _log(context, "Pick a material block or select an object with a node material", "WARNING")
            return {"CANCELLED"}
        if "FINISHED" not in bpy.ops.xrwb.workspace_editor(editor="ShaderNodeTree"):
            _log(context, "No side editor is available for the Shader node tree", "WARNING")
            return {"CANCELLED"}
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.ui_type != "ShaderNodeTree":
                    continue
                space = area.spaces.active
                space.shader_type = "OBJECT"
                # Pin the tree so the logic shown is this material, whatever is active.
                space.pin = True
                space.node_tree = material.node_tree
                _frame_nodes_after_redraw(window, area)
        if runtime.session_running(context):
            state = context.window_manager.xr_session_state
            if hasattr(state, "editor_surface_info") and not state.editor_surface_info(context)[0]:
                with ui._view3d_override(context):
                    bpy.ops.xrwb.editor_surface(hide=False)
            context.window_manager.xr_session_settings.shading.type = "MATERIAL"
        _log(context, f"{material.name}: node logic open on the desktop panel (pinned).")
        return {"FINISHED"}


class XRWB_OT_save_material_preset(bpy.types.Operator):
    bl_idname = "xrwb.save_material_preset"
    bl_label = "Save My Preset"
    bl_description = "Save the active object's material as a personal preset for every future scene"

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj is not None else None
        try:
            path = save_user_preset(material)
        except (editing.EditRejected, OSError, RuntimeError) as exc:
            _log(context, f"Preset not saved: {exc}", "WARNING")
            return {"CANCELLED"}
        _log(context, f"Saved preset {path.stem}. It appears on the shelf and Materials page.")
        return {"FINISHED"}


CLASSES = (XRWB_OT_material_shelf, XRWB_OT_pick_material, XRWB_OT_apply_material,
           XRWB_OT_show_material_logic, XRWB_OT_save_material_preset)
