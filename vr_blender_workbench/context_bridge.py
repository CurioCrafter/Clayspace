"""Context-safe bridge for tested Blender operators."""

from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Iterator

import bpy

from .model import operator_id_is_valid


def resolve_operator(operator_id: str):
    if not operator_id_is_valid(operator_id):
        return None
    namespace, name = operator_id.split(".", 1)
    owner = getattr(bpy.ops, namespace, None)
    return getattr(owner, name, None) if owner else None


def _view3d_parts(context):
    window_manager = context.window_manager
    preferred_window = context.window
    windows = list(window_manager.windows)
    if preferred_window in windows:
        windows.remove(preferred_window)
        windows.insert(0, preferred_window)
    for window in windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next(
                (item for item in area.regions if item.type == "WINDOW"), None
            )
            space = area.spaces.active
            if region is not None:
                return window, screen, area, region, space
    return None


@contextmanager
def view3d_override(context=None) -> Iterator[None]:
    context = context or bpy.context
    parts = _view3d_parts(context)
    if parts is None:
        yield
        return
    window, screen, area, region, space = parts
    with context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
    ):
        yield


def run_operator(
    operator_id: str,
    *,
    context=None,
    kwargs: dict[str, Any] | None = None,
    invoke: bool = False,
) -> tuple[bool, str]:
    """Execute a configured operator in a real 3D View context."""

    operator = resolve_operator(operator_id)
    if operator is None:
        return False, f"Unknown operator: {operator_id}"
    kwargs = kwargs or {}
    try:
        with view3d_override(context):
            if not operator.poll():
                return (
                    False,
                    f"{operator_id} is unavailable in the current mode/context",
                )
            execution = "INVOKE_DEFAULT" if invoke else "EXEC_DEFAULT"
            result = operator(execution, **kwargs)
    except Exception as exc:
        return False, f"{operator_id} failed: {exc}"
    if "CANCELLED" in result:
        return False, f"{operator_id} cancelled"
    return True, f"Ran {operator_id}"


def parse_operator_args(raw: str) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON arguments: {exc.msg}"
    if not isinstance(value, dict):
        return None, "Operator arguments must be a JSON object"
    return value, ""
