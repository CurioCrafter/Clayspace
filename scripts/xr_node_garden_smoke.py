"""Factory-profile check of the physical Node Garden and the radial tool wheel.

Real Blender node trees and undo; no XR session or headset. Run in a separate
background process with BLENDER_USER_CONFIG set to an empty temporary folder.
"""
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, materials_lab, node_garden, tool_wheel  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "garden-smoke", 0)


def aim_at(point, back=0.5):
    """A pose half a metre in front of ``point`` (viewer side, -Y), aiming at it."""
    point = Vector(point)
    origin = point + Vector((0.0, -back, 0.0))
    rotation = Vector((0.0, 0.0, -1.0)).rotation_difference((point - origin).normalized())
    return Pose(tuple(origin), tuple(rotation))


def sphere_of(garden, node_name, output, socket_name):
    tree = node_garden.material_of(garden).node_tree
    node = tree.nodes[node_name]
    sockets = node.outputs if output else node.inputs
    index = next(i for i, s in enumerate(sockets) if s.name == socket_name and s.enabled)
    return next(s for s in node_garden._sockets(garden) if node_garden.socket_key(s) ==
                (node_name, 1 if output else 0, index))


def drag(start_sphere, end_point):
    pose = aim_at(start_sphere.matrix_world.translation)
    stroke = node_garden.begin_link(bpy.context, pose, OWNER)
    assert isinstance(stroke, node_garden.NodeLinkStroke)
    end = aim_at(end_point)
    assert stroke.update(bpy.context, InputSample(OWNER, 0, end, 1.0)).decision is Decision.APPLY
    return stroke


def check_garden():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    material = materials_lab.build_preset("MARBLE")
    cube.data.materials.append(material)
    name = material.name
    bpy.ops.ed.undo_push(message="Garden baseline")
    object_count = len(bpy.data.objects)
    tree = material.node_tree
    nodes = len(tree.nodes)
    links = len(tree.links)

    assert bpy.ops.xrwb.node_garden(action="TOGGLE") == {"FINISHED"}
    garden = node_garden.root(bpy.context)
    assert garden is not None and len(node_garden.blocks(garden)) == nodes
    assert len(node_garden.link_lines(bpy.context)) == links, "Every link is drawn between its spheres"

    # Grab-moving a block moves its node; moving the node moves the block.
    block = next(b for b in node_garden.blocks(garden) if b[node_garden.BLOCK_KEY] == "Principled BSDF")
    node = tree.nodes["Principled BSDF"]
    start = node.location.copy()
    block.location.x += 100.0
    assert node_garden.sync(bpy.context) == "moved node"
    assert abs(node.location.x - (start.x + 100.0)) < 1.0e-3
    node.location.y -= 50.0
    assert node_garden.sync(bpy.context) == "moved block"
    assert abs(block.location.z - (node.location.y - garden["xrwb_centre"][1])) < 1.0e-3
    assert node_garden.sync(bpy.context) == ""

    # Drag from the ramp's Color output to the BSDF's Roughness input.
    bpy.ops.ed.undo_push(message="Garden before link")
    garden = node_garden.root(bpy.context)
    out = sphere_of(garden, "Color Ramp", True, "Color")
    target = sphere_of(garden, "Principled BSDF", False, "Roughness")
    stroke = drag(out, target.matrix_world.translation)
    assert stroke.target == target and len(stroke.lines) == 2
    result = stroke.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY, result.message
    tree = bpy.data.materials[name].node_tree
    assert tree.nodes["Principled BSDF"].inputs["Roughness"].is_linked and len(tree.links) == links + 1
    assert editing.active_transaction() is None
    assert bpy.ops.ed.undo() == {"FINISHED"}
    tree = bpy.data.materials[name].node_tree
    assert not tree.nodes["Principled BSDF"].inputs["Roughness"].is_linked, "One undo removes the link"
    assert bpy.ops.ed.redo() == {"FINISHED"}
    tree = bpy.data.materials[name].node_tree
    assert tree.nodes["Principled BSDF"].inputs["Roughness"].is_linked

    # Pick the link up by its input and drop it in empty space: removed.
    node_garden.sync(bpy.context)
    garden = node_garden.root(bpy.context)
    held = sphere_of(garden, "Principled BSDF", False, "Roughness")
    stroke = drag(held, held.matrix_world.translation + Vector((0.0, 0.0, 0.6)))
    assert stroke.picked_link is not None and stroke.target is None
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    tree = bpy.data.materials[name].node_tree
    assert not tree.nodes["Principled BSDF"].inputs["Roughness"].is_linked and len(tree.links) == links

    # A drag from an unlinked socket to nowhere changes nothing.
    garden = node_garden.root(bpy.context)
    free = sphere_of(garden, "Principled BSDF", False, "Metallic")
    stroke = drag(free, free.matrix_world.translation + Vector((0.0, 0.0, 0.6)))
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY and len(tree.links) == links

    # Add a node beside the active one: the garden grows a block.
    tree.nodes.active = tree.nodes["Principled BSDF"]
    assert bpy.ops.xrwb.node_garden(action="ADD", node_type="ShaderNodeTexVoronoi") == {"FINISHED"}
    garden = node_garden.root(bpy.context)
    tree = bpy.data.materials[name].node_tree
    assert len(tree.nodes) == nodes + 1 and len(node_garden.blocks(garden)) == nodes + 1
    assert bpy.ops.xrwb.node_garden(action="DELETE") == {"FINISHED"}
    assert len(bpy.data.materials[name].node_tree.nodes) == nodes
    assert len(node_garden.blocks(node_garden.root(bpy.context))) == nodes
    # The output node cannot be deleted from VR.
    tree = bpy.data.materials[name].node_tree
    tree.nodes.active = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    assert bpy.ops.xrwb.node_garden(action="DELETE") == {"CANCELLED"}

    # Putting the garden away leaves exactly the user's scene.
    assert bpy.ops.xrwb.node_garden(action="TOGGLE") == {"FINISHED"}
    assert not node_garden.is_out(bpy.context) and len(bpy.data.objects) == object_count
    assert bpy.data.collections.get(node_garden.COLLECTION) is None
    assert not [m for m in bpy.data.materials if m.name.startswith("XR Garden ")]
    assert bpy.data.meshes.get("XR Garden Socket") is None
    return nodes


