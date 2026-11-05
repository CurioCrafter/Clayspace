"""Factory-profile check of prefabs, starter scenes, your own prefabs, SDF blobs and the
0.17 control defaults (off-hand menus, grip a panel, Quest B = Add).

Real Blender and the real add-on; no XR session or headset. Run in a separate
background process with BLENDER_USER_CONFIG and BLENDER_USER_DATAFILES set to
empty temporary folders.
"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (  # noqa: E402
    controllers, editing, onboarding, physical_tools, polish, prefabs, ray_feedback, sdf_modeling, tool_wheel,
)
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "prefab-smoke", 0)
RIGHT, LEFT = controllers.RIGHT_HAND, controllers.LEFT_HAND


def pose_at(position, direction):
    rotation = Vector((0.0, 0.0, -1.0)).rotation_difference(Vector(direction).normalized())
    return Pose(tuple(position), tuple(rotation))


def fresh(ground=True):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    if ground:
        bpy.ops.mesh.primitive_plane_add(size=40)
        bpy.context.object.name = "Ground"
    bpy.ops.ed.undo_push(message="Prefab baseline")


def check_builtin():
    count = 0
    for key, (label, _category, _icon, _builder, modifiers, _smooth) in prefabs.PREFABS.items():
        obj, dims = prefabs.build(key, bpy.context)
        assert len(obj.data.vertices) > 7 and len(obj.data.polygons) > 5, key
        assert all(value > 0.0 for value in dims), (key, dims)
        assert obj.data.materials and all(obj.data.materials), key
        assert len(obj.modifiers) == len(modifiers), key
        bound = max(abs(v.co[i]) for v in obj.data.vertices for i in range(3))
        assert bound <= max(dims) / 2 + 1e-4, (key, "centred on its bounding box")
        mesh = obj.data
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        count += 1
    # Each part keeps its own material (BMesh reuses face slots; tagged by identity).
    tree, _dims = prefabs.build("TREE", bpy.context)
    by_slot = [0] * len(tree.data.materials)
    for polygon in tree.data.polygons:
        by_slot[polygon.material_index] += 1
    names = [m.name for m in tree.data.materials]
    assert by_slot[names.index("XR Wood")] == 12 and by_slot[names.index("XR Prefab Leaf")] == 60, (names, by_slot)
    tree_mesh = tree.data
    bpy.data.objects.remove(tree)
    bpy.data.meshes.remove(tree_mesh)
    # Real-world sizes are sensible.
    assert abs(prefabs.kind_dimensions("BUILTIN:TABLE")[2] - 0.75) < 1e-4
    assert abs(prefabs.kind_dimensions("BUILTIN:MANNEQUIN")[2] - 1.795) < 0.03
    return count


def check_place():
    fresh()
    settings = bpy.context.window_manager.xrwb_settings
    settings.prefab_kind = "BUILTIN:CHAIR"
    aim = pose_at((0.0, -3.0, 2.0), (0.0, 1.0, -0.66))
    stroke = physical_tools.begin(bpy.context, aim.position, aim.rotation, tool="place_prefab", owner=OWNER)
    assert isinstance(stroke, prefabs.PrefabStroke)
    chair = stroke.objects[0]
    bpy.context.view_layer.update()
    bottom = min((chair.matrix_world @ Vector(c)).z for c in chair.bound_box)
    assert 0.0 < bottom < 0.01, f"The chair rests on the ground ({bottom})"
    assert polish.readout(stroke).startswith("Chair · ")
    slide = pose_at((1.0, -3.0, 2.0), (0.0, 1.0, -0.66))
    assert stroke.update(bpy.context, InputSample(OWNER, 0, slide, 1.0)).decision is Decision.APPLY
    assert abs(chair.matrix_world.translation.x - 1.0) < 1e-3, "Holding slides it along the floor"
    shapes = ray_feedback._preview_shapes(bpy.context, settings, "place_prefab", 1.0, {})
    controllers._active_gesture = NS(transaction=stroke, user_path=RIGHT)
    try:
        shapes = ray_feedback._preview_shapes(bpy.context, settings, "place_prefab", 1.0, {})
        assert shapes and len(shapes[0][0]) == 24, "The prefab shows a live box outline"
    finally:
        controllers._active_gesture = None
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert "Chair" in bpy.data.objects and bpy.context.active_object.name == "Chair"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert "Chair" not in bpy.data.objects, "Placing a prefab is one undo step"
    # Cancel removes it and its mesh.
    meshes = len(bpy.data.meshes)
    stroke = physical_tools.begin(bpy.context, aim.position, aim.rotation, tool="place_prefab", owner=OWNER)
    stroke.cancel(bpy.context, OWNER)
    assert "Chair" not in bpy.data.objects and len(bpy.data.meshes) == meshes
    # Ghost before pressing: sized to the prefab.
    poses = {controllers._pointer_hand: (Vector(aim.position), None, Vector((0.0, 1.0, -0.66)).normalized())}
    ghost = ray_feedback._preview_shapes(bpy.context, settings, "place_prefab", 1.0, poses)
    assert ghost and len(ghost[0][0]) == 24
    # Picking from the menu sets the tool (with a simulated session).
    real = xr_workbench.runtime.session_running
    xr_workbench.runtime.session_running = lambda _c: True
    try:
        assert bpy.ops.xrwb.pick_prefab(kind="BUILTIN:TREE") == {"FINISHED"}
        assert settings.edit_tool == "place_prefab" and settings.prefab_kind == "BUILTIN:TREE"
        assert bpy.ops.xrwb.pick_prefab(kind="BUILTIN:NOPE") == {"CANCELLED"}
    finally:
        xr_workbench.runtime.session_running = real
    return 1


def check_mine_and_scenes():
    fresh(ground=False)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(10, 0, 0.5))
    base = bpy.context.object
    base.name = "Totem Base"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(10, 0, 1.3))
    head = bpy.context.object
    head.name = "Totem Head"
    base.select_set(True)
    bpy.context.view_layer.objects.active = head
    assert bpy.ops.xrwb.prefab_action(action="SAVE") == {"FINISHED"}
    assert prefabs.user_prefabs() == ["Totem Head"]
    assert abs(prefabs.kind_dimensions("USER:Totem Head")[2] - 1.6) < 1e-4
    fresh()
    settings = bpy.context.window_manager.xrwb_settings
    settings.prefab_kind = "USER:Totem Head"
    aim = pose_at((0.0, -3.0, 2.0), (0.0, 1.0, -0.66))
    stroke = physical_tools.begin(bpy.context, aim.position, aim.rotation, tool="place_prefab", owner=OWNER)
    assert len(stroke.objects) == 2
    stroke.commit(bpy.context, OWNER)
    placed = {obj.name.split(".")[0]: obj for obj in bpy.data.objects if obj.name.startswith("Totem")}
    gap = placed["Totem Head"].matrix_world.translation - placed["Totem Base"].matrix_world.translation
    assert abs(gap.z - 0.8) < 1e-4, "Your prefab keeps its parts' arrangement"
    bpy.context.view_layer.update()
    bottom = min((placed["Totem Base"].matrix_world @ Vector(c)).z for c in placed["Totem Base"].bound_box)
    assert 0.0 < bottom < 0.01, "and rests on the ground"
    # Starter scenes.
    for key, _label, _icon in prefabs.SCENES:
        fresh(ground=False)
        before = len(bpy.data.objects)
        assert bpy.ops.xrwb.prefab_action(action=key) == {"FINISHED"}, key
        made = len(bpy.data.objects) - before
        assert made >= 4, (key, made)
        assert bpy.context.scene.camera is not None
        assert any(obj.type == "LIGHT" for obj in bpy.data.objects), key
        assert bpy.ops.ed.undo() == {"FINISHED"}
        assert len(bpy.data.objects) == before, f"{key} is one undo step"
    return 1


def check_sdf():
    fresh(ground=False)
    settings = bpy.context.window_manager.xrwb_settings
    settings.sdf_radius_m, settings.sdf_mode, settings.sdf_shape, settings.sdf_carve = 0.1, "TRAIL", "BALL", False
    start = pose_at((0.0, -1.0, 1.0), (0.0, 1.0, 0.0))
    stroke = physical_tools.begin(bpy.context, start.position, start.rotation, tool="sdf_blob", owner=OWNER)
    assert isinstance(stroke, sdf_modeling.SdfStroke) and stroke.created_object
    obj = stroke.obj
    assert sdf_modeling.is_sdf(obj) and len(obj.data.elements) == 1
    tip = obj.matrix_world @ Vector(obj.data.elements[0].co)
    assert (tip - Vector((0.0, -0.8, 1.0))).length < 1e-5, "Blobs start at the pen tip"
    for step in range(1, 6):
        pose = pose_at((step * 0.1, -1.0, 1.0), (0.0, 1.0, 0.0))
        assert stroke.update(bpy.context, InputSample(OWNER, 0, pose, float(step))).decision is Decision.APPLY
    assert len(obj.data.elements) >= 5, "Trail lays blobs along the path"
    assert polish.readout(stroke).startswith("Blob ")
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    count = len(obj.data.elements)
    # Carve blob into it.
    settings.sdf_carve = True
    stroke = physical_tools.begin(bpy.context, pose_at((0.25, -1.4, 1.0), (0, 1, 0)).position,
                                  pose_at((0.25, -1.4, 1.0), (0, 1, 0)).rotation, tool="sdf_blob", owner=OWNER)
    stroke.commit(bpy.context, OWNER)
    obj = bpy.data.objects["XR SDF"]
    assert len(obj.data.elements) == count + 1 and obj.data.elements[-1].use_negative
    settings.sdf_carve = False
    # Trigger on an existing blob grabs it and moves it; cancel puts it back.
    first = obj.data.elements[0]
    centre = obj.matrix_world @ Vector(first.co)
    grab = pose_at(centre + Vector((0.0, -1.0, 0.0)), (0.0, 1.0, 0.0))
    stroke = physical_tools.begin(bpy.context, grab.position, grab.rotation, tool="sdf_blob", owner=OWNER)
    assert stroke.moved is not None and not stroke.added
    moved = Pose(tuple(Vector(grab.position) + Vector((0.0, 0.0, 0.3))), grab.rotation)
    stroke.update(bpy.context, InputSample(OWNER, 0, moved, 1.0))
    assert abs((obj.matrix_world @ Vector(first.co)).z - (centre.z + 0.3)) < 1e-5
    stroke.cancel(bpy.context, OWNER)
    assert ((obj.matrix_world @ Vector(first.co)) - centre).length < 1e-6
    # Undo removes a whole stroke; cancel of a first stroke removes the object.
    bpy.ops.ed.undo_push(message="SDF before undo test")
    assert bpy.ops.ed.undo() == {"FINISHED"}
    # Menu actions, then bake to a real mesh.
    obj = bpy.data.objects["XR SDF"]
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    resolution = obj.data.resolution
    assert bpy.ops.xrwb.sdf_action(action="FINER") == {"FINISHED"} and obj.data.resolution < resolution
    assert bpy.ops.xrwb.sdf_action(action="SMOOTHER") == {"FINISHED"}
    assert bpy.ops.xrwb.sdf_action(action="BAKE") == {"FINISHED"}
    baked = bpy.context.view_layer.objects.active
    assert baked.type == "MESH" and len(baked.data.vertices) > 100, "The field baked to a mesh"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert bpy.data.objects["XR SDF"].type == "META", "Bake is one undo step"
    # What you see is what you get: a 10 cm ball bakes to a 20 cm wide mesh.
    fresh(ground=False)
    settings.sdf_mode = "PLACE"
    stroke = physical_tools.begin(bpy.context, start.position, start.rotation, tool="sdf_blob", owner=OWNER)
    stroke.commit(bpy.context, OWNER)
    obj = bpy.data.objects["XR SDF"]
    obj.data.resolution = 0.005
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.xrwb.sdf_action(action="BAKE") == {"FINISHED"}
    width = bpy.context.view_layer.objects.active.dimensions.x
    assert abs(width - 0.2) < 0.012, f"Baked ball is {width:.3f} m wide, not 0.2"
    settings.sdf_mode = "TRAIL"
    # A first stroke that is cancelled leaves nothing behind.
    fresh(ground=False)
    metaballs = len(bpy.data.metaballs)
    stroke = physical_tools.begin(bpy.context, start.position, start.rotation, tool="sdf_blob", owner=OWNER)
    assert len(bpy.data.metaballs) == metaballs + 1
    stroke.cancel(bpy.context, OWNER)
    assert not [o for o in bpy.data.objects if o.type == "META"] and len(bpy.data.metaballs) == metaballs
    return count


def check_controls():
    # Menus sit on the off hand by default, whatever button opened them.
    assert controllers.menu_hand_for(RIGHT) == LEFT and controllers.menu_hand_for(LEFT) == LEFT
    onboarding.set_pref("left_handed", True)
    assert controllers.menu_hand_for(LEFT) == RIGHT
    onboarding.set_pref("left_handed", False)
    onboarding.set_pref("menu_hand", "PRESSING")
    assert controllers.menu_hand_for(RIGHT) == RIGHT
    onboarding.set_pref("menu_hand", "OFF")
    # Gripping a desktop panel grabs the panel (either grip).
    grabbed = []
    real_on, real_grab = controllers._ray_on_panel, controllers._begin_panel_grab
    controllers._ray_on_panel = lambda _c, _e: True
    controllers._begin_panel_grab = lambda _c, _e: grabbed.append(1) or (True, "panel")
    try:
        event = NS(type="XR_ACTION", value="PRESS", xr=NS(action_set=controllers.ACTION_SET_NAME, action="grab_move",
                                                          user_path=RIGHT, controller_location=(0, 0, 1),
                                                          controller_rotation=(1, 0, 0, 0)))
        assert controllers._begin_move(bpy.context, event, grip=True) == (True, "panel")
        event.xr.action, event.xr.user_path = "world_grab", LEFT
        assert controllers._begin_world_grip(bpy.context, event) == (True, "panel")
        assert grabbed == [1, 1]
    finally:
        controllers._ray_on_panel, controllers._begin_panel_grab = real_on, real_grab
    # Quest: B is Add (like Index), panels move by grip; labels agree.
    assert "B: Add Shapes" in onboarding.button_hints("QUEST", "RIGHT", "idle")[2]
    text = "\n".join(onboarding.controls_lines("QUEST"))
    assert "Right B: Add" in text and "Grip a floating desktop panel" in text
    # The Sculpt wheel reaches SDF; the Add menu reaches prefabs and SDF.
    assert any(item[1] == "XRWB_MT_sdf" for item in tool_wheel.WHEELS["SCULPT"])
    return 1


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    assert os.environ.get("BLENDER_USER_DATAFILES"), "Set BLENDER_USER_DATAFILES to an empty temporary folder"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        results = {name: check() for name, check in (
            ("builtin", check_builtin), ("place", check_place), ("mine_scenes", check_mine_and_scenes),
            ("sdf", check_sdf), ("controls", check_controls))}
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        controllers._active_gesture = None
        xr_workbench.unregister()
    print("XR_PREFABS_SDF_SMOKE_PASS " + " ".join(f"{k}={v}" for k, v in results.items())
          + " input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
