"""Real factory-profile source edge selection/bevel and Blender undo, no XR."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xr_workbench import edge_editing, editing, face_editing  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "edge-smoke", 0)


def obj():
    return bpy.data.objects["XR_Edge_Test"]


def begin(tool="select_edge", point=(.85, 0, 1), segments=3):
    target = obj()
    center = target.matrix_world @ Vector(point)
    normal = (target.matrix_world.inverted().transposed().to_3x3() @ Vector((0, 0, 1))).normalized()
    origin = center + normal * 3
    rotation = Vector((0, 0, -1)).rotation_difference(-normal)
    return edge_editing.begin(bpy.context, origin, rotation, tool=tool, owner=OWNER, segments=segments), origin


def sample(origin, owner=OWNER, timestamp=1, tracking=True):
    return InputSample(owner, 0, Pose(tuple(origin)), timestamp, tracking_active=tracking)


def manifold(mesh):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert all(edge.is_manifold for edge in bm.edges)
        assert all(face.calc_area() > 1.0e-8 for face in bm.faces)
    finally:
        bm.free()


def commit(transaction):
    result = transaction.commit(bpy.context, OWNER)
    assert result.decision is Decision.APPLY, result


def draw_cache_contract():
    """Exercise successful drawing with RNA/context access made impossible."""
    class NoBlenderReads:
        def __getattr__(self, name):
            raise AssertionError(f"Draw attempted Blender/RNA access: {name}")

    positions = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    changes, draws = [], []
    shader = SimpleNamespace(uniform_float=lambda *_args: None)
    state = SimpleNamespace(
        blend_get=lambda: "NONE", depth_test_get=lambda: "LESS_EQUAL", depth_mask_get=lambda: True,
        viewport_get=lambda: (0, 0, 640, 480),
        blend_set=lambda value: changes.append(("blend", value)),
        depth_test_set=lambda value: changes.append(("depth", value)),
        depth_mask_set=lambda value: changes.append(("mask", value)))

    def batch(_shader, primitive, content):
        assert primitive == "LINES" and content == {"pos": positions}
        return SimpleNamespace(draw=lambda _shader: draws.append(True))

    gpu = SimpleNamespace(state=state, shader=SimpleNamespace(from_builtin=lambda _name: shader))
    before = dict(edge_editing._draw_counts)
    edge_editing._draw_positions = positions
    try:
        with patch.object(edge_editing, "bpy", NoBlenderReads()), \
                patch.object(edge_editing, "selected_segments", side_effect=AssertionError("Draw scanned scene")), \
                patch.dict(sys.modules, {"gpu": gpu, "gpu_extras.batch": SimpleNamespace(batch_for_shader=batch)}):
            for region in ("WINDOW", "XR"):
                edge_editing._draw(region)
                assert edge_editing._draw_counts[region] == before[region] + 1
                assert not edge_editing._last_draw_error
                assert changes[-3:] == [("blend", "NONE"), ("depth", "LESS_EQUAL"), ("mask", True)]
            assert len(draws) == 2
            edge_editing._clear_overlay_cache()
            edge_editing._draw("XR")
            assert len(draws) == 2 and not edge_editing._draw_positions
    finally:
        edge_editing._clear_overlay_cache()
        edge_editing._draw_counts.update(before)


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0) and not bpy.data.filepath
    assert bpy.app.background, "Run in an owned factory background process"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    for item in tuple(bpy.data.objects):
        bpy.data.objects.remove(item, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.object.name = "XR_Edge_Test"
    original = obj().data
    material = bpy.data.materials.new("XR Edge Material")
    original.materials.append(material)
    attribute = original.attributes.new("edge_test_tag", "INT", "FACE")
    for i, value in enumerate(attribute.data):
        value.value = 30 + i
    for i, value in enumerate(original.uv_layers.active.data):
        value.uv = (i / 32, i / 64)
    pristine = face_editing._signature(original)
    mesh_count = len(bpy.data.meshes)
    assert any(edge.select for edge in original.edges), "Fixture must expose native default-selected edges"
    assert not edge_editing.selected_indices(original)
    assert not edge_editing.selected_segments(bpy.context)

    transaction, origin = begin()
    assert obj().data != original and len(edge_editing.selected_indices(obj().data)) == 1
    assert len(edge_editing.selected_segments(bpy.context)) == 2
    assert transaction.cancel(bpy.context, OWNER).decision is Decision.CANCEL_REQUIRED
    assert obj().data == original and face_editing._signature(original) == pristine
    assert len(bpy.data.meshes) == mesh_count

    # One selection click is one native undo and redo, with no inherited all-edge selection.
    assert bpy.ops.ed.undo_push(message="XR Edge Baseline") == {"FINISHED"}
    transaction, _origin = begin()
    commit(transaction)
    assert len(edge_editing.selected_indices(obj().data)) == 1
    first_selection = face_editing._signature(obj().data)
    assert first_selection[:-1] == pristine[:-1]
    assert sorted(item for item in first_selection[-1] if item[0] != edge_editing.SELECTION_ATTRIBUTE) == sorted(pristine[-1])
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert face_editing._signature(obj().data) == pristine
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert face_editing._signature(obj().data) == first_selection
    transaction, _origin = begin(point=(0, .85, 1))
    commit(transaction)
    assert len(edge_editing.selected_indices(obj().data)) == 2
    assert len(edge_editing.selected_segments(bpy.context)) == 4
    transaction, _origin = begin(point=(0, .85, 1))
    commit(transaction)
    assert len(edge_editing.selected_indices(obj().data)) == 1, "Second click must toggle only the hit edge"

    # Freeze source data and capture ownership; cancel restores the exact ID and custom data.
    original = obj().data
    selected_state = face_editing._signature(original)
    transaction, origin = begin("bevel_edge")
    stranger = CaptureOwner(InputProvenance.SIMULATOR, "stranger", 0)
    assert transaction.update(bpy.context, sample(origin, owner=stranger)).decision is Decision.REJECTED
    assert transaction.cancel(bpy.context, stranger).decision is Decision.REJECTED
    assert transaction.update(bpy.context, sample(origin + Vector((.2, 0, 0)))).decision is Decision.APPLY
    assert len(obj().data.vertices) > 8 and len(obj().data.polygons) > 6
    manifold(obj().data)
    assert edge_editing.selected_indices(obj().data), "Bevel preview must expose selected result edges"
    transaction.cancel(bpy.context, OWNER)
    assert obj().data == original and face_editing._signature(original) == selected_state
    assert len(bpy.data.meshes) == mesh_count

    # Tracking loss cannot commit a selection; bevel pauses then cancels on its watchdog.
    transaction, origin = begin()
    assert transaction.update(bpy.context, sample(origin + Vector((.005, 0, 0)))).decision is Decision.APPLY
    assert transaction.update(bpy.context, sample(origin, tracking=False)).decision is Decision.CANCEL_REQUIRED
    assert obj().data == original and face_editing._signature(original) == selected_state
    transaction, origin = begin()
    transaction.update(bpy.context, sample(origin + Vector((.005, 0, 0))))
    replaced = CaptureOwner(InputProvenance.SIMULATOR, "edge-smoke", 1)
    assert transaction.update(bpy.context, sample(origin, owner=replaced)).decision is Decision.CANCEL_REQUIRED
    assert obj().data == original and face_editing._signature(original) == selected_state
    transaction, origin = begin("bevel_edge")
    transaction.update(bpy.context, sample(origin + Vector((.1, 0, 0))))
    assert transaction.update(bpy.context, sample(origin, timestamp=2, tracking=False)).decision is Decision.PAUSED
    assert transaction.commit(bpy.context, OWNER).decision is Decision.REJECTED
    assert transaction.tick(bpy.context, 3).decision is Decision.CANCEL_REQUIRED
    assert obj().data == original and face_editing._signature(original) == selected_state

    # Zero-width return does not mutate source data or leave preview meshes.
    transaction, origin = begin("bevel_edge")
    transaction.update(bpy.context, sample(origin + Vector((.15, 0, 0))))
    transaction.update(bpy.context, sample(origin, timestamp=2))
    commit(transaction)
    assert obj().data == original and face_editing._signature(original) == selected_state

    # Retained source selections bevel only those edges. Existing corner UVs and
    # attributes on untouched faces must survive the native BMesh operation.
    saved_faces = {}
    for face in original.polygons:
        corners = tuple(tuple(original.vertices[i].co) for i in face.vertices)
        saved_faces[frozenset(corners)] = (
            original.attributes["edge_test_tag"].data[face.index].value,
            {co: tuple(original.uv_layers.active.data[loop].uv) for co, loop in zip(corners, face.loop_indices)})
    assert bpy.ops.ed.undo_push(message="XR Edge Bevel Baseline") == {"FINISHED"}
    transaction, origin = begin("bevel_edge", segments=3)
    assert len(transaction.edge_indices) == 1
    transaction.update(bpy.context, sample(origin + Vector((.2, 0, 0))))
    commit(transaction)
    assert obj().data == original
    manifold(original)
    assert len(original.vertices) > 8 and len(original.polygons) > 6
    assert original.materials[0] == material and original.uv_layers.active
    retained = 0
    for face in original.polygons:
        corners = tuple(tuple(original.vertices[i].co) for i in face.vertices)
        saved = saved_faces.get(frozenset(corners))
        if saved:
            tag, uv = saved
            assert original.attributes["edge_test_tag"].data[face.index].value == tag
            for co, loop in zip(corners, face.loop_indices):
                assert tuple(original.uv_layers.active.data[loop].uv) == uv[co]
            retained += 1
    assert retained >= 2, "Bevel unexpectedly modified every original face"
    final_state = face_editing._signature(original)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert face_editing._signature(obj().data) == selected_state
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert face_editing._signature(obj().data) == final_state
    assert len(bpy.data.meshes) == mesh_count
    assert bpy.ops.ed.undo() == {"FINISHED"}

    # A rotated, negative, non-uniform object still uses a world-space bevel.
    obj().matrix_world = (Matrix.Translation((2, -1, 3))
                          @ Quaternion((0, 1, 0), .4).to_matrix().to_4x4()
                          @ Matrix.Diagonal((-2, .7, 1.3, 1)))
    before = face_editing._signature(obj().data)
    transaction, origin = begin("bevel_edge", segments=1)
    assert transaction.update(bpy.context, sample(origin + Vector((.15, 0, 0)))).decision is Decision.APPLY
    manifold(obj().data)
    assert abs(transaction.value - .15) < 1.0e-5
    transaction.cancel(bpy.context)
    assert face_editing._signature(obj().data) == before

    # Same-count out-of-band data mutations are authoritative and must survive.
    transaction, origin = begin("bevel_edge")
    transaction.preview.vertices[0].co.x += .05
    externally_edited = obj().data
    assert transaction.update(bpy.context, sample(origin)).decision is Decision.CANCEL_REQUIRED
    assert obj().data == externally_edited
    assert editing.active_transaction() is None
    # Unsupported source stacks and disabled undo fail before changing a data ID.
    for reason in ("modifier", "undo", "segments"):
        modifier = obj().modifiers.new("Guard", "BEVEL") if reason == "modifier" else None
        if reason == "undo":
            bpy.context.preferences.edit.use_global_undo = False
        data = obj().data
        try:
            begin("bevel_edge", segments=0 if reason == "segments" else 3)
            raise AssertionError(f"Accepted unsupported {reason}")
        except editing.EditRejected:
            assert obj().data == data and editing.active_transaction() is None
        finally:
            if modifier:
                obj().modifiers.remove(modifier)
            bpy.context.preferences.edit.use_global_undo = True
    edge_editing.start_overlay()  # Background calls deliberately create no GPU handlers.
    edge_editing.stop_overlay()
    draw_cache_contract()
    print("XR_EDGE_EDITING_SMOKE_PASS nearest_visible_edge=1 accumulated_toggle=1 native_defaults_ignored=1 "
          "native_bevel=1 manifold=1 segments=1_3 cancel_exact=1 undo_redo=1 retained_uv_attributes=1 "
          "negative_nonuniform_transform=1 owner_tracking_guards=1 external_edit_preserved=1 "
          "overlay_endpoints=1 draw_without_context=1 camera=0 hardware=0 overlay_render_unverified=1", flush=True)


if __name__ == "__main__":
    main()
