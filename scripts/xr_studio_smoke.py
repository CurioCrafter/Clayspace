"""Factory-profile check of Studio coverage: modifiers, lights, camera, render, world,
physics, rigging, outline, the 3D Pen and Paint Colour.

Real Blender operators, simulators and undo; no XR session or headset. Run in a
separate background process with BLENDER_USER_CONFIG set to an empty folder.
"""
from pathlib import Path
import sys

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (  # noqa: E402
    controllers, creative_tools, editing, physical_tools, quick_actions, ray_feedback, studio, tool_wheel, ui,
)
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "studio-smoke", 0)


class Props:
    def __init__(self, name, records):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_records", records)

    def __setattr__(self, key, value):
        family, action = self._name.split(".")
        rna = getattr(getattr(bpy.ops, family), action).get_rna_type()
        assert key in rna.properties, (self._name, key)
        if self._name == "xrwb.open_hand_menu" and key == "menu":
            assert hasattr(bpy.types, value), f"Menu {value} is not registered"
        prop = rna.properties[key]
        if prop.type == "ENUM":
            assert value in {item.identifier for item in prop.enum_items}, (self._name, key, value)
        self._records.append(("set", self._name, key, value))


class Layout:
    """Records menu entries and validates operators, their arguments and properties."""

    def __init__(self, records):
        self.records = records
        self.enabled = True

    def row(self, **_kwargs):
        return Layout(self.records)

    def grid_flow(self, **_kwargs):
        return Layout(self.records)

    def label(self, **kwargs):
        self.records.append(("label", kwargs.get("text", "")))

    def separator(self):
        pass

    def prop(self, owner, name, **_kwargs):
        assert name in owner.bl_rna.properties, (owner, name)
        self.records.append(("prop", name))

    def operator(self, name, **kwargs):
        family, action = name.split(".")
        assert getattr(getattr(bpy.ops, family), action).get_rna_type()
        self.records.append(("operator", name, kwargs.get("text", "")))
        return Props(name, self.records)


def draw(menu_class):
    records = []
    menu_class.draw(type("Menu", (), {"layout": Layout(records)})(), bpy.context)
    return records


def fresh():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.object
    cube.name = "Studio Cube"
    bpy.ops.ed.undo_push(message="Studio baseline")
    return cube


def check_modifiers():
    cube = fresh()
    for kind, label, _icon, props in studio.MODIFIERS:
        assert bpy.ops.xrwb.modifier(action="ADD", kind=kind) == {"FINISHED"}, kind
        modifier = bpy.data.objects["Studio Cube"].modifiers[-1]
        assert modifier.type == kind
        for prop in props:
            assert hasattr(modifier, prop), (kind, prop)
    cube = bpy.data.objects["Studio Cube"]
    assert len(cube.modifiers) == len(studio.MODIFIERS)
    assert cube.modifiers["Geometry Nodes"].node_group is not None
    records = draw(studio.XRWB_MT_modifiers)
    assert sum(item[0] == "prop" for item in records) >= len(studio.MODIFIERS)
    draw(studio.XRWB_MT_modifier_add)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["Studio Cube"].modifiers) == len(studio.MODIFIERS) - 1, "One undo per add"

    cube = fresh()
    bpy.ops.xrwb.modifier(action="ADD", kind="SUBSURF")
    bpy.ops.xrwb.modifier(action="ADD", kind="SOLIDIFY")
    assert bpy.ops.xrwb.modifier(action="UP", name="Solidify") == {"FINISHED"}
    cube = bpy.data.objects["Studio Cube"]
    assert [m.name for m in cube.modifiers] == ["Solidify", "Subdivision"]
    assert bpy.ops.xrwb.modifier(action="TOGGLE", name="Solidify") == {"FINISHED"}
    assert not bpy.data.objects["Studio Cube"].modifiers["Solidify"].show_viewport
    assert bpy.ops.xrwb.modifier(action="REMOVE", name="Solidify") == {"FINISHED"}
    assert bpy.ops.xrwb.modifier(action="APPLY", name="Subdivision") == {"FINISHED"}
    cube = bpy.data.objects["Studio Cube"]
    assert not cube.modifiers and len(cube.data.vertices) == 98, len(cube.data.vertices)  # Level 2.
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["Studio Cube"].data.vertices) == 8
    assert bpy.ops.xrwb.modifier(action="REMOVE", name="Nope") == {"CANCELLED"}
    return len(studio.MODIFIERS)


