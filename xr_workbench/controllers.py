"""Index/Quest PCVR controller actions and main-thread routing for XR Workbench.

One active OpenXR action set contains both authoring and native navigation.
It never starts a session. Captures retain only plain pose values and edit
transactions; a watchdog cancels abandoned input on Blender's main thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos as math_cos, isfinite, pi, radians
from time import monotonic
from typing import Any

import bpy
from mathutils import Quaternion, Vector

from . import interaction_log, tool_wheel


ACTION_SET_NAME = "xr_workbench_controllers"
LEFT_HAND = "/user/hand/left"
RIGHT_HAND = "/user/hand/right"
INDEX_PROFILE = "/interaction_profiles/valve/index_controller"
INDEX_BINDING_NAME = "index"
QUEST_PROFILE = "/interaction_profiles/oculus/touch_controller"
CONTROLLER_FAMILIES = {"AUTO", "INDEX", "QUEST"}


@dataclass(slots=True)
class _ActiveGesture:
    """Main-thread-only state for one modal pointer or move gesture."""

    kind: str
    action: str
    user_path: str
    owner: Any | None = None
    transaction: Any | None = None
    pointer_pressed: bool = False
    pointer_button: str = "LEFTMOUSE"
    pointer_target: str = "EDITOR"
    navigation: tuple | None = None
    surface_generation: int = 0
    last_ray_origin: tuple[float, float, float] | None = None
    last_ray_direction: tuple[float, float, float] | None = None
    last_input_at: float = 0.0
    panel_pose: tuple | None = None
    panel_relative: tuple | None = None
    scale_origin: tuple | None = None
    scale_axis: tuple | None = None
    scale_distance: float = 1.0
    # Latest hand pose of the owning controller (plain tuples only).
    primary_position: tuple | None = None
    primary_rotation: tuple | None = None
    primary_direction: tuple | None = None
    # Second hand joined for two-handed stretch / twist.
    second_key: tuple[str, str] | None = None
    second_position: tuple | None = None
    two_hand_start: tuple | None = None
    # Right-stick push/pull along the holding hand's ray (world units).
    reel: float = 0.0
    # World grab (navigation.WorldGrab) while a grip holds empty space.
    world: Any | None = None
    # Live readout of the edit and the detent step it is on (polish).
    readout: str = ""
    detent: Any | None = None
    # Selection to restore if a grab that selected its target is cancelled.
    restore_selection: Any | None = None
    # World offset added to the hand (Drop onto surface).
    offset: tuple = (0.0, 0.0, 0.0)
    # Hold mode: 0 follow, 1 precision (slowed), 2 snap to grid/angles.
    hold_mode: int = 0
    precision_anchor: tuple | None = None
    # Copies stamped while holding; removed again if the capture cancels.
    stamps: list | None = None
    # Surface mode: (group centre, rebased anchor, rotation, bounding size, held objects).
    surface_base: tuple | None = None


_active_gesture: _ActiveGesture | None = None
_release_required: set[tuple[str, str]] = set()
_last_capture_status = "unavailable: no controller capture attempted"
_pointer_hand = RIGHT_HAND
@dataclass(slots=True)
class _HoverState:
    """Plain Python only: native undo invalidates RNA operator instances."""
    startup_deadline: float
    was_running: bool = False
    last_frame: int = -1
    last_target: str = "EDITOR"
    last_navigation: tuple | None = None


_hover_router: _HoverState | None = None
TRACKING_WATCHDOG_SECONDS = 1.0
HAPTIC_ACTION = "haptic"
PRECISION_FACTOR = 0.25
HOLD_MODES = ("Follow", "Precision", "Snap", "Surface")


def _feel(key, default):
    """A Settings > Tools value from vr_prefs.json, or its default if unavailable."""
    try:
        from . import onboarding
        return type(default)(onboarding.prefs()[key])
    except (ImportError, KeyError, TypeError, ValueError):
        return default


def hold_mode_name(index: int) -> str:
    """Hold mode label including the user's current precision and snap settings."""
    index %= len(HOLD_MODES)
    if index == 1:
        return f"Precision x{_feel('precision', PRECISION_FACTOR):g}"
    if index == 2:
        return f"Snap {_feel('snap_grid_cm', 5.0):g} cm / {_feel('snap_angle', 15.0):g}°"
    return HOLD_MODES[index]
# (seconds, amplitude) per feedback kind; scaled by Settings > Haptics.
HAPTICS = {"tick": (0.006, 0.2), "click": (0.018, 0.45), "begin": (0.03, 0.6),
           "commit": (0.035, 0.85), "cancel": (0.14, 0.3), "reject": (0.09, 0.35),
           "detent": (0.004, 0.32)}
# Follow-up pulses (delay s, duration s, amplitude): a commit is a crisp double
# tap, a refusal a low double buzz, so each outcome is recognisable blind.
HAPTIC_TAILS = {"commit": ((0.075, 0.02, 0.5),), "reject": ((0.13, 0.05, 0.25),)}
HAPTIC_MIN_GAP = {"tick": 0.08, "detent": 0.03}
_last_pulse: dict[str, float] = {}


def _cage_sync(context):
    """Live Cage enters at a stroke's start and starts settling at its end (never breaks input)."""
    try:
        from . import editing, live_cage
        live_cage.refresh(context, editing.active_transaction())
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        interaction_log.emit("error", f"Live Cage: {exc}", context, level="WARNING")


def _push_undo(message):
    from . import editing
    return editing.push_undo(message)


def _feel_event(context, user_path, kind):
    """The 0.21 feel layer's extra sound for an outcome (never breaks input)."""
    from . import physical_tools
    feel = physical_tools.optional("feel")
    if feel is not None and user_path in {LEFT_HAND, RIGHT_HAND}:
        try:
            feel.event(context, user_path, kind)
        except Exception:
            pass


def feel_commit(context, user_path, transaction):
    """After a committed stroke: the feel's commit sound and the 0.25 s success flash."""
    from . import physical_tools
    feel = physical_tools.optional("feel")
    if feel is None or not hasattr(transaction, "feel_commit"):  # Strokes only; moves keep their pulse.
        return
    try:
        if transaction.feel_commit:
            feel.event(context, user_path, "commit")
        if transaction.flashed:
            return  # Place, Prefab and Mask Extract flashed their own result.
        current, touched = getattr(transaction, "current", None), getattr(transaction, "touched", None)
        if current is not None and touched is not None and len(touched) == len(current) and touched.any():
            feel.flash(points=current[touched])  # feel caps it at 2,000 crosses.
        elif getattr(transaction, "obj", None) is not None:
            feel.flash(corners=physical_tools.bracket_lines(transaction.obj))
    except Exception:  # A removed object (ReferenceError) or a half-built stroke: no flash.
        pass


