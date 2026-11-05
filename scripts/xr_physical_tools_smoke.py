"""Factory-profile check of clay, spray, material blocks, presets, keys and scrubbing.

Real Blender data, operators and undo; synthetic controller poses only. No XR
session, headset or physical-input claim. Run in a separate factory-startup
background process with --python-exit-code 1; never in a user scene.
"""
import math
import os
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (  # noqa: E402
    animation_tools, editing, materials_lab, physical_tools, viewer_scale,
)
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "physical-smoke", 0)
DOWN = Quaternion()  # Identity aims along -Z.
_clock = [10.0]


def sample(position, rotation=DOWN):
    _clock[0] += 0.01
    return InputSample(OWNER, 0, Pose(tuple(position), tuple(rotation)), _clock[0])


def coords(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", values)
    return values


def materials(obj):
    indices = np.empty(len(obj.data.polygons), dtype=np.int32)
    obj.data.polygons.foreach_get("material_index", indices)
    return [m.name if m else "" for m in obj.data.materials], indices


def stroke(tool, start, path):
    transaction = physical_tools.begin(bpy.context, start, DOWN, tool=tool, owner=OWNER)
    assert transaction is not None and editing.active_transaction() is transaction
    for position in path:
        result = transaction.update(bpy.context, sample(position))
        assert result.decision is Decision.APPLY, (tool, result.message)
    return transaction


def undo_once():
    assert bpy.ops.ed.undo() == {"FINISHED"}


def check_sculpt(settings):
    assert bpy.ops.xrwb.add_clay() == {"FINISHED"}
    clay = bpy.context.object
    assert clay.name == "Clay" and len(clay.data.vertices) == 10242
    base = coords(clay)
    top = clay.matrix_world @ Vector((0, 0, 0.12))
    start = top + Vector((0, 0, 1))
    settings.sculpt_radius_m = 0.05

    # Cancel restores every coordinate exactly.
    transaction = stroke("sculpt_grab", start, [start + Vector((0, 0, 0.05))])
    assert not np.array_equal(coords(clay), base)
    transaction.cancel(bpy.context, OWNER)
    assert np.array_equal(coords(clay), base) and editing.active_transaction() is None

    # Grab pulls the brush region up; far vertices stay; commit is one undo step.
    transaction = stroke("sculpt_grab", start, [start + Vector((0, 0, 0.04))])
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    after = coords(bpy.data.objects["Clay"]).reshape(-1, 3)
    moved = np.linalg.norm(after - base.reshape(-1, 3), axis=1)
    assert 0.039 < moved.max() < 0.0401 and np.count_nonzero(moved > 1e-7) < len(moved) // 4
    undo_once()
    assert np.array_equal(coords(bpy.data.objects["Clay"]), base), "Grab was not one undo"

    # Clay builds outward along the surface normal; Dig carves inward.
    for invert, sign in ((False, 1.0), (True, -1.0)):
        clay = bpy.data.objects["Clay"]
        settings.sculpt_invert = invert
        path = [start + Vector((dx, 0, 0)) for dx in np.linspace(0.0, 0.04, 9)[1:]]
        transaction = stroke("sculpt_clay", start, path)
        z = coords(clay).reshape(-1, 3)[:, 2].max()
        assert (z - 0.12) * sign > 1e-4, (invert, z)
        assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
        undo_once()
    settings.sculpt_invert = False

    # Smooth relaxes a bump made by Grab.
    transaction = stroke("sculpt_grab", start, [start + Vector((0, 0, 0.03))])
    transaction.commit(bpy.context, OWNER)
    clay = bpy.data.objects["Clay"]
    bumped = coords(clay).reshape(-1, 3)[:, 2].max()
    high = clay.matrix_world @ Vector((0, 0, bumped)) + Vector((0, 0, 1))
    path = [high + Vector((dx, dy, 0)) for dx, dy in ((0.01, 0), (0, 0.01), (-0.01, 0), (0, -0.01)) * 3]
    transaction = stroke("sculpt_smooth", high, path)
    transaction.commit(bpy.context, OWNER)
    assert coords(bpy.data.objects["Clay"]).reshape(-1, 3)[:, 2].max() < bumped - 1e-4, "Smooth did not relax"
    undo_once()
    undo_once()
    assert np.array_equal(coords(bpy.data.objects["Clay"]), base)

    # Mirror X: a grab on +X moves the matching -X vertices by the mirrored amount.
    clay = bpy.data.objects["Clay"]
    settings.sculpt_mirror_x = True
    side = clay.matrix_world @ Vector((0.12, 0, 0))
    side_start = side + Vector((1, 0, 0))
    sideways = Quaternion((0, 1, 0), math.radians(90))  # Aim along -X, at the +X side.
    transaction = physical_tools.begin(bpy.context, side_start, sideways, tool="sculpt_grab", owner=OWNER)
    assert transaction.update(bpy.context, sample(side_start + Vector((0.03, 0, 0)), sideways)).decision is Decision.APPLY
    moved = coords(clay).reshape(-1, 3) - base.reshape(-1, 3)
    plus = int(np.argmax(moved[:, 0]))
    minus = int(np.argmin(moved[:, 0]))
    assert moved[plus, 0] > 0.02 and abs(moved[minus, 0] + moved[plus, 0]) < 1e-4, (moved[plus], moved[minus])
    transaction.cancel(bpy.context, OWNER)
    assert np.array_equal(coords(clay), base)
    settings.sculpt_mirror_x = False

    # Trigger pressure: a light squeeze builds less clay than a full one.
    heights = {}
    for pressure in (1.0, 0.25):
        transaction = physical_tools.begin(bpy.context, start, DOWN, tool="sculpt_clay", owner=OWNER)
        transaction.pressure = pressure
        for position in [start + Vector((dx, 0, 0)) for dx in np.linspace(0.0, 0.04, 9)[1:]]:
            transaction.update(bpy.context, sample(position))
        heights[pressure] = float(coords(clay).reshape(-1, 3)[:, 2].max()) - 0.12
        transaction.cancel(bpy.context, OWNER)
    assert 0 < heights[0.25] < heights[1.0] * 0.5, heights

    # Flatten levels a bump back toward the surface plane under the brush.
    clay = bpy.data.objects["Clay"]
    transaction = stroke("sculpt_grab", start, [start + Vector((0, 0, 0.03))])
    transaction.commit(bpy.context, OWNER)
    clay = bpy.data.objects["Clay"]
    bumped = coords(clay).reshape(-1, 3)[:, 2].max()
    high = clay.matrix_world @ Vector((0, 0, bumped)) + Vector((0, 0, 1))
    path = [high + Vector((dx, dy, 0)) for dx, dy in ((0.01, 0), (0, 0.01), (-0.01, 0), (0, -0.01)) * 3]
    transaction = stroke("sculpt_flatten", high, path)
    transaction.commit(bpy.context, OWNER)
    assert coords(bpy.data.objects["Clay"]).reshape(-1, 3)[:, 2].max() < bumped - 1e-3, "Flatten did not level"
    undo_once()
    undo_once()
    clay = bpy.data.objects["Clay"]
    assert np.array_equal(coords(clay), base)

    # Shrinking yourself makes the physical brush smaller in the world.
    real = viewer_scale.world_units_per_metre
    counts = {}
    try:
        for units in (1.0, 0.25):
            viewer_scale.world_units_per_metre = lambda _context, value=units: value
            transaction = stroke("sculpt_grab", start, [start + Vector((0, 0, 0.001))])
            counts[units] = int(np.count_nonzero(transaction.weights))
            transaction.cancel(bpy.context, OWNER)
    finally:
        viewer_scale.world_units_per_metre = real
    assert 0 < counts[0.25] < counts[1.0] / 8, counts

    # Remesh for Detail: physical voxel size, native voxel remesh, one undo.
    settings.remesh_detail_m = 0.02
    before = len(bpy.data.objects["Clay"].data.vertices)
    assert bpy.ops.xrwb.remesh_detail() == {"FINISHED"}
    remeshed = len(bpy.data.objects["Clay"].data.vertices)
    assert remeshed != before and remeshed > 100
    undo_once()
    assert len(bpy.data.objects["Clay"].data.vertices) == before
    print(f"XR_PHYSICAL_SCULPT grab=1 clay_build=1 clay_dig=1 smooth=1 cancel_exact=1 undo=1 "
          f"brush_scales_with_size={counts[1.0]}->{counts[0.25]} remesh={before}->{remeshed} "
          "mirror_x=1 pressure=1 flatten=1", flush=True)


def check_materials(settings):
    bpy.ops.mesh.primitive_cube_add(size=2, location=(4, 0, 0))
    cube = bpy.context.object
    cube.name = "Spray Target"
    assert bpy.ops.ed.undo_push(message="spray baseline") == {"FINISHED"}
    slots, indices = materials(cube)
    assert slots == [] and set(indices) == {0}

    # Every preset is a native node material with a labelled output.
    for key in materials_lab.PRESETS:
        material = materials_lab.build_preset(key)
        assert material.get(materials_lab.PRESET_KEY) == key and material.node_tree
        output = next(n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
        assert output.inputs["Surface"].is_linked and output.label.startswith("5.")
        assert materials_lab.build_preset(key) == material, "Presets must not duplicate"

    assert bpy.ops.xrwb.pick_material(identifier="GOLD") == {"FINISHED"}
    assert settings.spray_material == "XR Gold" and settings.edit_tool == "spray_material"
    start = Vector((4, 0, 3))
    # Cancel removes the appended slot and restores indices.
    transaction = stroke("spray_material", start, [])
    assert materials(cube)[0] == ["", "XR Gold"]
    transaction.cancel(bpy.context, OWNER)
    assert materials(cube)[0] == [] and set(materials(cube)[1]) == {0}
    # Commit paints only the top face under the nozzle; the rest keep an empty base slot.
    transaction = stroke("spray_material", start, [start + Vector((0.2, 0, 0))])
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    slots, indices = materials(bpy.data.objects["Spray Target"])
    assert slots == ["", "XR Gold"] and list(indices).count(1) == 1, (slots, indices)
    undo_once()
    assert materials(bpy.data.objects["Spray Target"])[0] == []

    # Two materials: the second spray paints only nearby faces.
    cube = bpy.data.objects["Spray Target"]
    cube.data.materials.append(materials_lab.build_preset("CLAY"))
    bpy.ops.ed.undo_push(message="clay base")
    settings.spray_material = materials_lab.build_preset("NEON").name
    transaction = stroke("spray_material", start, [])
    transaction.commit(bpy.context, OWNER)
    slots, indices = materials(bpy.data.objects["Spray Target"])
    assert slots == ["XR Clay", "XR Neon"] and list(indices).count(1) == 1, (slots, indices)
    # Eyedropper: trigger a painted face to load its material and switch to Spray.
    settings.edit_tool = "eyedropper"
    settings.spray_material = ""
    assert physical_tools.begin(bpy.context, start, DOWN, tool="eyedropper", owner=OWNER) is None
    assert settings.spray_material == "XR Neon" and settings.edit_tool == "spray_material", settings.spray_material

    undo_once()

    # Material shelf: physical blocks with labels, a dip loads the spray, removal is clean.
    before_objects = set(bpy.data.objects.keys())
    assert bpy.ops.xrwb.material_shelf() == {"FINISHED"}
    shelf = bpy.data.collections[materials_lab.SHELF_COLLECTION]
    blocks = [obj for obj in shelf.objects if materials_lab.SWATCH_KEY in obj.keys()]
    labels = [obj for obj in shelf.objects if materials_lab.LABEL_KEY in obj.keys()]
    assert len(blocks) == len(materials_lab.PRESETS) - len(materials_lab.UTILITY_PRESETS) == len(labels)
    assert all(obj.hide_render for obj in shelf.objects) and shelf.hide_render
    marble = next(obj for obj in blocks if obj[materials_lab.SWATCH_KEY] == "XR Marble")
    bpy.context.view_layer.update()
    # Aim from the viewer's side, as a person facing the shelf would.
    aim = marble.matrix_world.translation - Vector((0, 0.5, 0))
    forward = Quaternion((1, 0, 0), math.radians(90))
    settings.spray_material = ""
    assert physical_tools.begin(bpy.context, aim, forward, tool="spray_material", owner=OWNER) is None
    assert settings.spray_material == "XR Marble" and editing.active_transaction() is None
    assert bpy.ops.xrwb.material_shelf() == {"FINISHED"}
    assert set(bpy.data.objects.keys()) == before_objects and materials_lab.SHELF_COLLECTION not in bpy.data.collections
    assert not any(mesh.name.startswith("XR Swatch") for mesh in bpy.data.meshes)

    # Personal presets: one file per material, loaded only on request.
    cube = bpy.data.objects["Spray Target"]
    bpy.context.view_layer.objects.active = cube
    mine = bpy.data.materials.new("Studio Teal")
    mine.diffuse_color = (0.1, 0.6, 0.6, 1)
    cube.active_material = mine
    assert bpy.ops.xrwb.save_material_preset() == {"FINISHED"}
    assert materials_lab.user_presets() == ["Studio Teal"]
    bpy.data.materials.remove(mine)
    assert bpy.ops.xrwb.pick_material(identifier="USER:Studio Teal") == {"FINISHED"}
    loaded = bpy.data.materials[settings.spray_material]
    assert loaded.get(materials_lab.USER_PRESET_KEY) == "Studio Teal" and not loaded.use_fake_user
    assert tuple(loaded.diffuse_color)[:3] == (0.10000000149011612, 0.6000000238418579, 0.6000000238418579)
    assert bpy.ops.xrwb.material_shelf() == {"FINISHED"}
    assert len([o for o in bpy.data.collections[materials_lab.SHELF_COLLECTION].objects
                if materials_lab.SWATCH_KEY in o.keys()]) == len(materials_lab.PRESETS) - len(materials_lab.UTILITY_PRESETS) + 1
    assert bpy.ops.xrwb.material_shelf() == {"FINISHED"}
    print(f"XR_PHYSICAL_MATERIALS presets={len(materials_lab.PRESETS)} spray=1 spray_cancel_exact=1 "
          "spray_undo=1 eyedropper=1 shelf_blocks=1 dip=1 shelf_clean_removal=1 user_preset_file=1", flush=True)


def check_animation(settings):
    scene = bpy.context.scene
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    cube = bpy.data.objects["Spray Target"]
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    scene.frame_set(1)
    assert bpy.ops.xrwb.anim_key() == {"FINISHED"}
    scene.frame_set(20)
    cube.location.x = 8.0
    assert bpy.ops.xrwb.anim_key() == {"FINISHED"}
    cube = bpy.data.objects["Spray Target"]
    assert animation_tools.key_frames([cube]) == [1.0, 20.0]
    assert bpy.ops.xrwb.anim_frame(jump="PREV_KEY") == {"FINISHED"} and scene.frame_current == 1
    assert bpy.ops.xrwb.anim_frame(jump="NEXT_KEY") == {"FINISHED"} and scene.frame_current == 20
    assert bpy.ops.xrwb.anim_frame(jump="STEP", delta=-1) == {"FINISHED"} and scene.frame_current == 19

    # Scrub: 50 frames per physical metre sideways; cancel restores the frame; no undo step.
    scene.frame_set(1)
    settings.scrub_frames_per_m = 50.0
    right = Vector((1, 0, 0))
    transaction = stroke("scrub_time", Vector((0, 0, 1)), [Vector((0, 0, 1)) + right * 0.2])
    assert scene.frame_current == 11
    cube = bpy.data.objects["Spray Target"]
    assert 4.0 < cube.location.x < 8.0, "Scrub did not evaluate the animation"
    transaction.cancel(bpy.context, OWNER)
    assert scene.frame_current == 1
    transaction = stroke("scrub_time", Vector((0, 0, 1)), [Vector((0, 0, 1)) + right * 0.38])
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY and scene.frame_current == 20

    # Delete key on this frame, then native auto-key after a transform.
    assert bpy.ops.xrwb.anim_key(delete=True) == {"FINISHED"}
    assert animation_tools.key_frames([bpy.data.objects["Spray Target"]]) == [1.0]
    scene.tool_settings.use_keyframe_insert_auto = False
    assert animation_tools.autokey_after_transform(bpy.context) == 0
    scene.tool_settings.use_keyframe_insert_auto = True
    scene.frame_set(30)
    assert animation_tools.autokey_after_transform(bpy.context) > 0
    assert animation_tools.key_frames([bpy.data.objects["Spray Target"]]) == [1.0, 30.0]
    scene.tool_settings.use_keyframe_insert_auto = False
    print("XR_PHYSICAL_ANIMATION keys=1 jumps=1 scrub=1 scrub_cancel=1 delete_key=1 autokey=1", flush=True)


def check_viewer_scale():
    assert viewer_scale.clamp_scale(1000) == viewer_scale.MAX_SCALE
    head, navigation = Vector((1, 2, 1.7)), Vector((0, 0, 0))
    moved = viewer_scale.rescaled_location(navigation, head, 1.0, 0.25)
    # The head's tracking-space offset scales, but the head stays put in the world.
    assert (moved - Vector((0.75, 1.5, 1.275))).length < 1e-6
    try:
        viewer_scale.clamp_scale(float("nan"))
    except ValueError:
        pass
    else:
        raise AssertionError("NaN size accepted")
    assert bpy.ops.xrwb.viewer_scale.poll() is False, "Size changes need a running XR session"
    print("XR_PHYSICAL_SCALE head_pivot=1 clamp=1 session_required=1", flush=True)


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and bpy.app.background
    assert not bpy.data.filepath, "Only a factory unsaved test scene is allowed"
    assert os.environ.get("BLENDER_USER_DATAFILES"), "Set BLENDER_USER_DATAFILES to an empty temporary folder"
    assert not materials_lab.user_presets(), "Preset folder must start empty"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    xr_workbench.register()
    try:
        settings = bpy.context.window_manager.xrwb_settings
        bpy.ops.ed.undo_push(message="physical baseline")
        check_viewer_scale()
        check_sculpt(settings)
        check_materials(settings)
        check_animation(settings)
    finally:
        editing.cancel_active(bpy.context)
        xr_workbench.unregister()
    print("XR_PHYSICAL_TOOLS_SMOKE_PASS sculpt=3 spray=1 shelf=1 presets=1 animation=1 scale=1 "
          "input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
