"""Factory-profile native draw/cutter data, volume and actual undo/redo proof."""
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xr_workbench import draw_editing, editing, ui  # noqa: E402
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "draw-smoke", 0)


def pose_for_tip(tip, rotation=(1, 0, 0, 0), scale=1):
    return Pose(tuple(Vector(tip) - Quaternion(rotation) @ Vector((0, 0, -.2 * scale))), tuple(rotation))


def begin(tool, start=(-1, -1, -1), rotation=(1, 0, 0, 0)):
    pose = pose_for_tip(start, rotation)
    return draw_editing.begin(bpy.context, pose.position, pose.rotation, tool, OWNER)


def update(transaction, tip, timestamp=1, rotation=(1, 0, 0, 0), owner=OWNER, tracking=True, neutral=False):
    sample = InputSample(owner, 0, pose_for_tip(tip, rotation), timestamp,
                         tracking_active=tracking, neutral=neutral)
    return transaction.update(bpy.context, sample)


def mesh_signature(mesh):
    return (tuple(tuple(vertex.co) for vertex in mesh.vertices),
            tuple(tuple(face.vertices) for face in mesh.polygons))


def volume(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert all(edge.is_manifold for edge in bm.edges), "Preview produced a non-manifold volume"
        return abs(bm.calc_volume(signed=True) * obj.matrix_world.determinant())
    finally:
        bm.free()
        evaluated.to_mesh_clear()


def clear():
    assert editing.active_transaction() is None
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in tuple(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.context.view_layer.update()


def target():
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.object
    obj.name = "Native Cutter Target"
    # A pre-existing disabled modifier must retain its complete settings/order.
    modifier = obj.modifiers.new("Existing Bevel", "BEVEL")
    modifier.width = .137
    modifier.segments = 3
    modifier.show_viewport = False
    modifier.show_render = False
    return obj


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    assert bpy.app.background and not bpy.data.filepath, "Only an owned factory headless scene is allowed"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    bpy.utils.register_class(ui.XRWB_PG_settings)
    bpy.utils.register_class(ui.XRWB_OT_bevel_object)
    bpy.types.WindowManager.xrwb_settings = bpy.props.PointerProperty(type=ui.XRWB_PG_settings)
    clear()
    for tool, expected in (("draw_cube", 8), ("draw_sphere", 4.18879), ("draw_cylinder", 6.28318)):
        assert bpy.ops.ed.undo_push(message="Empty draw baseline") == {"FINISHED"}
        transaction = begin(tool)
        assert not bpy.context.scene.objects, "Press created a degenerate primitive"
        assert update(transaction, (1, 1, 1)).decision is Decision.APPLY
        assert transaction.obj.display_type == "TEXTURED" and not transaction.obj.show_in_front
        assert abs(volume(transaction.obj)-expected) < .12
        assert transaction.cancel(bpy.context, OWNER).decision is Decision.CANCEL_REQUIRED
        assert not bpy.context.scene.objects and not bpy.data.meshes
        transaction = begin(tool)
        assert update(transaction, (1, 1, 1)).decision is Decision.APPLY
        name = transaction.obj.name
        before_world = tuple(transaction.obj.matrix_world @ vertex.co for vertex in transaction.mesh.vertices)
        assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
        obj = bpy.data.objects[name]
        signature = mesh_signature(obj.data)
        bpy.context.view_layer.update()
        assert tuple(obj.scale) == (1, 1, 1)
        assert all((obj.matrix_world @ vertex.co-before).length < 1.0e-6
                   for vertex, before in zip(obj.data.vertices, before_world))
        assert obj.display_type == "TEXTURED" and obj.select_get() and bpy.context.object == obj
        assert abs(volume(obj)-expected) < .12
        assert bpy.ops.ed.undo() == {"FINISHED"}
        assert not bpy.context.scene.objects, f"{tool} failed single-step undo"
        assert bpy.ops.ed.redo() == {"FINISHED"}
        assert mesh_signature(bpy.data.objects[name].data) == signature
        assert abs(volume(bpy.data.objects[name])-expected) < .12
        clear()

    # A tilted, rolled controller still draws an upright box: only its heading
    # turns the box, so up is always height. Backwards nonuniform drags keep
    # world dimensions on commit with unit scale, ready for our Bevel UI.
    rotation = Quaternion(Vector((.3, .7, .2)).normalized(), .7)
    frame = draw_editing.draw_frame(rotation)
    assert abs((frame @ Vector((0, 0, 1))).z - 1) < 1e-6, "Draw frame must stay upright"
    start = Vector((1, 2, 3))
    end = start + frame @ Vector((-2, 3, .75))
    transaction = begin("draw_cube", start, rotation)
    assert update(transaction, end, rotation=rotation).decision is Decision.APPLY
    before_world = tuple(transaction.obj.matrix_world @ v.co for v in transaction.mesh.vertices)
    assert abs(volume(transaction.obj)-4.5) < 1.0e-5
    zs = [(transaction.obj.matrix_world @ v.co).z for v in transaction.mesh.vertices]
    assert abs(max(zs) - min(zs) - .75) < 1e-5, "Height must follow straight up/down"
    assert transaction.commit(bpy.context).decision is Decision.APPLY
    obj = bpy.context.object
    assert tuple(obj.scale) == (1, 1, 1)
    assert all((obj.matrix_world @ vertex.co-before).length < 1.0e-5
               for vertex, before in zip(obj.data.vertices, before_world))
    name = obj.name
    assert bpy.ops.xrwb.bevel_object() == {"FINISHED"}
    assert obj.modifiers[-1].type == "BEVEL" and 0 < volume(obj) < 4.5
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert not bpy.data.objects[name].modifiers and abs(volume(bpy.data.objects[name])-4.5) < 1.0e-5
    clear()
    for world_scale in (.001, 1, 40):
        requested = Vector((1, 2, 3))
        tip, _rotation = draw_editing._pose_tip(pose_for_tip(requested, rotation, world_scale), world_scale)
        assert (tip-requested).length < 1.0e-5

    # Click, tiny movement and returning to the anchor leave neither objects nor
    # a history entry; cancelling after tracking/provider loss is equally clean.
    assert bpy.ops.ed.undo_push(message="Noop empty baseline") == {"FINISHED"}
    sentinel = target()
    sentinel_name = sentinel.name
    assert bpy.ops.ed.undo_push(message="Sentinel baseline") == {"FINISHED"}
    for motion in (False, True):
        transaction = begin("draw_cube")
        if motion:
            update(transaction, (1, 1, 1))
            update(transaction, (-.999, -1, -1), timestamp=2)
        assert transaction.obj is None
        committed = transaction.commit(bpy.context)
        assert committed.decision is Decision.APPLY, (tool, committed)
        assert len(bpy.context.scene.objects) == 1 and bpy.context.object.name == sentinel_name
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert not bpy.context.scene.objects, "No-motion gesture created a history entry"
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert len(bpy.context.scene.objects) == 1 and bpy.context.object.name == sentinel_name
    transaction = begin("draw_cube")
    update(transaction, (1, 1, 1))
    stranger = CaptureOwner(InputProvenance.SIMULATOR, "other", 0)
    assert update(transaction, (2, 2, 2), owner=stranger).decision is Decision.REJECTED
    assert transaction.cancel(bpy.context, stranger).decision is Decision.REJECTED
    replaced = CaptureOwner(InputProvenance.SIMULATOR, "draw-smoke", 1)
    assert update(transaction, (2, 2, 2), owner=replaced).decision is Decision.CANCEL_REQUIRED
    assert len(bpy.context.scene.objects) == 1
    transaction = begin("draw_cube")
    update(transaction, (1, 1, 1))
    assert update(transaction, (1, 1, 1), timestamp=2, tracking=False).decision is Decision.PAUSED
    assert transaction.commit(bpy.context).decision is Decision.REJECTED
    assert transaction.tick(bpy.context, 2.3).decision is Decision.CANCEL_REQUIRED
    assert len(bpy.context.scene.objects) == 1 and bpy.context.object.name == sentinel_name
    clear()

    # Non-destructive exact Boolean volume with the source mesh and prior stack
    # untouched. The cutter survives commit as a separately editable wire object.
    for tool, expected in (("cut_difference", 4), ("cut_union", 12), ("cut_intersect", 4)):
        obj = target()
        target_name, source_name = obj.name, obj.data.name
        source = mesh_signature(obj.data)
        original_modifier = draw_editing._modifier_signature(obj)
        assert bpy.ops.ed.undo_push(message="Cutter source baseline") == {"FINISHED"}
        transaction = begin(tool, start=(0, -1, -1))
        assert update(transaction, (2, 1, 1)).decision is Decision.APPLY
        assert abs(volume(obj)-expected) < 1.0e-4, (tool, volume(obj))
        assert mesh_signature(obj.data) == source and obj.data.name == source_name
        assert transaction.cancel(bpy.context).decision is Decision.CANCEL_REQUIRED
        assert len(bpy.context.scene.objects) == 1 and abs(volume(obj)-8) < 1.0e-4
        assert draw_editing._modifier_signature(obj) == original_modifier
        transaction = begin(tool, start=(0, -1, -1))
        assert update(transaction, (2, 1, 1)).decision is Decision.APPLY
        cutter_name = transaction.obj.name
        assert transaction.commit(bpy.context).decision is Decision.APPLY
        assert mesh_signature(obj.data) == source and obj.data.name == source_name
        assert bpy.context.object == obj and obj.select_get()
        assert bpy.data.objects[cutter_name].display_type == "WIRE"
        assert tuple(bpy.data.objects[cutter_name].scale) == (1, 1, 1)
        assert bpy.data.objects[cutter_name].hide_render
        assert obj.modifiers[-1].solver == "EXACT" and obj.modifiers[-1].object.name == cutter_name
        assert abs(volume(obj)-expected) < 1.0e-4
        assert bpy.ops.ed.undo() == {"FINISHED"}
        obj = bpy.data.objects[target_name]
        assert len(bpy.context.scene.objects) == 1 and len(obj.modifiers) == 1
        assert mesh_signature(obj.data) == source and abs(obj.modifiers[0].width-.137) < 1.0e-6
        assert obj.modifiers[0].segments == 3 and not obj.modifiers[0].show_viewport
        assert abs(volume(obj)-8) < 1.0e-4
        assert bpy.ops.ed.redo() == {"FINISHED"}
        obj = bpy.data.objects[target_name]
        assert len(bpy.context.scene.objects) == 2 and abs(volume(obj)-expected) < 1.0e-4
        # Retained cutters are genuinely editable, not baked duplicates.
        bpy.data.objects[cutter_name].location.x += 4
        assert abs(volume(obj)-({"cut_difference": 8, "cut_union": 16, "cut_intersect": 0}[tool])) < 1.0e-4
        clear()

    for shape in ("SPHERE", "CYLINDER"):
        obj = target()
        bpy.context.window_manager.xrwb_settings.cutter_shape = shape
        transaction = begin("cut_difference", start=(-.5, -.5, -.5))
        # Changing the dropdown midway cannot change the captured cutter type.
        bpy.context.window_manager.xrwb_settings.cutter_shape = "CUBE"
        assert update(transaction, (.5, .5, .5)).decision is Decision.APPLY
        assert transaction.shape == shape and len(transaction.mesh.vertices) > 8
        cut_volume = volume(transaction.obj)
        assert abs(volume(obj)-(8-cut_volume)) < 1.0e-4
        transaction.cancel(bpy.context)
        assert abs(volume(obj)-8) < 1.0e-4 and len(obj.modifiers) == 1
        clear()

    # Target mutations are authoritative; cancel removes only the owned cutter
    # and Boolean modifier, not a concurrent edit to original geometry/settings.
    for change in ("geometry", "modifier", "selection", "undo"):
        obj = target()
        transaction = begin("cut_difference", start=(0, -1, -1))
        update(transaction, (2, 1, 1))
        if change == "geometry":
            obj.data.vertices[0].co.x -= .125
        elif change == "modifier":
            obj.modifiers[0].width = .25
        elif change == "selection":
            obj.select_set(False)
        else:
            bpy.context.preferences.edit.use_global_undo = False
        expected_mesh = mesh_signature(obj.data)
        assert update(transaction, (3, 1, 1), timestamp=2).decision is Decision.CANCEL_REQUIRED
        assert len(bpy.context.scene.objects) == 1 and len(obj.modifiers) == 1
        assert mesh_signature(obj.data) == expected_mesh
        if change == "modifier":
            assert abs(obj.modifiers[0].width-.25) < 1.0e-6
        if change == "selection":
            assert not obj.select_get()
        bpy.context.preferences.edit.use_global_undo = True
        clear()

    # An external edit/link/parent/data replacement to a preview takes ownership
    # of it. End capture without erasing that author's data or extra collection.
    for change in ("vertex", "collection", "parent", "replacement", "own_modifier", "material", "uv", "attribute"):
        obj = target()
        transaction = begin("cut_difference", start=(0, -1, -1))
        update(transaction, (2, 1, 1))
        preview = transaction.obj
        extra_collection = None
        if change == "vertex":
            preview.data.vertices[0].co.x += .2
        elif change == "collection":
            extra_collection = bpy.data.collections.new("External preview link")
            bpy.context.scene.collection.children.link(extra_collection)
            extra_collection.objects.link(preview)
        elif change == "parent":
            preview.parent = obj
        elif change == "replacement":
            preview.data = preview.data.copy()
            preview.data.vertices[0].co.x += .3
        elif change == "own_modifier":
            transaction.modifier.show_render = False
        elif change == "material":
            material = bpy.data.materials.new("External preview material")
            preview.data.materials.append(material)
            preview.material_slots[0].link = "OBJECT"
            preview.material_slots[0].material = material
        elif change in {"uv", "attribute"}:
            # Seed an owned fixture attribute so only its VALUES change after
            # the baseline, proving that metadata-only signatures are inadequate.
            if change == "uv":
                values = preview.data.uv_layers.new(name="FixtureUV").data
            else:
                values = preview.data.attributes.new("fixture_weight", "FLOAT", "POINT").data
            transaction.preview_geometry = draw_editing.face_editing._signature(preview.data)
            assert transaction._preview_untouched()
            if change == "uv":
                values[0].uv = (.25, .75)
            else:
                values[0].value = .625
        retained = draw_editing.face_editing._signature(preview.data)
        retained_slots = tuple((slot.link, slot.material) for slot in preview.material_slots)
        assert update(transaction, (3, 1, 1), timestamp=2).decision is Decision.CANCEL_REQUIRED
        assert preview.name in bpy.context.scene.objects and draw_editing.face_editing._signature(preview.data) == retained
        assert tuple((slot.link, slot.material) for slot in preview.material_slots) == retained_slots
        assert len(obj.modifiers) == (2 if change == "own_modifier" else 1)
        if extra_collection is not None:
            assert preview.name in extra_collection.objects
            bpy.data.collections.remove(extra_collection)
        clear()
    assert editing.active_transaction() is None
    print("XR_DRAW_EDITING_SMOKE_PASS primitives=3 native_shapes=1 booleans=3 cutter_shapes=3 exact_volume=1 retained_editable=1 unit_scale=1 bevel_drawn_box=1 cancel=1 undo_redo=1 target_guards=1 external_preview_preserved=1 material_uv_attribute_preserved=1 camera=0 hardware=0 third_party=0", flush=True)


if __name__ == "__main__":
    main()