def pulse(context, user_path: str, kind: str = "click") -> bool:
    """Short native vibration on one controller; silently skipped when unavailable."""
    if kind in {"reject", "cancel"}:
        _feel_event(context, user_path, kind)  # Extra, never a replacement for the pulse below.
    try:
        strength = float(context.window_manager.xrwb_settings.haptic_strength)
        if strength <= 0.0 or user_path not in {LEFT_HAND, RIGHT_HAND}:
            return False
        now = monotonic()
        if now - _last_pulse.get(user_path, 0.0) < HAPTIC_MIN_GAP.get(kind, 0.0):
            return False  # Ticks and detents never blur into a continuous buzz.
        duration, amplitude = HAPTICS[kind]
        state = context.window_manager.xr_session_state
        ok = bool(state.haptic_action_apply(context, ACTION_SET_NAME, HAPTIC_ACTION, user_path,
                                            duration, 0.0, min(1.0, amplitude * strength)))
        _last_pulse[user_path] = now
        for delay, tail_duration, tail_amplitude in HAPTIC_TAILS.get(kind, ()) if ok else ():
            _schedule_tail(user_path, delay, tail_duration, min(1.0, tail_amplitude * strength))
        return ok
    except (AttributeError, KeyError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _schedule_tail(user_path, delay, duration, amplitude):
    def tail():
        try:
            context = bpy.context
            context.window_manager.xr_session_state.haptic_action_apply(
                context, ACTION_SET_NAME, HAPTIC_ACTION, user_path, duration, 0.0, amplitude)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass
        return None
    if not bpy.app.background:
        bpy.app.timers.register(tail, first_interval=delay)


def _event_hand(event):
    xr = getattr(event, "xr", None)
    return getattr(xr, "user_path", "") if xr is not None else ""


def _new_item(
    actionmap, name: str, item_type: str, user_paths: tuple[str, ...], **attrs
):
    item = actionmap.actionmap_items.new(name, True)
    item.type = item_type
    for user_path in user_paths:
        item.user_paths.new(user_path)
    for key, value in attrs.items():
        if key != "op_properties":
            setattr(item, key, value)
    for key, value in attrs.get("op_properties", {}).items():
        setattr(item.op_properties, key, value)
    return item


def _bind_profile(
    item, components: tuple[str, ...], *, threshold: float = 0.30, axis="ANY",
    name=INDEX_BINDING_NAME, profile=INDEX_PROFILE,
):
    binding = item.bindings.new(name, True)
    binding.profile = profile
    binding.threshold = threshold
    binding.axis0_region = axis
    for component in components:
        binding.component_paths.new(component)
    return binding


def _operator_action(
    actionmap,
    name: str,
    operator: str,
    user_paths: tuple[str, ...],
    components: tuple[str, ...],
    *,
    threshold: float = 0.30,
    mode: str = "MODAL",
    item_type: str = "FLOAT",
    axis: str = "ANY",
    op_properties: dict | None = None,
):
    item = _new_item(
        actionmap,
        name,
        item_type,
        user_paths,
        op=operator,
        op_mode=mode,
        bimanual=False,
        op_properties=op_properties or {},
    )
    _bind_profile(item, components, threshold=threshold, axis=axis)
    return item


# Adjustable at the next XR start (Settings > Controls). Defaults reproduce the
# original right-handed layout exactly.
DEFAULT_SCHEME = {"left_handed": False, "trigger": 0.30, "grip": 0.45, "stick": 0.55,
                  "locomotion": "SMOOTH", "fly": False}
BUTTON_SLOTS = (("RIGHT_A", "Right A", "A on the right controller"),
                ("RIGHT_B", "Right B", "B on the right controller (Index; Quest B moves panels)"),
                ("RIGHT_STICK", "Right Stick Click", "Press the right thumbstick"),
                ("LEFT_A", "Left A / X", "A on the left Index controller, X on Quest"),
                ("LEFT_STICK", "Left Stick Click", "Press the left thumbstick"))
SLOT_ACTIONS = (("HAND_MENU", "Hand Menu", "Open or close the hand menu"),
                ("ADD_MENU", "Add Shapes", "The basic shapes menu"),
                ("TOOL_WHEEL", "Tool Wheel", "Radial tools for the workflow: flick toward one, press again"),
                ("QUICK_MENU", "Quick Menu", "Tools for the current workflow"),
                ("BLENDER_MENUS", "Blender Menus", "File, Edit, Add, Object and the rest"),
                ("DESKTOP", "Show / Hide Desktop", "The Blender desktop panels"),
                ("UNDO", "Undo", "One step back"),
                ("REDO", "Redo", "One step forward"),
                ("UNDO_RING", "Undo Ring", "Blender's undo history around your wrist: the stick scrubs it"),
                ("GUIDE", "Guide", "The walkthrough"),
                ("NEXT_WORKFLOW", "Next Workflow", "Model, Sculpt, Shade, Animate in turn"),
                ("PLAY", "Play / Pause", "Animation playback"),
                ("GO_TO", "Go to Selection", "Travel to stand in front of what is selected"),
                ("RESET_VIEW", "Reset View", "Normal size, upright, back at the scene origin"),
                ("NONE", "Nothing", "Leave this button unused"))
DEFAULT_SLOTS = {"RIGHT_A": "HAND_MENU", "RIGHT_B": "ADD_MENU", "RIGHT_STICK": "TOOL_WHEEL",
                 "LEFT_A": "UNDO", "LEFT_STICK": "DESKTOP"}


def build_actionmap(session_state, profile="INDEX", scheme=None):
    """Build shared authoring/navigation actions for the selected PCVR family.

    Pose actions are required by Blender before it can populate the controller
    pose attached to an ``XR_ACTION`` event.  The action set is intentionally
    separate from the legacy VR Workbench map. Blender activates only one set,
    so navigation must live here too.
    """

    if profile not in CONTROLLER_FAMILIES:
        raise ValueError(f"Unknown controller family: {profile}")
    scheme = {**DEFAULT_SCHEME, **(scheme or {})}
    for key in ("trigger", "grip", "stick"):
        if not 0.05 <= float(scheme[key]) <= 0.95:
            raise ValueError(f"{key} threshold must be between 0.05 and 0.95")
    # Dominant hand picks things up, turns and scrolls; the other hand walks
    # and grabs the world. Buttons stay on their physical controllers.
    main, off = (LEFT_HAND, RIGHT_HAND) if scheme["left_handed"] else (RIGHT_HAND, LEFT_HAND)
    actionmap = session_state.actionmaps.new(session_state, ACTION_SET_NAME, True)
    grip = _new_item(
        actionmap,
        "controller_grip",
        "POSE",
        (LEFT_HAND, RIGHT_HAND),
        pose_is_controller_grip=True,
        pose_is_controller_aim=False,
    )
    _bind_profile(grip, ("/input/grip/pose", "/input/grip/pose"))
    aim = _new_item(
        actionmap,
        "controller_aim",
        "POSE",
        (LEFT_HAND, RIGHT_HAND),
        pose_is_controller_grip=False,
        pose_is_controller_aim=True,
    )
    _bind_profile(aim, ("/input/aim/pose", "/input/aim/pose"))
    haptic = _new_item(actionmap, HAPTIC_ACTION, "VIBRATION", (LEFT_HAND, RIGHT_HAND))
    _bind_profile(haptic, ("/output/haptic", "/output/haptic"))
    _operator_action(
        actionmap,
        "pointer",
        "xrwb.controller_pointer",
        (LEFT_HAND, RIGHT_HAND),
        ("/input/trigger/value", "/input/trigger/value"),
        threshold=float(scheme["trigger"]),
    )
    _operator_action(
        actionmap,
        "grab_move",
        "xrwb.controller_grab_move",
        (main,),
        ("/input/squeeze/force",),
        threshold=float(scheme["grip"]),
    )
    _operator_action(
        actionmap,
        "cancel",
        "xrwb.controller_cancel",
        (LEFT_HAND,),
        ("/input/b/click",),
        mode="PRESS",
    )
    # Assignable buttons: one operator, the slot says which physical button.
    for name, hand, component, slot in (("command_menu", RIGHT_HAND, "/input/a/click", "RIGHT_A"),
                                        ("recall", LEFT_HAND, "/input/thumbstick/click", "LEFT_STICK")):
        _operator_action(actionmap, name, "xrwb.controller_button", (hand,), (component,),
                         mode="PRESS", op_properties={"slot": slot})
    _operator_action(
        actionmap, "panel_grab", "xrwb.controller_panel_grab", (RIGHT_HAND,),
        ("/input/trackpad/force",), threshold=0.35,
    )
    # Index right B (default Add menu); Quest's right B moves panels instead.
    for name, hand, component, slot in (("undo", LEFT_HAND, "/input/a/click", "LEFT_A"),
                                        ("add_menu", RIGHT_HAND, "/input/b/click", "RIGHT_B"),
                                        ("context_pointer", RIGHT_HAND, "/input/thumbstick/click", "RIGHT_STICK")):
        _operator_action(actionmap, name, "xrwb.controller_button", (hand,), (component,),
                         mode="PRESS", op_properties={"slot": slot})
    _operator_action(
        actionmap,
        "world_grab",
        "xrwb.controller_world_grip",
        (off,),
        ("/input/squeeze/force",),
        threshold=float(scheme["grip"]),
    )
    _operator_action(
        actionmap,
        "teleport",
        "wm.xr_navigation_teleport",
        (LEFT_HAND,),
        ("/input/trackpad/force",),
        threshold=0.35,
    )
    teleport_mode = scheme["locomotion"] == "TELEPORT"
    for name, mode, component, axis, hand in (
        ("move_forward", "VIEWER_FORWARD", "y", "POSITIVE", off),
        ("move_back", "VIEWER_BACK", "y", "NEGATIVE", off),
        ("move_left", "VIEWER_LEFT", "x", "NEGATIVE", off),
        ("move_right", "VIEWER_RIGHT", "x", "POSITIVE", off),
        ("turn_left", "TURNLEFT", "x", "NEGATIVE", main),
        ("turn_right", "TURNRIGHT", "x", "POSITIVE", main),
    ):
        if teleport_mode and name.startswith("move_"):
            # Comfort: push the walking stick forward to aim an arc, let go to jump.
            # Works on every controller (Quest has no pad). No smooth sliding at all.
            if name == "move_forward":
                _operator_action(actionmap, "teleport_stick", "wm.xr_navigation_teleport", (hand,),
                                 ("/input/thumbstick/y",), axis="POSITIVE", threshold=0.6,
                                 op_properties={"selectable_only": False})
            continue
        # Fly: the stick moves you where you look, up and down included.
        properties = {"mode": mode, "lock_location_z": not bool(scheme["fly"])}
        if name.startswith("move_"):
            fly_properties = bpy.ops.wm.xr_navigation_fly.get_rna_type().properties
            if "fly_speed_factor" in fly_properties:  # Blender 5.0+
                properties["fly_speed_factor"] = 0.1
            # Preserve compatibility with the frozen camera build.
            if "hold_acceleration_seconds" in fly_properties:
                properties.update(hold_acceleration_seconds=1.5, hold_start_factor=0.2,
                                  hold_acceleration_threshold=0.75)
        _operator_action(
            actionmap,
            name,
            "wm.xr_navigation_fly",
            (hand,),
            (f"/input/thumbstick/{component}",),
            axis=axis,
            op_properties=properties,
        )
    for axis, direction in (
        ("POSITIVE", "WHEELUPMOUSE"),
        ("NEGATIVE", "WHEELDOWNMOUSE"),
    ):
        _operator_action(
            actionmap,
            f"scroll_{axis.lower()}",
            "xrwb.controller_scroll",
            (main,),
            ("/input/thumbstick/y",),
            axis=axis,
            mode="PRESS",
            threshold=float(scheme["stick"]),
            op_properties={"direction": direction},
        )
    if profile != "INDEX":
        # Keep one action/operator contract. Touch compatibility changes only
        # physical components; it is not a standalone Quest or Touch Plus layer.
        for item in list(actionmap.actionmap_items):
            if item.name in {"panel_grab", "teleport"}:
                # Touch has no pressure pads. Panels move by gripping them (any
                # profile) and the stick walks, so B stays Add like on Index.
                if profile == "QUEST":
                    actionmap.actionmap_items.remove(item)
                continue
            original = item.bindings[0]
            components = []
            for hand, component in zip(item.user_paths, original.component_paths):
                path = component.path
                if path == "/input/squeeze/force":
                    path = "/input/squeeze/value"
                elif hand.path == LEFT_HAND:
                    path = {"/input/a/click": "/input/x/click",
                            "/input/b/click": "/input/y/click"}.get(path, path)
                components.append(path)
            _bind_profile(item, tuple(components), threshold=original.threshold,
                          axis=original.axis0_region, name="quest", profile=QUEST_PROFILE)
            if profile == "QUEST":
                item.bindings.remove(original)
    return actionmap


def current_scheme() -> dict:
    """The saved control scheme (Settings), or the defaults if unavailable."""
    try:
        from . import onboarding
        prefs = onboarding.prefs()
        return {"left_handed": bool(prefs["left_handed"]), "trigger": float(prefs["trigger_threshold"]),
                "grip": float(prefs["grip_threshold"]), "stick": float(prefs["stick_threshold"]),
                "locomotion": str(prefs["locomotion"]), "fly": bool(prefs["fly"])}
    except (AttributeError, ImportError, KeyError, RuntimeError, TypeError, ValueError):
        return dict(DEFAULT_SCHEME)


def slot_action(slot: str) -> str:
    try:
        from . import onboarding
        action = str(onboarding.prefs().get(f"slot_{slot}", DEFAULT_SLOTS[slot]))
    except (AttributeError, ImportError, KeyError, RuntimeError):
        action = DEFAULT_SLOTS.get(slot, "NONE")
    return action if action in {item[0] for item in SLOT_ACTIONS} else DEFAULT_SLOTS.get(slot, "NONE")


def _find_actionmap(session_state):
    for actionmap in session_state.actionmaps:
        if actionmap.name == ACTION_SET_NAME:
            return actionmap
    return None


def configure_action_map(context) -> tuple[bool, str]:
    """Attach the isolated actions to an already-created XR session.

    This is intended for the session-start lifecycle hook.  It cannot create a
    session and does not modify a map owned by Blender or another add-on.
    """

    session_state = getattr(context.window_manager, "xr_session_state", None)
    if session_state is None:
        return False, "XR session state is unavailable"
    if _find_actionmap(session_state) is not None:
        return False, "XR Workbench controller action set is already configured"
    try:
        settings = getattr(context.window_manager, "xrwb_settings", None)
        profile = str(getattr(settings, "controller_profile", "INDEX"))
        actionmap = build_actionmap(session_state, profile, current_scheme())
        if not session_state.action_set_create(context, actionmap):
            return False, "OpenXR rejected the XR Workbench controller action set"
        actions = bindings = 0
        for item in actionmap.actionmap_items:
            if not session_state.action_create(context, actionmap, item):
                return False, f"OpenXR rejected controller action {item.name}"
            actions += 1
            for binding in item.bindings:
                if not session_state.action_binding_create(
                    context, actionmap, item, binding
                ):
                    return False, f"OpenXR rejected {item.name} binding"
                bindings += 1
        if not session_state.controller_pose_actions_set(
            context, ACTION_SET_NAME, "controller_grip", "controller_aim"
        ):
            return False, "OpenXR could not assign controller pose actions"
        if not session_state.active_action_set_set(context, ACTION_SET_NAME):
            return False, "OpenXR could not activate XR Workbench controller actions"
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"XR Workbench controller setup failed: {exc}"
    start_router(context)
    from . import ray_feedback
    ray_feedback.start()
    return (
        True,
        f"XRWB_CONTROLLERS_ACTIONMAP_PASS actions={actions} bindings={bindings} profile={profile}",
    )


def action_spec_summary(profile="INDEX") -> dict[str, int | str]:
    """Pure source-level contract for headless and package checks."""

    if profile not in CONTROLLER_FAMILIES:
        raise ValueError(f"Unknown controller family: {profile}")
    return {
        "action_set": ACTION_SET_NAME,
        "profiles": 2 if profile == "AUTO" else 1,
        "pose_actions": 2,
        "modal_actions": 10 if profile == "QUEST" else 11,
        "navigation_actions": 7 if profile == "QUEST" else 8,
        "haptic_actions": 1,
        "total_actions": 20 if profile == "QUEST" else 22,
        "total_bindings": {"AUTO": 42, "INDEX": 22, "QUEST": 20}[profile],
    }


def capture_status() -> str:
    """Return the last truthful native-editor capture outcome."""

    return _last_capture_status


def hand_menu_info(context):
    """Read native menu state without retaining undo-invalidated RNA references."""
    try:
        return tuple(context.window_manager.xr_session_state.hand_menu_info(context))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return (0, 0, 0, -1, 0, 0)


def hand_menu_active(context):
    info = hand_menu_info(context)
    return bool(info[0] or info[4])


def _route_matches(event, action: str, user_path: str) -> bool:
    xr = getattr(event, "xr", None)
    return bool(
        xr
        and getattr(event, "type", "") == "XR_ACTION"
        and getattr(xr, "action_set", "") == ACTION_SET_NAME
        and getattr(xr, "action", "") == action
        and getattr(xr, "user_path", "") == user_path
    )


def _event_key(event):
    xr = getattr(event, "xr", None)
    if (
        xr
        and getattr(event, "type", "") == "XR_ACTION"
        and xr.action_set == ACTION_SET_NAME
    ):
        return xr.action, xr.user_path
    return None


def _drain_cancelled_input(event):
    """Consume held/recovered input until a genuinely tracked release arrives."""
    key = _event_key(event)
    if key not in _release_required:
        return None
    if getattr(event, "value", "") == "RELEASE" and _pose_from_event(event) is not None:
        _release_required.discard(key)
        return {"CANCELLED"}
    return {"RUNNING_MODAL"}


def _validated_pose(location, orientation):
    try:
        origin = Vector(tuple(float(value) for value in location))
        rotation = Quaternion(tuple(float(value) for value in orientation))
        if len(origin) != 3 or not all(
            isfinite(value) for value in (*origin, *rotation)
        ):
            return None
        norm = rotation.dot(rotation)
        if not isfinite(norm) or norm <= 1.0e-12:
            return None
        rotation.normalize()
        direction = (rotation @ Vector((0.0, 0.0, -1.0))).normalized()
        return tuple(origin), tuple(rotation), tuple(direction)
    except (AttributeError, TypeError, ValueError):
        return None


def _pose_from_event(event):
    """Read pose data carried by this event; never query global Blender context."""
    xr = getattr(event, "xr", None)
    if xr is None:
        return None
    return _validated_pose(
        getattr(xr, "controller_location", ()),
        getattr(xr, "controller_rotation", ()),
    )


def _navigation_signature(context):
    state = getattr(getattr(context, "window_manager", None), "xr_session_state", None)
    if state is None:
        return None
    return (
        tuple(state.navigation_location),
        tuple(state.navigation_rotation),
        float(state.navigation_scale),
    )


def _native_call(operator_name: str, **properties) -> tuple[bool, str]:
    """Call a native editor operator only when it explicitly accepts input."""

    global _last_capture_status
    operator = getattr(getattr(bpy.ops, "wm", None), operator_name, None)
    if operator is None:
        _last_capture_status = f"unavailable: native {operator_name} is not installed"
        return False, _last_capture_status
    try:
        result = operator("EXEC_DEFAULT", **properties)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _last_capture_status = (
            f"unavailable: native editor surface rejected input ({exc})"
        )
        return False, _last_capture_status
    if "FINISHED" not in result:
        _last_capture_status = "unavailable: native editor surface did not accept input"
        return False, _last_capture_status
    _last_capture_status = "accepted: native editor surface input"
    return True, _last_capture_status


def _editor_surface(context) -> tuple[bool, str]:
    """Recall through the UI wrapper so controller and sidebar stay consistent."""

    global _last_capture_status
    operator = getattr(getattr(bpy.ops, "xrwb", None), "editor_surface", None)
    if operator is None:
        _last_capture_status = (
            "unavailable: XR Workbench editor wrapper is not installed"
        )
        return False, _last_capture_status
    try:
        result = operator("EXEC_DEFAULT")
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _last_capture_status = f"unavailable: editor wrapper rejected recall ({exc})"
        return False, _last_capture_status
    if "FINISHED" not in result:
        _last_capture_status = "unavailable: editor wrapper did not accept recall"
        return False, _last_capture_status
    _last_capture_status = "accepted: native editor surface recall"
    return True, _last_capture_status


def _ray_from_event(event):
    pose = _pose_from_event(event)
    if pose is None:
        return None
    origin, _rotation, direction = pose
    return origin, direction


def _editor_event(
    context,
    event_type: str,
    value: str,
    *,
    ray: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    target: str = "AUTO",
) -> tuple[bool, str]:
    """Forward a pointer event to the separate native editor event operator."""

    properties: dict[str, Any] = {
        "type": event_type,
        "value": value,
        "pointer_id": 0,
        "unicode": "",
        "shift": False,
        "ctrl": False,
        "alt": False,
        "target": target,
    }
    if ray is None:
        # A release still belongs to pointer 0 even if tracking vanished.
        properties["use_ray"] = False
    else:
        origin, direction = ray
        properties.update(
            use_ray=True,
            ray_origin=origin,
            ray_direction=direction,
        )
    outcome = _native_call(
        "xr_editor_surface_event",
        **properties,
    )
    if event_type != "MOUSEMOVE":
        interaction_log.emit("ui_event", f"{target}: {event_type} {value}: {outcome[1]}", context,
                             action="native_ui", level="INFO" if outcome[0] else "WARNING")
    return outcome


def _editor_pointer(
    context,
    event_type: str,
    value: str,
    *,
    ray: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    target: str = "AUTO",
) -> tuple[bool, str]:
    return _editor_event(context, event_type, value, ray=ray, target=target)


def _editing_contract():
    """Load editing only when a controller gesture actually begins."""

    try:
        from . import editing, interaction
    except ImportError:
        return None, None
    return editing, interaction


def _finish_transaction(context, gesture: _ActiveGesture, method: str) -> Any:
    transaction = gesture.transaction
    if transaction is None:
        return None
    callback = getattr(transaction, method, None)
    if callback is None:
        return None
    return callback(context, gesture.owner)


def _release_pointer(context, gesture: _ActiveGesture) -> tuple[bool, str]:
    ray = None
    if gesture.last_ray_origin is not None and gesture.last_ray_direction is not None:
        ray = gesture.last_ray_origin, gesture.last_ray_direction
    return _editor_pointer(context, gesture.pointer_button, "RELEASE", ray=ray,
                           target=gesture.pointer_target)


def _task_event(context, phase, generation, ray):
    from . import hands

    # The existing task adapter takes hand names only to choose a pointer ID.
    # Controller and editor share pointer 0; this does not claim optical input.
    return hands._task_card_event(context, "LEFT", phase, generation, ray)


def _cancel_active(context, reason: str) -> tuple[bool, str]:
    global _active_gesture
    gesture = _active_gesture
    if gesture is None:
        return True, "No XR Workbench controller gesture is active"
    _release_required.add((gesture.action, gesture.user_path))
    try:
        if gesture.kind == "panel_grab" and gesture.panel_pose is not None:
            _native_call("xr_editor_surface_transform", position=gesture.panel_pose[:3],
                         rotation=gesture.panel_pose[3:7])
        if gesture.kind == "task_card":
            _task_event(context, "CANCEL", gesture.surface_generation, None)
        if gesture.kind == "pointer" and gesture.pointer_pressed:
            # A stale ray could release over a destructive button. Native no-ray
            # release cancels capture; ESC also cancels any native modal tool.
            _editor_event(context, "ESC", "PRESS", ray=None, target=gesture.pointer_target)
            _editor_pointer(context, gesture.pointer_button, "RELEASE", ray=None,
                            target=gesture.pointer_target)
        if gesture.transaction is not None:
            _finish_transaction(context, gesture, "cancel")
        if gesture.restore_selection is not None:
            from . import editing
            editing._restore_object_selection(context, gesture.restore_selection)
        if gesture.stamps:
            _remove_stamps(gesture)
    finally:
        if gesture.second_key is not None:
            _release_required.add(gesture.second_key)
        interaction_log.emit("cancel", reason, context, action=gesture.action,
                             hand=gesture.user_path, level="WARNING")
        pulse(context, gesture.user_path, "cancel")
        _active_gesture = None
        _cage_sync(context)
    return True, reason


def _begin_panel_grab(context, event):
    """Move the real editor rig without activating controls under the ray."""
    global _active_gesture
    if hand_menu_active(context):
        return False, "Close the hand menu with right A before moving desktop panels"
    if _active_gesture is not None or _event_key(event) in _release_required:
        return False, "Release the current controller capture first"
    editing, _interaction = _editing_contract()
    if editing is not None and editing.active_transaction() is not None:
        return False, "Finish the current XR edit before moving its panels"
    pose = _pose_from_event(event)
    if pose is None:
        return False, "Tracked controller pose required"
    try:
        state = context.window_manager.xr_session_state
        hit = state.editor_surface_hit(context, ray_origin=pose[0], ray_direction=pose[2])
        if not hit[0]:
            return False, "Point at a Blender panel, then squeeze the grip"
        original = tuple(state.editor_surface_pose(context))
        q = Quaternion(pose[1])
        relative_position = q.inverted() @ (Vector(original[:3]) - Vector(pose[0]))
        relative_rotation = q.inverted() @ Quaternion(original[3:7])
        _active_gesture = _ActiveGesture(
            "panel_grab", event.xr.action, event.xr.user_path,
            navigation=_navigation_signature(context), panel_pose=original,
            panel_relative=(tuple(relative_position), tuple(relative_rotation)),
            last_input_at=monotonic(),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False, "Movable panels require the updated source-built Blender"
    return True, "Panel rig grabbed; release to place, left B to restore"


def _update_panel_grab(context, event, gesture):
    pose = _pose_from_event(event)
    if pose is None:
        return False
    q = Quaternion(pose[1])
    position = Vector(pose[0]) + q @ Vector(gesture.panel_relative[0])
    rotation = q @ Quaternion(gesture.panel_relative[1])
    return _native_call("xr_editor_surface_transform", position=tuple(position),
                        rotation=tuple(rotation))[0]


def _begin_pointer(context, event, button="LEFTMOUSE") -> tuple[bool, str]:
    global _active_gesture, _pointer_hand
    joined = _try_join(context, event, trigger=True)
    if joined is not None:
        return joined
    if _event_key(event) in _release_required:
        return False, "Release the controller button with tracking active to rearm"
    if _active_gesture is not None:
        return False, "Another XR Workbench gesture is active"
    ray = _ray_from_event(event)
    if ray is None:
        return False, "Controller aim pose is unavailable"
    _pointer_hand = event.xr.user_path
    if button == "RIGHTMOUSE":
        from . import ui

        mode = str(getattr(context, "mode", "OBJECT")).lower()
        mode = {"edit_armature": "armature", "particle": "particle",
                "edit_grease_pencil": "greasepencil_edit"}.get(mode, mode)
        menu = f"VIEW3D_MT_{mode}_context_menu"
        if not hasattr(bpy.types, menu):
            return False, "This mode has no native context menu; right A opens VR tools"
        settings = getattr(getattr(context, "window_manager", None), "xrwb_settings", None)
        global _native_menu_open
        _native_menu_open = True
        with ui._view3d_override(context):
            ok, message = _native_call("xr_hand_menu", visible=True, panel="", menu=menu,
                                       width=float(getattr(settings, "hand_menu_width", 0.48)),
                                       controller=0 if event.xr.user_path == LEFT_HAND else 1)
        if ok:
            # Drain the physical stick release without injecting a right click
            # into the newly opened native UI (or the desktop behind it).
            _active_gesture = _ActiveGesture(
                "menu_open", event.xr.action, event.xr.user_path,
                navigation=_navigation_signature(context), last_input_at=monotonic(),
            )
        return ok, message
    target = "MENU" if hand_menu_active(context) else "EDITOR"
    state = getattr(getattr(context, "window_manager", None), "xr_session_state", None)
    if target == "EDITOR" and state is not None and hasattr(state, "task_card_info"):
        try:
            visible, generation, drawn = state.task_card_info(context)[:3]
            if (
                visible
                and generation > 0
                and generation == drawn
                and _task_event(context, "MOVE", int(generation), ray)
            ):
                if not _task_event(context, "PRESS", int(generation), ray):
                    return False, "Native task card rejected controller press"
                _active_gesture = _ActiveGesture(
                    "task_card",
                    event.xr.action,
                    event.xr.user_path,
                    navigation=_navigation_signature(context),
                    surface_generation=int(generation),
                    last_input_at=monotonic(),
                )
                return True, "Controller task card captured"
        except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
            return False, "Native task card is unavailable"
    ok, message = _editor_pointer(context, "MOUSEMOVE", "NOTHING", ray=ray, target=target)
    if not ok:
        return (
            _begin_scene_tool(context, event)
            if target == "EDITOR"
            else (False, message)
        )
    ok, message = _editor_pointer(context, button, "PRESS", ray=ray, target=target)
    if not ok:
        return False, message
    xr = event.xr
    _active_gesture = _ActiveGesture(
        "pointer",
        xr.action,
        xr.user_path,
        pointer_pressed=True,
        pointer_button=button,
        pointer_target=target,
        navigation=_navigation_signature(context),
        last_ray_origin=ray[0],
        last_ray_direction=ray[1],
        last_input_at=monotonic(),
    )
    return True, message


def _begin_component(context, event, tool):
    """Edit Mode: trigger picks / sweeps vertices, edges or faces, or extrudes them."""
    global _active_gesture
    from . import component_mode, interaction
    if hand_menu_active(context):
        return False, "Close the hand menu first"
    pose = _pose_from_event(event)
    if pose is None:
        return False, "Tracked controller pose required"
    owner = interaction.CaptureOwner(interaction.InputProvenance.CONTROLLER, event.xr.user_path, 0)
    try:
        stroke = component_mode.begin(context, tool, interaction.Pose(tuple(pose[0]), tuple(pose[1])), owner)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, str(exc)
    _active_gesture = _ActiveGesture(
        "move", event.xr.action, event.xr.user_path, owner, stroke,
        navigation=_navigation_signature(context), last_input_at=monotonic())
    return True, ("Pull to extrude; release to keep" if tool == "edit_extrude"
                  else "Sweep to add more; release to keep")


def _begin_node_link(context, event):
    """Any tool: a trigger on a node-garden socket starts dragging a link."""
    global _active_gesture
    from . import interaction, node_garden
    if not node_garden.is_out(context):
        return None, ""
    pose = _pose_from_event(event)
    if pose is None:
        return None, ""
    owner = interaction.CaptureOwner(interaction.InputProvenance.CONTROLLER, event.xr.user_path, 0)
    try:
        stroke = node_garden.begin_link(context, interaction.Pose(tuple(pose[0]), tuple(pose[1])), owner)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, str(exc)
    if stroke is None:
        return None, ""
    _active_gesture = _ActiveGesture(
        "move", event.xr.action, event.xr.user_path, owner, stroke,
        navigation=_navigation_signature(context), last_input_at=monotonic())
    return True, "Dragging a link: release on a socket"


def _tower_switch(context, event):
    """Any tool: a trigger on a modifier tower block shows or hides that modifier; None elsewhere."""
    from . import editing, modifier_tower
    ray = _ray_from_event(event)
    block = modifier_tower.block_at(context, *ray) if ray is not None else None
    if block is None:
        return None
    _release_required.add(_event_key(event))  # Done on press: drain this trigger until it is let go.
    try:
        return True, modifier_tower.switch(context, block[modifier_tower.BLOCK_KEY])
    except (editing.EditRejected, RuntimeError, ReferenceError, ValueError) as exc:
        return False, str(exc)


def _begin_scene_tool(context, event):
    """UI has first refusal; the same trigger then operates the chosen scene tool."""
    from . import hands

    global _active_gesture
    settings = context.window_manager.xrwb_settings
    tool = str(getattr(settings, "edit_tool", "select"))
    linked, message = _begin_node_link(context, event)
    if linked is not None:
        return linked, message
    switched = _tower_switch(context, event)
    if switched is not None:
        return switched
    if context.mode == "EDIT_MESH":
        return _begin_component(context, event, tool)
    if tool in {"extrude", "extrude_free", "inset", "face_scale", "select_edge", "bevel_edge",
                "draw_cube", "draw_sphere", "draw_cylinder",
                "cut_difference", "cut_union", "cut_intersect"}:
        from . import interaction

        if tool.startswith(("draw_", "cut_")):
            from . import draw_editing as adapter
        elif tool in {"select_edge", "bevel_edge"}:
            from . import edge_editing as adapter
        else:
            from . import face_editing as adapter

        pose = _pose_from_event(event)
        if pose is None:
            return False, "Tracked controller pose required"
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.CONTROLLER, event.xr.user_path, 0)
        try:
            options = {}
            if tool in {"select_edge", "bevel_edge"}:
                options["world_units_per_metre"] = float(
                    bpy.types.XrSessionState.hand_tracking_world_transform(context)[0])
                adapter.start_overlay()
            transaction = adapter.begin(context, pose[0], pose[1], tool=tool, owner=owner, **options)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            return False, str(exc)
        _active_gesture = _ActiveGesture(
            "move", event.xr.action, event.xr.user_path, owner, transaction,
            navigation=_navigation_signature(context), last_input_at=monotonic())
        if tool == "face_scale":
            _configure_scale_drag(context, _active_gesture, pose)
        return True, f"{tool} captured; move the trigger hand, release to confirm"
    from . import physical_tools

    contact_tools = physical_tools.optional("contact_tools")
    if tool in physical_tools.TOOLS or (contact_tools is not None and tool in contact_tools.TOOLS):
        from . import interaction

        pose = _pose_from_event(event)
        if pose is None:
            return False, "Tracked controller pose required"
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.CONTROLLER, event.xr.user_path, 0)
        try:
            transaction = physical_tools.begin(context, pose[0], pose[1], tool=tool, owner=owner)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            return False, str(exc)
        if transaction is None:
            # An instant action (a block dipped into the spray): drain this press.
            _release_required.add(_event_key(event))
            return True, "Retopo started" if tool == "retopo_loom" else "Material loaded into the spray"
        _active_gesture = _ActiveGesture(
            "move", event.xr.action, event.xr.user_path, owner, transaction,
            navigation=_navigation_signature(context), last_input_at=monotonic())
        return True, f"{tool} captured; move the trigger hand, release to confirm"
    if tool in {"move", "rotate", "scale"}:
        ray = _ray_from_event(event)
        try:
            target = hands._scene_raycast_target(context, ray) if ray else None
        except RuntimeError as exc:
            return False, str(exc)
        if target is not None and target.select_get():
            if (target not in context.selected_editable_objects or target.hide_select
                    or not target.visible_get(view_layer=context.view_layer)):
                return False, "The pointed object is not visible and editable"
            return _begin_move(context, event)
    return _begin_selection(context, event)


