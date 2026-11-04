"""Studio: the rest of Blender from your hand — modifiers, lights, camera,
render, world, physics, rigging and the scene outline.

Everything uses Blender's own data and operators, and every change is one
undo step. Things that belong somewhere (a light, a camera, an armature)
appear where your pointing hand is, aimed along its ray, so staging a scene
is as direct as holding a lamp or a camera. The menus are native Blender menus
on your hand, so the stick walks them and every value is Blender's own field.
"""
from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

from . import editing, viewer_scale

# ---- Shared helpers ------------------------------------------------------------


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("studio", message, context, level=level)


def _undo(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in bpy.ops.ed.undo_push(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def _guard(context, mode="OBJECT"):
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")
    if mode and context.mode != mode:
        raise editing.EditRejected("Switch to Object Mode first")


def hand_pose(context):
    """(position, rotation, ray direction) of the pointing hand, or in front of you."""
    from . import controllers, ray_feedback
    for path in (controllers._pointer_hand, controllers.RIGHT_HAND, controllers.LEFT_HAND):
        pose = ray_feedback.last_poses.get(path)
        if pose is not None:
            return Vector(pose[0]), Quaternion(pose[1]), Vector(pose[2])
    location, rotation, units = viewer_scale.viewer_frame(context)
    direction = rotation @ Vector((0.0, 0.0, -1.0))
    return location + direction * 0.5 * units, rotation, direction


def aim_rotation(direction):
    """Rotation whose -Z looks along ``direction`` with +Y kept up."""
    direction = Vector(direction)
    if direction.length < 1.0e-9:
        return Quaternion()
    return direction.normalized().to_track_quat("-Z", "Y")


def _make_active(context, obj):
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _active_of(context, kind):
    obj = context.active_object
    if obj is None or obj.type != kind:
        raise editing.EditRejected(f"Select a {kind.lower()} first")
    return obj


# ---- Modifiers --------------------------------------------------------------------

MODIFIERS = (
    ("SUBSURF", "Subdivision", "MOD_SUBSURF", ("levels", "render_levels")),
    ("MIRROR", "Mirror", "MOD_MIRROR", ("use_axis", "use_clip")),
    ("ARRAY", "Array", "MOD_ARRAY", ("count", "relative_offset_displace")),
    ("SOLIDIFY", "Solidify", "MOD_SOLIDIFY", ("thickness", "offset")),
    ("BEVEL", "Bevel", "MOD_BEVEL", ("width", "segments")),
    ("SMOOTH", "Smooth", "MOD_SMOOTH", ("factor", "iterations")),
    ("REMESH", "Remesh", "MOD_REMESH", ("mode", "voxel_size")),
    ("DECIMATE", "Decimate", "MOD_DECIM", ("ratio",)),
    ("WELD", "Weld", "AUTOMERGE_OFF", ("merge_threshold",)),
    ("WIREFRAME", "Wireframe", "MOD_WIREFRAME", ("thickness",)),
    ("SCREW", "Screw", "MOD_SCREW", ("angle", "steps", "screw_offset")),
    ("DISPLACE", "Displace", "MOD_DISPLACE", ("strength",)),
    ("SIMPLE_DEFORM", "Twist / Bend", "MOD_SIMPLEDEFORM", ("deform_method", "angle")),
    ("CAST", "Cast", "MOD_CAST", ("cast_type", "factor")),
    ("TRIANGULATE", "Triangulate", "MOD_TRIANGULATE", ("quad_method",)),
    ("NODES", "Geometry Nodes", "GEOMETRY_NODES", ("node_group",)),
)
MODIFIER_PROPS = {key: props for key, _label, _icon, props in MODIFIERS}
MODIFIER_PROPS.update({"BOOLEAN": ("operation", "object"), "CLOTH": (), "COLLISION": ()})


def _geometry_group():
    group = bpy.data.node_groups.new("XR Geometry", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    source = group.nodes.new("NodeGroupInput")
    source.location = (-300, 0)
    sink = group.nodes.new("NodeGroupOutput")
    sink.location = (300, 0)
    group.links.new(source.outputs[0], sink.inputs[0])
    return group


def modifier_action(context, action, kind="", name=""):
    _guard(context)
    obj = context.active_object
    if obj is None or obj.type not in {"MESH", "CURVE", "FONT", "SURFACE"}:
        raise editing.EditRejected("Select a mesh or curve for modifiers")
    if obj.library or obj.override_library:
        raise editing.EditRejected("Linked objects cannot change modifiers")
    if action == "ADD":
        label = next((item[1] for item in MODIFIERS if item[0] == kind), None)
        if label is None:
            raise editing.EditRejected("Unknown modifier")
        modifier = obj.modifiers.new(label, kind)
        if modifier is None:
            raise editing.EditRejected(f"{label} does not work on a {obj.type.lower()}")
        if kind == "NODES":
            modifier.node_group = _geometry_group()
        elif kind == "SUBSURF":
            modifier.levels = 2
        _undo(f"XR Add {label}")
        return f"{label} added to {obj.name}. Its values are in Modifiers."
    modifier = obj.modifiers.get(name)
    if modifier is None:
        raise editing.EditRejected("That modifier is gone")
    if action == "APPLY":
        if obj.data.users > 1:
            raise editing.EditRejected("Shared data: make it single-user before applying")
        with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
            if "FINISHED" not in bpy.ops.object.modifier_apply(modifier=name):
                raise editing.EditRejected(f"Blender could not apply {name}")
        _undo(f"XR Apply {name}")
        return f"{name} applied: it is now real geometry"
    if action == "REMOVE":
        obj.modifiers.remove(modifier)
        _undo(f"XR Remove {name}")
        return f"{name} removed"
    if action == "TOGGLE":
        modifier.show_viewport = not modifier.show_viewport
        _undo(f"XR Toggle {name}")
        return f"{name} {'shown' if modifier.show_viewport else 'hidden'}"
    if action == "UP":
        index = list(obj.modifiers).index(modifier)
        if index == 0:
            return f"{name} is already first"
        obj.modifiers.move(index, index - 1)
        _undo(f"XR Move {name}")
        return f"{name} moved up"
    raise editing.EditRejected("Unknown modifier action")


class XRWB_OT_modifier(bpy.types.Operator):
    bl_idname = "xrwb.modifier"
    bl_label = "Modifier"
    bl_description = "Add, apply, remove, show/hide or reorder a modifier; one undo each"
    action: bpy.props.EnumProperty(items=(("ADD", "Add", ""), ("APPLY", "Apply", ""), ("REMOVE", "Remove", ""),
                                          ("TOGGLE", "Show / Hide", ""), ("UP", "Move Up", "")),
                                   options={"SKIP_SAVE"})
    kind: bpy.props.StringProperty(options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: modifier_action(context, self.action, self.kind, self.name))


def _run(operator, context, action):
    try:
        message = action()
    except (editing.EditRejected, RuntimeError, ValueError, TypeError, ReferenceError) as exc:
        _log(context, str(exc), "WARNING")
        operator.report({"WARNING"}, str(exc))
        return {"CANCELLED"}
    _log(context, message)
    return {"FINISHED"}


class XRWB_MT_modifier_add(bpy.types.Menu):
    bl_idname = "XRWB_MT_modifier_add"
    bl_label = "Add Modifier"

    def draw(self, context):
        layout = self.layout
        layout.label(text="ADD A MODIFIER (NON-DESTRUCTIVE)", icon="MODIFIER")
        for kind, label, icon, _props in MODIFIERS:
            operator = layout.operator("xrwb.modifier", text=label, icon=icon)
            operator.action = "ADD"
            operator.kind = kind


class XRWB_MT_modifiers(bpy.types.Menu):
    bl_idname = "XRWB_MT_modifiers"
    bl_label = "Modifiers"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        layout.label(text=f"MODIFIERS: {obj.name}" if obj else "MODIFIERS", icon="MODIFIER")
        layout.operator("xrwb.open_hand_menu", text="Add Modifier  ›", icon="ADD").menu = "XRWB_MT_modifier_add"
        if obj is None or not hasattr(obj, "modifiers"):
            layout.label(text="Select an object first.")
            return
        if not obj.modifiers:
            layout.label(text="No modifiers yet: add one, then change it here.")
        for modifier in obj.modifiers:
            layout.separator()
            row = layout.row(align=True)
            row.label(text=modifier.name, icon="MODIFIER_ON" if modifier.show_viewport else "MODIFIER_OFF")
            for action, icon in (("TOGGLE", "HIDE_OFF" if modifier.show_viewport else "HIDE_ON"),
                                 ("UP", "TRIA_UP"), ("APPLY", "CHECKMARK"), ("REMOVE", "X")):
                operator = row.operator("xrwb.modifier", text="", icon=icon)
                operator.action = action
                operator.name = modifier.name
            for prop in MODIFIER_PROPS.get(modifier.type, ()):
                if hasattr(modifier, prop):
                    layout.prop(modifier, prop)
            if modifier.type == "NODES" and modifier.node_group is not None:
                layout.operator("xrwb.workspace_editor", text="Open Geometry Nodes",
                                icon="GEOMETRY_NODES").editor = "GeometryNodeTree"


# ---- Lights ----------------------------------------------------------------------

LIGHTS = (("POINT", "Point Light", "LIGHT_POINT", 100.0), ("SPOT", "Spot Light", "LIGHT_SPOT", 500.0),
          ("AREA", "Area Light", "LIGHT_AREA", 150.0), ("SUN", "Sun", "LIGHT_SUN", 3.0))
LIGHT_PROPS = {"POINT": ("energy", "color", "shadow_soft_size"), "SPOT": ("energy", "color", "spot_size", "spot_blend"),
               "AREA": ("energy", "color", "size"), "SUN": ("energy", "color", "angle")}


def place_light(context, kind):
    _guard(context)
    label, energy = next((item[1], item[3]) for item in LIGHTS if item[0] == kind)
    position, _rotation, direction = hand_pose(context)
    data = bpy.data.lights.new(f"XR {label}", kind)
    data.energy = energy
    if kind == "SPOT":
        data.spot_size = math.radians(50.0)
    obj = bpy.data.objects.new(f"XR {label}", data)
    obj.location = position
    obj.rotation_euler = aim_rotation(direction).to_euler()
    context.collection.objects.link(obj)
    _make_active(context, obj)
    _undo(f"XR Add {label}")
    return f"{label} placed at your hand, shining along your ray. Grip it to move and aim."


def aim_light(context):
    _guard(context)
    light = _active_of(context, "LIGHT")
    targets = [obj for obj in context.selected_objects if obj != light]
    if targets:
        target = sum((obj.matrix_world.translation for obj in targets), Vector()) / len(targets)
    else:
        target = context.scene.cursor.location
    light.rotation_euler = aim_rotation(target - light.matrix_world.translation).to_euler()
    _undo("XR Aim Light")
    return f"{light.name} now aims at " + ("the selection" if targets else "the 3D cursor")


class XRWB_OT_light(bpy.types.Operator):
    bl_idname = "xrwb.light"
    bl_label = "Light"
    bl_description = "Place a light at your hand aimed along the ray, or aim the active light"
    kind: bpy.props.EnumProperty(items=[(k, label, "") for k, label, _i, _e in LIGHTS] + [("AIM", "Aim", "")],
                                 options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: aim_light(context) if self.kind == "AIM" else place_light(context, self.kind))


class XRWB_MT_lights(bpy.types.Menu):
    bl_idname = "XRWB_MT_lights"
    bl_label = "Lights"

    def draw(self, context):
        layout = self.layout
        layout.label(text="LIGHTS: APPEAR AT YOUR HAND, AIMED ALONG THE RAY", icon="LIGHT")
        for kind, label, icon, _energy in LIGHTS:
            layout.operator("xrwb.light", text=label, icon=icon).kind = kind
        obj = context.active_object
        if obj is not None and obj.type == "LIGHT":
            layout.separator()
            layout.label(text=obj.name.upper(), icon="OUTLINER_OB_LIGHT")
            for prop in LIGHT_PROPS.get(obj.data.type, ()):
                layout.prop(obj.data, prop)
            layout.operator("xrwb.light", text="Aim at Selection / Cursor", icon="LIGHT_SPOT").kind = "AIM"


# ---- Camera and render ----------------------------------------------------------------


def camera_action(context, action):
    _guard(context)
    scene = context.scene
    if action == "ACTIVE":
        camera = _active_of(context, "CAMERA")
        scene.camera = camera
        _undo("XR Active Camera")
        return f"{camera.name} is now the render camera"
    if action == "VIEW":
        location, rotation, _units = viewer_scale.viewer_frame(context)
    else:
        location, _rotation, direction = hand_pose(context)
        rotation = aim_rotation(direction)
    camera = scene.camera
    if camera is None:
        camera = bpy.data.objects.new("XR Camera", bpy.data.cameras.new("XR Camera"))
        context.collection.objects.link(camera)
        scene.camera = camera
    camera.location = location
    camera.rotation_euler = Quaternion(rotation).to_euler()
    _make_active(context, camera)
    _undo("XR Camera Here")
    return ("The camera now sees exactly what you see" if action == "VIEW"
            else "The camera now looks along your hand's ray; grip it to fine-tune")


class XRWB_OT_camera(bpy.types.Operator):
    bl_idname = "xrwb.camera"
    bl_label = "Camera"
    bl_description = "Put the render camera where you look or where you point"
    action: bpy.props.EnumProperty(items=(("VIEW", "From My Eyes", ""), ("HAND", "From My Hand", ""),
                                          ("ACTIVE", "Make Active Camera", "")), options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: camera_action(context, self.action))


def render_output_path(context) -> Path:
    base = Path(bpy.path.abspath("//")) if bpy.data.filepath else Path(bpy.app.tempdir)
    folder = base / "renders"
    folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while (folder / f"xr_render_{index:03d}.png").exists():
        index += 1
    return folder / f"xr_render_{index:03d}.png"


def render_action(context, action):
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")
    scene = context.scene
    if action in {"BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"}:
        # Engines register at runtime (Cycles is an add-on), so try rather than list.
        candidates = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if "EEVEE" in action else (action,)
        engine = ""
        for candidate in candidates:
            try:
                scene.render.engine = candidate
                engine = candidate
                break
            except TypeError:
                continue
        if not engine:
            raise editing.EditRejected(f"{action.title()} is not available in this Blender")
        _undo("XR Render Engine")
        return f"Render engine: {engine.replace('BLENDER_', '').replace('_', ' ').title()}"
    if action == "LIVE":
        shading = context.window_manager.xr_session_settings.shading
        shading.type = "SOLID" if shading.type == "RENDERED" else "RENDERED"
        return ("Live render in the headset: lights, materials and shadows as they will render"
                if shading.type == "RENDERED" else "Back to solid view")
    if action == "STILL":
        if context.window is not None and not bpy.app.background:
            bpy.ops.render.render("INVOKE_DEFAULT")
            return "Rendering. The result opens in Blender's render view on the desktop; then Save Render"
        bpy.ops.render.render()
        return "Rendered"
    if action == "SAVE":
        image = bpy.data.images.get("Render Result")
        if image is None:
            raise editing.EditRejected("Render first, then save it")
        path = render_output_path(context)
        image.save_render(str(path), scene=scene)
        return f"Saved {path.name} to {path.parent}"
    raise editing.EditRejected("Unknown render action")


class XRWB_OT_render(bpy.types.Operator):
    bl_idname = "xrwb.render"
    bl_label = "Render"
    bl_description = "Render, save, switch engine, or see a live render in the headset"
    action: bpy.props.EnumProperty(items=(("STILL", "Render Image", ""), ("SAVE", "Save Render", ""),
                                          ("LIVE", "Live Render in Headset", ""),
                                          ("BLENDER_EEVEE_NEXT", "EEVEE", ""), ("CYCLES", "Cycles", ""),
                                          ("BLENDER_WORKBENCH", "Workbench", "")), options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: render_action(context, self.action))


class XRWB_MT_camera_render(bpy.types.Menu):
    bl_idname = "XRWB_MT_camera_render"
    bl_label = "Camera & Render"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text="CAMERA & RENDER", icon="CAMERA_DATA")
        layout.operator("xrwb.camera", text="Camera From My Eyes", icon="HIDE_OFF").action = "VIEW"
        layout.operator("xrwb.camera", text="Camera From My Hand", icon="VIEW_CAMERA").action = "HAND"
        layout.operator("xrwb.camera", text="Make Selected the Camera", icon="OUTLINER_OB_CAMERA").action = "ACTIVE"
        if scene.camera is not None and scene.camera.type == "CAMERA":
            data = scene.camera.data
            layout.prop(data, "lens", text="Lens (mm)")
            layout.prop(data.dof, "use_dof", text="Depth of Field")
            if data.dof.use_dof:
                layout.prop(data.dof, "focus_distance")
                layout.prop(data.dof, "aperture_fstop", text="F-stop")
        layout.separator()
        layout.operator("xrwb.render", text="Live Render in Headset", icon="SHADING_RENDERED").action = "LIVE"
        layout.operator("xrwb.render", text="Render Image", icon="RENDER_STILL").action = "STILL"
        layout.operator("xrwb.render", text="Save Render", icon="FILE_TICK").action = "SAVE"
        row = layout.row(align=True)
        row.operator("xrwb.render", text="EEVEE").action = "BLENDER_EEVEE_NEXT"
        row.operator("xrwb.render", text="Cycles").action = "CYCLES"
        row.operator("xrwb.render", text="Workbench").action = "BLENDER_WORKBENCH"
        layout.prop(scene.render, "resolution_percentage", text="Resolution %")
        if scene.render.engine == "CYCLES" and hasattr(scene, "cycles"):
            layout.prop(scene.cycles, "samples", text="Samples")
        elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            layout.prop(scene.eevee, "taa_render_samples", text="Samples")
        layout.operator("xrwb.workspace_editor", text="Render Properties on Desktop",
                        icon="PROPERTIES").editor = "PROPERTIES"


# ---- World ---------------------------------------------------------------------------

WORLDS = (("STUDIO", "Studio Grey", (0.05, 0.05, 0.055), 1.0), ("DAY", "Daylight Sky", (0.45, 0.6, 0.85), 1.0),
          ("SUNSET", "Sunset", (0.8, 0.4, 0.2), 0.8), ("NIGHT", "Night", (0.01, 0.012, 0.03), 0.4),
          ("WHITE", "White Void", (1.0, 1.0, 1.0), 1.0))


def world_background(context):
    scene = context.scene
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("XR World")
        scene.world = world
    if world.node_tree is None or world.node_tree.nodes.get("Background") is None:
        tree = world.node_tree
        background = tree.nodes.new("ShaderNodeBackground")
        output = next((n for n in tree.nodes if n.type == "OUTPUT_WORLD"), None) or tree.nodes.new("ShaderNodeOutputWorld")
        tree.links.new(background.outputs[0], output.inputs["Surface"])
    return world.node_tree.nodes["Background"]


def world_preset(context, key, push=True):
    _guard(context, mode=None)
    label, colour, strength = next((item[1], item[2], item[3]) for item in WORLDS if item[0] == key)
    background = world_background(context)
    background.inputs["Color"].default_value = (*colour, 1.0)
    background.inputs["Strength"].default_value = strength
    context.scene.world.color = colour
    if push:
        _undo(f"XR World {label}")
    return f"World: {label}. Fine-tune colour and strength here."


class XRWB_OT_world(bpy.types.Operator):
    bl_idname = "xrwb.world"
    bl_label = "World"
    bl_description = "Set the environment light and background"
    preset: bpy.props.EnumProperty(items=[(k, label, "") for k, label, _c, _s in WORLDS], options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: world_preset(context, self.preset))


class XRWB_MT_world(bpy.types.Menu):
    bl_idname = "XRWB_MT_world"
    bl_label = "World"

    def draw(self, context):
        layout = self.layout
        layout.label(text="WORLD: ENVIRONMENT LIGHT", icon="WORLD")
        for key, label, _colour, _strength in WORLDS:
            layout.operator("xrwb.world", text=label).preset = key
        world = context.scene.world
        background = world.node_tree.nodes.get("Background") if world and world.node_tree else None
        if background is not None:
            layout.prop(background.inputs["Color"], "default_value", text="Colour")
            layout.prop(background.inputs["Strength"], "default_value", text="Strength")


# ---- Physics -----------------------------------------------------------------------

GROUND = "XR Ground"


def physics_action(context, action):
    _guard(context)
    scene = context.scene
    if action == "GROUND":
        if GROUND in bpy.data.objects:
            raise editing.EditRejected("The physics ground is already there")
        bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0.0, 0.0, 0.0))
        ground = context.active_object
        ground.name = GROUND
        bpy.ops.rigidbody.object_add(type="PASSIVE")
        _undo("XR Physics Ground")
        return "A 20 m passive ground is under the scene"
    if action == "RESET":
        scene.frame_set(scene.frame_start)
        return "Simulation back to the start"
    targets = [obj for obj in context.selected_editable_objects if obj.type == "MESH"]
    if not targets:
        raise editing.EditRejected("Select meshes first")
    active = context.view_layer.objects.active
    count = 0
    for obj in targets:
        with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
            if action in {"ACTIVE", "PASSIVE"}:
                if obj.rigid_body is not None:
                    obj.rigid_body.type = action
                else:
                    bpy.ops.rigidbody.object_add(type=action)
            elif action == "REMOVE":
                if obj.rigid_body is not None:
                    bpy.ops.rigidbody.object_remove()
                for modifier in [m for m in obj.modifiers if m.type in {"CLOTH", "COLLISION"}]:
                    obj.modifiers.remove(modifier)
            elif action == "CLOTH":
                if not any(m.type == "CLOTH" for m in obj.modifiers):
                    obj.modifiers.new("Cloth", "CLOTH")
            elif action == "COLLISION":
                if not any(m.type == "COLLISION" for m in obj.modifiers):
                    obj.modifiers.new("Collision", "COLLISION")
        count += 1
    context.view_layer.objects.active = active
    _undo(f"XR Physics {action.title()}")
    words = {"ACTIVE": "fall and collide", "PASSIVE": "hold still for others", "REMOVE": "no longer simulate",
             "CLOTH": "behave as cloth", "COLLISION": "block cloth"}
    return f"{count} object(s) now {words[action]}. Press Play to simulate."


