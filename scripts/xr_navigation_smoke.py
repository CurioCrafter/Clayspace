"""Factory-profile check of movement: world grab with one or both hands (move, turn,
resize, 1x click), hand hand-over, Smooth / Teleport / Fly bindings on every profile,
Go to Selection and Reset View.

Real Blender and the real add-on with a stand-in XR session state; no headset.
Run in a separate background process with BLENDER_USER_CONFIG set to an empty folder.
"""
import math
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import controllers, navigation, onboarding  # noqa: E402

RIGHT, LEFT = controllers.RIGHT_HAND, controllers.LEFT_HAND


def close(a, b, tolerance=1e-6):
    return (Vector(a) - Vector(b)).length < tolerance


def state_at(location=(0, 0, 0), rotation=(1, 0, 0, 0), scale=1.0):
    return NS(navigation_location=Vector(location), navigation_rotation=Quaternion(rotation),
              navigation_scale=scale, viewer_pose_location=Vector((0, 0, 1.6)))


def check_solver():
    # One hand: the grabbed world point returns under the hand.
    rotation, scale = Quaternion((0, 0, 1), 0.3), 2.0
    location = navigation.solve_one((1, 2, 3), (0.5, 0.1, 1.2), rotation, scale)
    assert close(navigation.to_world((0.5, 0.1, 1.2), location, rotation, scale), (1, 2, 3))
    # Two hands pulled from 1 m to 2 m apart: the world halves (you shrink), anchors stay put.
    anchors = [(-0.5, 0, 1), (0.5, 0, 1)]
    hands = [(-1.0, 0, 1), (1.0, 0, 1)]
    location, rotation, scale = navigation.solve_two(anchors, hands, Quaternion(), 1.0)
    assert abs(scale - 0.5) < 1e-9 and rotation.angle < 1e-6
    for anchor, hand in zip(anchors, hands):
        assert close(navigation.to_world(hand, location, rotation, scale), anchor)
    # Turning the hands 90 degrees about the vertical turns the world; it never tilts.
    tilted = [(0, -0.5, 1.3), (0, 0.5, 0.7)]
    location, rotation, scale = navigation.solve_two(anchors, tilted, Quaternion(), 1.0)
    axis_z = abs((rotation @ Vector((0, 0, 1))).z)
    assert axis_z > 1 - 1e-6, "Only yaw: the horizon stays level"
    assert abs(abs(math.degrees(rotation.angle)) - 90.0) < 1e-4
    # Close to 1x clicks to exactly 1x; limits hold.
    _l, _r, scale = navigation.solve_two(anchors, [(-0.51, 0, 1), (0.51, 0, 1)], Quaternion(), 1.0)
    assert scale == 1.0
    _l, _r, scale = navigation.solve_two(anchors, [(-500, 0, 1), (500, 0, 1)], Quaternion(), 1.0)
    assert scale == navigation.viewer_scale.MIN_SCALE
    assert navigation.solve_two(anchors, [(0, 0, 1), (0, 0, 1)], Quaternion(), 1.0) is None, "Hands together: hold"
    return 1


def xr_event(action, hand, location, value="PRESS"):
    return NS(type="XR_ACTION", value=value, xr=NS(action_set=controllers.ACTION_SET_NAME, action=action,
                                                   user_path=hand, controller_location=location,
                                                   controller_rotation=(1, 0, 0, 0)))


def check_two_hand_routing():
    state = state_at()
    context = NS(window_manager=NS(xr_session_state=state, xrwb_settings=bpy.context.window_manager.xrwb_settings))
    controllers._active_gesture = None
    try:
        left = xr_event("world_grab", LEFT, (-0.5, 0.0, 1.0))
        ok, message = controllers._start_or_join_world(context, left)
        assert ok and controllers._active_gesture.kind == "world", message
        # One hand: slide the world 30 cm.
        left.xr.controller_location = (-0.2, 0.0, 1.0)
        assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
        assert close(state.navigation_location, (-0.3, 0.0, 0.0)), state.navigation_location
        # Hands now report world positions under the new navigation: the left hand is back at -0.5.
        left.xr.controller_location = (-0.5, 0.0, 1.0)
        assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
        assert close(state.navigation_location, (-0.3, 0.0, 0.0)), "Holding still keeps the world still"
        right = xr_event("grab_move", RIGHT, (0.5, 0.0, 1.0))
        ok, message = controllers._start_or_join_world(context, right)
        assert ok and "Both hands" in message, message
        gesture = controllers._active_gesture
        assert gesture.second_key == ("grab_move", RIGHT)
        # Pull apart: the right hand moves 1 m further out (second-hand event), then the frame solves.
        right.xr.controller_location = (1.5, 0.0, 1.0)
        assert controllers.dispatch_xr_event(context, right) == {"RUNNING_MODAL"}
        assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
        assert abs(state.navigation_scale - 0.5) < 1e-9, state.navigation_scale
        assert gesture.readout.startswith("You ×0.50")
        assert gesture.navigation == controllers._navigation_signature(context), "Our own moves never cancel"
        # Releasing the first hand hands the world to the second.
        left.value = "RELEASE"
        assert controllers.dispatch_xr_event(context, left) == {"FINISHED"}
        gesture = controllers._active_gesture
        assert gesture is not None and gesture.user_path == RIGHT and gesture.second_key is None
        right.value = "RELEASE"
        assert controllers.dispatch_xr_event(context, right) == {"FINISHED"}
        assert controllers._active_gesture is None
    finally:
        controllers._active_gesture = None
    return 1


