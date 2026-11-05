"""Headless, synthetic validation of XR Workbench controller bindings.

This test creates no OpenXR session and has no physical-device claim.  It
checks the Index binding contract and controller route matching with fake RNA
collections, so it is safe for the isolated Blender profile.
"""

from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Quaternion


ROOT = Path(__file__).resolve().parents[1]
expected_install = os.environ.get("XRWB_EXPECTED_INSTALL")
import_root = Path(expected_install).resolve().parent if expected_install else ROOT
if str(import_root) not in sys.path:
    sys.path.insert(0, str(import_root))

import xr_workbench.controllers as controllers  # noqa: E402
import xr_workbench  # noqa: E402

xr_workbench.register()


assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0), bpy.app.version_string
if expected_install:
    expected_module = Path(expected_install).resolve() / "controllers.py"
    assert Path(controllers.__file__).resolve() == expected_module
else:
    expected_source = os.environ.get("XRWB_EXPECTED_SOURCE")
    if expected_source:
        assert (
            Path(expected_source).resolve()
            in Path(controllers.__file__).resolve().parents
        )


class Collection(list):
    def new(self, *args):
        if len(args) == 2 and isinstance(args[1], bool):
            item = ActionItem(args[0])
        else:
            item = Binding(args[0])
        self.append(item)
        return item


class ComponentPaths(list):
    def new(self, path):
        self.append(path)


class Binding:
    def __init__(self, name):
        self.name = name
        self.component_paths = ComponentPaths()


class Bindings(Collection):
    def new(self, name, _replace):
        binding = Binding(name)
        self.append(binding)
        return binding


class ActionItem:
    def __init__(self, name):
        self.name = name
        self.user_paths = ComponentPaths()
        self.bindings = Bindings()
        self.op_properties = SimpleNamespace()


class ActionMaps(Collection):
    def new(self, _state, name, _replace):
        actionmap = SimpleNamespace(name=name, actionmap_items=Collection())
        self.append(actionmap)
        return actionmap


state = SimpleNamespace(actionmaps=ActionMaps())
actionmap = controllers.build_actionmap(state)
names = [item.name for item in actionmap.actionmap_items]
assert names == [
    "controller_grip",
    "controller_aim",
    "haptic",
    "pointer",
    "grab_move",
    "cancel",
    "command_menu",
    "recall",
    "panel_grab",
    "undo",
    "add_menu",
    "context_pointer",
    "world_grab",
    "teleport",
    "move_forward",
    "move_back",
    "move_left",
    "move_right",
    "turn_left",
    "turn_right",
    "scroll_positive",
    "scroll_negative",
], names
used_bindings = set()
for item in actionmap.actionmap_items:
    for identifier in ("type", "op_mode"):
        if hasattr(item, identifier):
            assert (
                getattr(item, identifier)
                in bpy.types.XrActionMapItem.bl_rna.properties[identifier].enum_items
            )
    for binding in item.bindings:
        assert (
            binding.axis0_region
            in bpy.types.XrActionMapBinding.bl_rna.properties["axis0_region"].enum_items
        )
    if hasattr(item, "op"):
        module, name = item.op.split(".")
        properties = getattr(getattr(bpy.ops, module), name).get_rna_type().properties
        for name, value in vars(item.op_properties).items():
            assert name in properties, (item.name, name)
            prop = properties[name]
            if prop.type == "ENUM":
                assert value in prop.enum_items, (item.name, name, value)
    for hand, component in zip(item.user_paths, item.bindings[0].component_paths):
        key = (hand, component, item.bindings[0].axis0_region)
        assert key not in used_bindings, key
        used_bindings.add(key)
pointer = next(item for item in actionmap.actionmap_items if item.name == "pointer")
assert pointer.op == "xrwb.controller_pointer"
assert pointer.bindings[0].profile == controllers.INDEX_PROFILE
assert pointer.bindings[0].component_paths == ["/input/trigger/value", "/input/trigger/value"]
assert pointer.user_paths == [controllers.LEFT_HAND, controllers.RIGHT_HAND]
cancel = next(item for item in actionmap.actionmap_items if item.name == "cancel")
assert cancel.op_mode == "PRESS"
assert cancel.type == "FLOAT"  # Blender's RNA maps click paths to float actions.
recall = next(item for item in actionmap.actionmap_items if item.name == "recall")
assert recall.type == "FLOAT"
summary = controllers.action_spec_summary()
assert summary["action_set"] == controllers.ACTION_SET_NAME
assert summary["total_actions"] == len(names) == 22
assert summary["modal_actions"] == sum(
    getattr(item, "op_mode", None) == "MODAL" for item in actionmap.actionmap_items
)
route_event = SimpleNamespace(
    type="XR_ACTION",
    xr=SimpleNamespace(
        action_set=controllers.ACTION_SET_NAME,
        action="pointer",
        user_path=controllers.RIGHT_HAND,
    ),
)
assert controllers._route_matches(route_event, "pointer", controllers.RIGHT_HAND)
assert not controllers._route_matches(route_event, "grab_move", controllers.RIGHT_HAND)
assert controllers.capture_status().startswith("unavailable:")
source = Path(controllers.__file__).read_text(encoding="utf-8")
assert "class XRWB_OT_controller_hover" not in source  # Undo must not free hover state.


native_calls = []
original_native_call = controllers._native_call


def fake_native_call(operator_name, **properties):
    native_calls.append((operator_name, properties))
    return True, "accepted: synthetic native event"


controllers._native_call = fake_native_call
pointer_event = SimpleNamespace(
    type="XR_ACTION",
    value="PRESS",
    xr=SimpleNamespace(
        action_set=controllers.ACTION_SET_NAME,
        action="pointer",
        user_path=controllers.RIGHT_HAND,
        controller_location=(1.0, 2.0, 3.0),
        controller_rotation=(1.0, 0.0, 0.0, 0.0),
    ),
)
ok, message = controllers._begin_pointer(SimpleNamespace(), pointer_event)
assert ok, message
controllers.stop_router(SimpleNamespace())
assert [call[0] for call in native_calls] == [
    "xr_editor_surface_event",
    "xr_editor_surface_event",
    "xr_editor_surface_event",
    "xr_editor_surface_event",
], native_calls
assert native_calls[0][1]["type"] == "MOUSEMOVE"
assert native_calls[1][1]["value"] == "PRESS"
assert native_calls[2][1]["type"] == "ESC"
assert native_calls[3][1]["value"] == "RELEASE"
assert native_calls[3][1]["use_ray"] is False
pointer_event.value = "RELEASE"
assert controllers.dispatch_xr_event(SimpleNamespace(), pointer_event) == {"CANCELLED"}
assert not controllers._release_required