class XRWB_OT_physics(bpy.types.Operator):
    bl_idname = "xrwb.physics"
    bl_label = "Physics"
    bl_description = "Rigid bodies, cloth and collisions with Blender's simulators; one undo each"
    action: bpy.props.EnumProperty(items=(("ACTIVE", "Falls (Active)", ""), ("PASSIVE", "Solid (Passive)", ""),
                                          ("CLOTH", "Cloth", ""), ("COLLISION", "Collision", ""),
                                          ("REMOVE", "Remove Physics", ""), ("GROUND", "Add Ground", ""),
                                          ("RESET", "Back to Start", "")), options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: physics_action(context, self.action))


class XRWB_MT_physics(bpy.types.Menu):
    bl_idname = "XRWB_MT_physics"
    bl_label = "Physics"

    def draw(self, context):
        layout = self.layout
        layout.label(text="PHYSICS: SELECT, CHOOSE, THEN PLAY", icon="PHYSICS")
        for action, label, icon in (("ACTIVE", "Falls and Collides", "RIGID_BODY"),
                                    ("PASSIVE", "Solid Obstacle", "RIGID_BODY_CONSTRAINT"),
                                    ("CLOTH", "Cloth", "MOD_CLOTH"), ("COLLISION", "Blocks Cloth", "MOD_PHYSICS"),
                                    ("GROUND", "Add Ground", "MESH_PLANE"), ("REMOVE", "Remove Physics", "X")):
            layout.operator("xrwb.physics", text=label, icon=icon).action = action
        layout.operator("xrwb.anim_play", text="Play / Pause", icon="PLAY")
        layout.operator("xrwb.physics", text="Back to Start", icon="REW").action = "RESET"


