"""Owned empty-scene XR test: native XR event queue, synthetic controller poses.

The caller owns the GUI process, session, main-thread timer and final failure
handling. No camera, physical controller, or user scene is accessed by this test.
"""
from math import log2, radians
import os
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector


def _matrix_close(left, right):
    return all(abs(a-b) < 1.0e-4 for ra, rb in zip(left, right) for a, b in zip(ra, rb))


def _geometry(mesh):
    return (tuple(tuple(v.co) for v in mesh.vertices),
            tuple(tuple(p.vertices) for p in mesh.polygons))


def _manifold(mesh):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert all(edge.is_manifold for edge in bm.edges), "Trigger extrusion is not manifold"
    finally:
        bm.free()


def run(context, addon):
    """Yield after event/undo boundaries; return verified synthetic-route counts."""
    root = Path(__file__).resolve().parents[1]
    simulated = os.environ.get("XRWB_HAND_MENU_SIMULATOR") == "1"
    if simulated:
        assert os.environ.get("XRWB_EDITOR_TEST_ALLOW") == "1"
        assert os.environ.get("SIMULATED_ENABLE") == "1"
        assert Path(os.environ["XR_RUNTIME_JSON"]).resolve() == (
            root / "native/steamvr_free/monado-inproc-build/openxr_monado-dev.json"
        ).resolve(), "Only the camera-free simulator is allowed"
    else:
        assert os.environ.get("XRWB_STEAMVR_SMOKE") == "1"
        assert os.environ.get("XRWB_STEAMVR") == "1" and not os.environ.get("SIMULATED_ENABLE")
    assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1"
    profile = Path(os.environ["BLENDER_USER_CONFIG"]).resolve().parent
    if simulated:
        assert profile.parent == root and profile.name.startswith(".tmp_xr_probe_")
    else:
        assert profile in (root / ".xr_profile", root / ".xr_addons_profile")
    assert not bpy.app.background and not bpy.data.filepath and context.mode == "OBJECT"
    assert not context.scene.objects, "Refusing to alter an external nonempty scene"
    assert addon.runtime.session_running(context)
    assert not context.window_manager.xr_session_settings.show_passthrough
    assert not any(os.environ.get(key) == "1" for key in (
        "XRWB_INDEX_CAMERA_OVERLAY", "XRWB_INDEX_CAMERA_GESTURE", "XRWB_INDEX_CAMERA_CONSENT"))
    controllers = addon.controllers
    assert controllers._active_gesture is None and addon.editing.active_transaction() is None
    assert not controllers._release_required
    assert not controllers.hand_menu_active(context), "Caller must close the native hand menu first"
    original_editor = bool(context.window_manager.xr_session_state.editor_surface_info(context)[0])
    original_card = bool(context.window_manager.xr_session_state.task_card_info(context)[0])
    original_hover = controllers._hover_router is not None
    original_hand = controllers._pointer_hand
    keys = ("edit_tool", "edit_constraint", "translation_snap", "rotation_snap_degrees", "scale_snap")
    settings = context.window_manager.xrwb_settings
    original_settings = {key: getattr(settings, key) for key in keys}
    undo_enabled, undo_steps = context.preferences.edit.use_global_undo, context.preferences.edit.undo_steps
    original_cursor = tuple(context.scene.cursor.location)
    counters = {"hands": 2, "transforms": 0, "extrude": 0, "face_scale": 0, "cancel": 0,
                "origin": 0, "cube_sides": 0, "draw": 0, "cut": 0, "edge": 0}

    def call(operator, **properties):
        with addon.ui._view3d_override(context):
            result = operator("EXEC_DEFAULT", **properties)
        assert result == {"FINISHED"}, (str(operator), properties, result)

    def event(hand, location, rotation=(1, 0, 0, 0), value="PRESS"):
        return SimpleNamespace(type="XR_ACTION", value=value, xr=SimpleNamespace(
            action_set=controllers.ACTION_SET_NAME, action="pointer", user_path=hand,
            controller_location=tuple(location), controller_rotation=tuple(rotation)))

    def enqueue(sample):
        # Do not call the Python handler directly: that bypasses the native
        # modal-context bug this test must catch on held/release events.
        call(bpy.ops.wm.xr_test_action, action=sample.xr.action, user_path=sample.xr.user_path,
             controller_location=sample.xr.controller_location,
             controller_rotation=sample.xr.controller_rotation, value=sample.value)
        yield

    def wait(predicate, message):
        # Several independent Blender timers can fire before the queued XR
        # event. A timer tick is not an acknowledgement of action dispatch.
        for _ in range(30):
            if predicate():
                return
            yield
        raise AssertionError(f"{message}: {addon.interaction_log.last_message()}")

    def press(sample):
        yield from enqueue(sample)
        yield from wait(lambda: controllers._active_gesture is not None, "Native press did not capture")
        assert controllers._active_gesture is not None
        assert controllers._active_gesture.kind == "move", "Trigger did not fall through native UI to scene tool"
        assert controllers._active_gesture.owner.identifier == sample.xr.user_path
        assert controllers._pointer_hand == sample.xr.user_path

    def dispatch(sample, expected):
        previous = controllers._active_gesture.last_input_at if controllers._active_gesture else 0
        yield from enqueue(sample)
        if expected == "RUNNING_MODAL":
            yield from wait(lambda: controllers._active_gesture is None or
                            controllers._active_gesture.last_input_at > previous,
                            "Native held event was not delivered")
            assert controllers._active_gesture is not None, "Native held input lost its capture"
            assert (sample.xr.action, sample.xr.user_path) not in controllers._release_required
        else:
            yield from wait(lambda: controllers._active_gesture is None and
                            (sample.xr.action, sample.xr.user_path) not in controllers._release_required,
                            "Native release did not drain")
            assert controllers._active_gesture is None, "Native release left its capture installed"
            assert (sample.xr.action, sample.xr.user_path) not in controllers._release_required

    def fixture():
        assert not context.scene.objects
        call(bpy.ops.xrwb.add_primitive, kind="CUBE")
        obj = context.view_layer.objects.active
        assert obj is not None and obj.select_get() and len(context.scene.objects) == 1
        return obj.name

    try:
        controllers.stop_router(context)
        call(bpy.ops.wm.xr_editor_surface, visible=False)
        call(bpy.ops.xrwb.task_card, hide=True)
        yield
        assert not context.window_manager.xr_session_state.editor_surface_info(context)[0]
        assert not context.window_manager.xr_session_state.task_card_info(context)[0]
        context.preferences.edit.use_global_undo = True
        context.preferences.edit.undo_steps = max(undo_steps, 32)
        settings.edit_constraint = "free"
        settings.translation_snap = settings.rotation_snap_degrees = settings.scale_snap = 0
        call(bpy.ops.ed.undo_push, message="XR Trigger Empty Baseline")
        yield

        for hand in (controllers.LEFT_HAND, controllers.RIGHT_HAND):
            for tool in ("move", "rotate", "scale", "extrude", "face_scale"):
                name = fixture()
                yield
                obj = context.scene.objects[name]
                original = obj.matrix_world.copy()
                original_geometry = _geometry(obj.data)
                pivot = original.translation.copy()
                scale = float(context.window_manager.xr_session_state.hand_tracking_world_transform(context)[0])
                assert 0 < scale < 1.0e6
                start = pivot + Vector((0, 0, .5 * scale))
                context.window_manager.xrwb_settings.edit_tool = tool
                sample = event(hand, start)
                yield from press(sample)
                if hand == controllers.LEFT_HAND and tool == "move":
                    # A simultaneous second trigger must neither steal nor
                    # finish the first hand's native modal operator.
                    other = event(controllers.RIGHT_HAND, start)
                    yield from enqueue(other)
                    yield from wait(lambda: ("pointer", controllers.RIGHT_HAND) in controllers._release_required,
                                    "Second-hand refusal was not delivered")
                    assert controllers._active_gesture.user_path == hand
                    assert ("pointer", controllers.RIGHT_HAND) in controllers._release_required
                    yield from dispatch(sample, "RUNNING_MODAL")
                    other.value = "RELEASE"
                    yield from enqueue(other)
                    yield from wait(lambda: not controllers._release_required, "Second-hand release was not drained")
                    assert controllers._active_gesture.user_path == hand
                    assert not controllers._release_required
                if tool == "move":
                    delta = Vector((.08 * scale, 0, 0))
                    sample.xr.controller_location = tuple(start + delta)
                    expected = Matrix.Translation(delta) @ original
                elif tool == "rotate":
                    rotation = Quaternion((0, 0, 1), radians(20))
                    sample.xr.controller_rotation = tuple(rotation)
                    expected = Matrix.Translation(pivot) @ rotation.to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ original
                elif tool == "scale":
                    sample.xr.controller_location = tuple(start + Vector((
                        log2(1.25) * settings.scale_drag_distance_m * scale, 0, 0)))
                    expected = Matrix.Translation(pivot) @ Matrix.Diagonal((1.25, 1.25, 1.25, 1)) @ Matrix.Translation(-pivot) @ original
                elif tool == "extrude":
                    sample.xr.controller_location = tuple(start + Vector((0, 0, .08 * scale)))
                else:
                    sample.xr.controller_location = tuple(start + Vector((.05 * scale, 0, 0)))
                    transaction = controllers._active_gesture.transaction
                    face_center = transaction.center.copy()
                    factor = 2 ** (.05 / settings.scale_drag_distance_m)
                yield from dispatch(sample, "RUNNING_MODAL")
                yield
                obj = context.scene.objects[name]
                if tool in {"move", "rotate", "scale"}:
                    assert _matrix_close(obj.matrix_world, expected), tool
                    assert _geometry(obj.data) == original_geometry
                elif tool == "extrude":
                    assert (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)) == (12, 20, 10)
                    _manifold(obj.data)
                    assert max(v.co.z for v in obj.data.vertices) > max(co[2] for co in original_geometry[0])
                else:
                    assert len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
                    for vertex, coordinate in zip(obj.data.vertices, original_geometry[0]):
                        before = original @ Vector(coordinate)
                        expected_vertex = face_center + (before-face_center) * factor if coordinate[2] > 0 else before
                        assert (obj.matrix_world @ vertex.co - expected_vertex).length < 1.0e-4 * max(1, scale)
                final_matrix, final_geometry = obj.matrix_world.copy(), _geometry(obj.data)
                sample.value = "RELEASE"
                yield from dispatch(sample, "FINISHED")
                yield
                assert controllers._active_gesture is None and addon.editing.active_transaction() is None
                obj = context.scene.objects[name]
                assert _matrix_close(obj.matrix_world, final_matrix) and _geometry(obj.data) == final_geometry
                call(bpy.ops.xrwb.history_undo)
                yield
                obj = context.scene.objects[name]
                assert _matrix_close(obj.matrix_world, original), f"{hand} {tool} did not undo exactly once"
                assert _geometry(obj.data) == original_geometry, f"{hand} {tool} mesh undo failed"
                call(bpy.ops.xrwb.history_undo)
                yield
                assert not context.scene.objects, f"{hand} {tool} added extra history entries"
                counters[tool if tool in {"extrude", "face_scale"} else "transforms"] += 1

            # A cancelled face preview must not add history; the next undo removes
            # only its fixture. Drain that hand's real release/rearm path as well.
            name = fixture()
            yield
            obj = context.scene.objects[name]
            original_geometry = _geometry(obj.data)
            start = obj.matrix_world.translation + Vector((0, 0, .5 * scale))
            context.window_manager.xrwb_settings.edit_tool = "extrude"
            sample = event(hand, start)
            yield from press(sample)
            sample.xr.controller_location = tuple(start + Vector((0, 0, .06 * scale)))
            yield from dispatch(sample, "RUNNING_MODAL")
            yield
            assert len(context.scene.objects[name].data.vertices) == 12
            call(bpy.ops.xrwb.controller_cancel)
            assert _geometry(context.scene.objects[name].data) == original_geometry
            sample.value = "RELEASE"
            yield from dispatch(sample, "CANCELLED")
            assert not controllers._release_required and addon.editing.active_transaction() is None
            call(bpy.ops.xrwb.history_undo)
            yield
            assert not context.scene.objects, "Cancel changed history"
            counters["cancel"] += 1

        # Hit every different cube side through the same native UI-miss trigger
        # route. A fixed +Z face test cannot prove that ray-face mapping works.
        for side, normal in enumerate((Vector((1, 0, 0)), Vector((-1, 0, 0)),
                                       Vector((0, 1, 0)), Vector((0, -1, 0)),
                                       Vector((0, 0, 1)), Vector((0, 0, -1)))):
            name = fixture()
            yield
            obj = context.scene.objects[name]
            original_geometry = _geometry(obj.data)
            matrix = obj.matrix_world.copy()
            start = matrix.translation + normal * (.5 * scale)
            rotation = Vector((0, 0, -1)).rotation_difference(-normal)
            sample = event((controllers.LEFT_HAND, controllers.RIGHT_HAND)[side % 2], start, rotation)
            context.window_manager.xrwb_settings.edit_tool = "extrude"
            before_extent = max((matrix @ Vector(co)).dot(normal) for co in original_geometry[0])
            yield from press(sample)
            sample.xr.controller_location = tuple(start + normal * (.05 * scale))
            yield from dispatch(sample, "RUNNING_MODAL")
            sample.value = "RELEASE"
            yield from dispatch(sample, "FINISHED")
            yield
            obj = context.scene.objects[name]
            assert (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)) == (12, 20, 10)
            _manifold(obj.data)
            actual_extent = max((obj.matrix_world @ vertex.co).dot(normal) for vertex in obj.data.vertices)
            assert abs(actual_extent-before_extent-.05*scale) < 1.0e-4 * max(1, scale), side
            call(bpy.ops.xrwb.history_undo)
            yield
            assert _geometry(context.scene.objects[name].data) == original_geometry
            call(bpy.ops.xrwb.history_undo)
            yield
            assert not context.scene.objects, f"Side {side} left a fixture or extra undo step"
            counters["cube_sides"] += 1

        # Production trigger path for project-owned tools, without purchased add-ons.
        state = context.window_manager.xr_session_state
        base = state.viewer_pose_location + state.viewer_pose_rotation @ Vector((0, 0, -.7 * scale))
        for index, tool in enumerate(("draw_cube", "draw_sphere", "draw_cylinder")):
            print(f"XR_NATIVE_GESTURE_CASE tool={tool} input=synthetic", flush=True)
            context.window_manager.xrwb_settings.edit_tool = tool
            start = base + Vector((0, 0, .2 * scale))
            sample = event((controllers.LEFT_HAND, controllers.RIGHT_HAND)[index % 2], start)
            yield from press(sample)
            sample.xr.controller_location = tuple(start + Vector((.2, .15, .1)) * scale)
            yield from dispatch(sample, "RUNNING_MODAL")
            yield
            sample.value = "RELEASE"
            yield from dispatch(sample, "FINISHED")
            yield
            assert len(context.scene.objects) == 1
            obj = context.view_layer.objects.active
            assert obj.type == "MESH" and obj.select_get() and len(obj.data.polygons) >= 6
            _manifold(obj.data)
            call(bpy.ops.xrwb.history_undo)
            yield
            assert not context.scene.objects, f"{tool} did not commit exactly one undo"
            counters["draw"] += 1

        for index, tool in enumerate(("cut_difference", "cut_union", "cut_intersect")):
            print(f"XR_NATIVE_GESTURE_CASE tool={tool} input=synthetic", flush=True)
            name = fixture()
            yield
            obj = context.scene.objects[name]
            geometry = _geometry(obj.data)
            start = obj.matrix_world.translation + Vector((0, 0, .2 * scale))
            context.window_manager.xrwb_settings.edit_tool = tool
            sample = event((controllers.RIGHT_HAND, controllers.LEFT_HAND)[index % 2], start)
            yield from press(sample)
            sample.xr.controller_location = tuple(start + Vector((.15, .15, .15)) * scale)
            yield from dispatch(sample, "RUNNING_MODAL")
            yield
            sample.value = "RELEASE"
            yield from dispatch(sample, "FINISHED")
            yield
            obj = context.scene.objects[name]
            assert len(context.scene.objects) == 2 and _geometry(obj.data) == geometry
            assert obj.modifiers[-1].type == "BOOLEAN" and obj.modifiers[-1].solver == "EXACT"
            assert obj.modifiers[-1].object.display_type == "WIRE"
            call(bpy.ops.xrwb.history_undo)
            yield
            assert len(context.scene.objects) == 1 and not context.scene.objects[name].modifiers
            call(bpy.ops.xrwb.history_undo)
            yield
            assert not context.scene.objects
            counters["cut"] += 1

        from xr_workbench import edge_editing
        print("XR_NATIVE_GESTURE_CASE tool=select_edge_bevel input=synthetic", flush=True)
        name = fixture()
        yield
        obj = context.scene.objects[name]
        start = obj.matrix_world.translation + Vector((.085, 0, .5)) * scale
        context.window_manager.xrwb_settings.edit_tool = "select_edge"
        sample = event(controllers.LEFT_HAND, start)
        yield from press(sample)
        sample.value = "RELEASE"
        yield from dispatch(sample, "FINISHED")
        for _ in range(3):
            yield
        assert len(edge_editing.selected_indices(context.scene.objects[name].data)) == 1
        assert not edge_editing._last_draw_error
        assert edge_editing._draw_counts.get("XR", 0) > 0, "Selected edges were not drawn by the XR overlay"
        context.window_manager.xrwb_settings.edit_tool = "bevel_edge"
        sample = event(controllers.RIGHT_HAND, start)
        yield from press(sample)
        sample.xr.controller_location = tuple(start + Vector((.02 * scale, 0, 0)))
        yield from dispatch(sample, "RUNNING_MODAL")
        yield
        sample.value = "RELEASE"
        yield from dispatch(sample, "FINISHED")
        yield
        assert len(context.scene.objects[name].data.vertices) > 8
        _manifold(context.scene.objects[name].data)
        call(bpy.ops.xrwb.history_undo)
        yield
        assert len(context.scene.objects[name].data.vertices) == 8
        assert len(edge_editing.selected_indices(context.scene.objects[name].data)) == 1
        call(bpy.ops.xrwb.history_undo)
        yield
        assert not edge_editing.selected_indices(context.scene.objects[name].data)
        call(bpy.ops.xrwb.history_undo)
        yield
        assert not context.scene.objects
        counters["edge"] = 2
        print("XR_NATIVE_GESTURE_FEATURES_PASS draw=3 cut=3 edge=2 xr_edge_overlay=1 undo=single input=synthetic", flush=True)

        # Exercise Blender's actual origin operation in a valid view context;
        # explicit history mirrors the controller adapters, not a fake undo stub.
        name = fixture()
        yield
        obj = context.scene.objects[name]
        matrix, geometry = obj.matrix_world.copy(), _geometry(obj.data)
        world_vertices = tuple(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)
        requested_origin = matrix.translation + Vector((.07 * scale, .03 * scale, 0))
        context.scene.cursor.location = requested_origin
        call(bpy.ops.object.origin_set, type="ORIGIN_CURSOR", center="MEDIAN")
        assert (obj.matrix_world.translation-requested_origin).length < 1.0e-4
        assert all((obj.matrix_world @ vertex.co-before).length < 1.0e-4
                   for vertex, before in zip(obj.data.vertices, world_vertices))
        call(bpy.ops.ed.undo_push, message="XR Origin API Check")
        yield
        call(bpy.ops.xrwb.history_undo)
        yield
        obj = context.scene.objects[name]
        assert _matrix_close(obj.matrix_world, matrix) and _geometry(obj.data) == geometry
        call(bpy.ops.xrwb.history_undo)
        yield
        assert not context.scene.objects
        counters["origin"] = 1
    finally:
        controllers.stop_router(context)
        if addon.editing.active_transaction() is not None:
            addon.editing.cancel_active(context)
        controllers._pointer_hand = original_hand
        context.preferences.edit.use_global_undo = undo_enabled
        context.preferences.edit.undo_steps = undo_steps
        for key, value in original_settings.items():
            setattr(context.window_manager.xrwb_settings, key, value)
        context.scene.cursor.location = original_cursor
        if addon.runtime.session_running(context):
            call(bpy.ops.wm.xr_editor_surface, visible=original_editor)
            if original_card:
                call(bpy.ops.xrwb.task_card)
            if original_hover:
                controllers.start_router(context)
    assert not context.scene.objects and addon.runtime.session_running(context)
    print("XRWB_SCENE_TRIGGER_FEATURES_PASS hands=2 transforms=6 extrude=2 face_scale=2 cube_sides=6 cancel=2 origin_api=1 undo=single input=synthetic native_event_queue=1 cross_hand_isolation=1 native_ui_miss=1 physical_controller_verified=0", flush=True)
    return counters