def _begin_selection(context, event) -> tuple[bool, str]:
    """Reuse the undoable scene-selection adapter when no panel was hit."""
    global _active_gesture
    if hand_menu_active(context):
        return False, "Close the hand menu with right A before selecting scene objects"
    from . import hands

    editing, interaction = _editing_contract()
    pose = _pose_from_event(event)
    if editing is None or interaction is None or pose is None:
        return False, "Object selection adapter or controller pose is unavailable"
    settings = context.window_manager.xrwb_settings
    select_tool = str(getattr(settings, "edit_tool", "")) == "select"
    try:
        target = hands._scene_raycast_target(context, (pose[0], pose[2]))
        if target is None:
            if select_tool and context.selected_objects:
                return _clear_selection(context, event)
            return False, "Controller ray hit no panel or object"
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.CONTROLLER, event.xr.user_path, 0
        )
        request = interaction.SelectionRequest(
            owner,
            0,
            interaction.Pose(pose[0], pose[1]),
            _world_units_per_metre(context),
            float(
                getattr(
                    context.window_manager.xrwb_settings, "selection_click_slop_m", 0.01
                )
            ),
        )
        toggle = select_tool and bool(getattr(settings, "multi_select", False))
        transaction = editing.begin_object_selection(context, request, target, toggle=toggle)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"Object selection could not begin: {exc}"
    _active_gesture = _ActiveGesture(
        "selection",
        event.xr.action,
        event.xr.user_path,
        owner,
        transaction,
        navigation=_navigation_signature(context),
        last_input_at=monotonic(),
    )
    return True, "Controller object selection captured"


