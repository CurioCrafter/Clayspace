"""Owned empty-scene SteamVR feature check, driven by the caller's UI timer.

Input poses are synthetic; neither a physical controller nor visible headset
output is verified. Optional captures contain only this Blender editor window.
The caller owns session startup/stop, process lifetime, and failure reporting.
"""
from math import radians
import os
import runpy
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Quaternion, Vector


def _close_matrix(left, right):
    return all(abs(a - b) < 1.0e-4 for ra, rb in zip(left, right) for a, b in zip(ra, rb))


def run(context, addon, output_dir=None):
    """Yield between real UI actions; return counts and UI capture paths on success."""
    root = Path(__file__).resolve().parents[1]
    assert os.environ.get("XRWB_STEAMVR_SMOKE") == "1", "Owned smoke opt-in required"
    assert os.environ.get("XRWB_STEAMVR") == "1" and not os.environ.get("SIMULATED_ENABLE")
    profile = Path(os.environ["BLENDER_USER_CONFIG"]).resolve().parent
    assert profile in (root / ".xr_profile", root / ".xr_addons_profile"), "Private profile required"
    assert not bpy.app.background and context.mode == "OBJECT"
    assert not context.scene.objects, "Refusing to modify a nonempty scene"
    assert not addon.editing.active_transaction() and addon.controllers._active_gesture is None
    assert not addon.controllers._release_required, "Controller input is still held"
    assert not context.window_manager.xr_session_settings.show_passthrough
    assert not any(os.environ.get(key) == "1" for key in (
        "XRWB_INDEX_CAMERA_OVERLAY", "XRWB_INDEX_CAMERA_GESTURE", "XRWB_INDEX_CAMERA_CONSENT"
    )), "Camera mode cannot capture UI test images"
    assert addon.runtime.session_running(context)
    assert addon.controllers._hover_router is not None, "Passive controller hover did not start"
    assert bpy.app.timers.is_registered(addon.controllers._hover_timer)
    assert not context.preferences.view.show_splash, "Copied preferences restored the splash"
    assert set(addon.ui.PRIMITIVES) == {"CUBE", "SPHERE", "CYLINDER", "CONE", "PLANE", "TORUS"}
    capture_dir = None
    if output_dir is not None:
        capture_dir = Path(output_dir).resolve()
        assert capture_dir.is_relative_to(root / "reports"), "Captures must stay in project reports"
        capture_dir.mkdir(parents=True, exist_ok=True)
    captures = []
    controllers = addon.controllers
    names = ("edit_tool", "edit_constraint", "translation_snap", "rotation_snap_degrees", "scale_snap", "command_page")
    settings = context.window_manager.xrwb_settings
    original_settings = {name: getattr(settings, name) for name in names}
    undo_enabled = context.preferences.edit.use_global_undo
    undo_steps = context.preferences.edit.undo_steps
    panel_pose = tuple(context.window_manager.xr_session_state.editor_surface_pose(context))
    assert panel_pose[7] > 0 and panel_pose[8] > 0

    def call(operator, **properties):
        with addon.ui._view3d_override(context):
            result = operator("EXEC_DEFAULT", **properties)
        assert result == {"FINISHED"}, (str(operator), result)

    def event(action, position, rotation=(1.0, 0.0, 0.0, 0.0), value="PRESS"):
        return SimpleNamespace(type="XR_ACTION", value=value, xr=SimpleNamespace(
            action_set=controllers.ACTION_SET_NAME, action=action, user_path=controllers.RIGHT_HAND,
            controller_location=tuple(position), controller_rotation=tuple(rotation),
        ))

    def begin(function, sample):
        with addon.ui._view3d_override(context):
            accepted, message = function(context, sample)
        assert accepted, message

    def dispatch(sample, expected):
        with addon.ui._view3d_override(context):
            result = controllers.dispatch_xr_event(context, sample)
        assert result == {expected}, (sample.xr.action, result, expected)

    def snapshot():
        return tuple(context.window_manager.xr_session_state.editor_surface_pose(context))

    def close_pose(left, right):
        return (Vector(left[:3]) - Vector(right[:3])).length < 1.0e-4 and abs(
            abs(Quaternion(left[3:7]).dot(Quaternion(right[3:7]))) - 1.0
        ) < 1.0e-4

    try:
        for enabled, steps in ((False, 32), (True, 0)):
            context.preferences.edit.use_global_undo = enabled
            context.preferences.edit.undo_steps = steps
            assert not addon.ui.XRWB_OT_add_primitive.poll(context)
            assert addon.ui._add_primitive(context, "CUBE") == {"CANCELLED"}
            for function, arguments in ((addon.editing.begin, (context, None)),
                                        (addon.editing.begin_object_selection, (context, None, None))):
                try:
                    function(*arguments)
                    raise AssertionError("Editing accepted unavailable undo history")
                except addon.editing.EditRejected as exc:
                    assert "Undo" in str(exc)
            assert not context.scene.objects and addon.editing.active_transaction() is None
        context.preferences.edit.use_global_undo = True
        context.preferences.edit.undo_steps = max(undo_steps, 32)
        print("XRWB_UNDO_DISABLED_GUARD_PASS global_off=1 zero_steps=1 no_scene_change=1", flush=True)
        call(bpy.ops.ed.undo_push, message="XR Controller Features Empty Baseline")
        yield
        # Undo each add before testing the next: no stale object RNA survives undo.
        for kind in addon.ui.PRIMITIVES:
            call(bpy.ops.xrwb.add_primitive, kind=kind)
            yield
            obj = context.view_layer.objects.active
            assert len(context.scene.objects) == 1 and obj is not None and obj.type == "MESH", kind
            assert obj.select_get() and len(context.selected_objects) == 1, kind
            assert len(obj.data.vertices) >= 4 and len(obj.data.polygons) >= 1, kind
            if kind == "PLANE":
                assert len(obj.data.vertices) == 4 and len(obj.data.polygons) == 1
            if kind == "CUBE":
                assert len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
            call(bpy.ops.xrwb.history_undo)
            yield
            assert not context.scene.objects, f"{kind} add did not undo to the empty scene"
            assert bpy.app.timers.is_registered(controllers._hover_timer), "Undo removed hover timer"
            assert addon.runtime.session_running(context), "Undo stopped XR"
        print("XRWB_CONTROLLER_PRIMITIVES_PASS created=6 selected_meshes=6 undo=6 input=synthetic", flush=True)

        call(bpy.ops.xrwb.add_primitive, kind="CUBE")
        yield
        target_name = context.view_layer.objects.active.name
        original = context.view_layer.objects.active.matrix_world.copy()
        pivot = original.translation.copy()
        scale = float(context.window_manager.xr_session_state.hand_tracking_world_transform(context)[0])
        assert 0 < scale < 1.0e6
        settings = context.window_manager.xrwb_settings
        settings.edit_constraint = "free"
        settings.translation_snap = settings.rotation_snap_degrees = settings.scale_snap = 0.0
        start = pivot + Vector((0, 0, 0.5 * scale))
        for tool in ("move", "rotate", "scale"):
            context.window_manager.xrwb_settings.edit_tool = tool
            sample = event("grab_move", start)
            begin(controllers._begin_move, sample)
            if tool == "move":
                delta = Vector((0.08 * scale, 0, 0))
                sample.xr.controller_location = tuple(start + delta)
                expected = Matrix.Translation(delta) @ original
            elif tool == "rotate":
                rotation = Quaternion((0, 0, 1), radians(20))
                sample.xr.controller_rotation = tuple(rotation)
                expected = Matrix.Translation(pivot) @ rotation.to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ original
            else:
                from math import log2
                sample.xr.controller_location = tuple(start + Vector((
                    log2(1.25) * settings.scale_drag_distance_m * scale, 0, 0)))
                expected = Matrix.Translation(pivot) @ Matrix.Diagonal((1.25, 1.25, 1.25, 1)) @ Matrix.Translation(-pivot) @ original
            dispatch(sample, "RUNNING_MODAL")
            assert _close_matrix(context.scene.objects[target_name].matrix_world, expected), tool
            yield
            sample.value = "RELEASE"
            dispatch(sample, "FINISHED")
            assert addon.editing.active_transaction() is None and controllers._active_gesture is None
            yield
            assert _close_matrix(context.scene.objects[target_name].matrix_world, expected), f"{tool} commit"
            call(bpy.ops.xrwb.history_undo)
            yield
            assert _close_matrix(context.scene.objects[target_name].matrix_world, original), f"{tool} undo"

        context.window_manager.xrwb_settings.edit_tool = "move"
        sample = event("grab_move", start)
        begin(controllers._begin_move, sample)
        sample.xr.controller_location = tuple(start + Vector((0.05 * scale, 0, 0)))
        dispatch(sample, "RUNNING_MODAL")
        assert not _close_matrix(context.scene.objects[target_name].matrix_world, original)
        yield
        # Exercise the real left-B operator, then the neutral rearm dispatcher.
        call(bpy.ops.xrwb.controller_cancel)
        assert _close_matrix(context.scene.objects[target_name].matrix_world, original)
        sample.value = "RELEASE"
        dispatch(sample, "CANCELLED")
        assert not controllers._release_required and addon.editing.active_transaction() is None
        call(bpy.ops.xrwb.history_undo)
        yield
        assert not context.scene.objects, "Cancelled edit added undo history or left its fixture cube"
        print("XRWB_CONTROLLER_TRANSFORMS_PASS move=1 rotate=1 scale=1 undo=3 cancel=1 input=synthetic", flush=True)

        # The ray is derived from the native rig itself, not guessed headset axes.
        rig = snapshot()
        rotation = Quaternion(rig[3:7])
        origin = Vector(rig[:3]) + rotation @ Vector((0, 0, max(rig[7], 0.5)))
        offset = rotation @ Vector((0.12, 0, 0))
        sample = event("panel_grab", origin, rotation)
        begin(controllers._begin_panel_grab, sample)
        sample.xr.controller_location = tuple(origin + offset)
        dispatch(sample, "RUNNING_MODAL")
        assert (Vector(snapshot()[:3]) - Vector(rig[:3]) - offset).length < 1.0e-4
        yield
        sample.value = "RELEASE"
        dispatch(sample, "FINISHED")
        committed = snapshot()
        assert (Vector(committed[:3]) - Vector(rig[:3]) - offset).length < 1.0e-4
        sample = event("panel_grab", origin + offset, rotation)
        begin(controllers._begin_panel_grab, sample)
        sample.xr.controller_location = tuple(origin + offset * 2)
        dispatch(sample, "RUNNING_MODAL")
        yield
        sample.xr.controller_rotation = (0, 0, 0, 0)
        dispatch(sample, "CANCELLED")
        assert close_pose(snapshot(), committed), "Lost-tracking panel grab did not restore placement"
        sample.xr.controller_rotation = tuple(rotation)
        sample.value = "RELEASE"
        dispatch(sample, "CANCELLED")
        assert not controllers._release_required
        call(bpy.ops.wm.xr_editor_surface_transform, position=rig[:3], rotation=rig[3:7], width=rig[7])
        assert close_pose(snapshot(), rig)
        print("XRWB_CONTROLLER_PANEL_GRAB_PASS commit=1 cancel=1 restored=1 input=synthetic", flush=True)

        hand_module = runpy.run_path(str(Path(__file__).with_name("xr_hand_menu_features_test.py")))
        hand_result = yield from hand_module["run"](context, addon, capture_dir)
        captures.extend(hand_result["captures"])
        call(bpy.ops.xrwb.editor_surface)
        yield
        assert not context.scene.objects and addon.runtime.session_running(context)
        assert addon.editing.active_transaction() is None and controllers._active_gesture is None
        result = {"primitives": 6, "transforms": 3, "cancel": 1, "panel_grab": 1,
                  "menu_pages": len(addon.ui.COMMAND_PAGES), "ui_captures": captures, "physical_controller_verified": False}
    finally:
        # Only this explicitly owned test process; never delete an external scene.
        controllers.stop_router(context)
        context.preferences.edit.use_global_undo = undo_enabled
        context.preferences.edit.undo_steps = undo_steps
        for name, value in original_settings.items():
            setattr(context.window_manager.xrwb_settings, name, value)
        if addon.runtime.session_running(context):
            call(bpy.ops.wm.xr_editor_surface_transform, position=panel_pose[:3],
                 rotation=panel_pose[3:7], width=panel_pose[7])
    print(f"XRWB_CONTROLLER_FEATURES_PASS primitives=6 transforms=3 cancel=1 panel_grab=1 menu_pages={len(addon.ui.COMMAND_PAGES)} input=synthetic physical_controller_verified=0", flush=True)
    return result