def check_grip_on_nothing():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    calls = []
    real = controllers._start_or_join_world
    controllers._start_or_join_world = lambda _c, _e: calls.append(1) or (True, "world")
    try:
        empty = xr_event("grab_move", RIGHT, (10.0, 10.0, 5.0))  # Pointing down at nothing.
        assert controllers._begin_move(bpy.context, empty, grip=True) == (True, "world")
        on_cube = xr_event("grab_move", RIGHT, (0.0, 0.0, 5.0))
        ok, _message = controllers._begin_move(bpy.context, on_cube, grip=True)
        assert ok and controllers._active_gesture.kind == "move", "On an object the grip still picks it up"
        controllers._cancel_active(bpy.context, "test")
        controllers._release_required.clear()
        onboarding.set_pref("grip_empty", "SELECTION")
        assert controllers._begin_move(bpy.context, empty, grip=True)[0]
        assert controllers._active_gesture.kind == "move", "Setting: empty grip grabs the selection"
        controllers._cancel_active(bpy.context, "test")
        controllers._release_required.clear()
    finally:
        controllers._start_or_join_world = real
        onboarding.set_pref("grip_empty", "WORLD")
        controllers._active_gesture = None
    assert calls == [1]
    return 1


class _Collection(list):
    def new(self, *args):
        item = _Item(args[0]) if len(args) == 2 and isinstance(args[1], bool) else _Binding(args[0])
        self.append(item)
        return item


class _Paths(list):
    def new(self, path):
        self.append(NS(path=path))


class _Binding:
    def __init__(self, name):
        self.name, self.component_paths = name, _Paths()


class _Bindings(_Collection):
    def new(self, name, _replace):
        binding = _Binding(name)
        self.append(binding)
        return binding


class _Item:
    def __init__(self, name):
        self.name, self.user_paths, self.bindings, self.op_properties = name, _Paths(), _Bindings(), NS()


class _Maps(_Collection):
    def new(self, _state, name, _replace):
        actionmap = NS(name=name, actionmap_items=_Collection())
        self.append(actionmap)
        return actionmap


def check_locomotion_bindings():
    for family in ("INDEX", "QUEST", "AUTO"):
        for locomotion in ("SMOOTH", "TELEPORT"):
            for fly in (False, True):
                actionmap = controllers.build_actionmap(NS(actionmaps=_Maps()), family,
                                                        {"locomotion": locomotion, "fly": fly})
                items = {item.name: item for item in actionmap.actionmap_items}
                seen = set()
                for item in actionmap.actionmap_items:
                    for binding in item.bindings:
                        for hand, component in zip(item.user_paths, binding.component_paths):
                            key = (binding.profile, hand.path, component.path, binding.axis0_region)
                            assert key not in seen, (family, locomotion, item.name, key)
                            seen.add(key)
                if locomotion == "TELEPORT":
                    assert "move_forward" not in items and "move_back" not in items
                    assert items["teleport_stick"].op == "wm.xr_navigation_teleport", "Stick teleport on every profile"
                    assert items["teleport_stick"].user_paths[0].path == LEFT
                else:
                    assert "teleport_stick" not in items
                    assert items["move_forward"].op_properties.lock_location_z is (not fly)
                assert "turn_left" in items and "world_grab" in items
    assert controllers.slot_action("RIGHT_A") == "HAND_MENU"
    assert {"GO_TO", "RESET_VIEW"} <= {key for key, _l, _d in controllers.SLOT_ACTIONS}
    return 1


def check_go_to_and_reset():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2, location=(10, 5, 1))
    cube = bpy.context.object
    state = state_at()
    context = NS(selected_objects=[cube], window_manager=NS(xr_session_state=state))
    real = xr_workbench.runtime.session_running
    xr_workbench.runtime.session_running = lambda _c: True
    try:
        message = navigation.go_to_selection(context)
        assert "Cube" in message
        head = Vector(state.viewer_pose_location) + Vector(state.navigation_location)
        flat = Vector((10, 5, 0)) - Vector((head.x, head.y, 0))
        distance = flat.length
        assert 0.59 < distance < 4.0, distance
        assert abs(head.z - 1.6) < 1e-6, "You stay at standing height for nearby things"
        state.navigation_scale, state.navigation_rotation = 3.0, Quaternion((0, 0, 1), 1.0)
        navigation.reset_view(context)
        assert state.navigation_scale == 1.0 and state.navigation_rotation.angle < 1e-9
        assert close(state.navigation_location, (0, 0, 0))
    finally:
        xr_workbench.runtime.session_running = real
    context.selected_objects = []
    xr_workbench.runtime.session_running = lambda _c: True
    try:
        navigation.go_to_selection(context)
        raise AssertionError("Going nowhere must be refused")
    except xr_workbench.editing.EditRejected:
        pass
    finally:
        xr_workbench.runtime.session_running = real
    return 1


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    xr_workbench.register()
    try:
        results = {name: check() for name, check in (
            ("solver", check_solver), ("two_hand", check_two_hand_routing), ("grip_empty", check_grip_on_nothing),
            ("locomotion", check_locomotion_bindings), ("go_to_reset", check_go_to_and_reset))}
    finally:
        controllers._active_gesture = None
        xr_workbench.unregister()
    print("XR_NAVIGATION_SMOKE_PASS " + " ".join(f"{k}={v}" for k, v in results.items())
          + " input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