def _clear_selection(context, event) -> tuple[bool, str]:
    """Select tool on empty space: deselect everything, as one undo step."""
    editing, _interaction = _editing_contract()
    if context.mode != "OBJECT" or editing is None or not editing.undo_available(context):
        return False, "Clearing the selection needs Object Mode and Global Undo"
    snapshot = editing._selection_snapshot(context)
    for obj in tuple(context.selected_objects):
        obj.select_set(False)
    context.view_layer.objects.active = None
    try:
        if "FINISHED" not in _push_undo(message="XR Clear Selection"):
            raise RuntimeError("Blender rejected the undo entry")
    except RuntimeError as exc:
        editing._restore_object_selection(context, snapshot)
        return False, f"Selection kept: {exc}"
    key = _event_key(event)
    if key is not None:
        _release_required.add(key)  # Drain this trigger press.
    return True, "Selection cleared (pointed at empty space)"


def _try_join(context, event, trigger=False):
    """The other hand's trigger or grip joins a held object transform.

    Returns None when this press is not a join, so normal routing continues.
    """
    gesture = _active_gesture
    key = _event_key(event)
    if (gesture is None or key is None or gesture.kind != "move" or gesture.second_key is not None
            or key[1] == gesture.user_path or gesture.primary_position is None):
        return None
    if _is_sculpt(gesture.transaction):
        return _join_twin(context, event, gesture, key) if trigger else None
    editing, interaction = _editing_contract()
    if (editing is None or not isinstance(gesture.transaction, editing.EditTransaction)
            or gesture.transaction.mode != "OBJECT"):
        return None
    pose = _pose_from_event(event)
    if pose is None:
        return False, "Second controller pose is unavailable"
    first = Vector(gesture.primary_position)
    span = Vector(pose[0]) - first
    if span.length < 0.02 * _world_units_per_metre(context):
        return False, "Hands are too close together to stretch"
    middle = first + span * 0.5
    try:
        gesture.transaction.rebase(
            context, interaction.Pose(tuple(middle), tuple(gesture.primary_rotation)),
            interaction.TransformTool.GRAB)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"Two-hand grab could not start: {exc}"
    gesture.second_key = key
    gesture.second_position = tuple(pose[0])
    gesture.two_hand_start = (tuple(middle), tuple(span), tuple(gesture.primary_rotation))
    gesture.scale_origin = None
    gesture.reel = 0.0
    axis_name = (_choose_stretch_axis(context, gesture.transaction, span)
                 if _feel("two_hand", "SCALE_ROTATE") == "AXIS" else "")
    if not axis_name:
        gesture.transaction.stretch_axis = None
    pulse(context, key[1], "begin")
    if axis_name:
        return True, f"Stretching along {axis_name}: pull apart or push together"
    return True, "Both hands: pull apart to scale, twist to rotate, move to carry"


def _belt_grab(context, event):
    """Tool Belt: a grip squeezed near a belt slot swaps to that tool. None when not near one."""
    if not tool_wheel._belt or _active_gesture is not None:
        return None
    pose = _pose_from_event(event)
    picked = tool_wheel.belt_pick(pose[0], _world_units_per_metre(context)) if pose is not None else None
    if picked is None:
        return None
    _release_required.add(_event_key(event))  # Done on press: drain this grip until it is let go.
    from . import ui
    with ui._view3d_override(context):
        result = bpy.ops.xrwb.set_edit_tool(tool=picked[0])
    return "FINISHED" in result, f"Tool: {picked[1]}"


def _is_sculpt(transaction):
    from . import physical_tools
    return isinstance(transaction, physical_tools.SculptStroke)


def _twin_sample(event, key):
    _editing, interaction = _editing_contract()
    pose = _pose_from_event(event)
    if pose is None:
        return None, None
    owner = interaction.CaptureOwner(interaction.InputProvenance.CONTROLLER, key[1], 0)
    return owner, interaction.InputSample(owner=owner, target_generation=0,
                                          pose=interaction.Pose(tuple(pose[0]), tuple(pose[1])),
                                          timestamp_seconds=monotonic())


def _join_twin(context, event, gesture, key):
    """Twin Hands: the other trigger adds a second brush to the held sculpt stroke."""
    _owner, sample = _twin_sample(event, key)
    if sample is None:
        return False, "Second controller pose is unavailable"
    result = gesture.transaction.update_secondary(context, sample)
    if result.decision.value != "apply":
        return False, result.message
    gesture.second_key = key
    return True, "Twin Hands: both brushes shape the surface; let go of both to finish"


def _twin_second_hand(context, event, gesture) -> set[str]:
    global _active_gesture
    owner, sample = _twin_sample(event, gesture.second_key)
    if sample is None:
        _cancel_active(context, "Second controller lost tracking; stroke cancelled")
        return {"CANCELLED"}
    gesture.last_input_at = monotonic()
    transaction = gesture.transaction
    if getattr(event, "value", "") == "RELEASE":
        result = transaction.release(context, owner)
        gesture.second_key = None
        if transaction.finished:  # The first brush was already up: that was the commit.
            interaction_log.emit("commit", getattr(result, "message", "Stroke committed"), context,
                                 action=gesture.action, hand=_event_hand(event))
            pulse(context, _event_hand(event), "commit")
            feel_commit(context, gesture.user_path, transaction)
            _active_gesture = None
        return {"FINISHED"}
    transaction.pressure = _trigger_pressure(context, event)
    result = transaction.update_secondary(context, sample)
    if result.decision.value == "cancel_required":
        _cancel_active(context, result.message)
        return {"CANCELLED"}
    _polish_feedback(context, gesture, result)
    return {"RUNNING_MODAL"}


STRETCH_ALIGN_DEGREES = 25.0


def _choose_stretch_axis(context, transaction, span):
    """Hands lined up with one of the object's own axes stretch only that axis."""
    objects = [item.object_ref for item in transaction.original_objects]
    active = context.view_layer.objects.active
    obj = active if active in objects else objects[0]
    direction = Vector(span).normalized()
    best, best_axis, best_name = 0.0, None, ""
    for index, name in enumerate("XYZ"):
        axis = obj.matrix_world.to_3x3().col[index]
        if axis.length < 1.0e-9:
            continue
        axis = axis.normalized()
        alignment = abs(axis.dot(direction))
        if alignment > best:
            best, best_axis, best_name = alignment, axis, name
    if best_axis is not None and best >= math_cos(radians(STRETCH_ALIGN_DEGREES)):
        transaction.stretch_axis = tuple(best_axis)
        return f"its {best_name} axis"
    transaction.stretch_axis = None
    return ""


TWO_HAND_TURN_DEADZONE = radians(6.0)
TWO_HAND_SCALE_DEADZONE = 0.03  # log-ratio: about 3 % either way.


def _soft_deadzone(amount, zone):
    """0 inside the zone, ramping back to the exact value by twice the zone.

    Small wobbles do nothing, deliberate motion is exact (no permanent offset).
    """
    size = abs(amount)
    if size <= zone:
        return 0.0
    if size >= 2.0 * zone:
        return amount
    return (2.0 * (size - zone)) * (1.0 if amount > 0 else -1.0)


def _deadzoned_swing(swing):
    """Ignore the small tilts of a straight pull; beyond them, turn exactly."""
    angle = _soft_deadzone(swing.angle, TWO_HAND_TURN_DEADZONE)
    return Quaternion(swing.axis, angle) if angle else Quaternion()


def _deadzoned_scale(ratio):
    from math import exp, log
    return exp(_soft_deadzone(log(max(ratio, 1.0e-9)), TWO_HAND_SCALE_DEADZONE))


def _two_hand_sample_pose(gesture):
    """Midpoint, swing rotation and distance ratio between both hands.

    Settings > Tools > Two Hands picks Scale + Turn (default), Scale only, or
    Stretch (along the object axis the hands line up with). Small wobbles
    neither turn nor scale, so a clean pull is a clean resize.
    """
    start_middle, start_span, start_rotation = gesture.two_hand_start
    first, second = Vector(gesture.primary_position), Vector(gesture.second_position)
    span = second - first
    start_span = Vector(start_span)
    if span.length < 1.0e-6 or start_span.length < 1.0e-6:
        return None
    mode = _feel("two_hand", "SCALE_ROTATE")
    swing = Quaternion() if mode == "SCALE" else _deadzoned_swing(start_span.rotation_difference(span))
    rotation = swing @ Quaternion(start_rotation)
    return (first + second) * 0.5, rotation, _deadzoned_scale(span.length / start_span.length)


def _end_two_hand(context, gesture):
    """Second hand released: keep the result, continue following the first hand."""
    _editing, interaction = _editing_contract()
    gesture.second_key = None
    gesture.second_position = None
    gesture.two_hand_start = None
    gesture.reel = 0.0
    gesture.transaction.rebase(
        context, interaction.Pose(tuple(gesture.primary_position), tuple(gesture.primary_rotation)),
        interaction.TransformTool.GRAB)


def _begin_world_grip(context, event) -> tuple[bool, str]:
    """Off-hand grip: join a held object, pick up what it points at, or move the world.

    Squeezing both grips on one object is the natural way to resize it, so the
    second grip always joins first. On an object it picks that object up. On
    nothing it grabs the world, like Blender's own navigation grab.
    """
    global _active_gesture
    joined = _try_join(context, event)
    if joined is not None:
        return joined
    belt = _belt_grab(context, event)
    if belt is not None:
        return belt
    if _active_gesture is not None and _active_gesture.kind == "world":
        return _start_or_join_world(context, event)
    if _active_gesture is not None:
        return False, "Another XR Workbench gesture is active"
    if not hand_menu_active(context) and _ray_on_panel(context, event):
        return _begin_panel_grab(context, event)
    if not hand_menu_active(context):
        from . import hands
        ray = _ray_from_event(event)
        try:
            target = hands._scene_raycast_target(context, ray) if ray else None
        except RuntimeError:
            target = None
        if target is not None and not target.library:
            return _begin_move(context, event, grip=True)
    return _start_or_join_world(context, event)


def _aim_positions(context) -> dict:
    """World aim positions of both tracked controllers, read in the same frame."""
    positions = {}
    try:
        state = context.window_manager.xr_session_state
        actionmap = _find_actionmap(state)
        aim = next(item for item in actionmap.actionmap_items if item.name == "controller_aim")
        for index, entry in enumerate(aim.user_paths):
            if state.controller_aim_active_get(context, index):
                location = tuple(state.controller_aim_location_get(context, index))
                if len(location) == 3 and all(isfinite(v) for v in location):
                    positions[entry.path] = location
    except (AttributeError, ReferenceError, RuntimeError, StopIteration, TypeError, ValueError):
        pass
    return positions


def _world_hands(context, gesture) -> dict:
    """Latest world positions of the hands holding the world (same-frame when possible)."""
    hands = {gesture.user_path: gesture.primary_position}
    if gesture.second_key is not None:
        hands[gesture.second_key[1]] = gesture.second_position
    fresh = _aim_positions(context)
    return {hand: tuple(fresh.get(hand, position)) for hand, position in hands.items() if position is not None
            or hand in fresh}


def _start_or_join_world(context, event) -> tuple[bool, str]:
    """Grip on empty space: hold the world. A second grip joins to turn and resize it."""
    global _active_gesture
    from . import navigation
    pose = _pose_from_event(event)
    if pose is None:
        return False, "Controller pose is unavailable"
    state = context.window_manager.xr_session_state
    xr = event.xr
    gesture = _active_gesture
    if (gesture is not None and gesture.kind == "world" and gesture.second_key is None
            and xr.user_path != gesture.user_path):
        gesture.second_key = (xr.action, xr.user_path)
        gesture.second_position = tuple(pose[0])
        gesture.world.rebase(state, _world_hands(context, gesture))
        gesture.world.order = [gesture.user_path, xr.user_path]
        gesture.detent = gesture.world.detent(state)
        return True, "Both hands on the world: pull apart to shrink yourself, together to grow, turn to rotate"
    if gesture is not None:
        return False, "Another XR Workbench gesture is active"
    fresh = _aim_positions(context).get(xr.user_path, tuple(pose[0]))
    _active_gesture = _ActiveGesture("world", xr.action, xr.user_path, navigation=_navigation_signature(context),
                                     last_input_at=monotonic(), primary_position=tuple(fresh))
    _active_gesture.world = navigation.WorldGrab(state, xr.user_path, fresh)
    return True, "Holding the world: move your hand to move it; the other grip joins to turn and resize"


