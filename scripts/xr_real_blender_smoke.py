"""Factory-profile check of the 0.19 real-Blender tools: Undo Ring, Modifier Tower
and Geometry Nodes in the Node Garden.

Real Blender undo history, modifier stacks and node trees; no XR session or
headset. Run in a separate background process with BLENDER_USER_CONFIG set to
an empty temporary folder.
"""
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, modifier_tower, node_garden, undo_ring  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "real-blender-smoke", 0)


def fresh_cube(name):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    cube.name = name
    return cube


def check_undo_ring():
    cube = fresh_cube("Ring Cube")
    bpy.ops.ed.undo_push(message="Ring Start")
    cube.location.x = 1.0
    bpy.ops.ed.undo_push(message="Ring Move One")
    bpy.data.objects["Ring Cube"].location.x = 2.0
    bpy.ops.ed.undo_push(message="Ring Move Two")
    steps = undo_ring.read_steps(bpy.context)
    names = [name for _index, name, _active in steps]
    assert names[-3:] == ["Ring Start", "Ring Move One", "Ring Move Two"], names
    assert steps[-1][2] and not steps[-2][2], "The newest step is the current one"
    assert bpy.ops.xrwb.undo_ring(action="TOGGLE") == {"FINISHED"} and undo_ring.is_open()
    kinds = [kind for _angle, _name, kind in undo_ring.layout(undo_ring.steps())]
    assert kinds.count("current") == 1 and "undone" not in kinds
    ok, message = undo_ring.step(bpy.context, -1)
    assert ok and "Ring Move One" in message, message
    assert abs(bpy.data.objects["Ring Cube"].location.x - 1.0) < 1e-6, "Scrubbing back is a real undo"
    assert undo_ring.step(bpy.context, -1)[0]
    assert abs(bpy.data.objects["Ring Cube"].location.x) < 1e-6
    assert "undone" in [kind for _a, _n, kind in undo_ring.layout(undo_ring.steps())]
    assert undo_ring.step(bpy.context, 1)[0] and undo_ring.step(bpy.context, 1)[0]
    assert abs(bpy.data.objects["Ring Cube"].location.x - 2.0) < 1e-6, "Scrubbing forward is a real redo"
    ok, message = undo_ring.step(bpy.context, 1)
    assert not ok and "Newest" in message
    assert bpy.ops.xrwb.undo_ring(action="TOGGLE") == {"FINISHED"} and not undo_ring.is_open()
    return len(steps)


def block_of(name):
    return next(b for b in modifier_tower.blocks(modifier_tower.root(bpy.context))
                if b[modifier_tower.BLOCK_KEY] == name)


def ray_at(block):
    """A ray from 15 cm in front of a block's face to its middle (clear of the cube)."""
    tower = modifier_tower.root(bpy.context)
    centre = block.matrix_world @ Vector((modifier_tower.WIDTH_M / 2.0, 0.0, -modifier_tower.HEIGHT_M / 2.0))
    toward = tower.matrix_world.to_quaternion() @ Vector((0.0, 1.0, 0.0))
    return centre - toward * 0.15, toward


def check_tower():
    cube = fresh_cube("Tower Cube")
    assert bpy.ops.xrwb.modifier(action="ADD", kind="BEVEL") == {"FINISHED"}
    assert bpy.ops.xrwb.modifier(action="ADD", kind="SUBSURF") == {"FINISHED"}
    object_count = len(bpy.data.objects)
    assert bpy.ops.xrwb.modifier_tower(action="TOGGLE") == {"FINISHED"}
    tower = modifier_tower.root(bpy.context)
    assert modifier_tower.object_of(tower) == cube and len(modifier_tower.blocks(tower)) == 2
    assert block_of("Bevel").location.z < block_of("Subdivision").location.z, "First modifier at the bottom"
    result = modifier_tower.sync(bpy.context)
    assert result == "", result

    # A ray at a block finds it; trigger and stick act on its modifier.
    origin, direction = ray_at(block_of("Subdivision"))
    assert modifier_tower.block_at(bpy.context, origin, direction) == block_of("Subdivision")
    assert bpy.ops.xrwb.modifier_tower(action="DIAL", name="Subdivision", notches=1) == {"FINISHED"}
    assert bpy.data.objects["Tower Cube"].modifiers["Subdivision"].levels == 3
    assert bpy.ops.xrwb.modifier_tower(action="SWITCH", name="Bevel") == {"FINISHED"}
    assert not bpy.data.objects["Tower Cube"].modifiers["Bevel"].show_viewport
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert bpy.data.objects["Tower Cube"].modifiers["Bevel"].show_viewport, "One undo per switch"
    assert modifier_tower.sync(bpy.context) == "rebuilt", "Undo rebuilds the tower from the real stack"
    width = bpy.data.objects["Tower Cube"].modifiers["Bevel"].width
    modifier_tower.dial(bpy.context, "Bevel", -2)
    assert bpy.data.objects["Tower Cube"].modifiers["Bevel"].width < width

    # Grip the Bevel block above Subdivision: the stack follows, one undo step.
    block = block_of("Bevel")
    block.location.z = block_of("Subdivision").location.z + modifier_tower.PITCH_M * 0.6
    bpy.ops.ed.undo_push(message="XR Workbench Edit")  # What the grip itself records.
    assert modifier_tower.sync(bpy.context) == "reordered"
    cube = bpy.data.objects["Tower Cube"]
    assert [m.name for m in cube.modifiers] == ["Subdivision", "Bevel"]
    assert block_of("Bevel").location.z > block_of("Subdivision").location.z
    assert modifier_tower.sync(bpy.context) == ""
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert modifier_tower.sync(bpy.context) == "rebuilt", "Undo never replays a block move"
    assert [m.name for m in bpy.data.objects["Tower Cube"].modifiers] == ["Bevel", "Subdivision"]

    # With a block active, modifier tools still act on the tower's object.
    block = block_of("Bevel")
    bpy.context.view_layer.objects.active = block
    assert bpy.ops.xrwb.modifier(action="ADD", kind="NODES") == {"FINISHED"}
    assert not block.modifiers and len(bpy.data.objects["Tower Cube"].modifiers) == 3
    assert modifier_tower.sync(bpy.context) == "rebuilt" and len(modifier_tower.blocks(modifier_tower.root())) == 3

    assert bpy.ops.xrwb.modifier_tower(action="TOGGLE") == {"FINISHED"}
    assert not modifier_tower.is_out(bpy.context) and len(bpy.data.objects) == object_count
    assert bpy.data.collections.get(modifier_tower.COLLECTION) is None
    assert not [m for m in bpy.data.materials if m.name.startswith("XR Tower ")]
    return 3


