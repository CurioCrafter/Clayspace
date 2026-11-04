"""Factory-profile, camera-free face geometry and real Blender undo check."""
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xr_workbench import editing, face_editing  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "face-smoke", 0)


def state(mesh):
    return (tuple(tuple(v.co) for v in mesh.vertices),
            tuple(tuple(f.vertices) for f in mesh.polygons),
            tuple(f.material_index for f in mesh.polygons))


def sample(origin, factor=None, owner=OWNER, timestamp=1.0):
    return InputSample(owner, 0, Pose(tuple(origin)), timestamp, uniform_scale=factor)


def begin(tool="extrude", transform=None):
    obj = bpy.data.objects["XR_Face_Test"]
    if transform is not None:
        obj.matrix_world = transform
    bpy.context.view_layer.update()
    center = obj.matrix_world @ Vector((0, 0, 1))
    normal = (obj.matrix_world.inverted().transposed().to_3x3() @ Vector((0, 0, 1))).normalized()
    origin = center + normal * 3
    rotation = Vector((0, 0, -1)).rotation_difference(-normal)
    transaction = face_editing.begin(bpy.context, origin, rotation, tool=tool, owner=OWNER)
    return obj, transaction, origin, normal


def require_manifold(mesh, counts=(12, 20, 10)):
    assert (len(mesh.vertices), len(mesh.edges), len(mesh.polygons)) == counts
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert all(edge.is_manifold for edge in bm.edges), "Extrude made internal or boundary edges"
        assert all(face.calc_area() > 1.0e-8 for face in bm.faces)
    finally:
        bm.free()


