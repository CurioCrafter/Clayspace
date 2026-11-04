"""Factory-startup behavioral smoke for Blender VR Workbench."""

from __future__ import annotations

from pathlib import Path
import inspect
import math
import sys
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vr_blender_workbench as addon  # noqa: E402
from vr_blender_workbench.action_map import PROFILES, action_spec_summary  # noqa: E402
from vr_blender_workbench.action_map import build_actionmap  # noqa: E402
from vr_blender_workbench.tools import (  # noqa: E402
    ControllerPose,
    _delete,
    _duplicate,
    _mesh_extrude,
    _mesh_bevel,
    _mesh_inset,
    _mesh_subdivide,
    _selection_signature,
    execute_press_tool,
    select_at_ray,
    tool_needs_outer_undo,
)
from vr_blender_workbench.transactions import SculptTransaction, TransformTransaction  # noqa: E402


def assert_close(actual, expected, epsilon=1.0e-5):
    if (Vector(actual) - Vector(expected)).length > epsilon:
        raise AssertionError(f"Expected {expected}, got {actual}")


assert bpy.app.version[:3] == (4, 5, 11), bpy.app.version_string
assert bpy.app.build_options.xr_openxr

addon.register()
assert hasattr(bpy.types.Scene, "vrw_settings")
assert hasattr(bpy.ops.vrw, "primary_action")
assert (
    bpy.app.handlers.xr_session_start_pre.count(addon.operators.xr_session_start_pre)
    == 1
)
assert hasattr(addon.operators.xr_session_start_pre, "_bpy_persistent")
assert not hasattr(addon.runtime, "_runtime_tick")
for runtime_value in vars(addon.runtime).values():
    if (
        inspect.isfunction(runtime_value)
        and runtime_value.__module__ == addon.runtime.__name__
    ):
        assert not bpy.app.timers.is_registered(runtime_value), runtime_value.__name__
assert "bpy.context" not in inspect.getsource(addon.runtime._draw_callback)
assert addon.runtime._draw_handle is None
addon.runtime.prepare_xr_session(bpy.context)
assert addon.runtime._draw_handle is not None
assert len(addon.runtime._palette_render_entries) == 12
addon.runtime.end_xr_session(bpy.context)
assert addon.runtime._draw_handle is None

# Scene cleanup failure must never skip transaction cancellation.
cancel_calls = 0
original_cancel_all = addon.transactions.cancel_all_transactions


def count_cancel_all():
    global cancel_calls
    cancel_calls += 1


class InvalidSceneContext:
    @property
    def scene(self):
        raise ReferenceError("scene was replaced")


addon.transactions.cancel_all_transactions = count_cancel_all
try:
    addon.runtime.end_xr_session(InvalidSceneContext())
finally:
    addon.transactions.cancel_all_transactions = original_cancel_all
assert cancel_calls == 1
assert bpy.context.scene.vrw_settings.active_tool == "SELECT"
favorite_properties = addon.properties.VRW_AddonPreferences.bl_rna.properties
assert favorite_properties["favorite_5_creates_undo"].default is False
assert favorite_properties["favorite_6_creates_undo"].default is False
assert action_spec_summary()["total_actions"] == 19
assert {profile.name for profile in PROFILES} == {
    "index",
    "oculus",
    "vive",
    "wmr",
    "simple",
}

# Favorites expose explicit outer-undo metadata, while history operators always
# suppress the wrapper state so Undo/Redo cannot erase their own Redo branch.
original_get_preferences = tool_needs_outer_undo.__globals__["get_preferences"]
favorite_preferences = SimpleNamespace(
    favorite_5_id="object.select_all",
    favorite_5_creates_undo=False,
    favorite_6_id="view3d.view_all",
    favorite_6_creates_undo=False,
)
tool_needs_outer_undo.__globals__["get_preferences"] = (
    lambda _context: favorite_preferences
)
try:
    assert not tool_needs_outer_undo(bpy.context, "FAVORITE_5")
    assert not tool_needs_outer_undo(bpy.context, "FAVORITE_6")
    favorite_preferences.favorite_5_id = "ed.undo"
    favorite_preferences.favorite_5_creates_undo = True
    assert not tool_needs_outer_undo(bpy.context, "FAVORITE_5")
    favorite_preferences.favorite_5_id = "ed.redo"
    assert not tool_needs_outer_undo(bpy.context, "FAVORITE_5")
    favorite_preferences.favorite_5_id = "mesh.subdivide"
    assert tool_needs_outer_undo(bpy.context, "FAVORITE_5")
