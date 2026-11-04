"""Combined OpenXR action set for navigation and authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bpy

from .model import ACTION_MAP_NAME, LEFT_HAND, RIGHT_HAND


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    path: str
    grip_pose: str
    aim_pose: str
    trigger: str
    squeeze: str
    menu_left: str
    primary_left: str
    primary_right: str
    secondary_right: str
    stick_x: str
    stick_y: str
    stick_click: str
    haptic: str = "/output/haptic"


PROFILES: tuple[Profile, ...] = (
    Profile(
        "index",
        "/interaction_profiles/valve/index_controller",
        "/input/grip/pose",
        "/input/aim/pose",
        "/input/trigger/value",
        "/input/squeeze/force",
        "/input/b/click",
        "/input/a/click",
        "/input/a/click",
        "/input/b/click",
        "/input/thumbstick/x",
        "/input/thumbstick/y",
        "/input/thumbstick/click",
    ),
    Profile(
        "oculus",
        "/interaction_profiles/oculus/touch_controller",
        "/input/grip/pose",
        "/input/aim/pose",
        "/input/trigger/value",
        "/input/squeeze/value",
        "/input/y/click",
        "/input/x/click",
        "/input/a/click",
        "/input/b/click",
        "/input/thumbstick/x",
        "/input/thumbstick/y",
        "/input/thumbstick/click",
    ),
    Profile(
        "vive",
        "/interaction_profiles/htc/vive_controller",
        "/input/grip/pose",
        "/input/aim/pose",
        "/input/trigger/value",
        "/input/squeeze/click",
        "/input/menu/click",
        "/input/menu/click",
        "/input/menu/click",
        "/input/trackpad/click",
        "/input/trackpad/x",
        "/input/trackpad/y",
        "/input/trackpad/click",
    ),
    Profile(
        "wmr",
        "/interaction_profiles/microsoft/motion_controller",
        "/input/grip/pose",
        "/input/aim/pose",
        "/input/trigger/value",
        "/input/squeeze/click",
        "/input/menu/click",
        "/input/menu/click",
        "/input/menu/click",
        "/input/trackpad/click",
        "/input/thumbstick/x",
        "/input/thumbstick/y",
        "/input/thumbstick/click",
    ),
    Profile(
        "simple",
        "/interaction_profiles/khr/simple_controller",
        "/input/grip/pose",
        "/input/aim/pose",
        "/input/select/click",
        "/input/menu/click",
        "/input/menu/click",
        "/input/menu/click",
        "/input/select/click",
        "/input/menu/click",
        "/input/select/click",
        "/input/select/click",
        "/input/menu/click",
    ),
)


def _new_item(
    actionmap, name: str, item_type: str, user_paths: tuple[str, ...], **attrs
):
    item = actionmap.actionmap_items.new(name, True)
    item.type = item_type
    for path in user_paths:
        item.user_paths.new(path)
    for key, value in attrs.items():
        if key == "op_properties":
            continue
        setattr(item, key, value)
    for key, value in attrs.get("op_properties", {}).items():
        try:
            setattr(item.op_properties, key, value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid action property {name}.{key}: {exc}") from exc
    return item


def _bind(
    item, profile: Profile, components: tuple[str, ...], *, axis0="ANY", threshold=0.30
):
    binding = item.bindings.new(profile.name, True)
    binding.profile = profile.path
    binding.threshold = threshold
    binding.axis0_region = axis0
    for component in components:
        binding.component_paths.new(component)
    return binding


def _bind_all(item, selector, *, axis0="ANY", threshold=0.30):
    for profile in PROFILES:
        components = selector(profile)
        if components is None:
            continue
        if isinstance(components, str):
            components = (components,)
        _bind(item, profile, tuple(components), axis0=axis0, threshold=threshold)


def _operator_action(
    actionmap,
    name: str,
    operator: str,
    user_paths: tuple[str, ...],
    selector,
    *,
    mode="PRESS",
    axis0="ANY",
    threshold=0.30,
    haptic_name="",
    haptic_mode="PRESS",
    op_properties: dict[str, Any] | None = None,
):
    item = _new_item(
        actionmap,
        name,
        "FLOAT",
        user_paths,
        op=operator,
        op_mode=mode,
        bimanual=False,
        haptic_name=haptic_name,
        haptic_match_user_paths=bool(haptic_name),
        haptic_duration=0.035 if haptic_name else 0.0,
        haptic_frequency=0.0,
        haptic_amplitude=0.25 if haptic_name else 0.0,
        haptic_mode=haptic_mode,
        op_properties=op_properties or {},
    )
    _bind_all(item, selector, axis0=axis0, threshold=threshold)
    return item


def build_actionmap(session_state):
    """Create the complete action-map RNA before OpenXR attaches it."""

    actionmap = session_state.actionmaps.new(session_state, ACTION_MAP_NAME, True)

    grip = _new_item(
        actionmap,
        "controller_grip",
        "POSE",
        (LEFT_HAND, RIGHT_HAND),
        pose_is_controller_grip=True,
        pose_is_controller_aim=False,
    )
    _bind_all(grip, lambda profile: (profile.grip_pose, profile.grip_pose))

    aim = _new_item(
        actionmap,
        "controller_aim",
        "POSE",
        (LEFT_HAND, RIGHT_HAND),
        pose_is_controller_grip=False,
        pose_is_controller_aim=True,
    )
    _bind_all(aim, lambda profile: (profile.aim_pose, profile.aim_pose))

    haptic = _new_item(actionmap, "haptic", "VIBRATION", (LEFT_HAND, RIGHT_HAND))
    _bind_all(haptic, lambda profile: (profile.haptic, profile.haptic))

    _operator_action(
        actionmap,
        "primary",
        "vrw.primary_action",
        (RIGHT_HAND,),
        lambda profile: profile.trigger,
        mode="MODAL",
        haptic_name="haptic",
    )
    _operator_action(
        actionmap,
        "direct_grab",
        "vrw.direct_grab",
        (RIGHT_HAND,),
        lambda profile: None if profile.name == "simple" else profile.squeeze,
        mode="MODAL",
        threshold=0.45,
    )
    _operator_action(
        actionmap,
        "world_grab",
        "wm.xr_navigation_grab",
        (LEFT_HAND,),
        lambda profile: None if profile.name == "simple" else profile.squeeze,
        mode="MODAL",
        threshold=0.45,
        op_properties={"lock_rotation": False, "lock_scale": False},
    )
    _operator_action(
        actionmap,
        "palette",
        "vrw.palette_toggle",
        (LEFT_HAND,),
        lambda profile: profile.menu_left,
        haptic_name="haptic",
    )
    _operator_action(
        actionmap,
        "undo",
        "vrw.undo",
        (LEFT_HAND,),
        lambda profile: profile.primary_left
        if profile.name in {"index", "oculus"}
        else None,
    )
    _operator_action(
        actionmap,
        "redo",
        "vrw.redo",
        (RIGHT_HAND,),
        lambda profile: profile.primary_right
        if profile.name in {"index", "oculus"}
        else None,
    )
    _operator_action(
        actionmap,
        "next_page",
        "vrw.cycle_palette",
        (RIGHT_HAND,),
        lambda profile: profile.secondary_right
        if profile.name in {"index", "oculus"}
        else None,
        op_properties={"direction": 1},
    )
    _operator_action(
        actionmap,
        "recenter",
        "wm.xr_navigation_reset",
        (RIGHT_HAND,),
        lambda profile: profile.stick_click
        if profile.name in {"index", "oculus"}
        else None,
        op_properties={"location": True, "rotation": True, "scale": False},
    )

    locomotion = (
        ("move_forward", "VIEWER_FORWARD", lambda p: p.stick_y, "POSITIVE", LEFT_HAND),
        ("move_back", "VIEWER_BACK", lambda p: p.stick_y, "NEGATIVE", LEFT_HAND),
        ("move_left", "VIEWER_LEFT", lambda p: p.stick_x, "NEGATIVE", LEFT_HAND),
        ("move_right", "VIEWER_RIGHT", lambda p: p.stick_x, "POSITIVE", LEFT_HAND),
        ("turn_left", "TURNLEFT", lambda p: p.stick_x, "NEGATIVE", RIGHT_HAND),
        ("turn_right", "TURNRIGHT", lambda p: p.stick_x, "POSITIVE", RIGHT_HAND),
        ("move_up", "UP", lambda p: p.stick_y, "POSITIVE", RIGHT_HAND),
        ("move_down", "DOWN", lambda p: p.stick_y, "NEGATIVE", RIGHT_HAND),
    )
    for name, fly_mode, selector, axis, hand in locomotion:
        _operator_action(
            actionmap,
            name,
            "wm.xr_navigation_fly",
            (hand,),
            lambda profile, selector=selector: None
            if profile.name == "simple"
            else selector(profile),
            mode="MODAL",
            axis0=axis,
            op_properties={
                "mode": fly_mode,
                "lock_location_z": fly_mode
                in {"VIEWER_FORWARD", "VIEWER_BACK", "VIEWER_LEFT", "VIEWER_RIGHT"},
            },
        )
    return actionmap


def create_action_set(context=None) -> tuple[bool, str]:
    """Build, attach, and activate the combined action set at XR start."""

    context = context or bpy.context
    session_state = context.window_manager.xr_session_state
    if session_state is None:
        return False, "XR session state is not available"
    try:
        actionmap = build_actionmap(session_state)
        if not session_state.action_set_create(context, actionmap):
            return False, "OpenXR rejected the VR Workbench action set"

        created_actions = 0
        created_bindings = 0
        for item in actionmap.actionmap_items:
            if not session_state.action_create(context, actionmap, item):
                return False, f"OpenXR rejected action {item.name}"
            created_actions += 1
            for binding in item.bindings:
                if not session_state.action_binding_create(
                    context, actionmap, item, binding
                ):
                    return False, (
                        f"OpenXR rejected {item.name} binding for {binding.name}"
                    )
                created_bindings += 1

        if not session_state.controller_pose_actions_set(
            context, ACTION_MAP_NAME, "controller_grip", "controller_aim"
        ):
            return False, "Could not assign controller pose actions"
        if not session_state.active_action_set_set(context, ACTION_MAP_NAME):
            return False, "Could not activate VR Workbench actions"
    except Exception as exc:
        return False, f"XR action setup failed: {exc}"
    return (
        True,
        f"VRW_ACTIONMAP_PASS actions={created_actions} bindings={created_bindings}",
    )


def action_spec_summary() -> dict[str, int]:
    """Stable source-level summary used by pure tests and package audits."""

    return {
        "profiles": len(PROFILES),
        "pose_actions": 2,
        "haptic_actions": 1,
        "authoring_actions": 7,
        "navigation_actions": 9,
        "total_actions": 19,
    }