def check_lights_camera_world():
    fresh()
    hand = controllers.RIGHT_HAND
    direction = Vector((0.0, 1.0, -0.5)).normalized()
    ray_feedback.last_poses[hand] = ((1.0, -2.0, 1.5), tuple(Quaternion()), tuple(direction))
    try:
        for kind, label, _icon, energy in studio.LIGHTS:
            assert bpy.ops.xrwb.light(kind=kind) == {"FINISHED"}, kind
            light = bpy.context.active_object
            assert light.type == "LIGHT" and light.data.type == kind and light.data.energy == energy
            assert (light.location - Vector((1.0, -2.0, 1.5))).length < 1e-6, "Lights appear at the hand"
            aim = light.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
            assert (aim - direction).length < 1e-5, "Lights shine along the ray"
        records = draw(studio.XRWB_MT_lights)
        assert ("prop", "angle") in records, "Sun values show for the active sun"
        cube = bpy.data.objects["Studio Cube"]
        light = bpy.context.active_object
        cube.select_set(True)
        bpy.context.view_layer.objects.active = light
        assert bpy.ops.xrwb.light(kind="AIM") == {"FINISHED"}
        aim = light.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        assert (aim - (cube.location - light.location).normalized()).length < 1e-5

        assert bpy.ops.xrwb.camera(action="HAND") == {"FINISHED"}
        camera = bpy.context.scene.camera
        assert camera is not None and (camera.location - Vector((1.0, -2.0, 1.5))).length < 1e-6
        look = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        assert (look - direction).length < 1e-5
        assert bpy.ops.xrwb.camera(action="VIEW") == {"FINISHED"}
        assert bpy.context.scene.camera == camera, "Camera From My Eyes moves the same camera"
        bpy.ops.object.camera_add()
        second = bpy.context.active_object
        assert bpy.ops.xrwb.camera(action="ACTIVE") == {"FINISHED"} and bpy.context.scene.camera == second
        draw(studio.XRWB_MT_camera_render)
    finally:
        ray_feedback.last_poses.clear()

    for key, _label, colour, strength in studio.WORLDS:
        assert bpy.ops.xrwb.world(preset=key) == {"FINISHED"}
        background = bpy.context.scene.world.node_tree.nodes["Background"]
        assert all(abs(a - b) < 1e-6 for a, b in zip(background.inputs["Color"].default_value, colour))
        assert abs(background.inputs["Strength"].default_value - strength) < 1e-6
    assert ("prop", "default_value") in draw(studio.XRWB_MT_world)
    return len(studio.LIGHTS)


def check_render():
    fresh()
    scene = bpy.context.scene
    assert bpy.ops.xrwb.render(action="CYCLES") == {"FINISHED"} and scene.render.engine == "CYCLES"
    assert bpy.ops.xrwb.render(action="BLENDER_EEVEE_NEXT") == {"FINISHED"}
    assert "EEVEE" in scene.render.engine
    shading = bpy.context.window_manager.xr_session_settings.shading
    shading.type = "SOLID"
    assert bpy.ops.xrwb.render(action="LIVE") == {"FINISHED"} and shading.type == "RENDERED"
    assert bpy.ops.xrwb.render(action="LIVE") == {"FINISHED"} and shading.type == "SOLID"
    assert bpy.ops.xrwb.render(action="BLENDER_WORKBENCH") == {"FINISHED"}
    assert scene.render.engine == "BLENDER_WORKBENCH"
    # Headless containers have no GPU: render the still with CPU Cycles at 1 sample.
    assert bpy.ops.xrwb.render(action="CYCLES") == {"FINISHED"}
    scene.cycles.samples = 1
    scene.cycles.device = "CPU"
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 64, 48, 100
    if scene.camera is None:
        bpy.ops.object.camera_add(location=(0, -6, 0), rotation=(1.5708, 0, 0))
        scene.camera = bpy.context.active_object
    assert bpy.ops.xrwb.render(action="STILL") == {"FINISHED"}
    assert bpy.ops.xrwb.render(action="SAVE") == {"FINISHED"}
    saved = sorted((Path(bpy.app.tempdir) / "renders").glob("xr_render_*.png"))
    assert saved and saved[-1].stat().st_size > 0, "The render was saved as a PNG"
    return 1