# ---- Rig -----------------------------------------------------------------------------


def rig_action(context, action):
    if action == "ADD":
        _guard(context)
        position, _rotation, _direction = hand_pose(context)
        bpy.ops.object.armature_add(location=position)
        _undo("XR Add Armature")
        return "Armature placed at your hand. Extrude bones on the desktop, then Pose."
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")
    if action in {"POSE", "OBJECT", "EDIT"}:
        armature = _active_of(context, "ARMATURE") if action != "OBJECT" else context.active_object
        if armature is None:
            raise editing.EditRejected("Select something first")
        bpy.ops.object.mode_set(mode=action)
        return {"POSE": "Pose Mode: pose bones with the desktop tools; Object Mode brings hand tools back",
                "EDIT": "Armature Edit Mode: add and place bones", "OBJECT": "Object Mode"}[action]
    if action == "BIND":
        _guard(context)
        armature = _active_of(context, "ARMATURE")
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not meshes:
            raise editing.EditRejected("Select the mesh(es), then the armature last")
        with context.temp_override(active_object=armature, object=armature,
                                   selected_objects=[*meshes, armature],
                                   selected_editable_objects=[*meshes, armature]):
            if "FINISHED" not in bpy.ops.object.parent_set(type="ARMATURE_AUTO"):
                raise editing.EditRejected("Blender could not bind the mesh")
        _undo("XR Bind to Armature")
        return f"{len(meshes)} mesh(es) bound with automatic weights"
    raise editing.EditRejected("Unknown rig action")