finally:
    tool_needs_outer_undo.__globals__["get_preferences"] = original_get_preferences
assert tool_needs_outer_undo(bpy.context, "ADD_CUBE")
assert tool_needs_outer_undo(bpy.context, "DELETE")
assert not tool_needs_outer_undo(bpy.context, "SELECT")
assert not tool_needs_outer_undo(bpy.context, "UNDO")
bpy.ops.wm.read_homefile(use_empty=True)
assert (
    bpy.app.handlers.xr_session_start_pre.count(addon.operators.xr_session_start_pre)
    == 1
)
assert hasattr(bpy.context.scene, "vrw_settings")
for runtime_value in vars(addon.runtime).values():
    if (
        inspect.isfunction(runtime_value)
        and runtime_value.__module__ == addon.runtime.__name__
    ):
        assert not bpy.app.timers.is_registered(runtime_value), runtime_value.__name__


class FakeCollection:
    def __init__(self, factory):
        self.factory = factory
        self.values = []

    def new(self, *args):
        value = self.factory(*args)
        self.values.append(value)
        return value

    def __iter__(self):
        return iter(self.values)


class FakePath:
    def __init__(self, path):
        self.path = path


class FakeBinding:
    def __init__(self, name, _replace=True):
        self.name = name
        self.profile = ""
        self.threshold = 0.0
        self.axis0_region = "ANY"
        self.component_paths = FakeCollection(FakePath)


class FakeProps:
    pass


class FakeItem:
    def __init__(self, name, _replace=True):
        self.name = name
        self.user_paths = FakeCollection(FakePath)
        self.bindings = FakeCollection(FakeBinding)
        self.op_properties = FakeProps()


class FakeMap:
    def __init__(self, name):
        self.name = name
        self.actionmap_items = FakeCollection(FakeItem)


class FakeMaps:
    def __init__(self):
        self.values = []

    def new(self, _state, name, _replace=True):
        value = FakeMap(name)
        self.values.append(value)
        return value


class FakeState:
    def __init__(self):
        self.actionmaps = FakeMaps()


fake_actionmap = build_actionmap(FakeState())
assert len(fake_actionmap.actionmap_items.values) == 19
for item in fake_actionmap.actionmap_items:
    for binding in item.bindings:
        assert len(binding.component_paths.values) == len(item.user_paths.values), (
            item.name
        )
press_bindings = {}
for item in fake_actionmap.actionmap_items:
    if item.type != "FLOAT":
        continue
    for binding in item.bindings:
        for user_path, component in zip(item.user_paths, binding.component_paths):
            key = (
                binding.name,
                user_path.path,
                component.path,
                binding.axis0_region,
            )
            assert key not in press_bindings, (key, press_bindings.get(key), item.name)
            press_bindings[key] = item.name


class FakeTransaction:
    def __init__(self):
        self.valid = True
        self.applies = 0
        self.commits = 0
        self.cancels = 0

    def apply(self, *_args):
        self.applies += 1

    def commit(self):
        self.commits += 1
        self.valid = False

    def cancel(self):
        self.cancels += 1
        self.valid = False


class ModalRouteProbe(addon.operators._VRW_ContinuousMixin):
    def report(self, *_args):
        pass


def xr_event(action_set, action, user_path, value="PRESS"):
    return SimpleNamespace(
        type="XR_ACTION",
        value=value,
        xr=SimpleNamespace(
            action_set=action_set,
            action=action,
            user_path=user_path,
        ),
    )