def check_physics():
    cube = fresh()
    cube.location.z = 3.0
    bpy.ops.ed.undo_push(message="Physics baseline")
    assert bpy.ops.xrwb.physics(action="GROUND") == {"FINISHED"}
    assert bpy.ops.xrwb.physics(action="GROUND") == {"CANCELLED"}, "Only one ground"
    ground = bpy.data.objects[studio.GROUND]
    assert ground.rigid_body.type == "PASSIVE"
    cube = bpy.data.objects["Studio Cube"]
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    assert bpy.ops.xrwb.physics(action="ACTIVE") == {"FINISHED"}
    assert cube.rigid_body.type == "ACTIVE"
    scene = bpy.context.scene
    start = scene.frame_start
    for frame in range(start, start + 40):
        scene.frame_set(frame)
    fallen = cube.matrix_world.translation.z
    assert fallen < 2.0, f"The active body fell under gravity ({fallen:.2f})"
    assert bpy.ops.xrwb.physics(action="RESET") == {"FINISHED"}
    assert abs(cube.matrix_world.translation.z - 3.0) < 1e-3, "Back to start restores the pose"
    assert bpy.ops.xrwb.physics(action="CLOTH") == {"FINISHED"}
    assert bpy.ops.xrwb.physics(action="COLLISION") == {"FINISHED"}
    assert {m.type for m in cube.modifiers} == {"CLOTH", "COLLISION"}
    assert bpy.ops.xrwb.physics(action="REMOVE") == {"FINISHED"}
    assert cube.rigid_body is None and not cube.modifiers
    draw(studio.XRWB_MT_physics)
    return 1


def check_rig():
    cube = fresh()
    ray_feedback.last_poses[controllers.RIGHT_HAND] = ((0.0, 0.0, -0.5), tuple(Quaternion()), (0.0, 1.0, 0.0))
    try:
        assert bpy.ops.xrwb.rig(action="ADD") == {"FINISHED"}
    finally:
        ray_feedback.last_poses.clear()
    armature = bpy.context.active_object
    assert armature.type == "ARMATURE" and (armature.location - Vector((0, 0, -0.5))).length < 1e-6
    cube = bpy.data.objects["Studio Cube"]
    cube.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    assert bpy.ops.xrwb.rig(action="BIND") == {"FINISHED"}
    assert cube.parent == armature and any(m.type == "ARMATURE" for m in cube.modifiers)
    assert len(cube.vertex_groups) >= 1, "Automatic weights made a vertex group per bone"
    assert bpy.ops.xrwb.rig(action="POSE") == {"FINISHED"} and bpy.context.mode == "POSE"
    assert bpy.ops.xrwb.rig(action="OBJECT") == {"FINISHED"} and bpy.context.mode == "OBJECT"
    draw(studio.XRWB_MT_rig)
    return 1


def check_outline():
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 0))
    sphere = bpy.context.active_object
    sphere.name = "Studio Sphere"
    helper = bpy.data.objects.new("Helper", None)
    helper["xrwb_swatch"] = "x"
    bpy.context.collection.objects.link(helper)
    names = [obj.name for obj in studio.outline_objects(bpy.context)]
    assert names == ["Studio Cube", "Studio Sphere"], names
    assert bpy.ops.xrwb.outline(action="SELECT", name="Studio Cube") == {"FINISHED"}
    assert bpy.context.active_object.name == "Studio Cube" and not sphere.select_get()
    assert bpy.ops.xrwb.outline(action="HIDE", name="Studio Sphere") == {"FINISHED"} and sphere.hide_get()
    assert bpy.ops.xrwb.outline(action="HIDE", name="Studio Sphere") == {"FINISHED"} and not sphere.hide_get()
    assert bpy.ops.xrwb.outline(action="ISOLATE", name="Studio Sphere") == {"FINISHED"}
    assert bpy.data.objects["Studio Cube"].hide_get() and not sphere.hide_get()
    assert bpy.ops.xrwb.outline(action="SHOW_ALL") == {"FINISHED"}
    assert not bpy.data.objects["Studio Cube"].hide_get()
    records = draw(studio.XRWB_MT_outline)
    assert sum(1 for item in records if item[:3] == ("set", "xrwb.outline", "name")) == 6  # 2 objects x 3
    return 1


def pose_at(position, direction):
    rotation = Vector((0.0, 0.0, -1.0)).rotation_difference(Vector(direction).normalized())
    return Pose(tuple(position), tuple(rotation))