class XRWB_OT_rig(bpy.types.Operator):
    bl_idname = "xrwb.rig"
    bl_label = "Rig"
    bl_description = "Armatures: add, edit, pose and bind with automatic weights"
    action: bpy.props.EnumProperty(items=(("ADD", "Add Armature", ""), ("EDIT", "Edit Bones", ""),
                                          ("POSE", "Pose Mode", ""), ("OBJECT", "Object Mode", ""),
                                          ("BIND", "Bind Mesh", "")), options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: rig_action(context, self.action))


class XRWB_MT_rig(bpy.types.Menu):
    bl_idname = "XRWB_MT_rig"
    bl_label = "Rig"

    def draw(self, context):
        layout = self.layout
        layout.label(text="RIGGING", icon="ARMATURE_DATA")
        for action, label, icon in (("ADD", "Add Armature at Hand", "BONE_DATA"), ("EDIT", "Edit Bones", "EDITMODE_HLT"),
                                    ("POSE", "Pose Mode", "POSE_HLT"), ("OBJECT", "Object Mode", "OBJECT_DATAMODE"),
                                    ("BIND", "Bind Selected Mesh (auto weights)", "MOD_ARMATURE")):
            layout.operator("xrwb.rig", text=label, icon=icon).action = action
        layout.label(text="Order: pick the mesh, Multi-Select the armature last.")