def sphere_of(garden, node_name, output, socket_name):
    tree = node_garden.tree_of(garden)
    node = tree.nodes[node_name]
    sockets = node.outputs if output else node.inputs
    index = next(i for i, s in enumerate(sockets) if s.name == socket_name and s.enabled)
    return next(s for s in node_garden._sockets(garden) if node_garden.socket_key(s) ==
                (node_name, 1 if output else 0, index))


def link(start, end):
    origin = start.matrix_world.translation + Vector((0.0, -0.5, 0.0))
    aim = Vector((0.0, 0.0, -1.0)).rotation_difference(start.matrix_world.translation - origin)
    stroke = node_garden.begin_link(bpy.context, Pose(tuple(origin), tuple(aim)), OWNER)
    origin = end.matrix_world.translation + Vector((0.0, -0.5, 0.0))
    aim = Vector((0.0, 0.0, -1.0)).rotation_difference(end.matrix_world.translation - origin)
    assert stroke.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin), tuple(aim)), 1.0)).decision \
        is Decision.APPLY
    assert stroke.target == end
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY


def lowest_z(name):
    obj = bpy.data.objects[name].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return min((obj.matrix_world @ v.co).z for v in obj.data.vertices)


def check_geometry_garden():
    fresh_cube("Geo Cube")
    assert bpy.ops.xrwb.node_garden(action="GEOMETRY") == {"CANCELLED"}, "No Geometry Nodes yet"
    assert bpy.ops.xrwb.modifier(action="ADD", kind="NODES") == {"FINISHED"}
    group = bpy.data.objects["Geo Cube"].modifiers[-1].node_group.name
    assert bpy.ops.xrwb.node_garden(action="GEOMETRY") == {"FINISHED"}
    garden = node_garden.root(bpy.context)
    assert node_garden.source_of(garden) == bpy.data.node_groups[group] and node_garden.material_of(garden) is None
    assert len(node_garden.blocks(garden)) == 2 and len(node_garden.link_lines(bpy.context)) == 1
    assert ("GeometryNodeTransform", "Transform Geometry") in node_garden.add_nodes(node_garden.tree_of(garden))
    assert bpy.ops.xrwb.node_garden(action="ADD", node_type="GeometryNodeTransform") == {"FINISHED"}
    tree = bpy.data.node_groups[group]
    transform = tree.nodes.active
    transform.inputs["Translation"].default_value = (0.0, 0.0, 3.0)
    before = lowest_z("Geo Cube")
    garden = node_garden.root(bpy.context)
    assert len(node_garden.blocks(garden)) == 3
    link(sphere_of(garden, "Group Input", True, "Geometry"), sphere_of(garden, transform.name, False, "Geometry"))
    garden = node_garden.root(bpy.context)
    link(sphere_of(garden, transform.name, True, "Geometry"), sphere_of(garden, "Group Output", False, "Geometry"))
    assert abs(lowest_z("Geo Cube") - before - 3.0) < 1e-4, "Rewiring the tree changes the object live"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert abs(lowest_z("Geo Cube") - before) < 1e-4, "One undo per link"
    assert bpy.ops.ed.redo() == {"FINISHED"}
    node_garden.sync(bpy.context)
    tree = bpy.data.node_groups[group]
    tree.nodes.active = tree.nodes["Group Output"]
    assert bpy.ops.xrwb.node_garden(action="DELETE") == {"CANCELLED"}, "Group input/output stay"
    assert bpy.ops.xrwb.node_garden(action="TOGGLE") == {"FINISHED"} and not node_garden.is_out(bpy.context)
    return len(tree.nodes)


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        steps = check_undo_ring()
        blocks = check_tower()
        nodes = check_geometry_garden()
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        xr_workbench.unregister()
    print(f"XR_REAL_BLENDER_SMOKE_PASS undo_steps={steps} tower_blocks={blocks} geometry_nodes={nodes} "
          "input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