def _update_world_grip(context, event, gesture) -> set[str]:
    """Primary hand of a world grab: solve the navigation from both hands this frame."""
    global _active_gesture
    pose = _pose_from_event(event)
    gesture.last_input_at = monotonic()
    state = getattr(context.window_manager, "xr_session_state", None)
    if getattr(event, "value", "") == "RELEASE" or pose is None:
        if gesture.second_key is not None and state is not None:
            # The other hand keeps holding: it becomes the only hand.
            second = gesture.second_key
            hands = _world_hands(context, gesture)
            gesture.world.leave(state, gesture.user_path, hands)
            gesture.action, gesture.user_path = second
            gesture.primary_position, gesture.second_key, gesture.second_position = hands[second[1]], None, None
            gesture.navigation = _navigation_signature(context)
            return {"FINISHED"}
        _active_gesture = None
        return {"FINISHED"}
    gesture.primary_position = tuple(pose[0])
    try:
        gesture.world.update(state, _world_hands(context, gesture))
        gesture.navigation = _navigation_signature(context)
    except (AttributeError, KeyError, ReferenceError, RuntimeError, TypeError, ValueError):
        _active_gesture = None
        return {"CANCELLED"}
    gesture.readout = gesture.world.readout
    step = gesture.world.detent(state)
    if gesture.detent is not None and step != gesture.detent and _feel("detents", True):
        pulse(context, gesture.user_path, "commit" if step[1] else "detent")  # Firm click at exactly 1x.
    gesture.detent = step
    return {"RUNNING_MODAL"}


def _world_second_hand(context, event, gesture) -> set[str]:
    """Second hand of a world grab: record its pose; releasing hands back to one hand."""
    pose = _pose_from_event(event)
    gesture.last_input_at = monotonic()
    if getattr(event, "value", "") == "RELEASE" or pose is None:
        state = context.window_manager.xr_session_state
        hands = _world_hands(context, gesture)
        gesture.world.leave(state, gesture.second_key[1], hands)
        gesture.second_key, gesture.second_position = None, None
        gesture.navigation = _navigation_signature(context)
        return {"FINISHED"}
    gesture.second_position = tuple(pose[0])
    return {"RUNNING_MODAL"}


def _grab_pointed_object(context, event):
    """Grip on an unselected object makes it the selection, like picking it up."""
    from . import editing, hands

    ray = _ray_from_event(event)
    try:
        target = hands._scene_raycast_target(context, ray) if ray else None
    except RuntimeError:
        return None
    if (target is None or target.select_get() or target not in context.selectable_objects
            or not target.visible_get(view_layer=context.view_layer) or target.library):
        return None
    snapshot = editing._selection_snapshot(context)
    for obj in tuple(context.selected_objects):
        obj.select_set(False)
    target.select_set(True)
    context.view_layer.objects.active = target
    return snapshot


def _grip_on_nothing(context, event) -> bool:
    from . import hands
    ray = _ray_from_event(event)
    try:
        return ray is not None and hands._scene_raycast_target(context, ray) is None
    except RuntimeError:
        return False


def _ray_on_panel(context, event) -> bool:
    """True when the controller ray is on a floating Blender desktop panel."""
    pose = _pose_from_event(event)
    try:
        state = context.window_manager.xr_session_state
        return bool(pose is not None and state.editor_surface_hit(
            context, ray_origin=pose[0], ray_direction=pose[2])[0])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _begin_move(context, event, grip=False) -> tuple[bool, str]:
    global _active_gesture
    joined = _try_join(context, event)
    if joined is not None:
        return joined
    belt = _belt_grab(context, event) if grip else None
    if belt is not None:
        return belt
    if grip and _active_gesture is not None and _active_gesture.kind == "world":
        return _start_or_join_world(context, event)  # Both grips on the world: turn and resize it.
    if grip and _active_gesture is None and not hand_menu_active(context) and _ray_on_panel(context, event):
        # Grip a desktop panel to move it, like picking up a window (all controllers).
        return _begin_panel_grab(context, event)
    if (grip and _active_gesture is None and context.mode == "OBJECT" and not hand_menu_active(context)
            and (_feel("grip_empty", "WORLD") == "WORLD" or not context.selected_objects)
            and _grip_on_nothing(context, event)):
        return _start_or_join_world(context, event)  # Grip on empty space holds the world.
    if hand_menu_active(context):
        return False, "Close the hand menu with right A before transforming objects"
    if _event_key(event) in _release_required:
        return False, "Release the controller button with tracking active to rearm"
    if _active_gesture is not None:
        return False, "Another XR Workbench gesture is active"
    editing, interaction = _editing_contract()
    if editing is None or interaction is None:
        return False, "unavailable: editing transaction adapter is not installed"
    pose = _pose_from_event(event)
    if pose is None:
        return False, "Controller aim pose is unavailable"
    origin, rotation, direction = pose
    xr = event.xr
    restore_selection = None
    try:
        settings = getattr(context.window_manager, "xrwb_settings", None)
        selected_tool = str(getattr(settings, "edit_tool", "move"))
        constraint = interaction.Constraint(
            str(getattr(settings, "edit_constraint", interaction.Constraint.FREE.value))
        )
        # Grip picks up what it points at and carries it rigidly, turning with
        # the hand. Explicit Rotate/Scale tools and world constraints keep their
        # precise single-purpose behaviour.
        if selected_tool in {"rotate", "scale"}:
            tool = interaction.TransformTool(selected_tool)
        elif grip and constraint is interaction.Constraint.FREE:
            tool = interaction.TransformTool.GRAB
        else:
            tool = interaction.TransformTool.MOVE
        if grip and context.mode == "OBJECT":
            restore_selection = _grab_pointed_object(context, event)
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.CONTROLLER, xr.user_path, 0
        )
        request = interaction.EditRequest(
            owner=owner,
            tool=tool,
            target_generation=0,
            pose=interaction.Pose(origin, rotation),
            options=interaction.TransformOptions(
                constraint=constraint,
                translation_snap=float(getattr(settings, "translation_snap", 0.0)),
                rotation_snap_radians=radians(
                    float(getattr(settings, "rotation_snap_degrees", 0.0))
                ),
                scale_snap=float(getattr(settings, "scale_snap", 0.0)),
            ),
        )
        transaction = editing.begin(context, request)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        if restore_selection is not None:
            editing._restore_object_selection(context, restore_selection)
        return False, f"Move transaction could not begin: {exc}"
    if transaction is None:
        if restore_selection is not None:
            editing._restore_object_selection(context, restore_selection)
        return False, "Move transaction was rejected"
    _active_gesture = _ActiveGesture(
        "move",
        xr.action,
        xr.user_path,
        owner,
        transaction,
        navigation=_navigation_signature(context),
        last_input_at=monotonic(),
        primary_position=tuple(origin),
        primary_rotation=tuple(rotation),
        primary_direction=tuple(direction),
        restore_selection=restore_selection,
    )
    if tool is interaction.TransformTool.SCALE:
        _configure_scale_drag(context, _active_gesture, pose)
    return True, f"XR Workbench {tool.value} capture started"


def _world_units_per_metre(context):
    from . import runtime
    scale = (float(bpy.types.XrSessionState.hand_tracking_world_transform(context)[0])
             if runtime.session_running(context) else 1.0)
    if not isfinite(scale) or not 1.0e-6 <= scale <= 1.0e6:
        raise ValueError("XR world scale is outside the supported editing range")
    return scale


def _configure_scale_drag(context, gesture, pose):
    """Freeze a physical sideways axis: range and mesh size never alter sensitivity."""
    distance = float(getattr(context.window_manager.xrwb_settings, "scale_drag_distance_m", .2))
    # RNA stores a float32: its exact 3 cm slider endpoint is slightly below .03.
    if not isfinite(distance) or not .03 - 1e-6 <= distance <= 1.0:
        raise ValueError("Scale drag distance must be between 3 cm and 1 m")
    gesture.scale_origin = pose[0]
    gesture.scale_axis = tuple(Quaternion(pose[1]) @ Vector((1, 0, 0)))
    gesture.scale_distance = distance * _world_units_per_metre(context)


def _update_move(context, event, gesture: _ActiveGesture) -> tuple[bool, str]:
    _editing, interaction = _editing_contract()
    if interaction is None:
        return False, "editing transaction adapter disappeared"
    pose = _pose_from_event(event)
    if pose is None:
        return False, "Controller aim pose is unavailable"
    origin, rotation, direction = pose
    gesture.primary_position = tuple(origin)
    gesture.primary_rotation = tuple(rotation)
    gesture.primary_direction = tuple(direction)
    if hasattr(gesture.transaction, "pressure"):
        gesture.transaction.pressure = _trigger_pressure(context, event)
        if getattr(gesture.transaction, "contact_mode", False):
            # Contact mode: pushing the tip in is the pressure; the trigger only engages the tool.
            from . import physical_tools
            touch = physical_tools.current_contact(gesture.user_path)
            if touch is not None:
                gesture.transaction.pressure = max(0.15, float(touch.pressure))
    return _apply_move(context, gesture)


def _trigger_pressure(context, event) -> float:
    """0.15..1 from how far past its threshold the trigger is pulled."""
    xr = getattr(event, "xr", None)
    settings = getattr(getattr(context, "window_manager", None), "xrwb_settings", None)
    if xr is None or not getattr(settings, "use_pressure", True):
        return 1.0
    try:
        value, threshold = float(xr.state[0]), float(xr.float_threshold)
    except (AttributeError, IndexError, TypeError, ValueError):
        return 1.0
    if not isfinite(value) or not 0.0 <= threshold < 1.0:
        return 1.0
    return max(0.15, min(1.0, (value - threshold) / (1.0 - threshold)))


def _apply_move(context, gesture: _ActiveGesture) -> tuple[bool, str]:
    """Feed the transaction from the latest one- or two-hand pose."""
    _editing, interaction = _editing_contract()
    try:
        scale = None
        if gesture.second_key is not None:
            two_hand = _two_hand_sample_pose(gesture)
            if two_hand is None:
                return True, "Hands overlap; holding the last stretch"
            origin, rotation, scale = two_hand
        else:
            origin, rotation = _virtual_hand(gesture, context)
        if gesture.second_key is None and gesture.scale_origin is not None:
            drag = (Vector(origin) - Vector(gesture.scale_origin)).dot(Vector(gesture.scale_axis))
            scale = 2.0 ** max(-10.0, min(10.0, drag / gesture.scale_distance))
        sample = interaction.InputSample(
            owner=gesture.owner,
            target_generation=0,
            pose=interaction.Pose(tuple(origin), tuple(rotation)),
            timestamp_seconds=monotonic(),
            uniform_scale=scale,
        )
        result = gesture.transaction.update(context, sample)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return False, f"Move transaction update failed: {exc}"
    decision = getattr(getattr(result, "decision", None), "value", "apply")
    if decision in {"cancel_required", "rejected"}:
        return False, getattr(result, "message", "Move transaction rejected input")
    _polish_feedback(context, gesture, result)
    return True, getattr(result, "message", "XR Workbench move updated")


def _polish_feedback(context, gesture, result) -> None:
    """Refresh the live readout; tick once per detent crossed (never breaks an edit)."""
    from . import polish
    try:
        from . import live_cage
        gesture.readout = ((polish.readout(gesture.transaction, gesture) or str(getattr(result, "message", "")))
                           + live_cage.readout_suffix())
        step = polish.detent_index(gesture.transaction)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return
    if getattr(gesture.transaction, "pulse", False) and _feel("detents", True):
        pulse(context, gesture.user_path, "detent")  # Lab Dial / Grow crossed a detent.
    if step is not None and gesture.detent is not None and step != gesture.detent and _feel("detents", True):
        pulse(context, gesture.user_path, "detent")
    gesture.detent = step


def dispatch_xr_event(context, event) -> set[str]:
    """Route matching XR events on Blender's main thread without a timer."""

    global _active_gesture
    drained = _drain_cancelled_input(event)
    if drained is not None:
        return drained
    gesture = _active_gesture
    if gesture is not None and gesture.second_key is not None and _event_key(event) == gesture.second_key:
        return _dispatch_second_hand(context, event, gesture)
    if gesture is None or not _route_matches(event, gesture.action, gesture.user_path):
        return {"PASS_THROUGH"}
    if gesture.kind == "world":
        return _update_world_grip(context, event, gesture)
    if gesture.navigation != _navigation_signature(context):
        _cancel_active(context, "Navigation changed during controller capture")
        return {"CANCELLED"}
    gesture.last_input_at = monotonic()
    if gesture.kind == "menu_open":
        if _pose_from_event(event) is None:
            _cancel_active(context, "Controller tracking lost while opening the hand menu")
            return {"CANCELLED"}
        if getattr(event, "value", "") == "RELEASE":
            _active_gesture = None
            return {"FINISHED"}
        return {"RUNNING_MODAL"}
    if gesture.kind == "panel_grab":
        if not _update_panel_grab(context, event, gesture):
            _cancel_active(context, "Panel grab lost tracking; original placement restored")
            return {"CANCELLED"}
        if getattr(event, "value", "") == "RELEASE":
            _active_gesture = None
            return {"FINISHED"}
        return {"RUNNING_MODAL"}
    if gesture.kind == "task_card":
        ray = _ray_from_event(event)
        phase = "RELEASE" if getattr(event, "value", "") == "RELEASE" else "MOVE"
        if ray is None or not _task_event(
            context, phase, gesture.surface_generation, ray
        ):
            _cancel_active(context, "Controller task card cancelled")
            return {"CANCELLED"}
        if phase == "RELEASE":
            _active_gesture = None
            return {"FINISHED"}
        return {"RUNNING_MODAL"}
    if gesture.kind == "pointer":
        ray = _ray_from_event(event)
        if ray is None:
            _cancel_active(context, "Controller tracking lost; pointer cancelled")
            return {"CANCELLED"}
        gesture.last_ray_origin, gesture.last_ray_direction = ray
        if getattr(event, "value", "") == "RELEASE":
            ok, _ = _release_pointer(context, gesture)
            _active_gesture = None
            return {"FINISHED"} if ok else {"CANCELLED"}
        ok, _ = _editor_pointer(context, "MOUSEMOVE", "NOTHING", ray=ray,
                                target=gesture.pointer_target)
        if not ok:
            _cancel_active(context, "Native editor rejected controller capture")
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}
    # Validate/apply the final sample BEFORE committing, including selection
    # slop and target generation checks. A release is not evidence of tracking.
    ok, message = _update_move(context, event, gesture)
    if not ok:
        _cancel_active(context, message)
        return {"CANCELLED"}
    if getattr(event, "value", "") == "RELEASE":
        twin = _is_sculpt(gesture.transaction)
        result = _finish_transaction(context, gesture, "release" if twin else "commit")
        if twin and not gesture.transaction.finished:
            return {"FINISHED"}  # The second brush is still down; its release commits the stroke.
        if result is False or getattr(result, "applied", True) is False:
            _cancel_active(context, getattr(result, "message", "Controller edit commit rejected"))
            return {"CANCELLED"}
        interaction_log.emit("commit", getattr(result, "message", "Edit committed"), context,
                             action=gesture.action, hand=gesture.user_path)
        if gesture.stamps and "one Blender undo step" not in str(getattr(result, "message", "")):
            # The held object ended where it started, but stamped copies still
            # need their own undo step.
            try:
                _push_undo(message="XR Stamp Copies")
            except RuntimeError:
                pass
        pulse(context, gesture.user_path, "commit")
        feel_commit(context, gesture.user_path, gesture.transaction)
        if gesture.second_key is not None:
            _release_required.add(gesture.second_key)  # Drain the still-held second hand.
        _active_gesture = None
        _autokey(context, gesture)
        return {"FINISHED"}
    return {"RUNNING_MODAL"}