# ---- Scene outline ---------------------------------------------------------------------

OUTLINE_LIMIT = 30


def outline_action(context, action, name=""):
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")
    view_layer = context.view_layer
    if action == "SHOW_ALL":
        for obj in view_layer.objects:
            obj.hide_set(False)
        _undo("XR Show All")
        return "Everything is visible again"
    obj = view_layer.objects.get(name)
    if obj is None:
        raise editing.EditRejected(f"{name} is not in this view layer")
    if action == "SELECT":
        if context.mode != "OBJECT":
            raise editing.EditRejected("Switch to Object Mode to select from the outline")
        obj.hide_set(False)
        _make_active(context, obj)
        _undo("XR Outline Select")
        return f"{obj.name} selected"
    if action == "HIDE":
        obj.hide_set(not obj.hide_get())
        _undo("XR Outline Hide")
        return f"{obj.name} {'hidden' if obj.hide_get() else 'shown'}"
    if action == "ISOLATE":
        for other in view_layer.objects:
            other.hide_set(other != obj)
        _undo("XR Isolate")
        return f"Only {obj.name} is visible; Show All brings the rest back"
    raise editing.EditRejected("Unknown outline action")


class XRWB_OT_outline(bpy.types.Operator):
    bl_idname = "xrwb.outline"
    bl_label = "Outline"
    bl_description = "Select, hide or isolate scene objects from your hand"
    action: bpy.props.EnumProperty(items=(("SELECT", "Select", ""), ("HIDE", "Hide / Show", ""),
                                          ("ISOLATE", "Isolate", ""), ("SHOW_ALL", "Show All", "")),
                                   options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: outline_action(context, self.action, self.name))