def check_wheel():
    settings = bpy.context.window_manager.xrwb_settings
    # Geometry: up is wedge 0, clockwise; the middle picks nothing.
    assert tool_wheel.wedge_at(0.0, 0.1, 0.1, 8) == 0
    assert tool_wheel.wedge_at(0.1, 0.0, 0.1, 8) == 2
    assert tool_wheel.wedge_at(0.0, -0.1, 0.1, 8) == 4
    assert tool_wheel.wedge_at(-0.1, 0.0, 0.1, 8) == 6
    assert tool_wheel.wedge_at(0.005, 0.005, 0.1, 8) == -1
    settings.workflow = "MODEL"
    settings.wheel_set = "AUTO"
    assert tool_wheel.items_for(settings) is tool_wheel.WHEELS["MODEL"]
    settings.wheel_set = "SHADE"
    assert tool_wheel.items_for(settings) is tool_wheel.WHEELS["SHADE"]
    settings.wheel_set = "AUTO"
    # Upright wheel facing -Y: right is +X, up is +Z.
    from mathutils import Quaternion
    import math
    upright = Quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    tool_wheel.open_wheel(bpy.context, "/user/hand/right", (0.0, 0.0, 1.0), upright, 1.0)
    assert tool_wheel.is_open() and tool_wheel.highlighted() is None
    assert tool_wheel.follow((0.08, 0.0, 1.0)) and tool_wheel.highlighted()[1] == "extrude_free"
    assert not tool_wheel.follow((0.09, 0.0, 1.0)), "Same wedge: no extra tick"
    for kind, value, _label in tool_wheel.WHEELS["MODEL"]:
        if kind == "tool":
            assert value in {item[0] for item in xr_workbench.ui.ALL_TOOLS}, value
    tool_wheel.close()
    # Every wheel's operator entries exist.
    for items in tool_wheel.WHEELS.values():
        for kind, value, _label in items:
            if kind == "op":
                group, name = value[0].split(".")
                assert hasattr(getattr(bpy.ops, group), name), value
            elif kind == "toggle":
                assert hasattr(settings, value), value
            elif kind == "tool":
                assert value in {item[0] for item in xr_workbench.ui.ALL_TOOLS}, value
    # Toggling from the wheel works without a session.
    before = settings.sculpt_mirror_x
    tool_wheel.open_wheel(bpy.context, "/user/hand/right", (0.0, 0.0, 1.0), upright, 1.0)
    tool_wheel._state["items"] = tool_wheel.WHEELS["SCULPT"]
    tool_wheel.follow((-0.06, 0.0, 0.94))  # Wedge 5 of 8: down-left.
    assert tool_wheel.highlighted()[1] == "sculpt_mirror_x"
    ok, _message = tool_wheel.pick(bpy.context)
    assert ok and settings.sculpt_mirror_x != before and not tool_wheel.is_open()
    return len(tool_wheel.WHEELS)


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        nodes = check_garden()
        wheels = check_wheel()
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        xr_workbench.unregister()
    print(f"XR_NODE_GARDEN_SMOKE_PASS nodes={nodes} link=1 unlink=1 undo=1 sync=2 add_delete=1 wheels={wheels} "
          "input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
