"""Factory-profile check of free extrude, inset, UV islands and the UV actions.

Real Blender operators and undo; no XR session or headset. Run in a separate
background process with BLENDER_USER_CONFIG set to an empty temporary folder.
"""
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, face_editing, physical_tools, uv_tools  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "modeling-smoke", 0)


def sample(position, timestamp=1.0, rotation=(1.0, 0.0, 0.0, 0.0)):
    return InputSample(OWNER, 0, Pose(tuple(position), tuple(rotation)), timestamp)


def fresh_cube(name="XR_Model"):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.ed.undo_push(message="Modeling baseline")
    return obj


def top_ray():
    # From above, looking down (-Z) at the top face of the 2 m cube.
    return Vector((0.2, 0.1, 3.0)), Vector((0, 0, -1)).rotation_difference(Vector((0, 0, -1)))


def manifold(mesh):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        return all(edge.is_manifold for edge in bm.edges)
    finally:
        bm.free()


def check_extrude_free():
    obj = fresh_cube()
    origin, rotation = top_ray()
    transaction = face_editing.begin(bpy.context, origin, rotation, tool="extrude_free", owner=OWNER)
    delta = Vector((0.4, -0.3, 0.5))
    assert transaction.update(bpy.context, sample(origin + delta)).decision is Decision.APPLY
    top = [obj.matrix_world @ v.co for v in obj.data.vertices if v.co.z > 1.2]
    assert len(top) == 4, f"Free extrude should lift a four-corner cap, got {len(top)}"
    centre = sum(top, Vector()) / 4
    assert (centre - (Vector((0, 0, 1)) + delta)).length < 1.0e-5, centre
    assert manifold(obj.data)
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert (len(obj.data.vertices), len(obj.data.polygons)) == (12, 10)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["XR_Model"].data.vertices) == 8
    return 1


def check_chain_and_direction():
    obj = fresh_cube()
    origin, rotation = top_ray()
    first = face_editing.begin(bpy.context, origin, rotation, tool="extrude", owner=OWNER)
    try:
        face_editing.chain(bpy.context, first, origin, rotation)
        raise AssertionError("Chained without pulling first")
    except editing.EditRejected:
        pass
    up = origin + Vector((0, 0, 0.5))
    first.update(bpy.context, sample(up))
    second = face_editing.chain(bpy.context, first, up, rotation)
    assert first.finished and editing.active_transaction() is second
    assert abs(second.center.z - 1.5) < 1.0e-5 and second.normal.z > 0.99, "Next segment starts at the new cap"
    # Switch to free mid-drag: nothing jumps, then sideways motion moves the cap.
    second.update(bpy.context, sample(up + Vector((0, 0, 0.5)), timestamp=2))
    before = second.cap_center()
    assert abs(before.z - 2.0) < 1.0e-5
    second.toggle_direction(up + Vector((0, 0, 0.5)))
    assert second.tool == "extrude_free" and (second.cap_center() - before).length < 1.0e-6
    second.update(bpy.context, sample(up + Vector((0.3, 0, 0.5)), timestamp=3))
    assert (second.cap_center() - (before + Vector((0.3, 0, 0)))).length < 1.0e-5
    # Back to straight: sideways hand motion is ignored, only the normal counts.
    second.toggle_direction(up + Vector((0.3, 0, 0.5)))
    second.update(bpy.context, sample(up + Vector((0.6, 0, 0.7)), timestamp=4))
    assert (second.cap_center() - (before + Vector((0.3, 0, 0.2)))).length < 1.0e-5
    assert second.commit(bpy.context, OWNER).decision is Decision.APPLY
    mesh = bpy.data.objects["XR_Model"].data
    assert (len(mesh.vertices), len(mesh.polygons)) == (16, 14) and manifold(mesh)
    # Two segments, two undo steps.
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["XR_Model"].data.vertices) == 12
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["XR_Model"].data.vertices) == 8
    # A switch with no net pull still counts: the baked segment is kept.
    obj = bpy.data.objects["XR_Model"]
    third = face_editing.begin(bpy.context, origin, rotation, tool="extrude", owner=OWNER)
    third.update(bpy.context, sample(up))
    third.toggle_direction(up)
    assert third.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert len(obj.data.vertices) == 12
    return 1


def check_inset():
    obj = fresh_cube()
    origin, rotation = top_ray()
    transaction = face_editing.begin(bpy.context, origin, rotation, tool="inset", owner=OWNER)
    # Sideways (+X of an identity-rotated hand) by 30 cm: a 0.3 m border.
    assert transaction.update(bpy.context, sample(origin + Vector((0.3, 0, 0)))).decision is Decision.APPLY
    assert abs(transaction.value - 0.3) < 1.0e-6
    assert len(obj.data.polygons) == 10, "Inset adds four border faces around the top"
    inner = [v.co for v in obj.data.vertices if abs(v.co.z - 1) < 1.0e-6 and abs(v.co.x) < 0.99]
    assert len(inner) == 4 and all(abs(abs(co.x) - 0.7) < 1.0e-4 for co in inner), inner
    # Cannot inset past the middle of the face.
    transaction.update(bpy.context, sample(origin + Vector((5, 0, 0)), timestamp=2))
    assert transaction.value <= transaction.radius * 0.95 + 1.0e-9
    transaction.update(bpy.context, sample(origin + Vector((0.3, 0, 0)), timestamp=3))
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert len(obj.data.polygons) == 10 and manifold(obj.data)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["XR_Model"].data.polygons) == 6
    # A click without dragging changes nothing.
    obj = bpy.data.objects["XR_Model"]
    transaction = face_editing.begin(bpy.context, origin, rotation, tool="inset", owner=OWNER)
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert len(obj.data.polygons) == 6
    return 1