def main():
    assert bpy.app.version[:3] == (5, 1, 2)
    assert not bpy.data.filepath, "Only a factory unsaved test scene is allowed"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = "XR_Face_Test"
    material = bpy.data.materials.new("Face test material")
    obj.data.materials.append(material)
    attr = obj.data.attributes.new("face_test_tag", "INT", "FACE")
    for index, item in enumerate(attr.data):
        item.value = 17 + index
    for index, loop in enumerate(obj.data.uv_layers.active.data):
        loop.uv = (index / 32, index / 64)
    original = obj.data
    original_state = state(original)
    original_uv = tuple(tuple(v.uv) for v in original.uv_layers.active.data)
    original_tags = tuple(v.value for v in original.attributes["face_test_tag"].data)
    original_face_data = {}
    for face in original.polygons:
        corners = tuple(tuple(original.vertices[index].co) for index in face.vertices)
        uv = tuple(tuple(original.uv_layers.active.data[index].uv) for index in face.loop_indices)
        original_face_data[frozenset(corners)] = (original_tags[face.index], dict(zip(corners, uv)))
    mesh_count = len(bpy.data.meshes)

    # Live preview really changes mesh geometry, but cancellation restores the
    # identical original datablock, UVs, material slots and attributes.
    obj, transaction, origin, normal = begin()
    assert len(obj.data.vertices) == 8, "Button down made zero-depth geometry"
    assert transaction.update(bpy.context, sample(origin + normal * .5)).decision is Decision.APPLY
    require_manifold(obj.data)
    assert transaction.cancel(bpy.context, OWNER).decision is Decision.CANCEL_REQUIRED
    assert obj.data == original and state(obj.data) == original_state
    assert tuple(tuple(v.uv) for v in obj.data.uv_layers.active.data) == original_uv
    assert tuple(v.value for v in obj.data.attributes["face_test_tag"].data) == original_tags
    assert len(bpy.data.meshes) == mesh_count and editing.active_transaction() is None

    # Returning to zero or clicking without moving creates no extra geometry.
    for move_first in (False, True):
        obj, transaction, origin, normal = begin()
        if move_first:
            transaction.update(bpy.context, sample(origin + normal * .4))
            transaction.update(bpy.context, sample(origin, timestamp=2))
        assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
        assert obj.data == original and state(obj.data) == original_state

    # Scale only the hit face, around its own world-space center. The opposite
    # face must remain exact, even with rotated, negative, non-uniform scale.
    transform = Matrix.Translation((2, -1, 3)) @ Quaternion((0, 1, 0), .4).to_matrix().to_4x4() @ Matrix.Diagonal((-2, .7, 1.3, 1))
    obj, transaction, origin, normal = begin("face_scale", transform)
    center = transaction.center.copy()
    assert transaction.update(bpy.context, sample(origin, factor=1.8)).decision is Decision.APPLY
    for vertex, coordinate in zip(obj.data.vertices, original_state[0]):
        world_original = transform @ Vector(coordinate)
        expected = center + (world_original-center) * 1.8 if coordinate[2] > 0 else world_original
        assert (transform @ vertex.co - expected).length < 1.0e-5
    transaction.update(bpy.context, sample(origin, factor=1.0e8, timestamp=2))
    assert transaction.value == 20
    transaction.cancel(bpy.context)
    assert state(original) == original_state

    # Both inward and outward drags respect world units under that transform.
    for distance in (-.25, .5):
        obj, transaction, origin, normal = begin(transform=transform)
        transaction.update(bpy.context, sample(origin + normal * distance))
        require_manifold(obj.data)
        top = transaction.center + normal * distance
        assert all(abs((transform @ v.co - top).dot(normal)) < 1.0e-5 for v in transaction.vertices)
        transaction.cancel(bpy.context)

    # Input ownership and generation guards are inherited from CaptureState.
    obj, transaction, origin, normal = begin()
    stranger = CaptureOwner(InputProvenance.SIMULATOR, "other", 0)
    assert transaction.update(bpy.context, sample(origin, owner=stranger)).decision is Decision.REJECTED
    assert transaction.cancel(bpy.context, stranger).decision is Decision.REJECTED
    replaced = CaptureOwner(InputProvenance.SIMULATOR, "face-smoke", 1)
    assert transaction.update(bpy.context, sample(origin, owner=replaced)).decision is Decision.CANCEL_REQUIRED
    assert obj.data == original

    # External same-count mesh edits cancel safely instead of overwriting the
    # original, which remains untouched throughout preview.
    obj, transaction, origin, normal = begin()
    original.vertices[0].co.x += .125
    changed = state(original)
    assert transaction.update(bpy.context, sample(origin)).decision is Decision.CANCEL_REQUIRED
    assert obj.data == original and state(original) == changed
    original.vertices[0].co.x -= .125
    original.update()
    bpy.context.view_layer.update()

    # Shared data, modifiers and disabled undo cannot start an edit.
    for reason in ("shared", "modifier", "undo"):
        other = None
        modifier = None
        if reason == "shared":
            other = bpy.data.objects.new("Shared test", original)
            bpy.context.scene.collection.objects.link(other)
            other.hide_set(True)
        elif reason == "modifier":
            modifier = obj.modifiers.new("Unsupported", "SUBSURF")
        else:
            bpy.context.preferences.edit.use_global_undo = False
        try:
            begin()
            raise AssertionError(f"Accepted unsupported {reason}")
        except editing.EditRejected:
            pass
        finally:
            if other is not None:
                bpy.data.objects.remove(other, do_unlink=True)
            if modifier is not None:
                obj.modifiers.remove(modifier)
            bpy.context.preferences.edit.use_global_undo = True
    obj.matrix_world = Matrix.Identity(4)
    bpy.context.view_layer.update()

    # Real one-step undo/redo, not a fake history operator or snapshot assertion.
    assert bpy.ops.ed.undo_push(message="XR Face Baseline") == {"FINISHED"}
    obj, transaction, origin, normal = begin()
    transaction.update(bpy.context, sample(origin + normal * .5))
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    require_manifold(obj.data)
    final_state = state(obj.data)
    assert obj.data.materials[0] == material
    assert "face_test_tag" in obj.data.attributes and obj.data.uv_layers.active
    retained = 0
    for face in obj.data.polygons:
        corners = tuple(tuple(obj.data.vertices[index].co) for index in face.vertices)
        if all(abs(co[2]-1.5) < 1.0e-5 for co in corners):
            corners = tuple((co[0], co[1], 1.0) for co in corners)
        saved = original_face_data.get(frozenset(corners))
        if saved is not None:
            tag, uv = saved
            assert obj.data.attributes["face_test_tag"].data[face.index].value == tag
            for coordinate, loop_index in zip(corners, face.loop_indices):
                assert tuple(obj.data.uv_layers.active.data[loop_index].uv) == uv[coordinate]
            retained += 1
    assert retained == 6, "Extrusion lost existing UV corners or face attributes"
    assert len(bpy.data.meshes) == mesh_count
    assert bpy.ops.ed.undo() == {"FINISHED"}
    obj = bpy.data.objects["XR_Face_Test"]
    assert state(obj.data) == original_state
    assert tuple(tuple(v.uv) for v in obj.data.uv_layers.active.data) == original_uv
    assert tuple(v.value for v in obj.data.attributes["face_test_tag"].data) == original_tags
    assert bpy.ops.ed.redo() == {"FINISHED"}
    obj = bpy.data.objects["XR_Face_Test"]
    assert state(obj.data) == final_state
    require_manifold(obj.data)
    assert len(bpy.data.meshes) == mesh_count, "Redo resurrected a disposable preview"

    # Face scaling also records exactly one real history step.
    obj, transaction, origin, normal = begin("face_scale")
    transaction.update(bpy.context, sample(origin, factor=.75))
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    scaled_state = state(obj.data)
    assert scaled_state != final_state
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert state(bpy.data.objects["XR_Face_Test"].data) == final_state
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert state(bpy.data.objects["XR_Face_Test"].data) == scaled_state
    assert editing.active_transaction() is None
    print("XR_FACE_EDITING_SMOKE_PASS cube_extrude=12_20_10_manifold scale_face_only=1 negative_nonuniform_transform=1 negative_extrude=1 cancel_exact=1 undo_redo=1 custom_data=1 guards=1 camera=0 hardware=0", flush=True)


if __name__ == "__main__":
    main()
