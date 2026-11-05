"""First-run Guide and the few VR comfort choices that should persist.

Preferences live in one small JSON file in the add-on's own folder under
Blender's user config (the isolated VR profile when using the launchers). The
user's scenes and Blender preferences are never written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import bpy

DEFAULTS = {"guide_done": False, "desktop_at_start": False, "haptic_strength": 0.7, "ray_markers": True,
            "button_hints": True, "menu_at_start": True, "left_handed": False,
            "trigger_threshold": 0.30, "grip_threshold": 0.45, "stick_threshold": 0.55,
            "slot_RIGHT_A": "HAND_MENU", "slot_RIGHT_B": "ADD_MENU", "slot_RIGHT_STICK": "TOOL_WHEEL",
            "slot_LEFT_A": "UNDO", "slot_LEFT_STICK": "DESKTOP",
            "wheel_set": "AUTO", "wheel_size": 0.11, "snap_grid_cm": 5.0, "snap_angle": 15.0,
            "precision": 0.25, "hover_ticks": True, "label_scale": 1.0,
            "toasts": True, "readouts": True, "hover_outline": True, "detents": True,
            "two_hand": "SCALE_ROTATE", "menu_hand": "OFF",
            "locomotion": "SMOOTH", "fly": False, "grip_empty": "WORLD", "theme": "DEFAULT"}
_cache: dict | None = None


def prefs_path() -> Path:
    return Path(bpy.utils.user_resource("CONFIG", path="xr_workbench")) / "vr_prefs.json"


def prefs() -> dict:
    global _cache
    if _cache is None:
        _cache = dict(DEFAULTS)
        try:
            stored = json.loads(prefs_path().read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                _cache.update({key: type(DEFAULTS[key])(stored[key])
                               for key in DEFAULTS if key in stored})
        except (OSError, ValueError, TypeError):
            pass  # Missing or damaged file: defaults, rewritten on the next change.
    return _cache


def set_pref(key: str, value) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    data = prefs()
    data[key] = type(DEFAULTS[key])(value)
    path = prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        print(f"XR_WORKBENCH prefs not saved: {exc}", flush=True)


def reset_cache() -> None:
    global _cache
    _cache = None


def _getter(key):
    return lambda _self: prefs()[key]


def _setter(key):
    return lambda _self, value: set_pref(key, value)


def pref_property(kind, key, **options):
    """A settings property stored in vr_prefs.json instead of the scene."""
    return kind(get=_getter(key), set=_setter(key), options={"SKIP_SAVE"}, **options)


def enum_pref_property(key, items, **options):
    """An EnumProperty stored as its identifier in vr_prefs.json."""
    identifiers = [item[0] for item in items]

    def get(_self):
        value = prefs()[key]
        return identifiers.index(value) if value in identifiers else identifiers.index(DEFAULTS[key])

    def set_(_self, index):
        set_pref(key, identifiers[index])

    return bpy.props.EnumProperty(items=items, get=get, set=set_, options={"SKIP_SAVE"}, **options)


def reset_controls() -> None:
    for key in DEFAULTS:
        if key.startswith("slot_") or key in {"left_handed", "trigger_threshold", "grip_threshold",
                                              "stick_threshold", "locomotion", "fly", "grip_empty",
                                              "menu_hand"}:
            set_pref(key, DEFAULTS[key])


def slot_label(slot: str) -> str:
    """Short name of what a physical button currently does."""
    from . import controllers
    action = controllers.slot_action(slot)
    return next(label for key, label, _d in controllers.SLOT_ACTIONS if key == action)


def controls_lines(profile: str) -> list[str]:
    index = profile in {"AUTO", "INDEX"}
    quest = profile in {"AUTO", "QUEST"}
    lines = ["Menus sit on your left hand: point with the right and pull",
             "the trigger, or right stick up/down + stick click.",
             "Labels by each controller always show every button.",
             "Trigger (either hand): press buttons; use the chosen tool.",
             "Right grip: pick up and carry. Both grips on it: pull to",
             "resize. Left grip on nothing: move the world.",
             "Grip a floating desktop panel to move it.",
             "Right A: hand menu.  Right B: Add.  Stick click: wheel."]
    lines.append("Left A: undo.  Left B: back / cancel." if index and not quest else
                 "Quest X: undo.  Quest Y: back / cancel." if quest and not index else
                 "Undo: Index A / Quest X.  Back: Index B / Quest Y.")
    lines.append("Index left pad (press): teleport.  Settings > Buttons: remap."
                 if index else "Settings > Buttons: remap any button or go left-handed.")
    return lines


DEFAULT_SLOT_LABELS = {"RIGHT_A": "Hand Menu", "RIGHT_B": "Add Shapes", "RIGHT_STICK": "Tool Wheel",
                       "LEFT_A": "Undo", "LEFT_STICK": "Show / Hide Desktop"}


def button_hints(profile: str, hand: str, state: str, tool_label: str = "", hold_mode: str = "",
                 slots: dict | None = None, left_handed: bool = False) -> list[str]:
    """What each button on one controller does right now.

    ``state`` is "menu" (a hand menu is open), "holding" (an object is held),
    "extruding" (a face extrusion is being pulled) or "idle". ``slots`` names the assigned action of each button. Labels use
    Index names, Quest names, or both for Auto.
    """
    slots = {**DEFAULT_SLOT_LABELS, **(slots or {})}
    index, quest = profile in {"AUTO", "INDEX"}, profile in {"AUTO", "QUEST"}
    both = index and quest
    dominant = (hand == "LEFT") == left_handed
    grip = "Grip: pick up / world" if dominant else "Grip: world / pick up"
    stick = "Stick: turn" if dominant else "Stick: walk"
    if hand == "RIGHT":
        if state == "menu":
            lines = ["Stick click: press", "Trigger: point and press", "Left B/Y: back"]
            return (["Stick up/down: choose"] + lines) if dominant else lines
        if state == "edit":
            first = "Trigger: pull to extrude" if tool_label.startswith("extrude") else f"Trigger: pick {tool_label}"
            return [first, "Sweep: add more" if not tool_label.startswith("extrude") else "Release: keep it",
                    "Grip: move the selection", "Stick click: V / E / F wheel"]
        if state == "extruding":
            return ["A: keep segment, extrude again", "Stick click: straight / free",
                    "Release trigger: done", "B/Y (other hand): cancel"]
        if state == "holding":
            return ["Other grip: pull apart to resize", "A: stamp a copy",
                    f"Stick click: {hold_mode or 'hold mode'}", "Stick up/down: push / pull"]
        b = f"B: {slots['RIGHT_B']}"
        return [f"Trigger: {tool_label or 'use tool'}", f"{grip}   {stick}", f"A: {slots['RIGHT_A']}   {b}",
                f"Stick click: {slots['RIGHT_STICK']}"]
    undo, back = ("A/X", "B/Y") if both else ("X", "Y") if quest else ("A", "B")
    if state == "menu":
        return [f"{back}: back", "Stick up/down: choose" if dominant else stick]
    if state == "holding":
        return [f"{back}: cancel", "Stick click: set it down", "Grip: 2nd hand, pull to resize"]
    if state == "extruding":
        return [f"{back}: cancel the segment", "Right A: next segment"]
    if state == "edit":
        return [f"{undo}: {slots['LEFT_A']}   {back}: back", "Grip: world / 2nd hand",
                "Stick: walk" if left_handed is False else "Stick: turn"]
    return [f"Trigger: {tool_label or 'use tool'}", f"{grip}   {stick}",
            f"{undo}: {slots['LEFT_A']}   {back}: back", f"Stick click: {slots['LEFT_STICK']}"]


def guide_steps(profile: str) -> list[tuple[str, list[str], str]]:
    """(title, lines, try-it) for each Guide step."""
    return [
        ("Welcome to VR Blender",
         ["Menus sit on your left hand. Point the right controller at a",
          "button and pull its trigger (a dot shows where), or use the right",
          "stick: up/down picks, click presses. You feel each press."],
         "Pull the other trigger on Next."),
        ("Your controllers", controls_lines(profile),
         "Press Right A twice to close and reopen this menu."),
        ("Rays show what will happen",
         ["Each ray ends in a ring the colour of your tool: white to grab,",
          "red to cut, green to draw, orange to sculpt, the paint colour",
          "to spray. Sculpt and spray rings show your real brush size."],
         "Close the menu and sweep a ray across the floor."),
        ("Pick things up",
         ["Squeeze the right grip on an object: it follows your hand and",
          "turns with it. Squeeze the other grip on it too: pull apart",
          "to resize, swing to turn. Right stick: push away / pull closer.",
          "Several at once: turn on Multi-Select (Quick menu or Edit),",
          "trigger objects to add or remove; trigger empty space to clear."],
         "Create a Cube, then grab and stretch it."),
        ("While you hold something",
         ["Right A stamps a copy where it is and you keep carrying.",
          "Right stick click: Follow, Precision 1/4, Snap 5 cm, Surface",
          "(slides it upright along floors and tables).",
          "Left stick click sets it down on the surface below.",
          "Extruding? A keeps the segment and extrudes again; stick",
          "click switches straight-out / free."],
         "Grab a cube and stamp three copies in a row."),
        ("Make and cut",
         ["Add menu: pick a shape; a ghost shows where it lands.",
          "Trigger places it, hold to slide it. Draw tools show a ghost at",
          "the tip, then the real shape as you drag. Cut Away / Join /",
          "Keep Overlap draw live Boolean cutters."],
         "Open Add, pick Cube, and place it on the floor."),
        ("All of Blender",
         ["Quick menu > Blender Menus: File, Edit, Add, Object, Select,",
          "View, Render, Window, Help. Each opens on your hand; stick",
          "click goes deeper, B comes back. Nothing is hidden from you."],
         "Open Blender Menus > File and come back with B."),
        ("Sculpt, paint, animate",
         ["Sculpt: Clay Ball, then Clay / Grab / Smooth; squeeze harder",
          "for more, Mirror X for symmetry. Materials: shelf, trigger a",
          "block, spray. Animate: key, scrub sideways, Record every move."],
         "Try the Workflow buttons on the Desktop page."),
        ("Size, travel and comfort",
         ["Left stick walks, right stick turns. Grip empty space to pull",
          "the world; squeeze both grips and pull apart or together to",
          "change your size, twist to turn the world (it clicks at 1x).",
          "Travel page: Go to Selection, Reset View. Settings > Start &",
          "Travel: Smooth or Teleport (stick forward, aim, let go), Fly."],
         "Grip empty space with both hands and pull them apart."),
        ("Real Blender, in your hands",
         ["Undo Ring (Quick menu): your real undo history around your",
          "wrist. Right stick down steps back, up forward; a button closes.",
          "Modifier Tower (Modifiers menu): the stack as blocks. Grip one",
          "up or down to reorder, trigger it to switch it on or off, point",
          "and push the stick to dial its value. Node Garden > Geometry",
          "Nodes of Selected: relink procedural modifiers, live."],
         "Add Bevel and Subdivision, then swap them in the tower."),
    ]


class XRWB_OT_guide(bpy.types.Operator):
    bl_idname = "xrwb.guide"
    bl_label = "Guide"
    bl_description = "Step through the VR Guide; Finish marks it done"
    action: bpy.props.EnumProperty(items=(("OPEN", "Open", ""), ("NEXT", "Next", ""), ("BACK", "Back", ""),
                                          ("FINISH", "Finish", ""), ("SKIP", "Skip", "")),
                                   default="OPEN", options={"SKIP_SAVE"})

    def execute(self, context):
        from . import interaction_log, runtime, ui

        settings = context.window_manager.xrwb_settings
        count = len(guide_steps(settings.controller_profile))
        if self.action == "OPEN":
            settings.guide_step = 0
            if runtime.session_running(context) and context.window is not None:
                with ui._view3d_override(context):
                    return bpy.ops.xrwb.command_menu(page="GUIDE", controller=1)
            settings.command_page = "GUIDE"
            return {"FINISHED"}
        if self.action in {"NEXT", "BACK"}:
            settings.guide_step = max(0, min(count - 1, settings.guide_step + (1 if self.action == "NEXT" else -1)))
            return {"FINISHED"}
        set_pref("guide_done", True)
        settings.command_page = "CREATE"
        interaction_log.emit("guide", "Guide finished. Replay it any time from the Guide tab.", context)
        return {"FINISHED"}


CLASSES = (XRWB_OT_guide,)