def _surface_hand(context, gesture):
    """Surface mode: the held group's base follows where the ray lands, upright."""
    from . import physical_tools

    centre, anchor, rotation, dims, targets = gesture.surface_base
    location, _yaw, _hit = physical_tools.placement(
        context, Vector(gesture.primary_position), Vector(gesture.primary_direction), dims, exclude=targets)
    # Pure translation from the rebased anchor: carry the group's centre to
    # the centre of the same box resting where the ray lands.
    return Vector(anchor) + (location - Vector(centre)), Quaternion(rotation)


def _virtual_hand(gesture, context=None):
    """One-hand pose after precision slow-down, stick reel and drop offset."""
    if gesture.hold_mode == 3 and gesture.surface_base is not None and context is not None:
        return _surface_hand(context, gesture)
    origin, rotation = Vector(gesture.primary_position), Quaternion(gesture.primary_rotation)
    if gesture.hold_mode == 1 and gesture.precision_anchor is not None:
        anchor_position, anchor_rotation = gesture.precision_anchor
        anchor_position = Vector(anchor_position)
        factor = max(0.05, min(1.0, _feel("precision", PRECISION_FACTOR)))
        origin = anchor_position + (origin - anchor_position) * factor
        rotation = Quaternion(anchor_rotation).slerp(rotation, factor)
    if gesture.reel and gesture.primary_direction is not None:
        origin = origin + Vector(gesture.primary_direction) * gesture.reel
    return origin + Vector(gesture.offset), rotation


def _held_object_transaction(gesture):
    editing, _interaction = _editing_contract()
    if (gesture is None or gesture.kind != "move" or editing is None
            or not isinstance(gesture.transaction, editing.EditTransaction)
            or gesture.transaction.mode != "OBJECT" or gesture.transaction.finished):
        return None
    return gesture.transaction


def cycle_hold_mode(context) -> tuple[bool, str]:
    """Right stick click while holding: Follow -> Precision -> Snap -> Follow."""
    gesture = _active_gesture
    transaction = _held_object_transaction(gesture)
    if transaction is None or gesture.second_key is not None or gesture.primary_position is None:
        return False, "Hold an object with one hand to change how it follows"
    _editing, interaction = _editing_contract()
    gesture.hold_mode = (gesture.hold_mode + 1) % len(HOLD_MODES)
    gesture.precision_anchor = (gesture.primary_position, gesture.primary_rotation)
    units = _world_units_per_metre(context)
    transaction.snap = ((max(0.1, _feel("snap_grid_cm", 5.0)) / 100.0 * units,
                         radians(max(1.0, _feel("snap_angle", 15.0))))
                        if gesture.hold_mode == 2 else None)
    # Continue from exactly where the object is now: no jump between modes.
    gesture.surface_base = None
    origin, rotation = _virtual_hand(gesture)
    transaction.rebase(context, interaction.Pose(tuple(origin), tuple(rotation)))
    if gesture.hold_mode == 3:
        # The group's bounding box centre now; placement() returns such a centre.
        objects = [item.object_ref for item in transaction.original_objects]
        corners = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
        low = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
        high = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
        gesture.surface_base = (tuple((low + high) / 2), tuple(origin), tuple(rotation), tuple(high - low),
                                set(objects))
    ok, message = _apply_move(context, gesture)
    if not ok:
        _cancel_active(context, message)
        return False, message
    pulse(context, gesture.user_path, "begin")
    return True, f"Holding: {hold_mode_name(gesture.hold_mode)}"


def stamp_copy(context) -> tuple[bool, str]:
    """Right A while holding: leave a copy where the object is, keep carrying it."""
    gesture = _active_gesture
    transaction = _held_object_transaction(gesture)
    if transaction is None:
        return False, "Hold an object to stamp a copy"
    copies = []
    for item in transaction.original_objects:
        source = item.object_ref
        copy = source.copy()
        if source.data is not None:
            copy.data = source.data.copy()
        for collection in source.users_collection:
            collection.objects.link(copy)
        copy.select_set(False)
        copies.append(copy)
    if gesture.stamps is None:
        gesture.stamps = []
    gesture.stamps.extend(copies)
    pulse(context, gesture.user_path, "commit")
    return True, f"Stamped {len(copies)} cop{'y' if len(copies) == 1 else 'ies'}; keep carrying"


def _extrusion(gesture):
    from . import face_editing
    if (gesture is None or gesture.kind != "move"
            or not isinstance(gesture.transaction, face_editing.FaceTransaction)
            or gesture.transaction.finished or gesture.transaction.tool not in {"extrude", "extrude_free"}):
        return None
    return gesture.transaction


def chain_extrude(context) -> tuple[bool, str]:
    """Right A while extruding: keep this segment and extrude again from its cap."""
    from . import face_editing
    gesture = _active_gesture
    transaction = _extrusion(gesture)
    if transaction is None or gesture.primary_position is None:
        return False, "Extrude a face first"
    try:
        gesture.transaction = face_editing.chain(context, transaction, gesture.primary_position,
                                                 gesture.primary_rotation)
    except face_editing.editing.EditRejected as exc:
        return False, str(exc)
    except (ReferenceError, RuntimeError) as exc:
        _cancel_active(context, f"Chained extrude failed: {exc}")
        return False, f"Chained extrude failed: {exc}"
    pulse(context, gesture.user_path, "commit")
    return True, "Segment kept (one undo); keep pulling for the next"


def toggle_extrude_direction(context) -> tuple[bool, str]:
    """Right stick click while extruding: straight out <-> follow the hand freely."""
    gesture = _active_gesture
    transaction = _extrusion(gesture)
    if transaction is None or gesture.primary_position is None:
        return False, "Extrude a face first"
    return True, transaction.toggle_direction(gesture.primary_position)


def _remove_stamps(gesture):
    for copy in gesture.stamps or ():
        try:
            data = copy.data
            bpy.data.objects.remove(copy, do_unlink=True)
            if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except ReferenceError:
            pass
    gesture.stamps = None


def drop_onto_surface(context) -> tuple[bool, str]:
    """Left stick click while holding: set the object down on what is below it."""
    gesture = _active_gesture
    transaction = _held_object_transaction(gesture)
    if transaction is None or gesture.second_key is not None:
        return False, "Hold an object to set it down"
    objects = [item.object_ref for item in transaction.original_objects]
    corners = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    bottom = min(corner.z for corner in corners)
    centre = sum(corners, Vector()) / len(corners)
    units = _world_units_per_metre(context)
    context.view_layer.update()
    hit, location, _normal, _index, _obj, _matrix = context.scene.ray_cast(
        context.evaluated_depsgraph_get(), Vector((centre.x, centre.y, bottom - 0.001 * units)),
        Vector((0.0, 0.0, -1.0)))
    if not hit:
        return False, "Nothing below to set it on"
    offset = Vector(gesture.offset)
    offset.z += location.z - bottom
    gesture.offset = tuple(offset)
    ok, message = _apply_move(context, gesture)
    if not ok:
        _cancel_active(context, message)
        return False, message
    pulse(context, gesture.user_path, "commit")
    return True, "Set down on the surface below; keep holding to slide it"


def _dispatch_second_hand(context, event, gesture) -> set[str]:
    if gesture.kind == "world":
        return _world_second_hand(context, event, gesture)
    if _is_sculpt(gesture.transaction):
        return _twin_second_hand(context, event, gesture)
    if gesture.navigation != _navigation_signature(context):
        _cancel_active(context, "Navigation changed during two-hand capture")
        return {"CANCELLED"}
    pose = _pose_from_event(event)
    if pose is None:
        _cancel_active(context, "Second controller lost tracking; edit cancelled")
        return {"CANCELLED"}
    gesture.last_input_at = monotonic()
    if getattr(event, "value", "") == "RELEASE":
        try:
            _end_two_hand(context, gesture)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            _cancel_active(context, f"Two-hand release failed: {exc}")
            return {"CANCELLED"}
        pulse(context, _event_hand(event), "click")
        interaction_log.emit("two_hand", "Second hand released; still holding with the first",
                             context, hand=_event_hand(event))
        return {"FINISHED"}
    gesture.second_position = tuple(pose[0])
    ok, message = _apply_move(context, gesture)
    if not ok:
        _cancel_active(context, message)
        return {"CANCELLED"}
    return {"RUNNING_MODAL"}


def reel_active(context, direction: int) -> tuple[bool, str]:
    """Right stick while holding: push the object away or pull it closer."""
    gesture = _active_gesture
    editing, _interaction = _editing_contract()
    if (gesture is None or gesture.kind != "move" or gesture.second_key is not None
            or gesture.primary_direction is None or editing is None
            or not isinstance(gesture.transaction, editing.EditTransaction)
            or gesture.transaction.tool not in {editing.TransformTool.GRAB, editing.TransformTool.MOVE}
            or gesture.transaction.mode != "OBJECT"):
        return False, "Nothing held to push or pull"
    units = _world_units_per_metre(context)
    targets = [item.object_ref.matrix_world.translation for item in gesture.transaction.original_objects]
    centre = sum(targets, Vector()) / len(targets)
    distance = (centre - Vector(gesture.primary_position)).length
    step = max(0.02 * units, 0.15 * distance)
    if direction < 0:
        # Never pull the object through (or closer than 5 cm to) the hand.
        step = min(step, max(0.0, distance - 0.05 * units))
    gesture.reel += step * direction
    ok, message = _apply_move(context, gesture)
    if not ok:
        _cancel_active(context, message)
        return False, message
    pulse(context, gesture.user_path, "tick")
    return True, "Pushed away" if direction > 0 else "Pulled closer"


# Stick navigation of the hand menu: while it is recent and the ray has not
# moved, idle ray hover must not steal the highlight back.
STICK_HOVER_PAUSE_SECONDS = 4.0
_stick_nav: tuple[float, tuple] | None = None
# True while the hand shows a native list menu (Add, Quick, context menus),
# False for the command-deck panel. Lists take arrow keys; panels scroll.
_native_menu_open = False


def _key(context, key) -> bool:
    ok, _ = _editor_event(context, key, "PRESS", ray=None, target="MENU")
    if ok:
        _editor_event(context, key, "RELEASE", ray=None, target="MENU")
    return ok


def stick_navigate(context, direction: str, ray) -> bool:
    """Right stick up/down: move the highlight in a list menu, or scroll a panel.

    Keyboard events need no ray, so the list works wherever you point; this
    is Blender's own menu arrow-key handling.
    """
    global _stick_nav
    if _native_menu_open:
        ok = _key(context, "UP_ARROW" if direction == "WHEELUPMOUSE" else "DOWN_ARROW")
    else:
        ok, _ = _editor_event(context, direction, "PRESS", ray=ray, target="MENU")
    if ok:
        hover = _hover_ray(context)  # Hover follows the last trigger hand, not the stick hand.
        _stick_nav = (monotonic(), tuple((hover or ray)[1]))
    return ok


def _menu_press_slot() -> str:
    """The stick that walks menus (the scrolling hand) also presses in them."""
    return "LEFT_STICK" if _feel("left_handed", False) else "RIGHT_STICK"


def press_highlighted(context) -> bool:
    """Stick click in an open hand menu: Enter on the highlighted entry."""
    return _key(context, "RET")


def _hover_paused(ray) -> bool:
    if _stick_nav is None or ray is None:
        return False
    started, direction = _stick_nav
    if monotonic() - started > STICK_HOVER_PAUSE_SECONDS:
        return False
    return Vector(direction).angle(Vector(ray[1]), 0.0) < radians(2.0)


def _adjust_brush(context, direction: int) -> tuple[bool, str]:
    """Right stick with a brush tool and no panel under the ray: resize the brush."""
    settings = context.window_manager.xrwb_settings
    tool = settings.edit_tool
    if tool == "retopo_loom":
        from . import retopo
        settings.retopo_faces = retopo.step_target(settings.retopo_faces, 1 if direction > 0 else -1)
        return True, f"Retopo {retopo.quads_label(settings.retopo_faces)}"
    name = ("sculpt_radius_m" if tool.startswith("sculpt_") else
            {"spray_material": "spray_radius_m", "paint_vertex": "spray_radius_m",
             "draw_curve": "pen_radius_m", "sdf_blob": "sdf_radius_m"}.get(tool))
    if name is None:
        return False, "Right stick scrolls panels; with a brush tool it resizes the brush"
    prop = settings.bl_rna.properties[name]
    value = min(prop.hard_max, max(prop.hard_min, getattr(settings, name) * (1.2 if direction > 0 else 1 / 1.2)))
    setattr(settings, name, value)
    return True, f"Brush {value * 100:.1f} cm"


def _autokey(context, gesture):
    """Native auto-key after an object transform commit; never blocks the commit."""
    editing, _interaction = _editing_contract()
    if editing is None or not isinstance(gesture.transaction, editing.EditTransaction):
        return
    from . import animation_tools

    try:
        count = animation_tools.autokey_after_transform(context)
    except (AttributeError, RuntimeError, TypeError) as exc:
        interaction_log.emit("error", f"Auto key failed: {exc}", context, level="WARNING", error=exc)
        return
    if count:
        interaction_log.emit("animate", f"Auto key: {count} channels on frame {context.scene.frame_current}",
                             context, action=gesture.action, hand=gesture.user_path)