OUTLINE_ICONS = {"MESH": "OUTLINER_OB_MESH", "LIGHT": "OUTLINER_OB_LIGHT", "CAMERA": "OUTLINER_OB_CAMERA",
                 "CURVE": "OUTLINER_OB_CURVE", "ARMATURE": "OUTLINER_OB_ARMATURE", "EMPTY": "OUTLINER_OB_EMPTY",
                 "FONT": "OUTLINER_OB_FONT", "GREASEPENCIL": "OUTLINER_OB_GREASEPENCIL"}
HELPER_KEYS = ("xrwb_garden", "xrwb_garden_node", "xrwb_garden_socket", "xrwb_garden_label",
               "xrwb_swatch", "xrwb_swatch_label")


def outline_objects(context):
    """Scene objects worth listing: not our own helper blocks, sorted by name."""
    objects = [obj for obj in context.view_layer.objects if not any(key in obj.keys() for key in HELPER_KEYS)]
    return sorted(objects, key=lambda obj: obj.name.lower())


class XRWB_MT_outline(bpy.types.Menu):
    bl_idname = "XRWB_MT_outline"
    bl_label = "Scene Outline"

    def draw(self, context):
        layout = self.layout
        objects = outline_objects(context)
        layout.label(text=f"SCENE: {len(objects)} OBJECTS — PICK TO SELECT", icon="OUTLINER")
        layout.operator("xrwb.outline", text="Show All", icon="HIDE_OFF").action = "SHOW_ALL"
        for child in context.view_layer.layer_collection.children:
            layout.prop(child, "hide_viewport", text=f"Collection: {child.name} (hidden)")
        for obj in objects[:OUTLINE_LIMIT]:
            row = layout.row(align=True)
            operator = row.operator("xrwb.outline", text=obj.name, icon=OUTLINE_ICONS.get(obj.type, "OBJECT_DATA"),
                                    depress=obj.select_get())
            operator.action = "SELECT"
            operator.name = obj.name
            hide = row.operator("xrwb.outline", text="", icon="HIDE_ON" if obj.hide_get() else "HIDE_OFF")
            hide.action = "HIDE"
            hide.name = obj.name
            solo = row.operator("xrwb.outline", text="", icon="SOLO_ON")
            solo.action = "ISOLATE"
            solo.name = obj.name
        if len(objects) > OUTLINE_LIMIT:
            layout.label(text=f"+{len(objects) - OUTLINE_LIMIT} more: the full Outliner is on the desktop.")
            layout.operator("xrwb.workspace_editor", text="Open Outliner", icon="OUTLINER").editor = "OUTLINER"