def check_pen_and_paint():
    fresh()
    settings = bpy.context.window_manager.xrwb_settings
    settings.pen_radius_m = 0.01
    start = pose_at((0.0, -3.0, 2.0), (0.0, 1.0, 0.0))
    stroke = physical_tools.begin(bpy.context, start.position, start.rotation, tool="draw_curve", owner=OWNER)
    assert isinstance(stroke, creative_tools.CurvePenStroke)
    for step in range(1, 21):
        pose = pose_at((step * 0.02, -3.0, 2.0 + 0.01 * step), (0.0, 1.0, 0.0))
        stroke.pressure = 0.5 if step > 10 else 1.0
        assert stroke.update(bpy.context, InputSample(OWNER, 0, pose, float(step))).decision is Decision.APPLY
    assert len(stroke.points) == 21 and len(stroke.lines) == 40
    result = stroke.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY, result.message
    curve = bpy.context.active_object
    assert curve.type == "CURVE" and abs(curve.data.bevel_depth - 0.01) < 1e-7
    points = curve.data.splines[0].bezier_points
    assert len(points) == 21 and points[-1].radius == 0.5 and points[0].radius == 1.0
    tip = curve.matrix_world @ points[0].co
    assert (tip - Vector((0.0, -3.0 + creative_tools.PEN_TIP_M, 2.0))).length < 1e-5, "Strokes start at the pen tip"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert not [o for o in bpy.data.objects if o.type == "CURVE"], "One undo removes the stroke"
    # A click without drawing leaves nothing.
    stroke = physical_tools.begin(bpy.context, start.position, start.rotation, tool="draw_curve", owner=OWNER)
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert not [o for o in bpy.data.objects if o.type == "CURVE"]

    # Paint Colour on a subdivided cube: soft red dab; cancel restores, commit is one undo.
    cube = bpy.data.objects["Studio Cube"]
    bpy.context.view_layer.objects.active = cube
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.subdivide(number_cuts=6)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.ed.undo_push(message="Paint baseline")
    settings.paint_colour = (1.0, 0.0, 0.0, 1.0)
    settings.spray_radius_m = 0.4
    aim = pose_at((0.0, 0.0, 4.0), (0.0, 0.0, -1.0))
    stroke = physical_tools.begin(bpy.context, aim.position, aim.rotation, tool="paint_vertex", owner=OWNER)
    assert isinstance(stroke, creative_tools.VertexPaintStroke)
    attribute = cube.data.color_attributes[creative_tools.PAINT_ATTRIBUTE]
    top = max(range(len(cube.data.vertices)), key=lambda i: -(cube.data.vertices[i].co.xy.length)
              + cube.data.vertices[i].co.z)
    assert attribute.data[top].color[1] < 1.0, "The first dab paints right away"
    assert stroke.cancel(bpy.context, OWNER).decision is Decision.CANCEL_REQUIRED
    assert creative_tools.PAINT_ATTRIBUTE not in cube.data.color_attributes, "Cancel removes the new colours"
    stroke = physical_tools.begin(bpy.context, aim.position, aim.rotation, tool="paint_vertex", owner=OWNER)
    for step in range(5):
        stroke.update(bpy.context, InputSample(OWNER, 0, aim, 1.0 + step))
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    colour = cube.data.color_attributes[creative_tools.PAINT_ATTRIBUTE].data[top].color
    assert colour[0] > 0.9 and colour[1] < 0.3, tuple(colour)
    far = min(range(len(cube.data.vertices)), key=lambda i: cube.data.vertices[i].co.z)
    assert tuple(cube.data.color_attributes[creative_tools.PAINT_ATTRIBUTE].data[far].color) == (1, 1, 1, 1)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert creative_tools.PAINT_ATTRIBUTE not in bpy.data.objects["Studio Cube"].data.color_attributes
    assert editing.active_transaction() is None
    return 1


def check_menus_and_wheels():
    for menu in (studio.XRWB_MT_studio, studio.XRWB_MT_paint_draw):
        draw(menu)
    settings = bpy.context.window_manager.xrwb_settings
    settings.workflow = "STAGE"
    records = draw(quick_actions.XRWB_MT_quick)
    assert ("set", "xrwb.open_hand_menu", "menu", "XRWB_MT_studio") in records
    assert {"select", "draw_curve"} <= {tool for tool, _l, _i in quick_actions.WORKFLOW_TOOLS["STAGE"]}
    settings.workflow = "MODEL"
    for items in tool_wheel.WHEELS.values():
        for kind, value, _label in items:
            if kind == "menu":
                assert hasattr(bpy.types, value), value
            elif kind == "tool":
                assert value in {item[0] for item in ui.ALL_TOOLS}, value
            elif kind == "op":
                name, arguments = value
                family, action = name.split(".")
                rna = getattr(getattr(bpy.ops, family), action).get_rna_type()
                assert set(arguments) <= set(rna.properties.keys()), value
    assert tool_wheel.items_for(type("S", (), {"wheel_set": "AUTO", "workflow": "STAGE"})()) is tool_wheel.WHEELS["STAGE"]
    return len(studio.STUDIO_ENTRIES)


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        results = {name: check() for name, check in (
            ("modifiers", check_modifiers), ("lights", check_lights_camera_world), ("render", check_render),
            ("physics", check_physics), ("rig", check_rig), ("outline", check_outline),
            ("pen_paint", check_pen_and_paint), ("menus", check_menus_and_wheels))}
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        xr_workbench.unregister()
    print("XR_STUDIO_SMOKE_PASS " + " ".join(f"{k}={v}" for k, v in results.items())
          + " input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