def _hover_ray(context):
    """Idle hover only: never synthesize buttons or compete with captured input."""
    from . import editing, hands

    if (_active_gesture is not None or _release_required
            or editing.active_transaction() is not None
            or hands._router_operator is not None):
        return None
    try:
        state = context.window_manager.xr_session_state
        actionmap = _find_actionmap(state)
        if actionmap is None:
            return None
        items = {item.name: item for item in actionmap.actionmap_items}
        aim_paths = [entry.path for entry in items["controller_aim"].user_paths]
        for name in ("pointer", "grab_move", "panel_grab", "context_pointer",
                     "world_grab", "teleport", "move_forward", "move_right", "turn_right"):
            item = items.get(name)
            if item is None:
                continue
            threshold = min(binding.threshold for binding in item.bindings)
            for user_path in item.user_paths:
                values = state.action_state_get(context, ACTION_SET_NAME, name, user_path.path)
                if any(not isfinite(value) or abs(value) >= threshold for value in values):
                    return None
        # Old Blender getters return identity for a missing controller. Require
        # explicit native tracked/active status; missing API disables hover.
        rays = {}
        for index, path in enumerate(aim_paths):
            if state.controller_aim_active_get(context, index):
                pose = _validated_pose(state.controller_aim_location_get(context, index),
                                       state.controller_aim_rotation_get(context, index))
                if pose is not None:
                    rays[path] = (pose[0], pose[2])
        return _choose_hover_ray(context, rays)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError, KeyError, IndexError):
        return None


def _choose_hover_ray(context, rays):
    """Hover with the hand that points at the hand menu; otherwise the last trigger hand.

    The hand holding the menu rarely points at it, so following only the last
    trigger hand left the menu unhighlighted until you had clicked once.
    """
    global _pointer_hand
    from . import menu_pointer
    if hand_menu_active(context):
        hand = menu_pointer.choose_hand(menu_pointer.menu_pose(context), rays, _pointer_hand)
        if hand:
            _pointer_hand = hand
            return rays[hand]
    return rays.get(_pointer_hand)


def start_router(context) -> tuple[bool, str]:
    """Start passive hover; buttons retain their independent action modals."""
    global _hover_router
    if _hover_router is not None and bpy.app.timers.is_registered(_hover_timer):
        return True, "Controller hover is already running"
    if bpy.app.background or getattr(context, "window", None) is None:
        return False, "Controller hover requires a Blender window"
    try:
        _hover_router = _HoverState(monotonic() + 10.0)
        if not bpy.app.timers.is_registered(_hover_timer):
            bpy.app.timers.register(_hover_timer, first_interval=0.05, persistent=False)
        return True, "Controller hover routing started"
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _hover_router = None
        return False, f"Controller hover unavailable: {exc}"


def _hover_tick(context, hover):
    """Queue at most one fresh tracked MOUSEMOVE; never retain an RNA reference."""
    try:
        info = hand_menu_info(context)
        target = "MENU" if info[0] or info[4] else "EDITOR"
        if target == "EDITOR":
            info = context.window_manager.xr_session_state.editor_surface_info(context)
        frame = int(info[5])
        navigation = _navigation_signature(context)
        fresh = (target == hover.last_target and frame > hover.last_frame
                 and hover.last_frame >= 0)
        stationary = navigation == hover.last_navigation
        hover.last_frame, hover.last_navigation = frame, navigation
        hover.last_target = target
        if not info[0] or info[4] or not fresh or not stationary:
            return
        ray = _hover_ray(context)
        if ray is not None and not _hover_paused(ray):
            # Native routing also validates focus, views, panel hit and
            # capture ownership. A rejected/missing ray never clicks.
            _editor_pointer(context, "MOUSEMOVE", "NOTHING", ray=ray, target=target)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError, IndexError):
        hover.last_frame = -1


def _hover_timer():
    """Main-thread application timer survives undo, but not file loads."""
    global _hover_router
    from . import runtime, ui

    hover = _hover_router
    if hover is None:
        return None
    # Timers have no reliable active window. Refresh the existing view context
    # every tick; never hold window/area/operator RNA across undo or file loads.
    with ui._view3d_override(bpy.context):
        context = bpy.context
        if not runtime.session_running(context):
            if hover.was_running or monotonic() >= hover.startup_deadline:
                _hover_router = None
                return None
            return 0.05
        hover.was_running = True
        _hover_tick(context, hover)
    return 0.05


def stop_router(context=None) -> None:
    """Cancel the current transaction/capture before session teardown."""
    global _hover_router
    _hover_router = None
    if bpy.app.timers.is_registered(_hover_timer):
        bpy.app.timers.unregister(_hover_timer)
    _cancel_active(context, "XR session stopped; gesture cancelled")


def teardown(context=None) -> None:
    """Release the modal and remove our map only after the XR runtime stops.

    Blender 5.1 exposes no action-set destroy call.  Removing a configured map
    while OpenXR still owns its actions is unsafe, so the session stop lifecycle
    must call this after the runtime stops; then removing the RNA map is safe.
    """

    stop_router(context)
    if context is None:
        _release_required.clear()
        return
    state = getattr(context.window_manager, "xr_session_state", None)
    if state is None:
        _release_required.clear()
        return
    try:
        if state.is_running(context):
            return
        _release_required.clear()
        actionmap = _find_actionmap(state)
        if actionmap is not None:
            state.actionmaps.remove(state, actionmap)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        pass


