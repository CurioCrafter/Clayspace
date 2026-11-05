"""Undo Ring: Blender's real undo history as a ring around your wrist.

Open it (Quick menu > Undo Ring, or a button set to Undo Ring in Settings >
Controls) and the steps of the real undo stack ring the hand that holds your
menus, each named as in Edit > Undo History. The current step sits at the
top; older steps run anticlockwise, undone ones clockwise. Right stick down
steps back, up steps forward: every notch is a real jump through Blender's
history (ed.undo_history), so the scene changes as you scrub. A tick marks
each step and a buzz either end. Any button or B closes it where you are; a
new edit from there drops the undone steps, exactly as in Blender.
"""
from __future__ import annotations

import ctypes
import math
import os
import re
import sys
import tempfile

import bpy

VISIBLE = 13  # Steps drawn around the ring, the current one in the middle.
RADIUS_M = 0.075
_LINE = re.compile(r"^\[(.)(.)(.)(.)\]\s+(\d+)\s+\{[^}]*\}\s+type='[^']*', name='(.*)'$")
_state: dict = {}


def parse_steps(text: str) -> list[tuple[int, str, bool]]:
    """(stack index, name, active) per step of print_undo_steps() output, oldest first."""
    steps = []
    for line in text.splitlines():
        match = _LINE.match(line.strip())
        if match and match.group(4) != "S":  # Skipped steps are hidden in Blender's menu too.
            steps.append((int(match.group(5)), match.group(6), match.group(1) == "*"))
    return steps


def _flush_c_stdout():
    try:
        (ctypes.CDLL("ucrtbase") if sys.platform == "win32" else ctypes.CDLL(None)).fflush(None)
    except (AttributeError, OSError):
        pass


def read_steps(context=None) -> list[tuple[int, str, bool]]:
    """The real undo stack, oldest first; [] when there is none or it cannot be read."""
    # ponytail: Python has no undo-stack list, so this captures Blender's own
    # WindowManager.print_undo_steps() text; use an RNA list if one appears.
    window_manager = (context or bpy.context).window_manager
    try:
        sys.stdout.flush()
        _flush_c_stdout()
        saved = os.dup(1)
    except (AttributeError, OSError, ValueError):
        return []
    try:
        with tempfile.TemporaryFile() as sink:
            os.dup2(sink.fileno(), 1)
            try:
                window_manager.print_undo_steps()
                _flush_c_stdout()
            finally:
                os.dup2(saved, 1)
            sink.seek(0)
            return parse_steps(sink.read().decode("utf-8", "replace"))
    except (AttributeError, OSError, RuntimeError):
        return []
    finally:
        os.close(saved)


def active_position(steps) -> int:
    return next((position for position, step in enumerate(steps) if step[2]), len(steps) - 1)


def neighbour(steps, direction: int):
    """Stack index one notch back (-1) or forward (+1) from the current step; None at an end."""
    if not steps:
        return None
    target = active_position(steps) + direction
    return steps[target][0] if 0 <= target < len(steps) else None


def layout(steps, visible: int = VISIBLE):
    """(angle, name, kind) for the steps nearest the current one; kind is past, current or undone.

    Angle 0 is the top of the ring, positive clockwise (like the tool wheel).
    """
    position, half = active_position(steps), visible // 2
    placed = []
    for offset in range(-half, half + 1):
        if 0 <= position + offset < len(steps):
            kind = "current" if offset == 0 else "past" if offset < 0 else "undone"
            placed.append((offset * math.tau / visible, steps[position + offset][1], kind))
    return placed


def is_open() -> bool:
    return bool(_state)


def hand() -> str:
    return _state.get("hand", "")


def steps() -> list:
    return list(_state.get("steps", ()))


def open_ring(context, ring_hand: str) -> tuple[bool, str]:
    found = read_steps(context)
    if not found:
        return False, "No undo history yet (Global Undo must be on)"
    _state.clear()
    _state.update(hand=ring_hand, steps=found)
    position = active_position(found)
    return True, (f"Undo Ring: step {position + 1} of {len(found)}, {found[position][1]}. "
                  "Stick down: back, up: forward")


def close() -> str:
    found = _state.get("steps", ())
    _state.clear()
    return f"Undo Ring closed at {found[active_position(found)][1]}" if found else "Undo Ring closed"


def step(context, direction: int) -> tuple[bool, str]:
    """Jump one real undo step back (-1) or forward (+1)."""
    from . import editing
    if editing.active_transaction() is not None:
        return False, "Release the current edit first"
    target = neighbour(_state.get("steps") or read_steps(context), direction)
    if target is None:
        return False, "Oldest step: nothing further back" if direction < 0 else "Newest step: nothing to redo"
    try:
        result = bpy.ops.ed.undo_history("EXEC_DEFAULT", item=target)
    except RuntimeError as exc:
        return False, f"Undo history refused: {exc}"
    found = read_steps(context)
    if found:
        _state["steps"] = found
    if "FINISHED" not in result:
        return False, "Blender could not jump to that step"
    position = active_position(found)
    name = found[position][1] if found else "?"
    return True, f"{'Back' if direction < 0 else 'Forward'} to {name} ({position + 1}/{len(found)})"


class XRWB_OT_undo_ring(bpy.types.Operator):
    bl_idname = "xrwb.undo_ring"
    bl_label = "Undo Ring"
    bl_description = "Blender's real undo history around your wrist: right stick scrubs back and forward"
    action: bpy.props.EnumProperty(items=(("TOGGLE", "Open / Close", ""), ("BACK", "Step Back", ""),
                                          ("FORWARD", "Step Forward", "")),
                                   default="TOGGLE", options={"SKIP_SAVE"})

    def execute(self, context):
        from . import controllers, interaction_log
        if self.action == "TOGGLE":
            if is_open():
                ok, message = True, close()
            else:
                if controllers.hand_menu_active(context):
                    controllers._menu_stack.clear()
                    controllers._native_call("xr_hand_menu", visible=False)
                ok, message = open_ring(context, controllers.menu_hand_for(controllers._pointer_hand))
        else:
            ok, message = step(context, -1 if self.action == "BACK" else 1)
        interaction_log.emit("undo", message, context, hand=hand(), level="INFO" if ok else "WARNING")
        if not ok:
            self.report({"WARNING"}, message)
        return {"FINISHED"} if ok else {"CANCELLED"}


CLASSES = (XRWB_OT_undo_ring,)