probe = ModalRouteProbe()
probe._transaction = FakeTransaction()
probe._tool = "MOVE"
probe._timer = None
probe._blocked_wait = False
start_event = xr_event("vr_workbench", "primary", "/user/hand/right")
probe._capture_xr_route(start_event)
original_session_running = addon.runtime.session_running
addon.runtime.session_running = lambda _context: True
try:
    assert probe.modal(
        bpy.context, xr_event("other_set", "primary", "/user/hand/right")
    ) == {"PASS_THROUGH"}
    assert probe.modal(
        bpy.context, xr_event("vr_workbench", "redo", "/user/hand/right")
    ) == {"PASS_THROUGH"}
    assert probe._transaction.applies == 0
    assert probe._transaction.commits == 0
    assert probe.modal(
        bpy.context,
        xr_event("vr_workbench", "primary", "/user/hand/right", value="RELEASE"),
    ) == {"FINISHED"}
    assert probe._transaction.commits == 1
finally:
    addon.runtime.session_running = original_session_running

# Reset the factory scene and build deterministic ray targets.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0.0, 0.0, -1.0))
ground = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
cube = bpy.context.object
cube.name = "VRW_Smoke_Cube"
ground.select_set(False)
cube.select_set(False)

down_pose = ControllerPose(Vector((0.0, 0.0, 5.0)), Quaternion())
ok, message = select_at_ray(bpy.context, down_pose)
assert ok, message
assert bpy.context.active_object == cube and cube.select_get()
ground_pose = ControllerPose(Vector((4.0, 0.0, 5.0)), Quaternion())
ok, message = select_at_ray(bpy.context, ground_pose, "ADD")
assert ok, message
assert cube.select_get() and ground.select_get()
ok, message = select_at_ray(bpy.context, ground_pose, "SUBTRACT")
assert ok, message
assert cube.select_get() and not ground.select_get()

# Object transform previews must cancel back to the exact matrix.
original_matrix = cube.matrix_world.copy()
transaction = TransformTransaction(
    bpy.context,
    "MOVE",
    Vector((0.0, 0.0, 1.0)),
    Quaternion(),
)
assert transaction.valid, transaction.error
conflicting_transaction = TransformTransaction(
    bpy.context,
    "MOVE",
    Vector((0.0, 0.0, 1.0)),
    Quaternion(),
)
assert not conflicting_transaction.valid
assert "active VR gesture" in conflicting_transaction.error
ok, message = execute_press_tool(bpy.context, "DUPLICATE", down_pose)
assert not ok and "active VR gesture" in message
assert bpy.ops.vrw.redo() == {"CANCELLED"}
transaction.apply(
    Vector((1.25, -0.5, 1.0)), Quaternion(), bpy.context.scene.vrw_settings
)
assert_close(cube.matrix_world.translation, (1.25, -0.5, 0.0))
transaction.cancel()
assert cube.matrix_world == original_matrix

rotation_tx = TransformTransaction(
    bpy.context,
    "ROTATE",
    Vector((1.0, 0.0, 0.0)),
    Quaternion(),
)
rotation_tx.apply(
    Vector((1.0, 0.0, 0.0)),
    Quaternion(Vector((0.0, 0.0, 1.0)), math.radians(20.0)),
    bpy.context.scene.vrw_settings,
)
assert abs(cube.matrix_world.to_euler().z - math.radians(15.0)) < 1.0e-5
rotation_tx.cancel()
assert cube.matrix_world == original_matrix

scale_tx = TransformTransaction(
    bpy.context,
    "SCALE",
    Vector((1.0, 0.0, 0.0)),
    Quaternion(),
)
scale_tx.apply(Vector((2.0, 0.0, 0.0)), Quaternion(), bpy.context.scene.vrw_settings)
assert all(abs(value - 2.0) < 1.0e-5 for value in cube.matrix_world.to_scale())
scale_tx.cancel()
assert cube.matrix_world == original_matrix

# Duplicate and guarded-delete vertical slice.
ok, message = _duplicate(bpy.context)
assert ok, message
duplicate = bpy.context.active_object
assert duplicate != cube and duplicate.data != cube.data
duplicate_name = duplicate.name
second_scene = bpy.data.scenes.new("VRW_Second_Scene")
second_scene.collection.objects.link(duplicate)
assert duplicate.name in bpy.context.scene.objects
assert duplicate.name in second_scene.objects
ok, message = _delete(bpy.context)
assert not ok and "armed" in message.lower()
duplicate.select_set(False)
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
ok, message = _delete(bpy.context)
assert not ok and "target changed" in message.lower()
assert cube.name in bpy.data.objects and duplicate_name in bpy.data.objects
cube.select_set(False)
duplicate.select_set(True)
bpy.context.view_layer.objects.active = duplicate
ok, message = _delete(bpy.context)
assert not ok and "confirmation reset" in message.lower()
ok, message = _delete(bpy.context)
assert ok, message
assert duplicate_name not in bpy.context.scene.objects
assert duplicate_name in bpy.data.objects
assert duplicate_name in second_scene.objects