def check_uv_island():
    obj = fresh_cube()
    settings = bpy.context.window_manager.xrwb_settings
    settings.uv_island_angle = 30.0
    origin, rotation = top_ray()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # Pure helpers: one cube face at 30 degrees, all six past 90 (flood still stops at seams).
    top = next(f.index for f in bm.faces if f.normal.z > 0.9)
    assert uv_tools.region_faces(bm, top, 30.0) == {top}
    assert len(uv_tools.region_faces(bm, top, 91.0)) == 6
    assert len(uv_tools.boundary_edges(bm, {top})) == 4
    bm.free()

    stroke = physical_tools.begin(bpy.context, origin, rotation, tool="uv_island", owner=OWNER)
    assert isinstance(stroke, uv_tools.UVIslandStroke) and len(stroke.faces) == 1
    assert len(stroke.lines) == 8, "Overlay gets both ends of each of the four border edges"
    # Drag 40 cm sideways: 30 + 80 degrees, clamped at 89 -> still only coplanar faces on a cube.
    result = stroke.update(bpy.context, sample(origin + Vector((0.4, 0, 0))))
    assert result.decision is Decision.APPLY and stroke.angle == 89.0 and len(stroke.faces) == 1
    # Back to the left: narrower again.
    stroke.update(bpy.context, sample(origin - Vector((0.1, 0, 0)), timestamp=2))
    assert abs(stroke.angle - 10.0) < 1.0e-6
    before = [tuple(loop.uv) for loop in obj.data.uv_layers.active.data]
    result = stroke.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY, result.message
    assert editing.active_transaction() is None and bpy.context.mode == "OBJECT"
    mesh = obj.data
    assert sum(edge.use_seam for edge in mesh.edges) == 4
    after = [tuple(loop.uv) for loop in mesh.uv_layers.active.data]
    changed = {poly.index for poly in mesh.polygons
               if any(before[i] != after[i] for i in poly.loop_indices)}
    assert changed == {top}, f"Only the island is re-unwrapped, got {changed}"
    # Seams now bound the island: a wide flood from a side face never enters the top.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    side = next(f.index for f in bm.faces if f.normal.x > 0.9)
    assert top not in uv_tools.region_faces(bm, side, 91.0)
    bm.free()
    assert bpy.ops.ed.undo() == {"FINISHED"}
    mesh = bpy.data.objects["XR_Model"].data
    assert sum(edge.use_seam for edge in mesh.edges) == 0
    assert [tuple(loop.uv) for loop in mesh.uv_layers.active.data] == before

    # Cancel restores nothing because nothing was written.
    obj = bpy.data.objects["XR_Model"]
    stroke = physical_tools.begin(bpy.context, origin, rotation, tool="uv_island", owner=OWNER)
    assert stroke.cancel(bpy.context, OWNER).decision is Decision.CANCEL_REQUIRED
    assert sum(edge.use_seam for edge in obj.data.edges) == 0
    return 1


def check_uv_actions():
    obj = fresh_cube()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    steps = 0
    for action in ("SMART", "PACK", "CHECKER", "CLEAR_SEAMS"):
        assert bpy.ops.xrwb.uv_action(action=action) == {"FINISHED"}, action
        steps += 1
    obj = bpy.data.objects["XR_Model"]
    assert obj.active_material is not None and obj.active_material.get("xrwb_preset") == "UV_CHECKER"
    assert bpy.context.mode == "OBJECT"
    # Each action is its own undo step: undoing once removes only the last.
    assert bpy.ops.ed.undo() == {"FINISHED"}  # CLEAR_SEAMS
    assert bpy.ops.ed.undo() == {"FINISHED"}  # CHECKER
    obj = bpy.data.objects["XR_Model"]
    assert obj.active_material is None or obj.active_material.get("xrwb_preset") != "UV_CHECKER"
    # Nothing selected: a clear refusal, not an exception.
    bpy.ops.object.select_all(action="DESELECT")
    assert bpy.ops.xrwb.uv_action(action="SMART") == {"CANCELLED"}
    return steps


def main():
    assert bpy.app.version[:3] == (5, 1, 2)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        free = check_extrude_free()
        chain = check_chain_and_direction()
        inset = check_inset()
        island = check_uv_island()
        actions = check_uv_actions()
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        xr_workbench.unregister()
    print(f"XR_MODELING_SMOKE_PASS extrude_free={free} chain={chain} inset={inset} uv_island={island} uv_actions={actions} "
          "input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
