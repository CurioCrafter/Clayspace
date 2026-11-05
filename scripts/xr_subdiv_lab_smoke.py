"""Factory-profile check of the 0.20 Subdivision Lab (Geometry Nodes groups made from Python).

Real Blender data, modifiers, evaluation and undo; synthetic controller poses only.
Run in a separate background process with BLENDER_USER_CONFIG set to an empty
temporary folder, on every Blender we ship (4.5, 5.x).
"""
import math
from pathlib import Path
import sys

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, subdiv_lab  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "lab-smoke", 0)
_clock = [10.0]


def sample(position, rotation=(1.0, 0.0, 0.0, 0.0)):
    _clock[0] += 0.01
    return InputSample(OWNER, 0, Pose(tuple(position), tuple(rotation)), _clock[0])


def fresh_cube():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    bpy.context.view_layer.objects.active = cube
    return cube


def evaluated(obj):
    """(vertex coordinates, polygon count) of the evaluated mesh."""
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return [v.co.copy() for v in mesh.vertices], len(mesh.polygons)
    finally:
        ev.to_mesh_clear()


def lab(obj):
    return subdiv_lab.lab_modifier(obj)


def check_schemes():
    results = {}
    for kind in subdiv_lab.SCHEMES:
        cube = fresh_cube()
        bpy.ops.ed.undo_push(message="lab baseline")
        message = subdiv_lab.add_scheme(bpy.context, kind)
        mod = lab(cube)
        assert mod is not None and mod.node_group[subdiv_lab.MARKER].startswith(kind + ":"), kind
        name = mod.node_group.name
        if kind == "GREEBLE":
            subdiv_lab.set_inputs(cube, mod, {"Probability": 1.0})
        coords, faces = evaluated(cube)
        assert len(coords) > 8 and faces > 6, (kind, len(coords), faces, message)
        results[kind] = len(coords)
        # Same scheme again reuses the group (no numbered copy).
        subdiv_lab.add_scheme(bpy.context, kind)
        assert lab(cube).node_group.name == name and not any(
            g.name.startswith(name + ".") for g in bpy.data.node_groups), kind
        # One undo removes the modifier.
        count = len(cube.modifiers)
        assert bpy.ops.ed.undo() == {"FINISHED"}
        assert len(bpy.data.objects["Cube"].modifiers) == count - 1, kind
    return results


def check_twist():
    cube = fresh_cube()
    subdiv_lab.add_scheme(bpy.context, "TWIST")
    mod = lab(cube)
    subdiv_lab.set_inputs(cube, mod, {"Levels": 1, "Twist": 0.0})
    plain, _ = evaluated(cube)
    subdiv_lab.set_inputs(cube, mod, {"Twist": 30.0})
    twisted, _ = evaluated(cube)
    zmin, zmax = min(v.z for v in plain), max(v.z for v in plain)
    # The highest corner-ring vertex turns by Twist x its height fraction (centre of the box is the pivot).
    index = max(range(len(plain)), key=lambda i: (abs(plain[i].x) + abs(plain[i].y) > 0.5, plain[i].z))
    before, after = plain[index], twisted[index]
    turned = math.degrees(math.atan2(after.y, after.x) - math.atan2(before.y, before.x))
    expected = 30.0 * (before.z - zmin) / (zmax - zmin)
    assert abs(turned - expected) < 1.5 and expected > 20.0, (turned, expected)
    subdiv_lab.set_inputs(cube, mod, {"Levels": 2})
    assert len(evaluated(cube)[0]) > 26
    flat = fresh_cube()
    flat.scale.z = 1e-9
    bpy.context.view_layer.update()
    assert "height" in subdiv_lab.edge_warning(flat, "TWIST")
    return turned


def check_hard():
    cube = fresh_cube()
    subdiv_lab.add_scheme(bpy.context, "HARD")
    mod = lab(cube)
    subdiv_lab.set_inputs(cube, mod, {"Crease Angle": 30.0})
    coords, _ = evaluated(cube)
    size = max(v.x for v in coords) - min(v.x for v in coords)
    assert abs(size - 2.0) < 0.02, size  # Corners kept: still a 2 m box.
    subdiv_lab.set_inputs(cube, mod, {"Crease Angle": 90.0})  # Nothing above 90 degrees: plain smooth cube.
    coords, _ = evaluated(cube)
    assert max(v.x for v in coords) < 1.0 - 0.05
    return size


def check_greeble_edge():
    cube = fresh_cube()
    subdiv_lab.add_scheme(bpy.context, "GREEBLE")
    mod = lab(cube)
    subdiv_lab.set_inputs(cube, mod, {"Probability": 1.0, "Min Area": 100.0})
    coords, faces = evaluated(cube)
    assert (len(coords), faces) == (8, 6), (len(coords), faces)  # Min Area above every face: no change.


def check_dial():
    cube = fresh_cube()
    subdiv_lab.add_scheme(bpy.context, "TWIST")
    mod = lab(cube)
    subdiv_lab.set_inputs(cube, mod, {"Levels": 1})
    start = Vector((0.0, 0.0, 2.0))
    stroke = subdiv_lab.begin(bpy.context, Pose(tuple(start), (1.0, 0.0, 0.0, 0.0)), OWNER, 0)
    for z in (2.0, 2.04, 2.08):
        result = stroke.update(bpy.context, sample((0.0, 0.0, z)))
        assert result.decision is Decision.APPLY, result
    assert subdiv_lab.get_input(mod, "Levels") == 3, subdiv_lab.get_input(mod, "Levels")
    assert stroke.cancel(bpy.context).decision is Decision.CANCEL_REQUIRED
    assert subdiv_lab.get_input(lab(cube), "Levels") == 1, "Cancel restores the original values"
    # Wrist roll of 45 degrees about the pointing axis moves Twist (30 -> 120 on this scheme).
    stroke = subdiv_lab.begin(bpy.context, Pose(tuple(start), (1.0, 0.0, 0.0, 0.0)), OWNER, 0)
    roll = Quaternion((0.0, 0.0, 1.0), math.radians(45.0))
    stroke.update(bpy.context, sample(start))
    stroke.update(bpy.context, sample(start, tuple(roll)))
    twist = subdiv_lab.get_input(lab(cube), "Twist")
    assert abs(twist - 120.0) < 6.0, twist
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert editing.active_transaction() is None
    return twist


def check_bake():
    cube = fresh_cube()
    subdiv_lab.add_scheme(bpy.context, "ORGANIC")
    verts_before = len(cube.data.vertices)
    subdiv_lab.bake(bpy.context)
    assert not cube.modifiers and len(cube.data.vertices) > verts_before


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and bpy.app.background
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()  # Registers the Lab and Growth operators too (0.20).
    try:
        counts = check_schemes()
        turned = check_twist()
        size = check_hard()
        check_greeble_edge()
        twist = check_dial()
        check_bake()
    finally:
        editing.cancel_active(bpy.context)
        xr_workbench.unregister()
    print(f"XR_SUBDIV_LAB_SMOKE_PASS version={bpy.app.version_string} schemes={len(counts)} twist_deg={turned:.1f} "
          f"hard_box={size:.3f} roll_twist={twist:.0f} input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