class _XRWB_OT_modal_gesture(bpy.types.Operator):
    bl_options = {"BLOCKING"}

    _action = ""
    _user_path = ""
    _timer = None

    def _remove_timer(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except (AttributeError, ReferenceError, RuntimeError, ValueError):
                pass
            self._timer = None

    def _start_timer(self, context):
        if context.window is not None:
            self._timer = context.window_manager.event_timer_add(
                0.1, window=context.window
            )

    def modal(self, context, event):
        # Native XR routing may visit multiple instances of this same operator
        # type. A second hand must not drain or finish the first hand's modal.
        if event.type == "XR_ACTION" and not _route_matches(event, self._action, self._user_path):
            return {"PASS_THROUGH"}
        if event.type == "TIMER":
            from . import runtime

            if not runtime.session_running(context):
                _cancel_active(context, "XR session ended")
                _release_required.discard((self._action, self._user_path))
                self._remove_timer(context)
                return {"CANCELLED"}
        # XR routes only to the operator type/properties owning that action.
        # A cancelled modal must stay installed to see its eventual RELEASE;
        # otherwise Blender repeatedly invokes it on held PRESS events.
        drained = _drain_cancelled_input(event)
        if drained is not None:
            if drained == {"CANCELLED"}:
                self._remove_timer(context)
            return drained
        if (
            _active_gesture is None
            and (self._action, self._user_path) not in _release_required
        ):
            self._remove_timer(context)
            return {"CANCELLED"}
        owns_capture = _active_gesture is not None and (
            _active_gesture.action,
            _active_gesture.user_path,
        ) == (self._action, self._user_path)
        if owns_capture and event.type == "ESC":
            _cancel_active(context, "XR Workbench gesture cancelled")
            return {"RUNNING_MODAL"}
        gesture = _active_gesture
        if (
            event.type == "TIMER"
            and owns_capture
            and monotonic() - gesture.last_input_at >= TRACKING_WATCHDOG_SECONDS
        ):
            _cancel_active(context, "XR tracking input timed out; gesture cancelled")
            return {"RUNNING_MODAL"}
        from . import ui
        try:
            with ui._view3d_override(context):
                outcome = dispatch_xr_event(bpy.context, event)
                if outcome != {"PASS_THROUGH"}:
                    _cage_sync(bpy.context)
        except Exception as exc:
            interaction_log.emit("error", f"Controller action failed: {exc}", context,
                                 action=self._action, hand=self._user_path, level="ERROR", error=exc)
            _cancel_active(context, f"Controller action failed: {exc}")
            return {"RUNNING_MODAL"}
        if outcome != {"PASS_THROUGH"}:
            if outcome == {"CANCELLED"}:
                drained = _drain_cancelled_input(event)
                if drained is not None:
                    outcome = drained
            if outcome != {"RUNNING_MODAL"}:
                self._remove_timer(context)
            return outcome
        return {"PASS_THROUGH"}

    def _invoke_gesture(self, context, event, begin):
        key = _event_key(event)
        if key is None:
            return {"CANCELLED"}
        self._action, self._user_path = key
        from . import ui
        try:
            with ui._view3d_override(context):
                ok, message = begin(bpy.context, event)
                interaction_log.emit("begin" if ok else "rejected", message, bpy.context,
                                     action=key[0], hand=key[1],
                                     tool=getattr(bpy.context.window_manager.xrwb_settings, "edit_tool", ""),
                                     level="INFO" if ok else "WARNING")
                _cage_sync(bpy.context)
                captured = _active_gesture
                ui_press = captured is None or captured.kind in {"pointer", "task_card", "menu_open", "panel_grab"}
                pulse(bpy.context, key[1], ("click" if ui_press else "begin") if ok else "reject")
        except Exception as exc:
            ok, message = False, f"Controller action failed: {exc}"
            interaction_log.emit("error", message, context, action=key[0], hand=key[1],
                                 level="ERROR", error=exc)
            _cancel_active(context, message)
        if not ok:
            _release_required.add(key)
            self.report({"WARNING"}, message)
        self._start_timer(context)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if _active_gesture is not None and (
            _active_gesture.action, _active_gesture.user_path
        ) == (self._action, self._user_path):
            _cancel_active(context, "XR Workbench gesture cancelled")
        self._remove_timer(context)


class XRWB_OT_controller_pointer(_XRWB_OT_modal_gesture):
    bl_idname = "xrwb.controller_pointer"
    bl_label = "XR Workbench Controller Pointer"
    button: bpy.props.EnumProperty(
        items=[("LEFTMOUSE", "Click", ""), ("RIGHTMOUSE", "Context Menu", "")]
    )

    def invoke(self, context, event):
        if tool_wheel.is_open() and _active_gesture is None:
            # Either trigger picks the highlighted wedge of an open tool wheel.
            hand = _event_hand(event)
            ok, message = tool_wheel.pick(context)
            pulse(context, hand, "click" if ok else "reject")
            interaction_log.emit("wheel", message, context, hand=hand, level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        return self._invoke_gesture(
            context, event, lambda ctx, evt: _begin_pointer(ctx, evt, self.button)
        )


class XRWB_OT_controller_grab_move(_XRWB_OT_modal_gesture):
    bl_idname = "xrwb.controller_grab_move"
    bl_label = "XR Workbench Controller Move"

    def invoke(self, context, event):
        return self._invoke_gesture(context, event, lambda ctx, evt: _begin_move(ctx, evt, grip=True))


class XRWB_OT_controller_world_grip(_XRWB_OT_modal_gesture):
    bl_idname = "xrwb.controller_world_grip"
    bl_label = "XR Workbench Second Grip"

    def invoke(self, context, event):
        return self._invoke_gesture(context, event, _begin_world_grip)


class XRWB_OT_controller_panel_grab(_XRWB_OT_modal_gesture):
    bl_idname = "xrwb.controller_panel_grab"
    bl_label = "Grab Floating Blender Panels"

    def invoke(self, context, event):
        return self._invoke_gesture(context, event, _begin_panel_grab)


class _PulseOnPress:
    """Button actions confirm in the pressing hand, then run as before."""

    def invoke(self, context, event):
        result = self.execute(context)
        pulse(context, _event_hand(event), "click" if "FINISHED" in result else "reject")
        return result


class XRWB_OT_controller_cancel(_PulseOnPress, bpy.types.Operator):
    bl_idname = "xrwb.controller_cancel"
    bl_label = "Cancel XR Workbench Gesture"

    def execute(self, context):
        """B / Y: cancel what you hold; else back out of a menu; else close the desktop."""
        if tool_wheel.is_open():
            tool_wheel.close()
            return {"FINISHED"}
        from . import undo_ring
        if undo_ring.is_open():
            interaction_log.emit("undo", undo_ring.close(), context)
            return {"FINISHED"}
        menu_active = hand_menu_active(context)
        if _active_gesture is not None:
            _cancel_active(context, "XR Workbench gesture cancelled")
            return {"FINISHED"}
        if menu_active:
            ok, message = menu_back(context)
            interaction_log.emit("menu", message if ok else f"Menu: {message}", context,
                                 level="INFO" if ok else "WARNING")
            return {"FINISHED"}
        state = getattr(context.window_manager, "xr_session_state", None)
        if state is not None and hasattr(state, "editor_surface_info") and state.editor_surface_info(context)[0]:
            ok, message = _native_call("xr_editor_surface", visible=False)
            interaction_log.emit("desktop", "Desktop closed (left stick click brings it back)" if ok else message,
                                 context, level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        # Nothing to cancel, close or hide: let a focused native editor see Esc.
        _editor_event(context, "ESC", "PRESS", ray=None, target="EDITOR")
        return {"CANCELLED"}


class XRWB_OT_controller_recall(_PulseOnPress, bpy.types.Operator):
    bl_idname = "xrwb.controller_recall"
    bl_label = "Show / Close XR Desktop"

    def execute(self, context):
        if _held_object_transaction(_active_gesture) is not None:
            ok, message = drop_onto_surface(context)
            interaction_log.emit("drop", message, context, level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        _cancel_active(context, "Toggling desktop; previous capture cancelled")
        state = context.window_manager.xr_session_state
        visible = bool(state and state.editor_surface_info(context)[0])
        ok, message = (_native_call("xr_editor_surface", visible=False)
                       if visible else _editor_surface(context))
        if not ok:
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        return {"FINISHED"}


class XRWB_OT_controller_quick(bpy.types.Operator):
    bl_idname = "xrwb.controller_quick"
    bl_label = "Quick Menu / Hold Mode"
    bl_description = ("Right stick click: while holding, cycle Follow / Precision / Snap; "
                      "otherwise open the Quick menu for this workflow on the hand")

    def invoke(self, context, event):
        hand = _event_hand(event) or RIGHT_HAND
        if _held_object_transaction(_active_gesture) is not None:
            ok, message = cycle_hold_mode(context)
            interaction_log.emit("hold_mode", message, context, hand=hand, level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        if _active_gesture is not None:
            return {"CANCELLED"}
        if hand_menu_active(context):
            # Press whatever the stick (or ray) has highlighted, like Enter.
            ok = press_highlighted(context)
            pulse(context, hand, "click" if ok else "reject")
            return {"FINISHED"} if ok else {"CANCELLED"}
        ok, message = _open_hand_native_menu(context, "XRWB_MT_quick", hand)
        pulse(context, hand, "click" if ok else "reject")
        if not ok:
            interaction_log.emit("rejected", message, context, hand=hand, level="WARNING")
            return {"CANCELLED"}
        return {"FINISHED"}


# Drill-down stack of list menus shown on the hand. Each level replaces the
# panel (like a phone settings screen); B goes back one level. Blender's own
# Esc/Left keys close whole menu chains, so the stack is ours.
_menu_stack: list[str] = []
_menu_hand = RIGHT_HAND


def _show_hand_menu(context, menu):
    global _native_menu_open
    settings = context.window_manager.xrwb_settings
    _native_menu_open = True
    from . import ui
    with ui._view3d_override(context):
        return _native_call(
            "xr_hand_menu", visible=True, panel="", menu=menu,
            width=float(getattr(settings, "hand_menu_width", 0.48)),
            controller=0 if _menu_hand == LEFT_HAND else 1)


def menu_hand_for(pressing_hand: str) -> str:
    """Which hand holds a menu opened by ``pressing_hand``.

    Default (Settings > Buttons > Menu Hand: Off Hand): menus sit on the
    non-dominant hand so the dominant hand points, clicks and scrolls, the
    layout most VR apps use. "Pressing Hand" keeps the old behaviour.
    """
    if _feel("menu_hand", "OFF") == "PRESSING":
        return pressing_hand or RIGHT_HAND
    return RIGHT_HAND if _feel("left_handed", False) else LEFT_HAND


def _open_hand_native_menu(context, menu, hand, push=False):
    """Show a list menu on the hand: a new top level, or one level deeper."""
    global _menu_hand
    if push and _menu_stack:
        _menu_stack.append(menu)
    else:
        _menu_stack[:] = [menu]
        _menu_hand = menu_hand_for(hand)
    return _show_hand_menu(context, menu)


def menu_back(context) -> tuple[bool, str]:
    """B in a list menu: the previous level, or close at the top."""
    if _native_menu_open and len(_menu_stack) > 1:
        _menu_stack.pop()
        ok, message = _show_hand_menu(context, _menu_stack[-1])
        return ok, "Back" if ok else message
    _menu_stack.clear()
    return _native_call("xr_hand_menu", visible=False)


def _toggle_desktop(context) -> tuple[bool, str]:
    _cancel_active(context, "Toggling desktop; previous capture cancelled")
    state = context.window_manager.xr_session_state
    visible = bool(state and state.editor_surface_info(context)[0])
    return (_native_call("xr_editor_surface", visible=False) if visible else _editor_surface(context))


def toggle_tool_wheel(context, event, hand) -> tuple[bool, str]:
    """Open the wheel at the pressing hand; pressing again picks the highlight."""
    if tool_wheel.is_open():
        return tool_wheel.pick(context)
    pose = _pose_from_event(event)
    state = getattr(context.window_manager, "xr_session_state", None)
    if pose is None or state is None:
        return False, "Tracked controller pose required for the tool wheel"
    if hand_menu_active(context):
        _menu_stack.clear()
        _native_call("xr_hand_menu", visible=False)
    try:
        viewer = tuple(state.viewer_pose_rotation)
    except (AttributeError, TypeError):
        viewer = (1.0, 0.0, 0.0, 0.0)
    # Face the viewer but stay upright: yaw only, like the draw frame.
    from . import draw_editing
    upright = draw_editing.draw_frame(viewer) @ Quaternion((1.0, 0.0, 0.0), pi / 2.0)
    return True, tool_wheel.open_wheel(context, hand, pose[0], upright, _world_units_per_metre(context))


def run_slot_action(context, action: str, hand: str) -> tuple[bool, str]:
    """What an assignable button does when nothing is held and no menu owns it."""
    controller = 0 if hand == LEFT_HAND else 1
    from . import ui
    if action == "NONE":
        return False, "This button is set to Nothing (Settings > Controls)"
    if action == "TOOL_WHEEL":
        return False, "Press the button with a tracked controller to open the wheel"
    if action in {"ADD_MENU", "QUICK_MENU", "BLENDER_MENUS"}:
        menu = {"ADD_MENU": "XRWB_MT_add", "QUICK_MENU": "XRWB_MT_quick",
                "BLENDER_MENUS": "XRWB_MT_blender"}[action]
        if hand_menu_active(context) and _menu_stack[:1] == [menu]:
            _menu_stack.clear()
            return _native_call("xr_hand_menu", visible=False)
        return _open_hand_native_menu(context, menu, hand)
    if action == "HAND_MENU":
        controller = 0 if menu_hand_for(hand) == LEFT_HAND else 1
        with ui._view3d_override(context):
            result = bpy.ops.xrwb.command_menu(controller=controller)
        return "FINISHED" in result, "Hand menu"
    if action == "DESKTOP":
        return _toggle_desktop(context)
    if action in {"UNDO", "REDO"}:
        if _active_gesture is not None:
            _cancel_active(context, "Capture cancelled; press again to " + action.lower())
            return False, "Cancelled the current capture first"
        operator = getattr(bpy.ops.xrwb, f"history_{action.lower()}")
        ok = operator.poll() and "FINISHED" in operator("EXEC_DEFAULT")
        return ok, action.title() if ok else f"Nothing to {action.lower()}"
    if action == "UNDO_RING":
        from . import undo_ring
        if hand_menu_active(context):
            _menu_stack.clear()
            _native_call("xr_hand_menu", visible=False)
        return undo_ring.open_ring(context, menu_hand_for(hand))
    if action == "GUIDE":
        return "FINISHED" in bpy.ops.xrwb.guide(action="OPEN"), "Guide"
    if action == "NEXT_WORKFLOW":
        from . import viewer_scale
        keys = [item[0] for item in viewer_scale.WORKFLOWS]
        settings = context.window_manager.xrwb_settings
        following = keys[(keys.index(settings.workflow) + 1) % len(keys)]
        with ui._view3d_override(context):
            ok = "FINISHED" in bpy.ops.xrwb.workflow(workflow=following)
        return ok, f"Workflow: {following.title()}"
    if action == "PLAY":
        return "FINISHED" in bpy.ops.xrwb.anim_play(), "Play / Pause"
    if action in {"GO_TO", "RESET_VIEW"}:
        ok = "FINISHED" in bpy.ops.xrwb.travel(action="GO_TO" if action == "GO_TO" else "RESET")
        return ok, "Travelled" if ok else "Could not travel (see Last action)"
    return False, f"Unknown button action {action}"


class XRWB_OT_controller_button(bpy.types.Operator):
    """One assignable physical button (Settings > Controls).

    While holding something, buttons keep their fixed, labelled jobs: right A
    stamps, right stick click cycles hold modes, left stick click sets down.
    In an open list menu, right stick click presses the highlighted entry.
    """
    bl_idname = "xrwb.controller_button"
    bl_label = "Controller Button"
    slot: bpy.props.EnumProperty(items=BUTTON_SLOTS, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        hand = _event_hand(event) or (LEFT_HAND if self.slot.startswith("LEFT") else RIGHT_HAND)
        held = _held_object_transaction(_active_gesture) is not None
        extruding = _extrusion(_active_gesture) is not None
        wheel = slot_action(self.slot) == "TOOL_WHEEL" and _active_gesture is None
        menu_press = (self.slot == _menu_press_slot() and hand_menu_active(context)
                      and _active_gesture is None and not tool_wheel.is_open())
        from . import undo_ring
        if undo_ring.is_open():
            ok, message = True, undo_ring.close()  # Any button leaves history where you scrubbed it.
        elif menu_press:
            # An open menu always owns the stick click: press what is highlighted.
            ok, message = press_highlighted(context), "Pressed"
        elif wheel or (tool_wheel.is_open() and _active_gesture is None):
            ok, message = toggle_tool_wheel(context, event, hand) if wheel else tool_wheel.pick(context)
        elif extruding and self.slot == "RIGHT_A":
            ok, message = chain_extrude(context)
        elif extruding and self.slot == "RIGHT_STICK":
            ok, message = toggle_extrude_direction(context)
        elif held and self.slot == "RIGHT_A":
            ok, message = stamp_copy(context)
        elif self.slot == "RIGHT_A" and callable(getattr(getattr(_active_gesture, "transaction", None),
                                                         "reroll", None)):
            _active_gesture.transaction.reroll()  # Grow: a new random shape, same pull.
            ok, message = True, "Re-rolled"
        elif held and self.slot == "RIGHT_STICK":
            ok, message = cycle_hold_mode(context)
        elif held and self.slot == "LEFT_STICK":
            ok, message = drop_onto_surface(context)
        elif self.slot == "RIGHT_STICK" and hand_menu_active(context) and _active_gesture is None:
            ok, message = press_highlighted(context), "Pressed"
        elif _active_gesture is not None and slot_action(self.slot) not in {"UNDO", "REDO"}:
            ok, message = False, "Release what you are holding first"
        else:
            ok, message = run_slot_action(context, slot_action(self.slot), hand)
        pulse(context, hand, "click" if ok else "reject")
        interaction_log.emit("button", f"{self.slot}: {message}", context, hand=hand,
                             level="INFO" if ok else "WARNING")
        return {"FINISHED"} if ok else {"CANCELLED"}


class XRWB_OT_controller_add_menu(bpy.types.Operator):
    bl_idname = "xrwb.controller_add_menu"
    bl_label = "Add Shapes Menu"
    bl_description = "Index right B: the basic shapes menu on your hand; stick up/down, stick click picks"

    def invoke(self, context, event):
        hand = _event_hand(event) or RIGHT_HAND
        if _active_gesture is not None:
            pulse(context, hand, "reject")
            return {"CANCELLED"}
        if hand_menu_active(context):
            with_menu = _native_call("xr_hand_menu", visible=False)
            pulse(context, hand, "click" if with_menu[0] else "reject")
            return {"FINISHED"}
        ok, message = _open_hand_native_menu(context, "XRWB_MT_add", hand)
        pulse(context, hand, "click" if ok else "reject")
        interaction_log.emit("menu" if ok else "rejected", "Add: stick up/down, stick click or trigger picks"
                             if ok else message, context, hand=hand, level="INFO" if ok else "WARNING")
        return {"FINISHED"} if ok else {"CANCELLED"}


class XRWB_OT_controller_scroll(bpy.types.Operator):
    bl_idname = "xrwb.controller_scroll"
    bl_label = "XR Controller Panel Scroll"
    direction: bpy.props.EnumProperty(
        items=[("WHEELUPMOUSE", "Up", ""), ("WHEELDOWNMOUSE", "Down", "")]
    )

    def invoke(self, context, event):
        from . import editing, modifier_tower, undo_ring
        direction = 1 if self.direction == "WHEELUPMOUSE" else -1
        if _active_gesture is not None:
            ok, message = reel_active(context, direction)
            return {"FINISHED"} if ok else {"CANCELLED"}
        if undo_ring.is_open():
            # Down steps back through Blender's history, up steps forward.
            ok, message = undo_ring.step(context, direction)
            pulse(context, _event_hand(event), "tick" if ok else "reject")
            interaction_log.emit("undo", message, context, hand=_event_hand(event), level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        ray = _ray_from_event(event)
        if ray is None:
            return {"CANCELLED"}
        block = modifier_tower.block_at(context, *ray) if not hand_menu_active(context) else None
        if block is not None:
            # Pointing at a tower block, the stick is that modifier's dial.
            try:
                ok, message = True, modifier_tower.dial(context, block[modifier_tower.BLOCK_KEY], direction)
            except (editing.EditRejected, RuntimeError, ReferenceError, ValueError) as exc:
                ok, message = False, str(exc)
            pulse(context, _event_hand(event), "detent" if ok else "reject")
            interaction_log.emit("modifiers", message, context, hand=_event_hand(event),
                                 level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        if hand_menu_active(context):
            ok = stick_navigate(context, self.direction, ray)
            pulse(context, _event_hand(event), "tick" if ok else "reject")
            return {"FINISHED"} if ok else {"CANCELLED"}
        ok, _ = _editor_event(context, self.direction, "PRESS", ray=ray, target="EDITOR")
        if not ok:
            ok, message = _adjust_brush(context, direction)
            if ok:
                interaction_log.emit("brush", message, context, hand=_event_hand(event))
        if ok:
            pulse(context, _event_hand(event), "tick")
        return {"FINISHED"} if ok else {"CANCELLED"}


class XRWB_OT_controller_history(_PulseOnPress, bpy.types.Operator):
    bl_idname = "xrwb.controller_history"
    bl_label = "XR Controller History"
    direction: bpy.props.EnumProperty(
        items=[("undo", "Undo", ""), ("redo", "Redo", "")]
    )

    def execute(self, context):
        if _active_gesture is not None:
            _cancel_active(
                context, "Capture cancelled; press history again to continue"
            )
            return {"CANCELLED"}
        operator = getattr(bpy.ops.xrwb, f"history_{self.direction}")
        return operator("EXEC_DEFAULT") if operator.poll() else {"CANCELLED"}


CLASSES = (
    XRWB_OT_controller_pointer,
    XRWB_OT_controller_quick,
    XRWB_OT_controller_button,
    XRWB_OT_controller_add_menu,
    XRWB_OT_controller_grab_move,
    XRWB_OT_controller_panel_grab,
    XRWB_OT_controller_world_grip,
    XRWB_OT_controller_cancel,
    XRWB_OT_controller_recall,
    XRWB_OT_controller_scroll,
    XRWB_OT_controller_history,
)