# Direct BMesh extrusion should create real topology.
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
bpy.ops.object.mode_set(mode="EDIT")

# Ray selection must honor Blender's active mesh component mode.
for select_mode, sequence_name, label in (
    ((True, False, False), "verts", "vertex"),
    ((False, True, False), "edges", "edge"),
    ((False, False, True), "faces", "face"),
):
    bpy.context.tool_settings.mesh_select_mode = select_mode
    bpy.ops.mesh.select_all(action="DESELECT")
    ok, message = select_at_ray(bpy.context, down_pose)
    assert ok and label in message.lower(), message
    edit_mesh = bmesh.from_edit_mesh(cube.data)
    selected = [
        element for element in getattr(edit_mesh, sequence_name) if element.select
    ]
    assert len(selected) == 1, (label, message, len(selected))

bpy.context.tool_settings.mesh_select_mode = (False, False, True)
face_mode_signature = _selection_signature(bpy.context)
bpy.context.tool_settings.mesh_select_mode = (True, False, False)
vertex_mode_signature = _selection_signature(bpy.context)
assert face_mode_signature != vertex_mode_signature
bpy.context.tool_settings.mesh_select_mode = (False, False, True)
bpy.ops.mesh.select_all(action="SELECT")
before_vertices = len(cube.data.vertices)
ok, message = _mesh_extrude(bpy.context)
assert ok, message
bpy.ops.mesh.select_all(action="SELECT")
before_faces = len(cube.data.polygons)
ok, message = _mesh_inset(bpy.context)
assert ok, message
bpy.ops.mesh.select_all(action="SELECT")
ok, message = _mesh_subdivide(bpy.context)
assert ok, message
bpy.ops.mesh.select_all(action="SELECT")
ok, message = _mesh_bevel(bpy.context)
assert ok, message
bpy.ops.object.mode_set(mode="OBJECT")
assert len(cube.data.vertices) > before_vertices
assert len(cube.data.polygons) > before_faces

# Experimental sculpt must deform and cancel without leaving data changes.
bpy.ops.mesh.primitive_uv_sphere_add(
    segments=16, ring_count=8, location=(4.0, 0.0, 0.0)
)
sphere = bpy.context.object
original_coords = [vertex.co.copy() for vertex in sphere.data.vertices]
bpy.ops.object.mode_set(mode="SCULPT")
sculpt = SculptTransaction(bpy.context, "SCULPT_DRAW")
assert sculpt.valid, sculpt.error
sculpt.apply(
    Vector((4.0, 0.0, 3.0)),
    Vector((0.0, 0.0, -1.0)),
    bpy.context.scene.vrw_settings,
    1.0,
)
assert any(
    (vertex.co - original).length > 1.0e-7
    for vertex, original in zip(sphere.data.vertices, original_coords)
)
sculpt.cancel()
assert all(
    (vertex.co - original).length <= 1.0e-7
    for vertex, original in zip(sphere.data.vertices, original_coords)
)
bpy.ops.object.mode_set(mode="OBJECT")

addon.unregister()
assert not hasattr(bpy.types.Scene, "vrw_settings")
assert addon.operators.xr_session_start_pre not in bpy.app.handlers.xr_session_start_pre
print(
    "VRW_BLENDER_4511_SMOKE_PASS "
    "actionmap=19 handler_persistent=1 no_runtime_timer=1 draw_context_free=1 "
    "teardown_cancel=1 favorite_undo=1 modal_route=1 selection_multi=1 "
    "exclusive_gesture=1 guarded_delete=1 multiscene_delete=1 "
    "edit_guard_mode=1 mesh_ray_modes=1 move_rotate_scale_cancel=1 extrude=1 inset=1 "
    "subdivide=1 bevel=1 sculpt_cancel=1"
)
