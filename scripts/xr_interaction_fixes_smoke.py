"""Factory-profile check of the 0.16 interaction fixes.

* Menus: an open menu owns the stick click (the tool wheel never steals it);
  hover follows the hand that points at the menu; exact menu hit maths; rows.
* Both grips: the second grip joins a held object; pulling apart resizes
  exactly; small wobbles neither scale nor turn; a grip on nothing moves the world.
* Face previews: hover fill/outline/arrow and the live extrusion outline.
* Vertex / edge / face: modes, ray picking, click and sweep selection with
  cancel and undo, Blender-style extrude by hand, grip moves components.

Real Blender and the real add-on; no XR session or headset. Run in a separate
background process with BLENDER_USER_CONFIG set to an empty temporary folder.
"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (  # noqa: E402
    component_mode, controllers, editing, face_editing, menu_pointer, onboarding, ray_feedback, tool_wheel,
)
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "fixes-smoke", 0)
RIGHT, LEFT = controllers.RIGHT_HAND, controllers.LEFT_HAND


def xr_event(action, hand, location, rotation=(1.0, 0.0, 0.0, 0.0), value="PRESS"):
    return NS(type="XR_ACTION", value=value, xr=NS(action_set=controllers.ACTION_SET_NAME, action=action,
                                                   user_path=hand, controller_location=location,
                                                   controller_rotation=rotation))


def close(a, b, tolerance=1e-5):
    return all(abs(x - y) < tolerance for ra, rb in zip(a, b) for x, y in zip(ra, rb))


def fresh():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.object
    cube.name = "Fix Cube"
    bpy.ops.ed.undo_push(message="Fixes baseline")
    return cube


def check_menus():
    # Exact hit maths: a 0.4 x 0.3 menu at the origin facing +Z.
    pose = (Vector((0, 0, 0)), Quaternion(), 0.4, 0.3)
    u, v, distance, point = menu_pointer.hit(pose, (0.1, 0.05, 1.0), (0, 0, -1))
    assert abs(u - 0.75) < 1e-6 and abs(v - (0.05 / 0.3 + 0.5)) < 1e-6 and abs(distance - 1.0) < 1e-6
    assert (point - Vector((0.1, 0.05, 0.0))).length < 1e-6
    assert menu_pointer.hit(pose, (0.1, 0.05, -1.0), (0, 0, 1)) is None, "From behind never hits"
    assert menu_pointer.hit(pose, (0.5, 0.0, 1.0), (0, 0, -1)) is None, "Beside the menu never hits"
    assert menu_pointer.hit(pose, (0.0, 0.0, 1.0), (0, 0, 1)) is None, "Pointing away never hits"
    rays = {RIGHT: ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), LEFT: ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))}
    assert menu_pointer.choose_hand(pose, rays, RIGHT) == LEFT, "The hand pointing at the menu hovers"
    rays[RIGHT] = ((0.05, 0.0, 1.0), (0.0, 0.0, -1.0))
    assert menu_pointer.choose_hand(pose, rays, RIGHT) == RIGHT, "The preferred hand wins when both point"
    assert menu_pointer.row_of(1.0, 600, 1.5) == 0 and menu_pointer.row_of(0.0, 600, 1.5) == 20

    # Hover follows the pointing hand, not the last trigger hand.
    real_active, real_pose = controllers.hand_menu_active, menu_pointer.menu_pose
    controllers.hand_menu_active = lambda _context: True
    menu_pointer.menu_pose = lambda _context: pose
    controllers._pointer_hand = RIGHT
    try:
        rays = {RIGHT: ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), LEFT: ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))}
        assert controllers._choose_hover_ray(bpy.context, rays) == rays[LEFT]
        assert controllers._pointer_hand == LEFT

        # An open menu owns the stick click, even with the tool wheel on that button.
        assert controllers.slot_action("RIGHT_STICK") == "TOOL_WHEEL"
        pressed = []
        real_press = controllers.press_highlighted
        controllers.press_highlighted = lambda _context: pressed.append(1) or True
        try:
            operator = NS(slot="RIGHT_STICK")
            result = controllers.XRWB_OT_controller_button.invoke(operator, bpy.context,
                                                                  xr_event("context_pointer", RIGHT, (0, 0, 0)))
            assert result == {"FINISHED"} and pressed == [1] and not tool_wheel.is_open()
        finally:
            controllers.press_highlighted = real_press
    finally:
        controllers.hand_menu_active, menu_pointer.menu_pose = real_active, real_pose
        controllers._pointer_hand = RIGHT
    return 1


def check_two_grips():
    cube = fresh()
    start = cube.matrix_world.copy()
    # Right grip picks the cube up; the LEFT GRIP (world_grab action) joins.
    grip = xr_event("grab_move", RIGHT, (0.0, 0.0, 5.0))
    assert controllers._begin_move(bpy.context, grip, grip=True)[0]
    left = xr_event("world_grab", LEFT, (1.0, 1.0, 5.0))
    ok, message = controllers._begin_world_grip(bpy.context, left)
    assert ok and "Both hands" in message, message
    gesture = controllers._active_gesture
    assert gesture.second_key == ("world_grab", LEFT) and gesture.transaction.stretch_axis is None
    # A 1 % wobble with a 2 degree tilt changes nothing.
    left.xr.controller_location = (1.01, 1.0, 5.02)
    assert controllers.dispatch_xr_event(bpy.context, left) == {"RUNNING_MODAL"}
    assert close(cube.matrix_world.to_3x3(), start.to_3x3()), "Small wobbles neither scale nor turn"
    # Pulling apart to twice the span doubles the size exactly about the midpoint.
    left.xr.controller_location = (2.0, 2.0, 5.0)
    assert controllers.dispatch_xr_event(bpy.context, left) == {"RUNNING_MODAL"}
    stretched = (Matrix.Translation((1.0, 1.0, 5.0)) @ Matrix.Diagonal((2, 2, 2, 1))
                 @ Matrix.Translation((-0.5, -0.5, -5.0)) @ start)
    assert close(cube.matrix_world, stretched), cube.matrix_world
    left.value = "RELEASE"
    assert controllers.dispatch_xr_event(bpy.context, left) == {"FINISHED"}
    grip.value = "RELEASE"
    assert controllers.dispatch_xr_event(bpy.context, grip) == {"FINISHED"}
    assert abs(bpy.data.objects["Fix Cube"].scale.x - 2.0) < 1e-5
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert abs(bpy.data.objects["Fix Cube"].scale.x - 1.0) < 1e-5, "Two-grip resize is one undo step"

    # Scale Only never turns, however the hands swing.
    onboarding.set_pref("two_hand", "SCALE")
    try:
        gesture = NS(two_hand_start=((0, 0, 0), (1.0, 0.0, 0.0), (1, 0, 0, 0)),
                     primary_position=(0, 0, 0), second_position=(0.0, 2.0, 0.0))
        _middle, rotation, scale = controllers._two_hand_sample_pose(gesture)
        assert Quaternion(rotation).angle < 1e-9 and abs(scale - 2.0) < 1e-9
    finally:
        onboarding.set_pref("two_hand", "SCALE_ROTATE")
    gesture.second_position = (0.0, 2.0, 0.0)
    _middle, rotation, _scale = controllers._two_hand_sample_pose(gesture)
    assert abs(Quaternion(rotation).angle - 1.5707963) < 1e-5, "Beyond the dead zone the turn is exact"
    assert controllers._soft_deadzone(0.5, 0.1) == 0.5 and controllers._soft_deadzone(0.05, 0.1) == 0.0

    # Grip on nothing: the world point under the hand stays under the hand.
    state = NS(navigation_location=Vector((0, 0, 0)), navigation_rotation=(1, 0, 0, 0), navigation_scale=1.0)
    context = NS(window_manager=NS(xr_session_state=state))
    from xr_workbench import navigation
    world = NS(kind="world", user_path=LEFT, primary_position=(0.0, 0.0, 1.0), second_key=None,
               second_position=None, last_input_at=0.0, navigation=None, readout="", detent=None,
               world=navigation.WorldGrab(state, LEFT, (0.0, 0.0, 1.0)))
    event = xr_event("world_grab", LEFT, (0.3, 0.0, 1.0))
    controllers._active_gesture = world
    try:
        assert controllers._update_world_grip(context, event, world) == {"RUNNING_MODAL"}
        assert (Vector(state.navigation_location) - Vector((-0.3, 0.0, 0.0))).length < 1e-9
        assert world.navigation == controllers._navigation_signature(context), "Our own move never cancels it"
        event.value = "RELEASE"
        assert controllers._update_world_grip(context, event, world) == {"FINISHED"}
        assert controllers._active_gesture is None
    finally:
        controllers._active_gesture = None
    # The binding: left grip runs our operator (not Blender's navigation grab).
    actionmap = controllers.build_actionmap(NS(actionmaps=_FakeMaps()), "INDEX")
    item = next(i for i in actionmap.actionmap_items if i.name == "world_grab")
    assert item.op == "xrwb.controller_world_grip"
    return 1


class _Collection(list):
    def new(self, *args):
        item = _ActionItem(args[0]) if len(args) == 2 and isinstance(args[1], bool) else _Binding(args[0])
        self.append(item)
        return item


class _Paths(list):
    def new(self, path):
        self.append(path)


class _Binding:
    def __init__(self, name):
        self.name = name
        self.component_paths = _Paths()


class _Bindings(_Collection):
    def new(self, name, _replace):
        binding = _Binding(name)
        self.append(binding)
        return binding


class _ActionItem:
    def __init__(self, name):
        self.name = name
        self.user_paths = _Paths()
        self.bindings = _Bindings()
        self.op_properties = NS()


class _FakeMaps(_Collection):
    def new(self, _state, name, _replace):
        actionmap = NS(name=name, actionmap_items=_Collection())
        self.append(actionmap)
        return actionmap


def check_face_previews():
    cube = fresh()
    ray_feedback._fills.clear()
    top = next(p.index for p in cube.data.polygons if p.normal.z > 0.9)
    shapes = ray_feedback._hover_face(cube, top, "extrude", (0.2, 0.9, 1.0), 1.0)
    assert shapes is not None and len(ray_feedback._fills) == 1 and len(ray_feedback._fills[0][0]) == 6
    assert len(shapes[0]) == 8 + 6, "Outline of four edges plus a three-stroke normal arrow"
    assert ray_feedback._hover_face(cube, top, "select", (1, 1, 1), 1.0) is None, "Only face tools highlight faces"
    origin = Vector((0.2, 0.1, 3.0))
    transaction = face_editing.begin(bpy.context, origin, Quaternion(), tool="extrude", owner=OWNER)
    transaction.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin + Vector((0, 0, 0.5))), (1, 0, 0, 0)), 1.0))
    caps, start, sides = transaction.preview_outline()
    assert len(caps) == 1 and len(caps[0]) == 4 and all(abs(p[2] - 1.5) < 1e-5 for p in caps[0])
    assert len(start) == 4 and all(abs(p[2] - 1.0) < 1e-5 for p in start)
    assert len(sides) == 4 and all(abs(a[2] - 1.0) < 1e-5 and abs(b[2] - 1.5) < 1e-5 for a, b in sides)
    transaction.toggle_direction(origin + Vector((0, 0, 0.5)))
    assert len(transaction.preview_outline()[2]) == 4, "Side edges survive a direction switch"
    shapes = ray_feedback._face_preview(transaction, (0.2, 0.9, 1.0), 1.0)
    assert len(shapes) == 3
    transaction.cancel(bpy.context)
    transaction = face_editing.begin(bpy.context, origin, Quaternion(), tool="inset", owner=OWNER)
    transaction.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin + Vector((0.3, 0, 0))), (1, 0, 0, 0)), 1.0))
    caps, _start, sides = transaction.preview_outline()
    assert len(caps) == 1 and all(abs(abs(p[0]) - 0.7) < 1e-4 for p in caps[0]) and not sides
    transaction.cancel(bpy.context)
    return 1


def aim(position, direction=(0.0, 0.0, -1.0)):
    rotation = Vector((0.0, 0.0, -1.0)).rotation_difference(Vector(direction).normalized())
    return Pose(tuple(position), tuple(rotation))


def selected(bm, kind):
    return sorted(item.index for item in getattr(bm, kind) if item.select)


def check_components():
    cube = fresh()
    settings = bpy.context.window_manager.xrwb_settings
    assert bpy.ops.xrwb.component_mode(mode="FACE") == {"FINISHED"}
    assert bpy.context.mode == "EDIT_MESH" and component_mode.current_mode(bpy.context) == "FACE"
    assert tool_wheel.items_for(settings) is tool_wheel.WHEELS["EDIT"], "Edit Mode gets the V / E / F wheel"
    real_running = xr_workbench.runtime.session_running
    xr_workbench.runtime.session_running = lambda _context: True
    try:
        assert bpy.ops.xrwb.set_edit_tool(tool="edit_extrude") == {"FINISHED"}, "Extrude is choosable in Edit Mode"
        assert xr_workbench.ui._scene_tool_reason(bpy.context, "extrude").startswith("In Edit Mode")
    finally:
        xr_workbench.runtime.session_running = real_running
    settings.edit_tool = "select"
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.ed.undo_push(message="Edit baseline")
    obj, bm = component_mode.edit_bmesh(bpy.context)

    # Picking: face, nearest edge, nearest vertex on the top face.
    element, _location = component_mode.pick(obj, bm, (0.9, 0.1, 3.0), (0, 0, -1), "FACE")
    assert isinstance(element, bmesh.types.BMFace) and element.normal.z > 0.9
    element, _location = component_mode.pick(obj, bm, (0.9, 0.1, 3.0), (0, 0, -1), "EDGE")
    assert all(abs(v.co.x - 1.0) < 1e-6 and abs(v.co.z - 1.0) < 1e-6 for v in element.verts)
    element, _location = component_mode.pick(obj, bm, (0.9, 0.8, 3.0), (0, 0, -1), "VERT")
    assert tuple(element.co) == (1.0, 1.0, 1.0)
    assert component_mode.pick(obj, bm, (5.0, 5.0, 3.0), (0, 0, -1), "FACE") == (None, None)

    # Click selects the top face; sweeping down the +X side adds that face.
    stroke = component_mode.begin(bpy.context, "select", aim((0.2, 0.1, 3.0)), OWNER)
    assert stroke.selected_count() == 1
    side = aim((3.0, 0.1, 0.2), (-1, 0, 0))
    assert stroke.update(bpy.context, InputSample(OWNER, 0, side, 1.0)).decision is Decision.APPLY
    assert stroke.selected_count() == 2
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(selected(bm, "faces")) == 2
    # Cancel restores the exact selection.
    stroke = component_mode.begin(bpy.context, "select", aim((0.2, 0.1, -3.0), (0, 0, 1)), OWNER)
    assert stroke.cancel(bpy.context, OWNER).decision is Decision.CANCEL_REQUIRED
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(selected(bm, "faces")) == 2
    # Undo: back to nothing selected.
    assert bpy.ops.ed.undo() == {"FINISHED"}
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert selected(bm, "faces") == [], "A selection sweep is one undo step"
    # Multi-Select toggles: clicking a selected face removes it.
    settings.multi_select = True
    for _ in range(2):
        stroke = component_mode.begin(bpy.context, "select", aim((0.2, 0.1, 3.0)), OWNER)
        stroke.commit(bpy.context, OWNER)
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert selected(bm, "faces") == [], "Second multi-select click deselected it"
    settings.multi_select = False
    # Empty space clears.
    stroke = component_mode.begin(bpy.context, "select", aim((0.2, 0.1, 3.0)), OWNER)
    stroke.commit(bpy.context, OWNER)
    stroke = component_mode.begin(bpy.context, "select", aim((6.0, 6.0, 3.0)), OWNER)
    stroke.commit(bpy.context, OWNER)
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert selected(bm, "faces") == []

    # Extrude the top face by hand: 0.5 up, one undo; no pull = no geometry.
    stroke = component_mode.begin(bpy.context, "select", aim((0.2, 0.1, 3.0)), OWNER)
    stroke.commit(bpy.context, OWNER)
    bpy.ops.ed.undo_push(message="Before extrude")
    start = aim((0.0, -3.0, 1.0), (0, 1, 0))
    stroke = component_mode.begin(bpy.context, "edit_extrude", start, OWNER)
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(bm.verts) == 8, "Releasing without pulling adds nothing"
    stroke = component_mode.begin(bpy.context, "edit_extrude", start, OWNER)
    pulled = Pose((0.3, -3.0, 1.5), start.rotation)
    stroke.update(bpy.context, InputSample(OWNER, 0, pulled, 1.0))
    obj, bm = component_mode.edit_bmesh(bpy.context)
    moved = [v for v in bm.verts if v.select]
    assert len(bm.verts) == 12 and all(abs(v.co.z - 1.5) < 1e-5 for v in moved), "Pulled along the face normal"
    assert stroke.commit(bpy.context, OWNER).decision is Decision.APPLY
    assert bpy.ops.ed.undo() == {"FINISHED"}
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(bm.verts) == 8, "Extrude is one undo step"
    # Cancel mid-pull restores the exact mesh and selection.
    stroke = component_mode.begin(bpy.context, "edit_extrude", start, OWNER)
    stroke.update(bpy.context, InputSample(OWNER, 0, pulled, 1.0))
    stroke.cancel(bpy.context, OWNER)
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(bm.verts) == 8 and len(selected(bm, "faces")) == 1
    assert not [m for m in bpy.data.meshes if m.name.startswith("XR extrude backup")]

    # Vertex mode: extruding one vertex makes an edge, freely.
    assert bpy.ops.xrwb.component_mode(mode="VERT") == {"FINISHED"}
    stroke = component_mode.begin(bpy.context, "select", aim((0.9, 0.8, 3.0)), OWNER)
    stroke.commit(bpy.context, OWNER)
    stroke = component_mode.begin(bpy.context, "edit_extrude", start, OWNER)
    stroke.update(bpy.context, InputSample(OWNER, 0, Pose((0.2, -3.0, 1.0), start.rotation), 1.0))
    stroke.commit(bpy.context, OWNER)
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(bm.verts) == 9 and len(bm.edges) == 13
    tip = next(v for v in bm.verts if v.select)
    assert (tip.co - Vector((1.2, 1.0, 1.0))).length < 1e-5

    # Grip moves the selected components (Blender's own edit-mode transform).
    grip = xr_event("grab_move", RIGHT, (0.0, 0.0, 5.0))
    assert controllers._begin_move(bpy.context, grip, grip=True)[0]
    grip.xr.controller_location = (0.0, 0.5, 5.0)
    assert controllers.dispatch_xr_event(bpy.context, grip) == {"RUNNING_MODAL"}
    grip.value = "RELEASE"
    assert controllers.dispatch_xr_event(bpy.context, grip) == {"FINISHED"}
    obj, bm = component_mode.edit_bmesh(bpy.context)
    tip = next(v for v in bm.verts if v.select)
    assert (tip.co - Vector((1.2, 1.5, 1.0))).length < 1e-4, tuple(tip.co)
    assert bpy.data.objects["Fix Cube"].matrix_world == Matrix.Identity(4), "Only the vertex moved"

    # Mesh actions from the hand menu; face-mode Delete removes faces.
    assert bpy.ops.xrwb.component_mode(mode="FACE") == {"FINISHED"}
    assert bpy.ops.xrwb.mesh_action(action="SELECT_ALL") == {"FINISHED"}
    assert bpy.ops.xrwb.mesh_action(action="SUBDIVIDE") == {"FINISHED"}
    assert bpy.ops.xrwb.mesh_action(action="DELETE") == {"FINISHED"}
    obj, bm = component_mode.edit_bmesh(bpy.context)
    assert len(bm.faces) == 0
    # Hover shapes in each mode.
    for mode in ("VERT", "EDGE", "FACE"):
        bpy.ops.ed.undo()
    assert bpy.ops.xrwb.component_mode(mode="OBJECT") == {"FINISHED"} and bpy.context.mode == "OBJECT"
    cube = bpy.data.objects["Fix Cube"]
    bpy.context.view_layer.objects.active = cube
    for mode in ("VERT", "EDGE", "FACE"):
        bpy.ops.xrwb.component_mode(mode=mode)
        shapes = ray_feedback._edit_hover(bpy.context, (Vector((0.2, 0.1, 3.0)), Quaternion(), Vector((0, 0, -1))),
                                          1.0)
        assert shapes, mode
    bpy.ops.xrwb.component_mode(mode="OBJECT")
    assert editing.active_transaction() is None
    return 1


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        results = {name: check() for name, check in (
            ("menus", check_menus), ("two_grips", check_two_grips), ("face_previews", check_face_previews),
            ("components", check_components))}
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        controllers._active_gesture = None
        xr_workbench.unregister()
    print("XR_INTERACTION_FIXES_SMOKE_PASS " + " ".join(f"{k}={v}" for k, v in results.items())
          + " input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