# ---- Paint and pen --------------------------------------------------------------------


class XRWB_MT_paint_draw(bpy.types.Menu):
    bl_idname = "XRWB_MT_paint_draw"
    bl_label = "Paint & Draw"

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.xrwb_settings
        layout.label(text="PAINT COLOUR & 3D PEN", icon="BRUSH_DATA")
        layout.operator("xrwb.set_edit_tool", text="Paint Colour", icon="VPAINT_HLT").tool = "paint_vertex"
        layout.prop(settings, "paint_colour", text="Colour")
        layout.prop(settings, "spray_radius_m", text="Brush Size")
        layout.operator("xrwb.set_edit_tool", text="3D Pen", icon="GREASEPENCIL").tool = "draw_curve"
        layout.prop(settings, "pen_radius_m", text="Pen Thickness")
        layout.operator("xrwb.set_edit_tool", text="Spray Material", icon="MATERIAL").tool = "spray_material"


# ---- Hub ----------------------------------------------------------------------------

STUDIO_ENTRIES = (("XRWB_MT_prefabs", "Prefabs", "PACKAGE"), ("XRWB_MT_sdf", "SDF Blobs", "META_BALL"),
                  ("XRWB_MT_edit_mesh", "Edit V · E · F", "EDITMODE_HLT"),
                  ("XRWB_MT_modifiers", "Modifiers", "MODIFIER"), ("XRWB_MT_lights", "Lights", "LIGHT"),
                  ("XRWB_MT_camera_render", "Camera & Render", "CAMERA_DATA"), ("XRWB_MT_world", "World", "WORLD"),
                  ("XRWB_MT_physics", "Physics", "PHYSICS"), ("XRWB_MT_rig", "Rigging", "ARMATURE_DATA"),
                  ("XRWB_MT_paint_draw", "Paint & 3D Pen", "BRUSH_DATA"), ("XRWB_MT_outline", "Scene Outline", "OUTLINER"),
                  ("XRWB_MT_uv", "UV", "UV"), ("XRWB_MT_node_garden", "Node Garden", "NODETREE"),
                  ("XRWB_MT_blender", "Blender Menus", "BLENDER"))


class XRWB_MT_studio(bpy.types.Menu):
    bl_idname = "XRWB_MT_studio"
    bl_label = "Studio"

    def draw(self, context):
        layout = self.layout
        layout.label(text="STUDIO — EVERY PART OF BLENDER", icon="BLENDER")
        for menu, label, icon in STUDIO_ENTRIES:
            layout.operator("xrwb.open_hand_menu", text=f"{label}  ›", icon=icon).menu = menu


CLASSES = (XRWB_OT_modifier, XRWB_MT_modifier_add, XRWB_MT_modifiers, XRWB_OT_light, XRWB_MT_lights,
           XRWB_OT_camera, XRWB_OT_render, XRWB_MT_camera_render, XRWB_OT_world, XRWB_MT_world,
           XRWB_OT_physics, XRWB_MT_physics, XRWB_OT_rig, XRWB_MT_rig, XRWB_OT_outline, XRWB_MT_outline,
           XRWB_MT_paint_draw, XRWB_MT_studio)
