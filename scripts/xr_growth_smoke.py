"""Factory-profile check of the 0.20 Guided Growth: branch, vine, spiral seeds and Path Array.

Real Blender curves, Geometry Nodes evaluation and undo; synthetic controller poses
only. Run in a separate background process with BLENDER_USER_CONFIG set to an
empty temporary folder, on every Blender we ship (4.5, 5.x).
"""
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, growth, subdiv_lab  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "growth-smoke", 0)
IDENTITY = (1.0, 0.0, 0.0, 0.0)  # Aims along -Z.
_clock = [10.0]


def sample(position, rotation=IDENTITY):
    _clock[0] += 0.01
    return InputSample(OWNER, 0, Pose(tuple(position), tuple(rotation)), _clock[0])


def clear():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def grow(kind, start, path, commit=True, seed=1):
    growth.STATE.update(kind=kind, seed=seed)
    stroke = growth.begin(bpy.context, Pose(tuple(start), IDENTITY), OWNER, 0)
    for position in path:
        result = stroke.update(bpy.context, sample(position))
        assert result.decision is Decision.APPLY, (kind, result.message)
    assert stroke.preview_points() and len(sum(stroke.preview_points(), [])) <= growth.PREVIEW_POINTS
    if commit:
        assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    return stroke


def growth_objects():
    return [o for o in bpy.data.objects if growth.GROWTH_KEY in o.keys()]


def evaluated_faces(obj):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return len(mesh.polygons)
    finally:
        ev.to_mesh_clear()


def check_branch_spiral_undo():
    clear()
    bpy.ops.ed.undo_push(message="growth baseline")
    start = (3.0, 3.0, 3.0)  # Nothing below: the seed plants in the air.
    grow("BRANCH", start, [(3.0, 3.0, 3.2), (3.0, 3.0, 3.5)], commit=False)
    editing.cancel_active(bpy.context)
    assert not growth_objects(), "Cancel creates nothing"
    grow("BRANCH", start, [(3.0, 3.0, 3.2), (3.0, 3.0, 3.5)])
    objs = growth_objects()
    assert len(objs) == 1 and objs[0].type == "CURVE" and len(objs[0].data.splines) >= 1
    assert all(s.type == "POLY" for s in objs[0].data.splines)
    assert evaluated_faces(objs[0]) > 0, "The GN skin makes a mesh"
    splines = len(objs[0].data.splines)
    # Active Growth object: a spiral extends it instead of making a second one.
    grow("SPIRAL", (3.0, 4.0, 3.0), [(3.0, 4.0, 3.2), (3.0, 4.0, 3.4)])
    assert len(growth_objects()) == 1 and len(growth_objects()[0].data.splines) == splines + 1
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(growth_objects()) == 1 and len(growth_objects()[0].data.splines) == splines, "Undo removes the spiral"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert not growth_objects(), "One undo removes the branch object"
    return splines


def check_vine():
    clear()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0.0, 0.0, 0.0))
    sphere = bpy.context.object
    bpy.ops.ed.undo_push(message="vine baseline")
    grow("VINE", (0.0, 0.0, 2.0), [(0.2, 0.0, 2.0), (0.4, 0.0, 2.0)])
    vine = growth_objects()[0]
    worst, count = 0.0, 0
    for spline in vine.data.splines:
        for point in spline.points:
            ok, location, _n, _i = sphere.closest_point_on_mesh(Vector(point.co[:3]))
            gap = (Vector(point.co[:3]) - location).length - point.radius
            worst, count = max(worst, gap), count + 1
    assert count > 10 and worst <= 0.001, (count, worst)
    # No surface under the ray: refused.
    editing.cancel_active(bpy.context)
    try:
        growth.STATE["kind"] = "VINE"
        growth.begin(bpy.context, Pose((5.0, 5.0, 5.0), IDENTITY), OWNER, 0)
        raise AssertionError("A vine in the air must be refused")
    except editing.EditRejected as exc:
        assert "surface" in str(exc)
    return count


def instances_of(source):
    graph = bpy.context.evaluated_depsgraph_get()
    return sum(1 for inst in graph.object_instances if inst.is_instance and inst.object.original is source)


def check_path_array():
    clear()
    bpy.ops.mesh.primitive_cube_add(size=0.05, location=(0.0, 2.0, 0.0))
    source = bpy.context.object
    curve_data = bpy.data.curves.new("Line", "CURVE")
    curve_data.dimensions = "3D"
    spline = curve_data.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co = (0.0, 0.0, 0.0, 1.0)
    spline.points[1].co = (1.0, 0.0, 0.0, 1.0)
    curve = bpy.data.objects.new("Line", curve_data)
    bpy.context.collection.objects.link(curve)
    source.select_set(True)
    curve.select_set(True)
    bpy.context.view_layer.objects.active = curve
    bpy.ops.ed.undo_push(message="array baseline")
    assert bpy.ops.xrwb.path_array() == {"FINISHED"}
    mod = subdiv_lab.lab_modifier(curve)
    assert mod is not None and mod.node_group[subdiv_lab.MARKER].startswith("PATH_ARRAY")
    subdiv_lab.set_inputs(curve, mod, {"Spacing": 0.1})
    bpy.context.view_layer.update()
    count = instances_of(source)
    assert count == 11, count
    # Lab Dial on the curve: hand up one detent = spacing + 0.01.
    stroke = subdiv_lab.begin(bpy.context, Pose((0.0, 0.0, 2.0), IDENTITY), OWNER, 0)
    for z in (2.0, 2.04):
        assert stroke.update(bpy.context, sample((0.0, 0.0, z))).decision is Decision.APPLY
    assert abs(subdiv_lab.get_input(mod, "Spacing") - 0.11) < 1e-6
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert bpy.ops.ed.undo() == {"FINISHED"}  # Dial.
    assert bpy.ops.ed.undo() == {"FINISHED"}  # Array.
    assert not bpy.data.objects["Line"].modifiers, "One undo removes the Path Array"
    return count


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and bpy.app.background
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()  # Registers the Lab and Growth operators too (0.20).
    try:
        splines = check_branch_spiral_undo()
        points = check_vine()
        instances = check_path_array()
    finally:
        editing.cancel_active(bpy.context)
        xr_workbench.unregister()
    print(f"XR_GROWTH_SMOKE_PASS version={bpy.app.version_string} branch_splines={splines} vine_points={points} "
          f"array_instances={instances} input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