# Native tracking-loss contract is an all-zero quaternion; it must never click.
for rotation, location in (
    ((0.0, 0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
    ((float("nan"), 0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
    ((1.0e30, 0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
    ((1.0, 0.0, 0.0, 0.0), (float("inf"), 2.0, 3.0)),
):
    pointer_event.xr.controller_rotation = (1.0, 0.0, 0.0, 0.0)
    pointer_event.xr.controller_location = (1.0, 2.0, 3.0)
    assert controllers._begin_pointer(SimpleNamespace(), pointer_event)[0]
    pointer_event.value = "RELEASE"
    pointer_event.xr.controller_rotation = rotation
    pointer_event.xr.controller_location = location
    assert controllers.dispatch_xr_event(SimpleNamespace(), pointer_event) == {
        "CANCELLED"
    }
    assert native_calls[-1][1]["use_ray"] is False
    assert controllers._active_gesture is None
    # Invalid synthetic release cannot rearm; held tracking recovery is ignored.
    assert controllers._release_required
    pointer_event.xr.controller_rotation = (1.0, 0.0, 0.0, 0.0)
    pointer_event.xr.controller_location = (1.0, 2.0, 3.0)
    pointer_event.value = "PRESS"
    call_count = len(native_calls)
    assert not controllers._begin_pointer(SimpleNamespace(), pointer_event)[0]
    assert controllers.dispatch_xr_event(SimpleNamespace(), pointer_event) == {
        "RUNNING_MODAL"
    }
    assert len(native_calls) == call_count
    pointer_event.value = "RELEASE"
    assert controllers.dispatch_xr_event(SimpleNamespace(), pointer_event) == {
        "CANCELLED"
    }
    assert not controllers._release_required

pointer_event.xr.controller_rotation = (1.0, 0.0, 0.0, 0.0)
pointer_event.xr.controller_location = (0.0, 0.0, 5.0)
pointer_event.value = "PRESS"
original_view_override = xr_workbench.ui._view3d_override
xr_workbench.ui._view3d_override = lambda _ctx: nullcontext()
assert controllers._begin_pointer(SimpleNamespace(), pointer_event, "RIGHTMOUSE")[0]
assert controllers._active_gesture.kind == "menu_open"
assert native_calls[-1] == ("xr_hand_menu", {
    "visible": True, "panel": "", "menu": "VIEW3D_MT_object_context_menu",
    "width": 0.48, "controller": 1,
})
call_count = len(native_calls)
pointer_event.value = "RELEASE"
assert controllers.dispatch_xr_event(SimpleNamespace(), pointer_event) == {"FINISHED"}
assert len(native_calls) == call_count  # Never inject a desktop right click.

# The VR menu owns input even when a ray misses or the menu closes before release.
menu_info = [1, 900, 600, -1, 0, 10]
menu_context = SimpleNamespace(mode="OBJECT", window_manager=SimpleNamespace(
    xrwb_settings=SimpleNamespace(hand_menu_width=0.6),
    xr_session_state=SimpleNamespace(
        hand_menu_info=lambda _ctx: menu_info,
        navigation_location=(0, 0, 0), navigation_rotation=(1, 0, 0, 0),
        navigation_scale=1.0,
    ),
))
assert not controllers._begin_selection(menu_context, pointer_event)[0]
assert not controllers._begin_move(menu_context, pointer_event)[0]
assert not controllers._begin_panel_grab(menu_context, pointer_event)[0]
original_selection = controllers._begin_selection
def forbidden_selection(*_args):
    raise AssertionError("A hand-menu miss must not select a scene object")
controllers._begin_selection = forbidden_selection
controllers._native_call = lambda name, **props: (
    native_calls.append((name, props)) or (False, "Menu ray miss")
)
pointer_event.value = "PRESS"
assert not controllers._begin_pointer(menu_context, pointer_event)[0]
assert native_calls[-1][1]["target"] == "MENU"
controllers._begin_selection = original_selection
controllers._native_call = fake_native_call
assert controllers._begin_pointer(menu_context, pointer_event)[0]
assert controllers._active_gesture.pointer_target == "MENU"
menu_info[0] = 0
pointer_event.value = "RELEASE"
assert controllers.dispatch_xr_event(menu_context, pointer_event) == {"FINISHED"}
assert native_calls[-1][1]["target"] == "MENU"
assert native_calls[-1][1]["value"] == "RELEASE"
menu_info[0] = 1
pointer_event.value = "PRESS"
assert controllers._begin_pointer(menu_context, pointer_event)[0]
controllers._cancel_active(menu_context, "synthetic tracking loss")
assert all(call[1]["target"] == "MENU" for call in native_calls[-2:])
assert native_calls[-1][1]["use_ray"] is False
pointer_event.value = "RELEASE"
assert controllers.dispatch_xr_event(menu_context, pointer_event) == {"CANCELLED"}
assert not controllers._release_required

# Right A toggles only the native hand host, never desktop popup/surface APIs.
native_calls.clear()
menu_info[0] = 0
operator_stub = SimpleNamespace(report=lambda *_args: None, controller=1, page="")
assert xr_workbench.ui.XRWB_OT_command_menu.execute(operator_stub, menu_context) == {"FINISHED"}
assert native_calls == [("xr_hand_menu", {
    "visible": True, "panel": "XRWB_PT_command_deck", "width": 0.6, "controller": 1,
})]
menu_info[0] = 1
assert xr_workbench.ui.XRWB_OT_command_menu.execute(operator_stub, menu_context) == {"FINISHED"}
assert native_calls[-1][0] == "xr_hand_menu" and not native_calls[-1][1]["visible"]
controllers._native_call = lambda *_args, **_kwargs: (False, "API unavailable")
assert xr_workbench.ui.XRWB_OT_command_menu.execute(operator_stub, menu_context) == {"CANCELLED"}
controllers._native_call = fake_native_call
assert controllers.XRWB_OT_controller_cancel.execute(operator_stub, menu_context) == {"FINISHED"}
assert native_calls[-1] == ("xr_hand_menu", {"visible": False})
xr_workbench.ui._view3d_override = original_view_override

# Layout/navigation changes must cancel, not reuse an old editor ray.
nav = SimpleNamespace(
    navigation_location=(0, 0, 0),
    navigation_rotation=(1, 0, 0, 0),
    navigation_scale=1.0,
)
nav_context = SimpleNamespace(window_manager=SimpleNamespace(xr_session_state=nav))
assert controllers._begin_pointer(nav_context, pointer_event)[0]
nav.navigation_location = (1, 0, 0)
assert controllers.dispatch_xr_event(nav_context, pointer_event) == {"CANCELLED"}
assert native_calls[-1][1]["use_ray"] is False
assert controllers.dispatch_xr_event(nav_context, pointer_event) == {"CANCELLED"}

# Task cards share their existing native generation/capture implementation.
task_calls = []
original_task_event = controllers._task_event
controllers._task_event = lambda _ctx, phase, generation, ray: (
    task_calls.append((phase, generation, ray)) or True
)
nav.task_card_info = lambda _ctx: (True, 3, 3)
assert controllers._begin_pointer(nav_context, pointer_event)[0]
assert controllers._active_gesture.kind == "task_card"
assert controllers.dispatch_xr_event(nav_context, pointer_event) == {"FINISHED"}
assert [call[0] for call in task_calls] == ["MOVE", "PRESS", "RELEASE"]
assert controllers._begin_pointer(nav_context, pointer_event)[0]
pointer_event.xr.controller_rotation = (0, 0, 0, 0)
assert controllers.dispatch_xr_event(nav_context, pointer_event) == {"CANCELLED"}
assert task_calls[-1] == ("CANCEL", 3, None)
pointer_event.xr.controller_rotation = (1, 0, 0, 0)
assert controllers.dispatch_xr_event(nav_context, pointer_event) == {"CANCELLED"}
controllers._task_event = original_task_event

# Exercise operator lifecycle, not just the dispatcher: invalid RELEASE keeps
# the cancelled modal installed, and recovered held PRESS cannot recapture.
modal_calls = []
fake_operator = SimpleNamespace(
    _action="pointer",
    _user_path=controllers.RIGHT_HAND,
    _start_timer=lambda _ctx: modal_calls.append("timer_add"),
    _remove_timer=lambda _ctx: modal_calls.append("timer_remove"),
    report=lambda *_args: None,
)
modal_context = SimpleNamespace(
    window_manager=SimpleNamespace(
        xr_session_state=None,
        modal_handler_add=lambda _op: modal_calls.append("handler_add"),
    )
)
pointer_event.xr.controller_rotation = (0, 0, 0, 0)
pointer_event.value = "PRESS"
base_operator = controllers._XRWB_OT_modal_gesture
assert base_operator._invoke_gesture(
    fake_operator, modal_context, pointer_event, controllers._begin_pointer
) == {"RUNNING_MODAL"}
assert modal_calls == ["timer_add", "handler_add"]
pointer_event.value = "RELEASE"
assert base_operator.modal(fake_operator, modal_context, pointer_event) == {
    "RUNNING_MODAL"
}
pointer_event.xr.controller_rotation = (1, 0, 0, 0)
pointer_event.value = "PRESS"
assert base_operator.modal(fake_operator, modal_context, pointer_event) == {
    "RUNNING_MODAL"
}
assert controllers._active_gesture is None
pointer_event.value = "RELEASE"
assert base_operator.modal(fake_operator, modal_context, pointer_event) == {"CANCELLED"}
assert modal_calls[-1] == "timer_remove"
assert not controllers._release_required

# Left B is independently dispatched by native XR (not swallowed by BLOCKING).
# Its execute path cancels the old capture; that old modal then drains release.
pointer_event.value = "PRESS"
assert controllers._begin_pointer(modal_context, pointer_event)[0]
assert controllers.XRWB_OT_controller_cancel.execute(
    SimpleNamespace(), modal_context
) == {"FINISHED"}
assert base_operator.modal(fake_operator, modal_context, pointer_event) == {
    "RUNNING_MODAL"
}
pointer_event.value = "RELEASE"
assert base_operator.modal(fake_operator, modal_context, pointer_event) == {"CANCELLED"}
assert not controllers._release_required

# Scroll is a wheel PRESS, never an unsupported click/release value.
assert controllers.XRWB_OT_controller_scroll.invoke(
    SimpleNamespace(direction="WHEELUPMOUSE"), modal_context, pointer_event
) == {"FINISHED"}
assert native_calls[-1][1]["type"] == "WHEELUPMOUSE"
assert native_calls[-1][1]["value"] == "PRESS"
assert native_calls[-1][1]["use_ray"]
controllers._native_call = original_native_call

# Panel grabbing uses the same tracked-release/cancel lifecycle, never a UI click.
controllers._native_call = fake_native_call
panel_state = SimpleNamespace(
    navigation_location=(0, 0, 0), navigation_rotation=(1, 0, 0, 0), navigation_scale=1,
    editor_surface_hit=lambda *_args, **_kw: (1, 0, 0.5, 0.5, 1),
    editor_surface_pose=lambda _ctx: (0, 0, 0, 1, 0, 0, 0, 1.6, 0.9),
)
panel_context = SimpleNamespace(window_manager=SimpleNamespace(xr_session_state=panel_state))
pointer_event.xr.action = "panel_grab"
pointer_event.xr.controller_location = (0, 0, 1)
pointer_event.value = "PRESS"
assert controllers._begin_panel_grab(panel_context, pointer_event)[0]
pointer_event.xr.controller_location = (0.25, 0, 1)
assert controllers.dispatch_xr_event(panel_context, pointer_event) == {"RUNNING_MODAL"}
assert native_calls[-1] == ("xr_editor_surface_transform", {"position": (0.25, 0, 0), "rotation": (1, 0, 0, 0)})
pointer_event.xr.controller_rotation = (0, 0, 0, 0)
assert controllers.dispatch_xr_event(panel_context, pointer_event) == {"CANCELLED"}
assert native_calls[-1][1]["position"] == (0, 0, 0)
pointer_event.xr.controller_rotation = (1, 0, 0, 0)
pointer_event.value = "RELEASE"
assert controllers.dispatch_xr_event(panel_context, pointer_event) == {"CANCELLED"}
assert not controllers._release_required
controllers._native_call = original_native_call

# Idle pointer motion is strictly tracked, button-free, and capture-aware.
# Fake RNA includes the native active result independently of a plausible pose.
hover_map = controllers.build_actionmap(SimpleNamespace(actionmaps=ActionMaps()))
for item in hover_map.actionmap_items:
    item.user_paths[:] = [SimpleNamespace(path=path) for path in item.user_paths]
hover_values = {}
hover_indices = []
hover_state = SimpleNamespace(
    actionmaps=[hover_map],
    navigation_location=(0, 0, 0), navigation_rotation=(1, 0, 0, 0), navigation_scale=1,
    controller_aim_active_get=lambda _ctx, index: hover_indices.append(index) or True,
    controller_aim_location_get=lambda _ctx, index: (1 + 10 * index, 2, 3),
    controller_aim_rotation_get=lambda _ctx, _index: (1, 0, 0, 0),
    action_state_get=lambda _ctx, _set, name, _path: (hover_values.get(name, 0), 0),
)
hover_context = SimpleNamespace(window_manager=SimpleNamespace(xr_session_state=hover_state))
# Both hands are read (the one pointing at a hand menu hovers); with no menu
# the last trigger hand's ray is used.
assert controllers._hover_ray(hover_context) == ((11, 2, 3), (0, 0, -1))
assert set(hover_indices) == {0, 1}
hover_items = {item.name: item for item in hover_map.actionmap_items}
controllers._pointer_hand = controllers.LEFT_HAND
assert controllers._hover_ray(hover_context) == ((1, 2, 3), (0, 0, -1))
controllers._pointer_hand = controllers.RIGHT_HAND
active_get = hover_state.controller_aim_active_get
hover_state.controller_aim_active_get = lambda *_args: False
assert controllers._hover_ray(hover_context) is None
del hover_state.controller_aim_active_get
assert controllers._hover_ray(hover_context) is None  # Old native binary fails closed.
hover_state.controller_aim_active_get = active_get
for rotation in ((0, 0, 0, 0), (float("nan"), 0, 0, 0)):
    hover_state.controller_aim_rotation_get = lambda *_args: rotation
    assert controllers._hover_ray(hover_context) is None
hover_state.controller_aim_rotation_get = lambda *_args: (1, 0, 0, 0)
for name in ("pointer", "grab_move", "panel_grab", "context_pointer", "world_grab",
             "teleport", "move_forward", "move_right", "turn_right"):
    hover_values[name] = -0.9
    assert controllers._hover_ray(hover_context) is None, name
    hover_values.clear()
controllers._release_required.add(("pointer", controllers.RIGHT_HAND))
assert controllers._hover_ray(hover_context) is None
controllers._release_required.clear()
controllers._active_gesture = controllers._ActiveGesture("move", "grab_move", controllers.RIGHT_HAND)
assert controllers._hover_ray(hover_context) is None
controllers._active_gesture = None
original_transaction = xr_workbench.editing.active_transaction
xr_workbench.editing.active_transaction = lambda: object()
assert controllers._hover_ray(hover_context) is None
assert not controllers._begin_panel_grab(panel_context, pointer_event)[0]
xr_workbench.editing.active_transaction = original_transaction
original_hand_router = xr_workbench.hands._router_operator
xr_workbench.hands._router_operator = object()
assert controllers._hover_ray(hover_context) is None
xr_workbench.hands._router_operator = original_hand_router

controllers._native_call = fake_native_call
native_calls.clear()
hover_runtime = controllers._HoverState(0.0)
surface_info = [1, 1024, 768, -1, 0, 1]
hover_state.editor_surface_info = lambda _ctx: surface_info
controllers._hover_tick(hover_context, hover_runtime)
assert not native_calls  # Prime navigation/frame, never reuse a startup pose.
surface_info[5] += 1
controllers._hover_tick(hover_context, hover_runtime)
assert len(native_calls) == 1
assert native_calls[0][1]["type"] == "MOUSEMOVE"
assert native_calls[0][1]["value"] == "NOTHING"
controllers._hover_tick(hover_context, hover_runtime)
assert len(native_calls) == 1  # No new native draw => no stale-pose hover.
surface_info[5] += 1
surface_info[4] = 1
controllers._hover_tick(hover_context, hover_runtime)
assert len(native_calls) == 1  # Native capture from any source wins.
surface_info[4] = 0
surface_info[5] += 1
hover_state.navigation_location = (1, 0, 0)
controllers._hover_tick(hover_context, hover_runtime)
assert len(native_calls) == 1

# Hand-menu hover requires its own draw counter, not a visible desktop surface.
native_calls.clear()
hover_menu_info = [1, 900, 600, -1, 0, 20]
hover_state.hand_menu_info = lambda _ctx: hover_menu_info
def forbidden_editor_info(*_args):
    raise AssertionError("Hand hover must not depend on the desktop surface")
original_editor_info = hover_state.editor_surface_info
hover_state.editor_surface_info = forbidden_editor_info
controllers._hover_tick(hover_context, hover_runtime)
assert not native_calls  # Switching hosts primes the independent draw counter.
hover_menu_info[5] += 1
controllers._hover_tick(hover_context, hover_runtime)
assert len(native_calls) == 1 and native_calls[0][1]["target"] == "MENU"
assert native_calls[0][1]["type"] == "MOUSEMOVE"
hover_menu_info[4] = 1
hover_menu_info[5] += 1
controllers._hover_tick(hover_context, hover_runtime)
assert len(native_calls) == 1  # Capture still wins over passive hover.
hover_state.editor_surface_info = original_editor_info
del hover_state.hand_menu_info
controllers._native_call = original_native_call

# App-timer ownership contains no RNA and stops/restarts idempotently. Use the
# real timer registry without opening a window/session in this headless test.
lifecycle = []
hover_context.window = object()
original_bpy = controllers.bpy
controllers.bpy = SimpleNamespace(app=SimpleNamespace(background=False, timers=bpy.app.timers), context=bpy.context)
original_tick = controllers._hover_tick
controllers._hover_tick = lambda _ctx, _state: lifecycle.append("tick")
original_running = xr_workbench.runtime.session_running
xr_workbench.runtime.session_running = lambda _ctx: False
assert controllers.start_router(hover_context)[0]
timer_state = controllers._hover_router
assert bpy.app.timers.is_registered(controllers._hover_timer)
assert controllers.start_router(hover_context)[0] and controllers._hover_router is timer_state
assert controllers._hover_timer() == 0.05 and not lifecycle
xr_workbench.runtime.session_running = lambda _ctx: True
assert controllers._hover_timer() == 0.05
assert lifecycle == ["tick"]
xr_workbench.runtime.session_running = lambda _ctx: False
assert controllers._hover_timer() is None and controllers._hover_router is None
assert controllers.start_router(hover_context)[0]
controllers.stop_router(hover_context)
controllers.stop_router(hover_context)
assert not bpy.app.timers.is_registered(controllers._hover_timer)
assert controllers._hover_router is None and controllers._hover_timer() is None
controllers.bpy = original_bpy
controllers._hover_tick = original_tick
xr_workbench.runtime.session_running = original_running

# Real Blender scene adapter: default Select + squeeze means Move, cancellation
# restores the original matrix, final release applies its final pose before commit.
context = bpy.context
cube = context.active_object
original_matrix = cube.matrix_world.copy()
context.window_manager.xrwb_settings.edit_tool = "select"
pointer_event.xr.controller_rotation = (1, 0, 0, 0)
pointer_event.xr.action = "grab_move"
pointer_event.xr.controller_location = (0, 0, 5)
pointer_event.value = "PRESS"
assert controllers._begin_move(context, pointer_event)[0]
pointer_event.xr.controller_location = (0.5, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, pointer_event) == {"RUNNING_MODAL"}
assert abs(cube.matrix_world.translation.x - 0.5) < 1e-6
pointer_event.value = "RELEASE"
pointer_event.xr.controller_rotation = (0.0, 0.0, 0.0, 0.0)
assert controllers.dispatch_xr_event(context, pointer_event) == {"CANCELLED"}
assert cube.matrix_world == original_matrix
assert xr_workbench.editing.active_transaction() is None
pointer_event.xr.controller_rotation = (1.0, 0.0, 0.0, 0.0)
assert controllers.dispatch_xr_event(context, pointer_event) == {"CANCELLED"}
pointer_event.xr.controller_location = (0.0, 0.0, 5.0)
pointer_event.value = "PRESS"
assert controllers._begin_move(context, pointer_event)[0]
pointer_event.value = "RELEASE"
pointer_event.xr.controller_location = (0.25, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, pointer_event) == {"FINISHED"}
assert abs(cube.matrix_world.translation.x - 0.25) < 1e-6
assert xr_workbench.editing.active_transaction() is None

# Scene trigger uses the shared frozen-target/undo selection adapter.
scale_original = cube.matrix_world.copy()
context.window_manager.xrwb_settings.edit_tool = "scale"
for depth, distance in ((2.0, .2), (20.0, .2), (2.0, .03)):
    context.window_manager.xrwb_settings.scale_drag_distance_m = distance
    pointer_event.value = "PRESS"
    pointer_event.xr.controller_location = (0, 0, depth)
    assert controllers._begin_move(context, pointer_event)[0]
    pointer_event.xr.controller_location = (context.window_manager.xrwb_settings.scale_drag_distance_m, 0, depth)
    assert controllers.dispatch_xr_event(context, pointer_event) == {"RUNNING_MODAL"}
    assert all(abs(a - b * 2) < 1e-5 for a, b in zip(cube.scale, scale_original.to_scale()))
    controllers._cancel_active(context, "Scale range regression complete")
    pointer_event.value = "RELEASE"
    assert controllers.dispatch_xr_event(context, pointer_event) == {"CANCELLED"}
    assert cube.matrix_world == scale_original
context.window_manager.xrwb_settings.edit_tool = "select"
context.window_manager.xrwb_settings.scale_drag_distance_m = .2
pointer_event.xr.action = "pointer"
pointer_event.xr.controller_location = (0.25, 0.0, 5.0)
assert controllers._begin_selection(context, pointer_event)[0]
assert controllers.dispatch_xr_event(context, pointer_event) == {"FINISHED"}
assert context.active_object is cube

# Grip grab is a rigid follow: the object turns with the hand around the hand.
# The other hand's trigger joins to stretch/twist; the right stick reels.
haptic = next(item for item in actionmap.actionmap_items if item.name == controllers.HAPTIC_ACTION)
assert haptic.type == "VIBRATION" and haptic.bindings[0].component_paths == ["/output/haptic", "/output/haptic"]
TransformTool = xr_workbench.interaction.TransformTool


def close(a, b, tolerance=1e-5):
    return all(abs(x - y) < tolerance for ra, rb in zip(a, b) for x, y in zip(ra, rb))


def xr_event(action, hand, location, rotation=(1.0, 0.0, 0.0, 0.0), value="PRESS"):
    return SimpleNamespace(type="XR_ACTION", value=value, xr=SimpleNamespace(
        action_set=controllers.ACTION_SET_NAME, action=action, user_path=hand,
        controller_location=location, controller_rotation=rotation))


start = cube.matrix_world.copy()
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
assert controllers._active_gesture.transaction.tool is TransformTool.GRAB
quarter = Quaternion((0.0, 0.0, 1.0), 1.5707963267948966)
grip.xr.controller_rotation = tuple(quarter)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
hand = Matrix.Translation((0.0, 0.0, 5.0))
assert close(cube.matrix_world, hand @ quarter.to_matrix().to_4x4() @ hand.inverted() @ start), "Grab did not turn with the hand"
grip.xr.controller_rotation = (1.0, 0.0, 0.0, 0.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
assert close(cube.matrix_world, start)
# Left trigger joins on a diagonal (not along any cube axis): spreading from
# 1.41 m to 2.83 m doubles size uniformly about the midpoint.
left = xr_event("pointer", controllers.LEFT_HAND, (1.0, 1.0, 5.0))
ok, message = controllers._begin_pointer(context, left)
assert ok and "Both hands" in message, message
left.xr.controller_location = (2.0, 2.0, 5.0)
assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
stretched = Matrix.Translation((1.0, 1.0, 5.0)) @ Matrix.Diagonal((2, 2, 2, 1)) @ Matrix.Translation((-0.5, -0.5, -5.0)) @ start
assert close(cube.matrix_world, stretched), (cube.matrix_world, stretched)
left.value = "RELEASE"
assert controllers.dispatch_xr_event(context, left) == {"FINISHED"}
assert controllers._active_gesture.second_key is None and close(cube.matrix_world, stretched)
# Still held by the right hand: moving it carries the stretched result.
grip.xr.controller_location = (0.0, 1.0, 5.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
assert close(cube.matrix_world, Matrix.Translation((0.0, 1.0, 0.0)) @ stretched)
# Right stick pushes it away along the ray (-Z here) and pulls it back.
before_reel = cube.matrix_world.translation.copy()
assert controllers.reel_active(context, 1)[0]
assert cube.matrix_world.translation.z < before_reel.z - 1e-3
assert controllers.reel_active(context, -1)[0]
grip.value = "RELEASE"
assert controllers.dispatch_xr_event(context, grip) == {"FINISHED"}
assert xr_workbench.editing.active_transaction() is None
assert abs(cube.scale[0] / start.to_scale()[0] - 2.0) < 1e-5
# Cancelling a two-hand capture restores exactly and drains the other trigger.
committed = cube.matrix_world.copy()
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
left = xr_event("pointer", controllers.LEFT_HAND, (1.0, 0.0, 5.0))
assert controllers._begin_pointer(context, left)[0]
left.xr.controller_location = (3.0, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
controllers._cancel_active(context, "Two-hand cancel regression")
assert close(cube.matrix_world, committed)
assert ("pointer", controllers.LEFT_HAND) in controllers._release_required
left.value = "RELEASE"
assert controllers.dispatch_xr_event(context, left) == {"CANCELLED"}
grip.value = "RELEASE"
assert controllers.dispatch_xr_event(context, grip) == {"CANCELLED"}
assert not controllers._release_required
cube.matrix_world = start
print("XR_CONTROLLER_GRAB grip_follow_rotation=1 two_hand_scale=1 second_release=1 reel=1 cancel_exact=1", flush=True)

# Hands lined up with the cube's X axis stretch only X (all shapes, all directions)
# when Settings > Tools > Two Hands is Stretch Axis (the default is Scale + Turn).
xr_workbench.onboarding.set_pref("two_hand", "AXIS")
start = cube.matrix_world.copy()
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
left = xr_event("pointer", controllers.LEFT_HAND, (1.0, 0.0, 5.0))
ok, message = controllers._begin_pointer(context, left)
assert ok and "Stretching along its X axis" in message, message
left.xr.controller_location = (2.0, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
ratio = [a / b for a, b in zip(cube.matrix_world.to_scale(), start.to_scale())]
assert abs(ratio[0] - 2.0) < 1e-5 and abs(ratio[1] - 1) < 1e-5 and abs(ratio[2] - 1) < 1e-5, ratio
controllers._cancel_active(context, "Axis stretch regression complete")
for event in (left, grip):
    event.value = "RELEASE"
    assert controllers.dispatch_xr_event(context, event) == {"CANCELLED"}
assert close(cube.matrix_world, start)
# Along Z (vertical hands) stretches height only.
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
left = xr_event("pointer", controllers.LEFT_HAND, (0.0, 0.0, 6.0))
assert "Z axis" in controllers._begin_pointer(context, left)[1]
left.xr.controller_location = (0.0, 0.0, 5.5)
assert controllers.dispatch_xr_event(context, left) == {"RUNNING_MODAL"}
ratio = [a / b for a, b in zip(cube.matrix_world.to_scale(), start.to_scale())]
assert abs(ratio[2] - 0.5) < 1e-5 and abs(ratio[0] - 1) < 1e-5, ratio
controllers._cancel_active(context, "Z stretch regression complete")
for event in (left, grip):
    event.value = "RELEASE"
    assert controllers.dispatch_xr_event(context, event) == {"CANCELLED"}
assert close(cube.matrix_world, start)
print("XR_CONTROLLER_AXIS_STRETCH x_only=1 z_only=1 diagonal_uniform=1 cancel_exact=1", flush=True)
xr_workbench.onboarding.set_pref("two_hand", "SCALE_ROTATE")

# Hold modes: Precision moves a quarter as far as the hand; Snap lands on a
# 5 cm grid; switching modes never jumps the object.
cube_name = cube.name
start = cube.matrix_world.copy()
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
ok, message = controllers.cycle_hold_mode(context)
assert ok and "Precision" in message, message
assert close(cube.matrix_world, start)
grip.xr.controller_location = (0.4, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
assert abs(cube.matrix_world.translation.x - start.translation.x - 0.1) < 1e-5
precise = cube.matrix_world.copy()
ok, message = controllers.cycle_hold_mode(context)
assert ok and "Snap" in message, message
grip.xr.controller_location = (0.43, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
snapped = cube.matrix_world.translation
assert all(abs(value / 0.05 - round(value / 0.05)) < 1e-4 for value in snapped), snapped
assert controllers.cycle_hold_mode(context)[1].startswith("Holding: Surface")
assert controllers.cycle_hold_mode(context)[1].startswith("Holding: Follow")
# Right A while holding stamps a copy; cancel removes stamps, commit keeps them as one undo.
before = set(bpy.data.objects.keys())
ok, message = controllers.stamp_copy(context)
assert ok and "Stamped 1" in message, message
stamp = next(bpy.data.objects[name] for name in set(bpy.data.objects.keys()) - before)
assert close(stamp.matrix_world, cube.matrix_world) and not stamp.select_get()
assert stamp.data is not cube.data
controllers._cancel_active(context, "Stamp cancel regression")
assert set(bpy.data.objects.keys()) == before and close(cube.matrix_world, start)
grip.value = "RELEASE"
assert controllers.dispatch_xr_event(context, grip) == {"CANCELLED"}
assert bpy.ops.ed.undo_push(message="stamp base") == {"FINISHED"}
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
grip.xr.controller_location = (1.0, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
assert controllers.stamp_copy(context)[0]
grip.xr.controller_location = (2.0, 0.0, 5.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
assert controllers.stamp_copy(context)[0]
grip.value = "RELEASE"
assert controllers.dispatch_xr_event(context, grip) == {"FINISHED"}
cube = bpy.data.objects[cube_name]
assert len(bpy.data.objects) == len(before) + 2
xs = sorted(round(obj.matrix_world.translation.x - start.translation.x, 4)
            for obj in bpy.data.objects if obj.type == "MESH" and obj.data.name.startswith(cube.data.name))
assert xs == [1.0, 2.0, 2.0], xs
assert bpy.ops.ed.undo() == {"FINISHED"}
assert set(bpy.data.objects.keys()) == before, "Stamps were not part of the one undo step"
cube = bpy.data.objects[cube_name]
# Left stick click while holding sets the object down on the surface below.
bpy.ops.mesh.primitive_plane_add(size=20, location=(0.0, 0.0, -4.0))
floor = context.active_object
for obj in context.selected_objects:
    obj.select_set(False)
cube.select_set(True)
context.view_layer.objects.active = cube
context.view_layer.update()
grip = xr_event("grab_move", controllers.RIGHT_HAND, (0.0, 0.0, 5.0))
assert controllers._begin_move(context, grip, grip=True)[0]
ok, message = controllers.drop_onto_surface(context)
assert ok, message
bottom = min((cube.matrix_world @ Matrix.Translation(corner).translation).z for corner in cube.bound_box)
assert abs(bottom - (-4.0)) < 1e-4, bottom
controllers._cancel_active(context, "Drop regression complete")
grip.value = "RELEASE"
assert controllers.dispatch_xr_event(context, grip) == {"CANCELLED"}
bpy.data.objects.remove(floor, do_unlink=True)
cube.select_set(True)
context.view_layer.objects.active = cube
print("XR_CONTROLLER_HOLD precision=1 snap=1 no_jump=1 stamp=2 stamp_cancel=1 stamp_undo=1 drop=1", flush=True)

# Stick navigation: list menus take arrow keys with no ray (work wherever you
# point); panels keep ray-routed wheel scrolling; stick click is Enter; idle
# hover pauses while the ray stays still after stick use.
native_calls.clear()
restored_native_call = controllers._native_call
controllers._native_call = fake_native_call
controllers._native_menu_open = True
ray = ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
assert controllers.stick_navigate(context, "WHEELDOWNMOUSE", ray)
assert [(c[1]["type"], c[1]["value"], c[1]["use_ray"]) for c in native_calls] == [
    ("DOWN_ARROW", "PRESS", False), ("DOWN_ARROW", "RELEASE", False)], native_calls
assert controllers._hover_paused(ray)
turned = ((0.0, 0.0, 1.0), (0.1, 0.0, -1.0))
assert not controllers._hover_paused(turned), "Moving the ray must hand control back to hover"
native_calls.clear()
assert controllers.press_highlighted(context)
assert [c[1]["type"] for c in native_calls] == ["RET", "RET"]
native_calls.clear()
controllers._native_menu_open = False
assert controllers.stick_navigate(context, "WHEELUPMOUSE", ray)
assert native_calls[0][1]["type"] == "WHEELUPMOUSE" and native_calls[0][1]["use_ray"]
controllers._stick_nav = None
controllers._native_call = restored_native_call
print("XR_CONTROLLER_STICK_MENU arrows=1 enter=1 panel_wheel=1 hover_pause=1", flush=True)

# Multi-Select: trigger clicks add then remove; empty space clears (one undo).
cube = bpy.data.objects[cube_name]
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0.0, 3.0, 0.0))
ball = context.active_object
for obj in context.selected_objects:
    obj.select_set(False)
cube.select_set(True)
context.view_layer.objects.active = cube
settings = context.window_manager.xrwb_settings
settings.edit_tool = "select"
settings.multi_select = True


def click_at(location):
    event = xr_event("pointer", controllers.RIGHT_HAND, location)
    ok, message = controllers._begin_selection(context, event)
    assert ok, message
    event.value = "RELEASE"
    outcome = controllers.dispatch_xr_event(context, event)
    assert outcome in ({"FINISHED"}, {"CANCELLED"}), outcome
    return message


ball_top = (0.0, 3.0, 5.0)
click_at(ball_top)
assert {obj.name for obj in context.selected_objects} == {cube_name, ball.name}
assert context.active_object == ball
click_at(ball_top)
assert {obj.name for obj in context.selected_objects} == {cube_name}, "Second click must remove"
assert "cleared" in click_at((40.0, 40.0, 5.0)) and not context.selected_objects
assert bpy.ops.ed.undo() == {"FINISHED"}
assert {obj.name for obj in bpy.context.selected_objects} == {cube_name}, "Clear was not one undo"
settings.multi_select = False
cube, ball = bpy.data.objects[cube_name], bpy.data.objects[ball.name]
bpy.data.objects.remove(ball, do_unlink=True)
# Surface mode: the held object rests upright on the floor under the ray and slides.
bpy.ops.mesh.primitive_plane_add(size=40, location=(0.0, 0.0, -2.0))
floor = context.active_object
for obj in context.selected_objects:
    obj.select_set(False)
cube = bpy.data.objects[cube_name]
cube.select_set(True)
context.view_layer.objects.active = cube
context.view_layer.update()
aim = cube.matrix_world.translation
grip = xr_event("grab_move", controllers.RIGHT_HAND, (aim.x, aim.y, 5.0))  # Point at the cube.
assert controllers._begin_move(context, grip, grip=True)[0]
assert controllers._active_gesture.transaction.original_objects[0].object_ref == cube
for _ in range(3):
    message = controllers.cycle_hold_mode(context)[1]
assert message == "Holding: Surface", message


def bottom_and_centre(obj):
    corners = [obj.matrix_world @ Matrix.Translation(c).translation for c in obj.bound_box]
    return min(c.z for c in corners), sum(corners, Matrix().translation) / 8


assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
bottom, centre = bottom_and_centre(cube)
assert abs(bottom + 2.0) < 1e-3 and abs(centre.x - aim.x) < 1e-4 and abs(centre.y - aim.y) < 1e-4, (bottom, centre)
grip.xr.controller_location = (-2.0, 3.0, 5.0)
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
bottom, centre = bottom_and_centre(cube)
assert abs(bottom + 2.0) < 1e-3 and abs(centre.x + 2.0) < 1e-4 and abs(centre.y - 3.0) < 1e-4, (bottom, centre)
# Resting on the floor must not make the next ray skip it (coplanar faces).
assert controllers.dispatch_xr_event(context, grip) == {"RUNNING_MODAL"}
assert abs(bottom_and_centre(cube)[0] + 2.0) < 1e-3, "Surface mode flickered off the floor"
controllers._cancel_active(context, "Surface regression complete")
grip.value = "RELEASE"
assert controllers.dispatch_xr_event(context, grip) == {"CANCELLED"}
bpy.data.objects.remove(floor, do_unlink=True)
cube.select_set(True)
context.view_layer.objects.active = cube
# Adjustable scheme: left-handed swaps grips and sticks, thresholds apply,
# and no physical component is bound twice (Index, Quest, Auto).
for family in ("INDEX",):  # Quest/Auto: native/test_controller_profiles.py (full fakes).
    swapped = controllers.build_actionmap(SimpleNamespace(actionmaps=ActionMaps()), family,
                                          {"left_handed": True, "trigger": 0.2, "grip": 0.6, "stick": 0.7})
    by_name = {item.name: item for item in swapped.actionmap_items}
    assert by_name["grab_move"].user_paths == [controllers.LEFT_HAND]
    assert by_name["world_grab"].user_paths == [controllers.RIGHT_HAND]
    assert by_name["move_forward"].user_paths == [controllers.RIGHT_HAND]
    assert by_name["turn_left"].user_paths == [controllers.LEFT_HAND]
    assert by_name["scroll_positive"].user_paths == [controllers.LEFT_HAND]
    assert abs(by_name["pointer"].bindings[0].threshold - 0.2) < 1e-6
    assert abs(by_name["grab_move"].bindings[0].threshold - 0.6) < 1e-6
    assert abs(by_name["scroll_negative"].bindings[0].threshold - 0.7) < 1e-6
    seen = set()
    for item in swapped.actionmap_items:
        for binding in item.bindings:
            for hand, component in zip(item.user_paths, binding.component_paths):
                key = (binding.profile, getattr(hand, "path", hand), getattr(component, "path", component),
                       binding.axis0_region)
                assert key not in seen, (family, item.name, key)
                seen.add(key)
    assert len(swapped.actionmap_items) == controllers.action_spec_summary(family)["total_actions"]
try:
    controllers.build_actionmap(SimpleNamespace(actionmaps=ActionMaps()), "INDEX", {"trigger": 2.0})
except ValueError:
    pass
else:
    raise AssertionError("Out-of-range threshold accepted")
# Assignable buttons: menus open and toggle closed, Nothing is refused.
native_calls.clear()
controllers._native_call = fake_native_call
try:
    assert controllers.run_slot_action(context, "NONE", controllers.RIGHT_HAND)[0] is False
    assert controllers.run_slot_action(context, "BLENDER_MENUS", controllers.LEFT_HAND)[0]
    assert native_calls[-1][1]["menu"] == "XRWB_MT_blender" and native_calls[-1][1]["controller"] == 0
    assert controllers.slot_action("RIGHT_B") == "ADD_MENU" and controllers.slot_action("LEFT_A") == "UNDO"
finally:
    controllers._native_call = restored_native_call
    controllers._menu_stack.clear()
print("XR_CONTROLLER_SCHEME left_handed=1 thresholds=1 no_conflicts=1 range_guard=1 slots=1", flush=True)
print("XR_CONTROLLER_SELECT_SURFACE multi_add=1 multi_remove=1 empty_clear=1 clear_undo=1 surface_rest=1 "
      "surface_slide=1", flush=True)

# Regression for the actual integration failure: native undo destroys operator
# RNA. The hover owner is plain Python and the registered callback survives real
# undo/redo, then still stops cleanly. No XR session is created by this check.
bpy.context.preferences.edit.use_global_undo = True
assert bpy.ops.ed.undo_push(message="Hover lifetime base") == {"FINISHED"}
bpy.context.active_object.location.x += 1.0
assert bpy.ops.ed.undo_push(message="Hover lifetime changed") == {"FINISHED"}
controllers._hover_router = controllers._HoverState(controllers.monotonic() + 10.0)
undo_hover_state = controllers._hover_router
bpy.app.timers.register(controllers._hover_timer, first_interval=60.0, persistent=False)
assert bpy.ops.ed.undo() == {"FINISHED"}
assert controllers._hover_router is undo_hover_state
assert bpy.app.timers.is_registered(controllers._hover_timer)
assert bpy.ops.ed.redo() == {"FINISHED"}
assert controllers._hover_router is undo_hover_state
assert bpy.app.timers.is_registered(controllers._hover_timer)
controllers.stop_router(bpy.context)
assert controllers._hover_router is None and not bpy.app.timers.is_registered(controllers._hover_timer)


class ConfiguredState(SimpleNamespace):
    def __init__(self):
        super().__init__(actionmaps=ActionMaps())
        self.created = []

    def action_set_create(self, _context, actionmap):
        self.created.append(("set", actionmap.name))
        return True

    def action_create(self, _context, _actionmap, item):
        self.created.append(("action", item.name))
        return True

    def action_binding_create(self, _context, _actionmap, item, _binding):
        self.created.append(("binding", item.name))
        return True

    def controller_pose_actions_set(self, _context, action_set, grip, aim):
        self.created.append(("poses", action_set, grip, aim))
        return True

    def active_action_set_set(self, _context, action_set):
        self.created.append(("active", action_set))
        return True


configured = ConfiguredState()
fake_context = SimpleNamespace(
    window_manager=SimpleNamespace(xr_session_state=configured)
)
ok, message = controllers.configure_action_map(fake_context)
assert ok and "ACTIONMAP_PASS actions=22 bindings=22" in message, message
assert configured.created[0] == ("set", controllers.ACTION_SET_NAME)
assert configured.created[-1] == ("active", controllers.ACTION_SET_NAME)
xr_workbench.unregister()
print(
    "XRWB_CONTROLLER_SMOKE_PASS actions=22 haptic_action=1 grip_grab=1 two_hand=1 axis_stretch=1 reel=1 hold_modes=1 stamp=1 drop=1 stick_menu=1 multi_select=1 surface=1 scheme=1 native_rna=1 scene_edit=1 cancel_restore=1 passive_hover=1 hover_lifecycle=1 hover_native_undo=1 hand_menu_routing=1 hand_menu_hover=1 no_desktop_popup=1 profile=index physical_device=0",
    flush=True,
)
