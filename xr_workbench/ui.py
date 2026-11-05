"""M0 sidebar, report export, and safe OpenXR session controls."""

from __future__ import annotations

from contextlib import contextmanager
from math import isfinite, pi
import os
from pathlib import Path
from textwrap import wrap

from mathutils import Matrix, Quaternion, Vector

import bpy
from bpy_extras.io_utils import ExportHelper

from . import (
    controllers, editing, hands, onboarding, physical_tools, polish, retopo, runtime, tool_wheel, viewer_scale,
)

# Column-major in a 5 x 2 grid: Create/Edit, Sculpt/Materials, Animate/Menus,
# Desktop/Travel, Settings/Guide.
COMMAND_PAGES = (("CREATE", "Create", "Add objects"), ("EDIT", "Edit", "Select, transform or extrude"),
                 ("SCULPT", "Sculpt", "Clay tools and your size"),
                 ("PAINT", "Materials", "Material blocks, spray and node logic"),
                 ("ANIMATE", "Animate", "Keys, time scrubbing and recording"),
                 ("MENUS", "Menus", "Native Blender menus"),
                 ("WORKSPACE", "Desktop", "Workflows and every Blender editor"),
                 ("MOVE", "Travel", "Movement, comfort and your size"),
                 ("CONTROLS", "Settings", "Feel, startup, menu size, scale sensitivity and travel"),
                 ("GUIDE", "Guide", "A short walkthrough to get situated"))
PAGE_ICONS = {"CREATE": "ADD", "EDIT": "EDITMODE_HLT", "SCULPT": "SCULPTMODE_HLT", "PAINT": "MATERIAL",
              "ANIMATE": "RENDER_ANIMATION", "MENUS": "COLLAPSEMENU", "WORKSPACE": "WINDOW",
              "MOVE": "VIEW_PAN", "CONTROLS": "PREFERENCES", "GUIDE": "HELP"}
EDIT_TOOLS = (("select", "Select", "Trigger selects the object under its ray"),
              ("move", "Move", "Hold a trigger over selected geometry and move the hand"),
              ("rotate", "Rotate", "Hold a trigger over selected geometry and rotate the hand"),
              ("scale", "Scale Object", "Hold a trigger and drag sideways; Settings controls the distance for 2x size"),
              ("extrude", "Extrude Face", "Point at a mesh face; hold trigger and pull along its normal"),
              ("face_scale", "Scale Face", "Point at a face and drag sideways; Settings controls the distance for 2x size"))
EDGE_TOOLS = (("select_edge", "Select Edges", "Click near a face edge to toggle its selection"),
              ("bevel_edge", "Bevel Edges", "Hold trigger on the mesh and move to preview bevel width; release confirms"))
DRAW_TOOLS = (("draw_cube", "Draw Cube", "Hold a trigger at one corner, move to size a box, release to create"),
              ("draw_sphere", "Draw Sphere", "Hold and drag a volume for a sphere or ellipsoid; release to create"),
              ("draw_cylinder", "Draw Cylinder", "Hold and drag a volume for a cylinder; release to create"))
CUT_TOOLS = (("cut_difference", "Cut Away", "Draw a retained cutter into the selected mesh to subtract it"),
             ("cut_union", "Join Volume", "Draw a retained cutter to union with the selected mesh"),
             ("cut_intersect", "Keep Overlap", "Draw a retained cutter to keep the overlap with the selected mesh"))
SCULPT_TOOLS = (("sculpt_grab", "Grab", "Hold trigger on the surface and pull a soft region with your hand"),
                ("sculpt_clay", "Clay", "Hold trigger and sweep over the surface to build up (or dig)"),
                ("sculpt_smooth", "Smooth", "Hold trigger and sweep to relax bumps"),
                ("sculpt_flatten", "Flatten", "Hold trigger and sweep to level the surface under the brush"),
                ("sculpt_snake", "Snake Hook", "Hold trigger on the surface and pull out a long horn or tentacle"),
                ("sculpt_inflate", "Inflate", "Hold trigger and sweep to swell the surface along its normals"),
                ("sculpt_crease", "Crease", "Hold trigger and sweep to cut a sharp groove (Dig raises a ridge)"),
                ("sculpt_layer", "Layer", "Hold trigger and sweep to raise a flat layer of one fixed height"),
                ("sculpt_mask", "Mask", "Hold trigger and sweep to protect the surface from other brushes"))
# 0.20 procedural tools: Subdivision Lab dial, Guided Growth seed, CurioMesh Retopo Loom.
LAB_TOOLS = (("lab_dial", "Lab Dial", "Hold trigger: up/down sets levels, wrist roll the scheme's main value"),
             ("grow_seed", "Grow", "Hold trigger to plant, pull for direction and length; A re-rolls"),
             ("retopo_loom", "Retopo", "Point at a mesh and pull the trigger for a clean quad copy beside it"))
PAINT_TOOLS = (("spray_material", "Spray Material",
                "Hold trigger to spray the loaded material onto faces; trigger a block to load it"),
               ("paint_vertex", "Paint Colour",
                "Hold trigger and sweep to paint the chosen colour into the mesh's XR Paint colours"))
ANIMATE_TOOLS = (("scrub_time", "Scrub Time", "Hold trigger anywhere and move sideways through frames"),)
# Quick-only tools: chosen from the Add or Quick menus rather than a deck page.
QUICK_TOOLS = (("place_object", "Place Shape", "A ghost follows your ray; trigger places it, hold to slide"),
               ("measure", "Measure", "Hold trigger from one point to another; you feel a tick every 10 cm"),
               ("eyedropper", "Eyedropper", "Trigger any surface to load its material into the spray"),
               ("extrude_free", "Free Extrude", "Point at a face, hold trigger: the new face follows your hand anywhere"),
               ("uv_island", "UV Island", "Point at a face, hold to grow an island, release to unwrap just it"),
               ("inset", "Inset Face", "Point at a face, hold trigger and drag sideways to inset a border"),
               ("draw_curve", "3D Pen", "Hold trigger and draw in the air; release makes a smooth tube curve"),
               ("place_prefab", "Place Prefab", "A ghost of the chosen prefab follows your ray; trigger places it"),
               ("sdf_blob", "SDF Blobs", "Hold trigger to add melting blobs at the pen tip; trigger a blob to move it"),
               ("edit_extrude", "Extrude Selection",
                "Edit Mode: hold trigger and pull to extrude the selected vertices, edges or faces"))
PLACE_TOOLS = QUICK_TOOLS  # Older name kept for scripts.
ALL_TOOLS = (EDIT_TOOLS + EDGE_TOOLS + DRAW_TOOLS + CUT_TOOLS + SCULPT_TOOLS + PAINT_TOOLS + ANIMATE_TOOLS
             + QUICK_TOOLS + LAB_TOOLS)

def _set_status(settings, summary: str, detail: str = "") -> None:
    settings.status_summary = summary
    settings.status_detail = detail


def _update_opacity(settings, _context) -> None:
    runtime.configure_overlay(settings.overlay_enabled, settings.panel_opacity)


def _update_travel_speed(settings, context):
    if hasattr(context.window_manager.xr_session_settings, "fly_speed"):  # Blender 5.0+
        context.window_manager.xr_session_settings.fly_speed = settings.travel_speed


def set_runtime_passthrough(xr_settings, enabled: bool) -> bool:
    """Set Blender's native request flag; this add-on never reads camera frames."""
    if xr_settings is None or not hasattr(xr_settings, "show_passthrough"):
        return False
    try:
        xr_settings.show_passthrough = bool(enabled)
        return bool(xr_settings.show_passthrough) is bool(enabled)
    except (AttributeError, RuntimeError, TypeError):
        return False


@contextmanager
def _view3d_override(context):
    manager = context.window_manager
    windows = list(manager.windows)
    if context.window in windows:
        windows.remove(context.window)
        windows.insert(0, context.window)
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
            if region is not None:
                with context.temp_override(
                    window=window,
                    screen=screen,
                    area=area,
                    region=region,
                    space_data=area.spaces.active,
                ):
                    yield
                    return
    yield


def _summary_from_report(report: dict) -> tuple[str, str]:
    m0 = report.get("m0", {}) if isinstance(report, dict) else {}
    openxr = report.get("openxr", {}) if isinstance(report, dict) else {}
    status = m0.get("status", "unknown") if isinstance(m0, dict) else "unknown"
    runtime_info = openxr.get("runtime", {}) if isinstance(openxr, dict) else {}
    runtime_name = (
        runtime_info.get("name", "runtime unknown")
        if isinstance(runtime_info, dict)
        else "runtime unknown"
    )
    blockers = m0.get("blockers", []) if isinstance(m0, dict) else []
    detail = (
        "; ".join(str(item) for item in blockers) if blockers else str(runtime_name)
    )
    return f"M0: {status}", detail


def _overlay_status_from_report(report: dict) -> tuple[str, str, str]:
    m0 = report.get("m0", {}) if isinstance(report, dict) else {}
    sensors = report.get("sensors", {}) if isinstance(report, dict) else {}
    camera = sensors.get("camera", {}) if isinstance(sensors, dict) else {}
    input_sources = (
        sensors.get("input_sources", {}) if isinstance(sensors, dict) else {}
    )
    return (
        str(m0.get("status", "unknown")) if isinstance(m0, dict) else "unknown",
        str(camera.get("availability", "unknown"))
        if isinstance(camera, dict)
        else "unknown",
        str(input_sources.get("active_provenance") or "unknown")
        if isinstance(input_sources, dict)
        else "unknown",
    )


# Characters per line that fit the 24-unit hand card at any UI scale.
_CARD_TEXT_WIDTH = 64


def _label_lines(layout, text: str, width: int = 46) -> None:
    words, line = str(text).split(), ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if line and len(candidate) > width:
            layout.label(text=line)
            line = word
        else:
            line = candidate
    if line:
        layout.label(text=line)


EDIT_MODE_TOOLS = {"select", "move", "edit_extrude"}


def _scene_tool_reason(context, tool):
    """Explain the same basic guards used by our Object-mode gesture adapters."""
    if not runtime.session_running(context):
        return "Start XR to choose a controller tool."
    if context.mode != "OBJECT" and not (context.mode == "EDIT_MESH" and tool in EDIT_MODE_TOOLS):
        return ("In Edit Mode: Select, Move or Extrude Selection; Object Mode for the rest."
                if context.mode == "EDIT_MESH" else "Switch to Object Mode for controller scene tools.")
    if not editing.undo_available(context):
        return "Enable Global Undo and at least one undo step."
    if editing.active_transaction() is not None:
        return "Release or cancel the current edit first."
    if tool.startswith("cut_"):
        from . import draw_editing

        try:
            draw_editing._require_target(context)
        except (AttributeError, RuntimeError, ReferenceError, TypeError) as exc:
            return str(exc)
    return ""


def _tool_buttons(layout, context, tools, columns=3):
    grid = layout.grid_flow(columns=columns, even_columns=True, align=True)
    for identifier, label, _description in tools:
        cell = grid.row()
        cell.enabled = not _scene_tool_reason(context, identifier)
        cell.operator("xrwb.set_edit_tool", text=label,
                      depress=context.window_manager.xrwb_settings.edit_tool == identifier).tool = identifier


def _bevel_object_reason(context):
    if context.mode != "OBJECT":
        return "Switch to Object Mode to add an object bevel."
    obj = context.active_object
    if (obj is None or obj.type != "MESH" or obj not in context.selected_editable_objects
            or obj.library or obj.override_library):
        return "Select an editable mesh for Object Bevel."
    if not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
        return "Enable Global Undo and at least one undo step."
    if editing.active_transaction() is not None:
        return "Release or cancel the current edit first."
    scales = tuple(obj.matrix_world.to_scale())
    if (not all(isfinite(value) for row in obj.matrix_world for value in row)
            or min(scales) <= 1e-8 or max(scales)-min(scales) > max(scales)*1e-4):
        return "Object Bevel needs valid uniform scale; check transforms or Apply Scale."
    return ""


def _controller_help(profile):
    lines = ["Right A: menu (on your left hand). Right B: Add.",
             "Either trigger: click or tool; release confirms.",
             "Right stick click: tool wheel. Left stick click: desktop.",
             "Right grip: pick up. Left grip: move world / 2nd hand.",
             "Grip a desktop panel to move it."]
    if profile in {"AUTO", "INDEX"}:
        lines.extend(("Index left A / B: undo / back.", "Index pads: left press teleports."))
    if profile in {"AUTO", "QUEST"}:
        lines.extend(("Quest X / Y: undo / back.",))
    return tuple(lines)


def _size_controls(layout, context):
    running = runtime.session_running(context)
    row = layout.row(align=True)
    row.label(text=f"YOUR SIZE x{viewer_scale.current_scale(context):g}" if running else "YOUR SIZE (XR only)",
              icon="VIEW_ZOOM")
    row.enabled = running
    row.operator("xrwb.viewer_scale", text="Smaller /2").factor = 0.5
    row.operator("xrwb.viewer_scale", text="Bigger x2").factor = 2.0
    row = layout.row(align=True)
    row.enabled = running
    for key, label, _value, _description in viewer_scale.PRESETS:
        row.operator("xrwb.viewer_scale", text=label).preset = key


def _action_feedback(layout):
    from . import interaction_log

    message = interaction_log.last_message() or "Ready: choose a tool, then use either trigger."
    warning = interaction_log.last_level() in {"ERROR", "WARNING"}
    # Two fixed, normal-pitch lines: the header shares the first line so the
    # tallest (Edit / Settings) cards fit a 1080p window at 150% UI scale,
    # and outcomes never move the buttons above.
    column = layout.box().column(align=True)
    column.scale_y = 0.8
    lines = wrap(f"Last action: {message}", width=_CARD_TEXT_WIDTH, max_lines=2, placeholder="...")
    for index in range(2):
        column.label(text=lines[index] if index < len(lines) else " ",
                     icon=("ERROR" if warning else "INFO") if index == 0 else "BLANK1")


class XRWB_PG_settings(bpy.types.PropertyGroup):
    controller_profile: bpy.props.EnumProperty(
        name="Controller Profile", default="AUTO", options={"SKIP_SAVE"},
        items=(("AUTO", "Auto: Index + Quest", "Offer both controller families to the runtime"),
               ("INDEX", "Valve Index", "Index force sensors and thumb pad"),
               ("QUEST", "Meta Quest 3 / Touch", "PC VR Touch-compatible controls; not standalone")),
    )
    command_page: bpy.props.EnumProperty(
        name="Command Page", items=COMMAND_PAGES, options={"SKIP_SAVE"},
    )
    hand_menu_width: bpy.props.FloatProperty(
        name="Hand Menu Width (m)", default=0.48, min=0.22, max=0.9,
        description="Physical width of the controller menu; takes effect when reopened",
        options={"SKIP_SAVE"},
    )
    travel_speed: bpy.props.FloatProperty(
        name="Maximum Travel Speed (m/s)", default=1.4, min=0.1, max=4.0,
        options={"SKIP_SAVE"}, update=_update_travel_speed,
    )
    controller_fallback: bpy.props.BoolProperty(
        name="Tracked Controller Input", default=False, options={"SKIP_SAVE"}
    )
    hand_status: bpy.props.StringProperty(
        default="Optical hand provider not yet available.", options={"SKIP_SAVE"}
    )
    pinch_close: bpy.props.FloatProperty(
        name="Pinch Close (m)",
        default=0.020,
        min=0.005,
        max=0.08,
        options={"SKIP_SAVE"},
    )
    pinch_open: bpy.props.FloatProperty(
        name="Pinch Open (m)",
        default=0.030,
        min=0.006,
        max=0.12,
        options={"SKIP_SAVE"},
    )
    pinch_dwell: bpy.props.FloatProperty(
        name="Pinch Persistence (s)",
        default=0.050,
        min=0.01,
        max=0.5,
        options={"SKIP_SAVE"},
    )
    editor_width: bpy.props.FloatProperty(
        name="Editor Width (m)", default=1.6, min=0.4, max=4.0, options={"SKIP_SAVE"}
    )
    editor_distance: bpy.props.FloatProperty(
        name="Editor Distance (m)", default=1.2, min=0.4, max=5.0, options={"SKIP_SAVE"}
    )
    editor_separated: bpy.props.BoolProperty(
        name="Separate Editor Panels", default=True, options={"SKIP_SAVE"}
    )
    addon_status: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})
    edit_tool: bpy.props.EnumProperty(
        name="Spatial Edit Tool",
        items=ALL_TOOLS,
        default="select",
        options={"SKIP_SAVE"},
    )
    guide_step: bpy.props.IntProperty(name="Guide Step", default=0, min=0, options={"SKIP_SAVE"})
    haptic_strength: onboarding.pref_property(
        bpy.props.FloatProperty, "haptic_strength", name="Haptics", min=0.0, max=1.0,
        description="Strength of the controller clicks and buzzes; 0 turns them off")
    ray_markers: onboarding.pref_property(
        bpy.props.BoolProperty, "ray_markers", name="Ray Rings",
        description="Tool-coloured ring where each controller ray lands")
    button_hints: onboarding.pref_property(
        bpy.props.BoolProperty, "button_hints", name="Button Labels",
        description="Small labels beside each controller saying what every button does right now")
    settings_section: bpy.props.EnumProperty(
        name="Settings Section", default="FEEL", options={"SKIP_SAVE"},
        items=(("FEEL", "Feel", "Haptics, rings, labels, menu and scale feel", "PREFERENCES", 0),
               ("BUTTONS", "Buttons", "What each button does, handedness and sensitivity", "EVENT_A", 1),
               ("TOOLS", "Tools", "Tool wheel, snapping, precision and UVs", "TOOL_SETTINGS", 2),
               ("START", "Start & Travel", "Startup, travel and comfort", "VIEW_PAN", 3)))
    left_handed: onboarding.pref_property(
        bpy.props.BoolProperty, "left_handed", name="Left-Handed",
        description="Left grip picks up and the left stick turns; right grip moves the world and the "
                    "right stick walks. Applies next time VR starts")
    trigger_threshold: onboarding.pref_property(
        bpy.props.FloatProperty, "trigger_threshold", name="Trigger Click Point", min=0.1, max=0.9,
        description="How far the trigger travels before it clicks (lower = lighter). Next VR start")
    grip_threshold: onboarding.pref_property(
        bpy.props.FloatProperty, "grip_threshold", name="Grip Point", min=0.1, max=0.9,
        description="How hard to squeeze before the grip picks up (Quest grips are lighter). Next VR start")
    stick_threshold: onboarding.pref_property(
        bpy.props.FloatProperty, "stick_threshold", name="Stick Step Point", min=0.2, max=0.9,
        description="How far to push the stick for one menu step, push/pull or brush step. Next VR start")
    slot_RIGHT_A: onboarding.enum_pref_property("slot_RIGHT_A", controllers.SLOT_ACTIONS, name="Right A")
    slot_RIGHT_B: onboarding.enum_pref_property("slot_RIGHT_B", controllers.SLOT_ACTIONS, name="Right B (Index)")
    slot_RIGHT_STICK: onboarding.enum_pref_property("slot_RIGHT_STICK", controllers.SLOT_ACTIONS,
                                                    name="Right Stick Click")
    slot_LEFT_A: onboarding.enum_pref_property("slot_LEFT_A", controllers.SLOT_ACTIONS, name="Left A / X")
    slot_LEFT_STICK: onboarding.enum_pref_property("slot_LEFT_STICK", controllers.SLOT_ACTIONS,
                                                   name="Left Stick Click")
    wheel_set: onboarding.enum_pref_property(
        "wheel_set", tool_wheel.WHEEL_SETS, name="Wheel Tools",
        description="Which tools the radial wheel offers")
    wheel_size_m: onboarding.pref_property(
        bpy.props.FloatProperty, "wheel_size", name="Wheel Size", min=0.06, max=0.25,
        subtype="DISTANCE", unit="LENGTH",
        description="Radius of the tool wheel; smaller = shorter flicks")
    snap_grid_cm: onboarding.pref_property(
        bpy.props.FloatProperty, "snap_grid_cm", name="Snap Grid (cm)", min=0.1, max=100.0,
        description="Snap hold mode: position steps in centimetres")
    snap_angle: onboarding.pref_property(
        bpy.props.FloatProperty, "snap_angle", name="Snap Angle", min=1.0, max=90.0,
        description="Snap hold mode: rotation steps in degrees")
    precision: onboarding.pref_property(
        bpy.props.FloatProperty, "precision", name="Precision", min=0.05, max=1.0,
        description="Precision hold mode: how much of your hand motion reaches the object")
    hover_ticks: onboarding.pref_property(
        bpy.props.BoolProperty, "hover_ticks", name="Hover Ticks",
        description="A light tick when a ray lands on a new object")
    label_scale: onboarding.pref_property(
        bpy.props.FloatProperty, "label_scale", name="Label Size", min=0.6, max=2.0,
        description="Size of the button labels beside the controllers")
    locomotion: onboarding.enum_pref_property(
        "locomotion", (("SMOOTH", "Smooth", "The walking stick glides you smoothly (motion vignette applies)"),
                       ("TELEPORT", "Teleport", "Push the walking stick forward to aim, let go to jump; "
                                                "most comfortable, works on Quest too")),
        name="Locomotion", description="How the walking stick moves you. Applies next time VR starts")
    fly: onboarding.pref_property(
        bpy.props.BoolProperty, "fly", name="Fly",
        description="Smooth moves go where you look, up and down included. Applies next time VR starts")
    grip_empty: onboarding.enum_pref_property(
        "grip_empty", (("WORLD", "Moves the World", "Grip on empty space holds the world; both grips turn and "
                                                    "resize it"),
                       ("SELECTION", "Grabs Selection", "Grip on empty space picks up the selection from afar")),
        name="Grip on Empty Space", description="What the pick-up grip does when it points at nothing")
    menu_hand: onboarding.enum_pref_property(
        "menu_hand", (("OFF", "Off Hand", "Menus sit on your non-dominant hand; point and click with the other"),
                      ("PRESSING", "Pressing Hand", "Menus appear on the hand whose button opened them")),
        name="Menu Hand", description="Which hand holds the hand menu and list menus")
    two_hand: onboarding.enum_pref_property(
        "two_hand", (("SCALE_ROTATE", "Scale + Turn", "Pull apart to resize, swing to turn"),
                     ("SCALE", "Scale Only", "Both hands only resize and carry; no turning"),
                     ("AXIS", "Stretch Axis", "Hands lined up with an object axis stretch just that axis")),
        name="Two Hands", description="What holding one object with both hands does")
    theme: onboarding.enum_pref_property(
        "theme", [(key, label, description) for key, (label, description, _values) in polish.THEMES.items()],
        name="Theme", description="Colours of the labels, plates, wheel and rings in VR. Applies at once")
    toasts: onboarding.pref_property(
        bpy.props.BoolProperty, "toasts", name="Toasts",
        description="The outcome of each action floats briefly by the hand that did it")
    readouts: onboarding.pref_property(
        bpy.props.BoolProperty, "readouts", name="Live Readouts",
        description="Live distance, angle and size beside the working hand while you edit")
    hover_outline: onboarding.pref_property(
        bpy.props.BoolProperty, "hover_outline", name="Hover Outline",
        description="Corner brackets around the object your ray is on")
    detents: onboarding.pref_property(
        bpy.props.BoolProperty, "detents", name="Detents",
        description="A crisp tick every centimetre, 10 % or snap step while you drag, like a dial")
    menu_at_start: onboarding.pref_property(
        bpy.props.BoolProperty, "menu_at_start", name="Menu at Start",
        description="Open the hand menu when VR starts")
    desktop_at_start: onboarding.pref_property(
        bpy.props.BoolProperty, "desktop_at_start", name="Desktop at Start",
        description="Show the Blender desktop panels as soon as VR starts")
    prefab_kind: bpy.props.StringProperty(name="Prefab", default="BUILTIN:TABLE", options={"SKIP_SAVE"})
    sdf_shape: bpy.props.EnumProperty(
        name="Blob Shape", items=[(k, label, "", icon, i) for i, (k, label, icon) in enumerate(
            (("BALL", "Ball", "MESH_UVSPHERE"), ("CAPSULE", "Capsule", "MESH_CAPSULE"),
             ("ELLIPSOID", "Ellipsoid", "MESH_UVSPHERE"), ("CUBE", "Rounded Cube", "MESH_CUBE")))],
        default="BALL", options={"SKIP_SAVE"})
    sdf_radius_m: bpy.props.FloatProperty(
        name="Blob Size", default=0.06, min=0.005, max=0.5, subtype="DISTANCE", unit="LENGTH",
        description="Physical blob radius; squeeze harder for bigger, right stick resizes", options={"SKIP_SAVE"})
    sdf_carve: bpy.props.BoolProperty(name="Carve", default=False, options={"SKIP_SAVE"},
                                      description="New blobs cut away instead of adding (a smooth boolean)")
    sdf_blend: bpy.props.FloatProperty(name="Blend", default=2.0, min=0.3, max=10.0, options={"SKIP_SAVE"},
                                       description="Stiffness: lower melts blobs together more softly")
    sdf_mode: bpy.props.EnumProperty(
        name="SDF Mode", items=(("TRAIL", "Trail", "Moving leaves a string of blobs, like drawing with clay"),
                                ("PLACE", "Place", "One blob follows the pen tip; release places it")),
        default="TRAIL", options={"SKIP_SAVE"})
    pen_radius_m: bpy.props.FloatProperty(
        name="Pen Thickness", default=0.006, min=0.0005, max=0.1, subtype="DISTANCE", unit="LENGTH",
        description="Physical tube radius of the 3D Pen; squeeze harder for thicker", options={"SKIP_SAVE"})
    paint_colour: bpy.props.FloatVectorProperty(
        name="Paint Colour", subtype="COLOR", size=4, min=0.0, max=1.0, default=(0.9, 0.25, 0.2, 1.0),
        description="Colour for Paint Colour", options={"SKIP_SAVE"})
    workflow: bpy.props.EnumProperty(name="Workflow", items=viewer_scale.WORKFLOWS, default="MODEL",
                                     options={"SKIP_SAVE"})
    spray_material: bpy.props.StringProperty(name="Spray Material", default="", options={"SKIP_SAVE"})
    spray_radius_m: bpy.props.FloatProperty(
        name="Spray Size", default=0.03, min=0.005, max=0.3, subtype="DISTANCE", unit="LENGTH",
        description="Physical spray radius; shrink yourself for finer painting", options={"SKIP_SAVE"})
    sculpt_radius_m: bpy.props.FloatProperty(
        name="Brush Size", default=0.04, min=0.005, max=0.3, subtype="DISTANCE", unit="LENGTH",
        description="Physical brush radius; shrink yourself for finer detail", options={"SKIP_SAVE"})
    sculpt_strength: bpy.props.FloatProperty(name="Strength", default=0.5, min=0.05, max=1.0,
                                             options={"SKIP_SAVE"})
    sculpt_radial: bpy.props.IntProperty(
        name="Radial", default=0, min=0, max=12, options={"SKIP_SAVE"},
        description="Repeat every brush this many times around the object's local Z (0 = off)")
    sculpt_layer_height_m: bpy.props.FloatProperty(
        name="Layer Height", default=0.005, min=0.001, max=0.05, subtype="DISTANCE", unit="LENGTH",
        description="Physical height of the Layer brush", options={"SKIP_SAVE"})
    mask_extract_m: bpy.props.FloatProperty(
        name="Extract Thickness", default=0.004, min=0.0005, max=0.05, subtype="DISTANCE", unit="LENGTH",
        description="Physical thickness of a Mask Extract shell", options={"SKIP_SAVE"})
    sculpt_detail: bpy.props.EnumProperty(
        name="Detail Where Stretched", default="AUTO", options={"SKIP_SAVE"},
        items=(("AUTO", "Auto", "On for Snake Hook and Inflate"), ("ON", "On", "Every stroke adds detail"),
               ("OFF", "Off", "Never add detail")))
    retopo_faces: bpy.props.IntProperty(
        name="Target Faces", default=5000, min=500, max=20000, step=500, options={"SKIP_SAVE"},
        description="How many quads the Retopo Loom aims for (steps of 500)")
    retopo_quality: bpy.props.EnumProperty(
        name="Quality", default="Balanced", options={"SKIP_SAVE"},
        items=tuple((key, key, f"CurioMesh {value.title()}") for key, value in retopo.QUALITY.items()))
    tool_props: onboarding.pref_property(
        bpy.props.BoolProperty, "tool_props", name="Tool Props",
        description="Hold a small 3D model of each tool; off shows only the ring cursors")
    tool_belt: onboarding.pref_property(
        bpy.props.BoolProperty, "tool_belt", name="Tool Belt",
        description="Up to 6 tools float at your hip: reach and squeeze a grip near one to swap")
    live_cage: onboarding.pref_property(
        bpy.props.BoolProperty, "live_cage", name="Live Cage",
        description="Heavy modifiers pause while you hold an edit and come back when you let go")
    menu_compact: onboarding.pref_property(
        bpy.props.BoolProperty, "menu_compact", name="Compact",
        description="Shorter hand menu: no help lines, icon-only page tabs")
    place_kind: bpy.props.EnumProperty(
        name="Shape", default="CUBE", options={"SKIP_SAVE"},
        items=tuple((key, label, "", icon, index) for index, (key, (label, icon, _size))
                    in enumerate(physical_tools.PLACE_SHAPES.items())))
    sculpt_mirror_x: bpy.props.BoolProperty(
        name="Mirror X", default=False, options={"SKIP_SAVE"},
        description="Sculpt strokes also shape the mirror side across the object's local X")
    uv_island_angle: bpy.props.FloatProperty(
        name="UV Crease Limit", default=30.0, min=1.0, max=89.0, options={"SKIP_SAVE"},
        description="An island stops where neighbouring faces bend more than this (drag while holding to change)")
    multi_select: bpy.props.BoolProperty(
        name="Multi-Select", default=False, options={"SKIP_SAVE"},
        description="Select tool clicks add or remove objects instead of replacing the selection")
    use_pressure: bpy.props.BoolProperty(
        name="Trigger Pressure", default=True, options={"SKIP_SAVE"},
        description="Squeeze the trigger harder for stronger clay and a wider spray")
    sculpt_invert: bpy.props.BoolProperty(name="Dig", default=False,
                                          description="Clay carves into the surface instead of building up",
                                          options={"SKIP_SAVE"})
    remesh_detail_m: bpy.props.FloatProperty(
        name="Detail", default=0.005, min=0.001, max=0.05, subtype="DISTANCE", unit="LENGTH",
        description="Physical voxel size for Remesh for Detail", options={"SKIP_SAVE"})
    scrub_frames_per_m: bpy.props.FloatProperty(
        name="Frames per Metre", default=50.0, min=5.0, max=500.0,
        description="Frames moved per metre of sideways hand travel", options={"SKIP_SAVE"})
    selection_click_slop_m: bpy.props.FloatProperty(
        name="Select Click Movement Limit (m)",
        default=0.01,
        min=0.001,
        max=0.1,
        options={"SKIP_SAVE"},
    )
    scale_drag_distance_m: bpy.props.FloatProperty(
        name="Scale Drag (2x)", default=0.2, min=0.03, max=1.0,
        subtype="DISTANCE", unit="LENGTH",
        description="Physical sideways hand distance that doubles object or face size",
        options={"SKIP_SAVE"},
    )
    bevel_segments: bpy.props.IntProperty(
        name="Bevel Segments", default=3, min=1, max=12, options={"SKIP_SAVE"}
    )
    cutter_shape: bpy.props.EnumProperty(
        name="Cutter Shape", default="CUBE", options={"SKIP_SAVE"},
        items=(("CUBE", "Box", "Box cutter"), ("SPHERE", "Sphere", "Ellipsoid cutter"),
               ("CYLINDER", "Cylinder", "Cylinder cutter"))
    )
    bevel_width_m: bpy.props.FloatProperty(
        name="Bevel Width (m)", default=0.01, min=0.0001, max=1.0,
        precision=4, options={"SKIP_SAVE"}
    )
    edit_constraint: bpy.props.EnumProperty(
        name="World Constraint",
        items=tuple(
            (key, key.title(), "") for key in ("free", "x", "y", "z", "xy", "xz", "yz")
        ),
        default="free",
        options={"SKIP_SAVE"},
    )
    translation_snap: bpy.props.FloatProperty(
        name="Move Snap (m)", default=0.0, min=0.0, max=100.0, options={"SKIP_SAVE"}
    )
    rotation_snap_degrees: bpy.props.FloatProperty(
        name="Rotation Snap (degrees)",
        default=0.0,
        min=0.0,
        max=180.0,
        options={"SKIP_SAVE"},
    )
    scale_snap: bpy.props.FloatProperty(
        name="Scale Snap", default=0.0, min=0.0, max=1.0, options={"SKIP_SAVE"}
    )
    scale_one_handed: bpy.props.BoolProperty(
        name="One-handed Scale Alternative", default=False, options={"SKIP_SAVE"}
    )
    controller_status: bpy.props.StringProperty(
        default="Index bindings attach when XR starts.", options={"SKIP_SAVE"}
    )
    panel_opacity: bpy.props.FloatProperty(
        name="Panel Background Opacity",
        default=0.25,
        min=0.0,
        max=1.0,
        options={"SKIP_SAVE"},
        update=_update_opacity,
    )
    overlay_enabled: bpy.props.BoolProperty(
        name="Diagnostic Overlay",
        default=False,
        options={"SKIP_SAVE"},
        update=_update_opacity,
    )
    passthrough_opt_in: bpy.props.BoolProperty(
        name="Passthrough Opt-in", default=False, options={"SKIP_SAVE"}
    )
    status_summary: bpy.props.StringProperty(
        default="Refresh capability status", options={"SKIP_SAVE"}
    )
    status_detail: bpy.props.StringProperty(
        default="No camera or optical hand access is attempted by this add-on.",
        options={"SKIP_SAVE"},
    )


def clear_transient_options() -> None:
    """Clear consent at lifecycle boundaries; this add-on never owns a camera."""
    try:
        manager = bpy.context.window_manager
        hands.stop_router(bpy.context)
        controllers.stop_router(bpy.context)
        editing.cancel_active(bpy.context)
        editing.clear_commit_payloads()
        settings = manager.xrwb_settings
        settings.passthrough_opt_in = False
        settings.overlay_enabled = False
        set_runtime_passthrough(getattr(manager, "xr_session_settings", None), False)
        controllers.teardown(bpy.context)
    except (AttributeError, ReferenceError):
        pass
    runtime.shutdown_runtime()


class XRWB_OT_refresh_status(bpy.types.Operator):
    bl_idname = "xrwb.refresh_status"
    bl_label = "Refresh Capability Status"

    def execute(self, context):
        from .diagnostics import collect_report

        report = collect_report(context)
        summary, detail = _summary_from_report(report)
        _set_status(context.window_manager.xrwb_settings, summary, detail)
        runtime.set_overlay_report(*_overlay_status_from_report(report))
        self.report({"INFO"}, summary)
        return {"FINISHED"}


class XRWB_OT_export_report(bpy.types.Operator, ExportHelper):
    bl_idname = "xrwb.export_report"
    bl_label = "Export Redacted Capability Report"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        from .diagnostics import collect_report, write_report

        try:
            path = write_report(collect_report(context), Path(self.filepath))
        except Exception as exc:
            self.report({"ERROR"}, f"Report export failed: {exc}")
            return {"CANCELLED"}
        _set_status(context.window_manager.xrwb_settings, "Report exported", str(path))
        self.report({"INFO"}, f"Report exported: {path}")
        return {"FINISHED"}


def configure_steamvr_session(context):
    """Use the runtime's floor origin, never a fabricated seated-head offset."""
    settings = context.window_manager.xrwb_settings
    first_setup = not settings.controller_fallback
    settings.controller_fallback = True
    settings.controller_status = "Controller profile will attach when the XR session starts"
    xr = context.window_manager.xr_session_settings
    xr.use_positional_tracking = True
    xr.use_absolute_tracking = True
    xr.base_pose_type = "CUSTOM"
    xr.base_pose_location = (0.0, 0.0, 0.0)
    xr.base_pose_angle = 0.0
    xr.base_scale = 1.0
    xr.view_scale = 1.0
    xr.show_floor = True
    xr.show_selection = True
    xr.show_controllers = True
    xr.controller_draw_style = "LIGHT_RAY"
    if first_setup:
        navigation = getattr(context.preferences.inputs, "xr_navigation", None)  # Blender 5.0+
        if navigation is not None:
            navigation.snap_turn = True
            navigation.turn_amount = pi / 6
            xr.fly_speed = settings.travel_speed
    set_runtime_passthrough(xr, False)


class XRWB_OT_start_session(bpy.types.Operator):
    bl_idname = "xrwb.start_session"
    bl_label = "Start OpenXR Session"

    @classmethod
    def poll(cls, _context):
        return bool(getattr(bpy.app.build_options, "xr_openxr", False))

    def execute(self, context):
        if runtime.session_exists(context):
            _set_status(
                context.window_manager.xrwb_settings, "OpenXR session already exists"
            )
            return {"FINISHED"}
        try:
            xr_settings = context.window_manager.xr_session_settings
            controllers.teardown(context)
            if os.environ.get("XRWB_STEAMVR") == "1":
                configure_steamvr_session(context)
            # Saved files can retain this native flag; a new session needs new consent.
            set_runtime_passthrough(xr_settings, False)
            context.window_manager.xrwb_settings.passthrough_opt_in = False
            xr_settings.show_custom_overlays = True
            with _view3d_override(context):
                result = bpy.ops.wm.xr_session_toggle("EXEC_DEFAULT")
        except Exception as exc:
            _set_status(
                context.window_manager.xrwb_settings, "OpenXR start failed", str(exc)
            )
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if "CANCELLED" in result:
            _set_status(
                context.window_manager.xrwb_settings,
                "OpenXR did not start",
                "Check the active runtime and headset.",
            )
            return {"CANCELLED"}
        _set_status(
            context.window_manager.xrwb_settings,
            "OpenXR session starting",
            "Desktop Blender remains available.",
        )
        return {"FINISHED"}


class XRWB_OT_stop_session(bpy.types.Operator):
    bl_idname = "xrwb.stop_session"
    bl_label = "Stop OpenXR Session"

    def execute(self, context):
        controllers.stop_router(context)
        hands.stop_router(context)
        editing.cancel_active(context)
        set_runtime_passthrough(context.window_manager.xr_session_settings, False)
        context.window_manager.xrwb_settings.passthrough_opt_in = False
        if not runtime.session_exists(context):
            controllers.teardown(context)
            _set_status(
                context.window_manager.xrwb_settings, "No OpenXR session to stop"
            )
            return {"FINISHED"}
        try:
            with _view3d_override(context):
                result = bpy.ops.wm.xr_session_toggle("EXEC_DEFAULT")
        except Exception as exc:
            _set_status(
                context.window_manager.xrwb_settings, "OpenXR stop failed", str(exc)
            )
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if "CANCELLED" in result:
            return {"CANCELLED"}
        controllers.teardown(context)
        _set_status(
            context.window_manager.xrwb_settings,
            "OpenXR session stopped",
            "Desktop Blender remains intact.",
        )
        return {"FINISHED"}


class XRWB_OT_request_passthrough(bpy.types.Operator):
    bl_idname = "xrwb.request_passthrough"
    bl_label = "Request Runtime Passthrough"
    bl_description = "Request the active runtime route; camera access is not verified"

    @classmethod
    def poll(cls, context):
        return runtime.session_running(context) and hasattr(
            context.window_manager.xr_session_settings, "show_passthrough"
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, _context):
        self.layout.label(
            text="Allow the active OpenXR runtime to request headset-camera passthrough?"
        )
        self.layout.label(
            text="XR Workbench will not read, record, or export camera frames."
        )
        self.layout.label(
            text="The result is requested/unverified until the report records live evidence."
        )

    def execute(self, context):
        settings = context.window_manager.xrwb_settings
        if not set_runtime_passthrough(
            context.window_manager.xr_session_settings, True
        ):
            _set_status(
                settings,
                "Passthrough request failed",
                "The runtime did not accept Blender's request setting.",
            )
            return {"CANCELLED"}
        settings.passthrough_opt_in = True
        _set_status(
            settings,
            "Passthrough requested",
            "Unverified: camera, calibration, freshness, composition.",
        )
        return {"FINISHED"}


class XRWB_OT_revoke_passthrough(bpy.types.Operator):
    bl_idname = "xrwb.revoke_passthrough"
    bl_label = "Revoke Runtime Passthrough"

    def execute(self, context):
        settings = context.window_manager.xrwb_settings
        set_runtime_passthrough(
            getattr(context.window_manager, "xr_session_settings", None), False
        )
        settings.passthrough_opt_in = False
        _set_status(
            settings, "Passthrough revoked", "No camera data is retained by M0."
        )
        return {"FINISHED"}


class XRWB_OT_toggle_overlay(bpy.types.Operator):
    bl_idname = "xrwb.toggle_overlay"
    bl_label = "Toggle Diagnostic Overlay"

    def execute(self, context):
        settings = context.window_manager.xrwb_settings
        settings.overlay_enabled = not settings.overlay_enabled
        runtime.configure_overlay(settings.overlay_enabled, settings.panel_opacity)
        _set_status(
            settings,
            "Diagnostic overlay enabled"
            if settings.overlay_enabled
            else "Diagnostic overlay removed",
        )
        return {"FINISHED"}


class XRWB_OT_place_overlay(bpy.types.Operator):
    bl_idname = "xrwb.place_overlay"
    bl_label = "Recall Diagnostic Overlay"

    @staticmethod
    def viewer_matrix(location, rotation):
        rotation = Quaternion(rotation).normalized()
        matrix = rotation.to_matrix().to_4x4() @ Matrix.Rotation(-pi / 2, 4, "X")
        matrix.translation = Vector(location) + rotation @ Vector((0.26, 0.0, -1.8))
        return matrix

    def execute(self, context):
        settings = context.window_manager.xrwb_settings
        matrix = Matrix.Translation((0.0, 0.0, 1.2))
        in_xr = runtime.session_running(context)
        if in_xr:
            state = context.window_manager.xr_session_state
            matrix = self.viewer_matrix(
                state.viewer_pose_location, state.viewer_pose_rotation
            )
        runtime.configure_overlay(
            settings.overlay_enabled,
            settings.panel_opacity,
            matrix,
        )
        _set_status(
            settings,
            "Overlay recalled in front of headset"
            if in_xr
            else "Diagnostic overlay placed at world origin",
        )
        return {"FINISHED"}


class XRWB_OT_editor_surface(bpy.types.Operator):
    bl_idname = "xrwb.editor_surface"
    bl_label = "Show / Recall Blender Editor"
    bl_description = "Place Blender's real window in XR; requires the native XR build"

    hide: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    full_window: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return runtime.session_running(context)

    def execute(self, context):
        settings = context.window_manager.xrwb_settings
        try:
            # Older verified builds remain launchable during the next source build.
            extra = {}
            if "separated" in bpy.ops.wm.xr_editor_surface.get_rna_type().properties:
                extra["separated"] = settings.editor_separated and not self.full_window
            result = bpy.ops.wm.xr_editor_surface(
                visible=not self.hide,
                recall=not self.hide,
                width=settings.editor_width,
                distance=settings.editor_distance,
                **extra,
            )
        except (AttributeError, RuntimeError) as exc:
            self.report({"WARNING"}, f"Native editor unavailable: {exc}")
            return {"CANCELLED"}
        return result


class XRWB_OT_cancel_edit(bpy.types.Operator):
    bl_idname = "xrwb.cancel_edit"
    bl_label = "Cancel Active Gesture"

    def execute(self, context):
        controllers.stop_router(context)
        hands.stop_router(context)
        editing.cancel_active(context)
        return {"FINISHED"}


class XRWB_OT_cancel_scene_edit(bpy.types.Operator):
    bl_idname = "xrwb.cancel_scene_edit"
    bl_label = "Cancel XR Scene Edit"

    @classmethod
    def poll(cls, context):
        return runtime.session_running(context) and editing.active_transaction() is not None

    def execute(self, context):
        controllers.stop_router(context)
        hands.cancel_scene_edit(context)
        editing.cancel_active(context)
        return {"FINISHED"}


class XRWB_OT_task_card(bpy.types.Operator):
    bl_idname = "xrwb.task_card"
    bl_label = "Show Native XR Controls"
    hide: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return runtime.session_running(context)

    def execute(self, context):
        try:
            simulated = (
                os.environ.get("XRWB_TASK_TEST_ALLOW") == "1"
                and os.environ.get("SIMULATED_ENABLE") == "1"
            )
            return bpy.ops.wm.xr_task_card(
                visible=not self.hide,
                recall=not self.hide,
                allow_simulated_input=simulated,
            )
        except (AttributeError, RuntimeError) as exc:
            self.report({"WARNING"}, f"Native task controls unavailable: {exc}")
            return {"CANCELLED"}


class XRWB_OT_set_edit_tool(bpy.types.Operator):
    bl_idname = "xrwb.set_edit_tool"
    bl_label = "Set XR Hand Tool"
    bl_description = "Choose an implemented object hand tool from native XR controls"

    tool: bpy.props.EnumProperty(
        items=ALL_TOOLS,
    )

    @classmethod
    def description(cls, _context, properties):
        return (_scene_tool_reason(_context, properties.tool)
                or next(item[2] for item in ALL_TOOLS if item[0] == properties.tool))

    @classmethod
    def poll(cls, context):
        reason = _scene_tool_reason(context, "select")
        if reason:
            cls.poll_message_set(reason)
            return False
        return hasattr(context.window_manager, "xrwb_settings")

    def execute(self, context):
        from . import interaction_log

        reason = _scene_tool_reason(context, self.tool)
        if reason:
            interaction_log.emit("rejected", reason, context, tool=self.tool, level="WARNING")
            self.report({"WARNING"}, reason)
            return {"CANCELLED"}
        context.window_manager.xrwb_settings.edit_tool = self.tool
        label = next(item[1] for item in ALL_TOOLS if item[0] == self.tool)
        interaction_log.emit("tool", f"{label} selected. Close the menu, then use a trigger.", context, tool=self.tool)
        return {"FINISHED"}


class XRWB_OT_bevel_object(bpy.types.Operator):
    bl_idname = "xrwb.bevel_object"
    bl_label = "Bevel Object"
    bl_description = "Add an editable native bevel after the selected object's existing modifiers"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        reason = _bevel_object_reason(context)
        if reason:
            cls.poll_message_set(reason)
        return not reason

    def execute(self, context):
        obj = context.active_object
        # Nonuniform object scale makes modifier width unequal in world space.
        # Do not silently apply the user's transforms just to add a bevel.
        reason = _bevel_object_reason(context)
        if reason:
            self.report({"WARNING"}, reason)
            return {"CANCELLED"}
        scales = tuple(obj.matrix_world.to_scale())
        settings = context.window_manager.xrwb_settings
        world_scale = (float(bpy.types.XrSessionState.hand_tracking_world_transform(context)[0])
                       if runtime.session_running(context) else 1.0)
        if not isfinite(world_scale) or world_scale <= 0:
            return {"CANCELLED"}
        modifier = obj.modifiers.new("XR Bevel", "BEVEL")
        modifier.width = settings.bevel_width_m * world_scale / scales[0]
        modifier.segments = settings.bevel_segments
        modifier.limit_method = "ANGLE"
        modifier.use_clamp_overlap = True
        try:
            if "FINISHED" in editing.push_undo(message="XR Bevel Object"):
                return {"FINISHED"}
        except RuntimeError:
            pass
        obj.modifiers.remove(modifier)
        self.report({"WARNING"}, "No valid undo context; bevel was not retained")
        return {"CANCELLED"}


class XRWB_OT_add_cube(bpy.types.Operator):
    bl_idname = "xrwb.add_cube"
    bl_label = "Add XR Cube"
    bl_description = "Add an undoable cube in front of the XR viewer"

    @classmethod
    def poll(cls, context):
        return (
            runtime.session_running(context)
            and context.mode == "OBJECT"
            and editing.undo_available(context)
            and editing.active_transaction() is None
            and bpy.ops.mesh.primitive_cube_add.poll()
            and bpy.ops.ed.undo_push.poll()
        )

    def execute(self, context):
        return _add_primitive(context, "CUBE")


PRIMITIVES = {
    "CUBE": ("Cube", "MESH_CUBE", "primitive_cube_add", {"size": 0.2}),
    "SPHERE": ("Sphere", "MESH_UVSPHERE", "primitive_uv_sphere_add", {"radius": 0.1}),
    "CYLINDER": ("Cylinder", "MESH_CYLINDER", "primitive_cylinder_add", {"radius": 0.1, "depth": 0.2}),
    "CONE": ("Cone", "MESH_CONE", "primitive_cone_add", {"radius1": 0.1, "depth": 0.2}),
    "PLANE": ("Plane", "MESH_PLANE", "primitive_plane_add", {"size": 0.2}),
    "TORUS": ("Torus", "MESH_TORUS", "primitive_torus_add", {"major_radius": 0.12, "minor_radius": 0.035}),
}


def _add_primitive(context, kind):
    if not editing.undo_available(context):
        return {"CANCELLED"}
    state = context.window_manager.xr_session_state
    scale = bpy.types.XrSessionState.hand_tracking_world_transform(context)[0]
    if not isfinite(scale) or scale <= 0:
        return {"CANCELLED"}
    location = state.viewer_pose_location + state.viewer_pose_rotation @ Vector(
        (0.0, 0.0, -0.7 * scale)
    )
    selected = tuple(context.selected_objects)
    active = context.view_layer.objects.active
    label, _icon, name, dimensions = PRIMITIVES[kind]
    if "FINISHED" not in getattr(bpy.ops.mesh, name)(
        "EXEC_DEFAULT", location=location,
        **{key: value * scale for key, value in dimensions.items()},
    ):
        return {"CANCELLED"}
    obj = context.view_layer.objects.active
    # The nested primitive call does not retain a post-add XR undo state.
    if "FINISHED" in editing.push_undo(message=f"XR Add {label}"):
        return {"FINISHED"}
    # Never leave an add that cannot be undone.
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for obj in selected:
        obj.select_set(True)
    context.view_layer.objects.active = active
    return {"CANCELLED"}


class XRWB_OT_add_primitive(bpy.types.Operator):
    bl_idname = "xrwb.add_primitive"
    bl_label = "Add Object in VR"
    bl_description = "Create a selected, undoable object within reach of the headset"
    kind: bpy.props.EnumProperty(items=tuple((key, item[0], "") for key, item in PRIMITIVES.items()))

    @classmethod
    def poll(cls, context):
        return XRWB_OT_add_cube.poll(context)

    def execute(self, context):
        return _add_primitive(context, self.kind)


class XRWB_OT_command_menu(bpy.types.Operator):
    bl_idname = "xrwb.command_menu"
    bl_label = "Toggle Hand Menu"
    bl_description = "Right A: toggle one menu on that same hand; the opposite trigger can click it"
    controller: bpy.props.IntProperty(default=1, min=0, max=1, options={"SKIP_SAVE"})
    page: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})

    def invoke(self, context, event):
        from . import interaction_log

        xr = getattr(event, "xr", None)
        if xr is not None and controllers._held_object_transaction(controllers._active_gesture) is not None:
            # A while holding stamps a copy instead of dropping what you carry.
            ok, message = controllers.stamp_copy(context)
            interaction_log.emit("stamp", message, context, hand=xr.user_path, level="INFO" if ok else "WARNING")
            return {"FINISHED"} if ok else {"CANCELLED"}
        if xr is not None:
            self.controller = 0 if controllers.menu_hand_for(xr.user_path) == controllers.LEFT_HAND else 1
        result = self.execute(context)
        if xr is not None:
            controllers.pulse(context, xr.user_path, "click" if "FINISHED" in result else "reject")
        return result

    @classmethod
    def poll(cls, context):
        return runtime.session_running(context) and context.window is not None

    def execute(self, context):
        visible = controllers.hand_menu_active(context)
        settings = context.window_manager.xrwb_settings
        if self.page:
            if self.page not in {item[0] for item in COMMAND_PAGES}:
                return {"CANCELLED"}
            settings.command_page = self.page
            visible = False  # Settings opens/replaces the one existing surface.
        controllers._cancel_active(context, "Hand menu toggled; previous capture cancelled")
        if not visible:
            from . import live_cage
            live_cage.restore_all(context, include_caged=True, timed=False)  # Menus show the real result.
        controllers._native_menu_open = False  # The command deck is a panel, not a list.
        with _view3d_override(context):
            ok, message = controllers._native_call(
                "xr_hand_menu", visible=not visible, panel="XRWB_PT_command_deck",
                width=settings.hand_menu_width, controller=self.controller,
            )
            controllers.start_router(context)
        if not ok:
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        return {"FINISHED"}


class XRWB_OT_command_page(bpy.types.Operator):
    bl_idname = "xrwb.command_page"
    bl_label = "Switch Hand Menu Page"
    page: bpy.props.EnumProperty(items=COMMAND_PAGES)

    @classmethod
    def description(cls, _context, properties):
        return next(item[2] for item in COMMAND_PAGES if item[0] == properties.page)

    def execute(self, context):
        context.window_manager.xrwb_settings.command_page = self.page
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


class XRWB_OT_travel_preset(bpy.types.Operator):
    bl_idname = "xrwb.travel_preset"
    bl_label = "Set VR Travel Speed"
    speed: bpy.props.FloatProperty(default=1.4, min=0.1, max=4.0)

    def execute(self, context):
        context.window_manager.xrwb_settings.travel_speed = self.speed
        return {"FINISHED"}


WORKFLOW_SETUP = {
    # page, tool, desktop editor, XR shading type, XR shading light
    "MODEL": ("EDIT", "move", "PROPERTIES", "SOLID", "STUDIO"),
    "SCULPT": ("SCULPT", "sculpt_clay", "PROPERTIES", "SOLID", "MATCAP"),
    "SHADE": ("PAINT", "spray_material", "ShaderNodeTree", "MATERIAL", "STUDIO"),
    "ANIMATE": ("ANIMATE", "scrub_time", "DOPESHEET", "SOLID", "STUDIO"),
    "STAGE": ("MENUS", "select", "PROPERTIES", "MATERIAL", "STUDIO"),
}


class XRWB_OT_feel_preset(bpy.types.Operator):
    bl_idname = "xrwb.feel_preset"
    bl_label = "Feel Preset"
    bl_description = "Set haptics, labels, wheel, snapping and precision together"
    preset: bpy.props.EnumProperty(
        items=[(key, label, description) for key, (label, description, _values) in polish.FEEL_PRESETS.items()],
        options={"SKIP_SAVE"})

    @classmethod
    def description(cls, _context, properties):
        return polish.FEEL_PRESETS[properties.preset][1]

    def execute(self, context):
        from . import interaction_log
        label = polish.apply_preset(self.preset)
        interaction_log.emit("settings", f"Feel: {label}. Fine-tune anything in Feel and Tools.", context)
        return {"FINISHED"}


class XRWB_OT_reset_controls(bpy.types.Operator):
    bl_idname = "xrwb.reset_controls"
    bl_label = "Reset Buttons"
    bl_description = "Default button actions, right-handed, default sensitivity"

    def execute(self, context):
        from . import interaction_log

        onboarding.reset_controls()
        interaction_log.emit("settings", "Buttons reset to defaults (sensitivity: next VR start).", context)
        return {"FINISHED"}


class XRWB_OT_workflow(bpy.types.Operator):
    bl_idname = "xrwb.workflow"
    bl_label = "Set Up Workflow"
    bl_description = "Open the matching page, tool, desktop editor, headset shading and remembered size"
    workflow: bpy.props.EnumProperty(items=viewer_scale.WORKFLOWS, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.window is not None and editing.active_transaction() is None

    def execute(self, context):
        from . import interaction_log

        settings = context.window_manager.xrwb_settings
        page, tool, editor, shading, light = WORKFLOW_SETUP[self.workflow]
        settings.workflow = self.workflow
        settings.command_page = page
        notes = []
        if not _scene_tool_reason(context, tool):
            settings.edit_tool = tool
        else:
            notes.append("tool unchanged")
        if "FINISHED" not in bpy.ops.xrwb.workspace_editor(editor=editor):
            notes.append("no side editor")
        xr_shading = context.window_manager.xr_session_settings.shading
        xr_shading.type = shading
        if shading == "SOLID":
            xr_shading.light = light
        remembered = viewer_scale.remembered_scale(self.workflow)
        if remembered is not None and runtime.session_running(context):
            try:
                viewer_scale.set_scale(context, remembered)
                notes.append(f"size x{remembered:g}")
            except (editing.EditRejected, ValueError) as exc:
                notes.append(str(exc))
        label = next(item[1] for item in viewer_scale.WORKFLOWS if item[0] == self.workflow)
        interaction_log.emit("workflow", f"{label} workflow ready" + (f" ({'; '.join(notes)})" if notes else "."),
                             context, tool=settings.edit_tool)
        return {"FINISHED"}


# ---- Folding Hand Menu ------------------------------------------------------
# Each hand-menu page is cut into named sections: a header button (click =
# fold), a summary chip when closed, and a pin. Open / pinned / compact state
# lives in vr_prefs.json (onboarding), so it survives restarts.

def _compact():
    return bool(onboarding.prefs()["menu_compact"])


def _help(layout, text):
    """A help line; Compact mode hides it."""
    if not _compact():
        layout.label(text=text)


def _section_create_shapes(layout, context, settings):
    grid = layout.grid_flow(columns=3, even_columns=True, align=True)
    for key, (label, icon, _operator, _dimensions) in PRIMITIVES.items():
        grid.operator("xrwb.add_primitive", text=label, icon=icon).kind = key
    layout.menu("VIEW3D_MT_add", text="All Blender Add Options", icon="ADD")


def _section_create_draw(layout, context, settings):
    _tool_buttons(layout, context, DRAW_TOOLS)
    _help(layout, "Close menu. Start at the controller tip; drag the opposite corner.")


def _section_create_booleans(layout, context, settings):
    layout.prop(settings, "cutter_shape", expand=True)
    _tool_buttons(layout, context, CUT_TOOLS)
    reason = _scene_tool_reason(context, "cut_difference")
    if reason:
        _label_lines(layout, reason, _CARD_TEXT_WIDTH)
    row = layout.row(align=True)
    row.label(text="Combine:")
    for operation, label in (("UNION", "Union"), ("DIFFERENCE", "Subtract"), ("INTERSECT", "Intersect")):
        row.operator("xrwb.boolean_selected", text=label).operation = operation
    row.operator("xrwb.apply_booleans", text="Apply")
    _help(layout, "Cutters stay editable: grab one to change the result. Target = active.")


def _section_edit_tools(layout, context, settings):
    from . import component_mode
    _tool_buttons(layout, context, EDIT_TOOLS)
    row = layout.row(align=True)
    mode = component_mode.current_mode(context)
    for key, label, icon in component_mode.MODES:
        row.operator("xrwb.component_mode", text=label, icon=icon, depress=mode == key).mode = key
    row.prop(settings, "multi_select", text="Multi", toggle=True)
    row = layout.row(align=True)
    row.operator("xrwb.history_undo", text="Undo")
    row.operator("xrwb.history_redo", text="Redo")
    row.operator("xrwb.controller_cancel", text="Cancel Edit")
    _help(layout, "Hold trigger / drag / release. Left B or Y cancels.")


def _section_edit_edges(layout, context, settings):
    _tool_buttons(layout, context, EDGE_TOOLS, columns=2)
    layout.row(align=True).operator("xrwb.bevel_object", text="Bevel Object")
    reason = _scene_tool_reason(context, "select") or _bevel_object_reason(context)
    if reason:
        _label_lines(layout, reason, _CARD_TEXT_WIDTH)
    row = layout.row(align=True)
    row.prop(settings, "bevel_width_m", text="Object Bevel")
    row.prop(settings, "bevel_segments", text="Segments")
    _help(layout, "Edge tools edit the base mesh; Object Bevel works after Boolean modifiers.")


def _section_edit_snap(layout, context, settings):
    layout.prop(settings, "edit_constraint", expand=True)
    row = layout.row(align=True)
    row.prop(settings, "translation_snap", text="Move Snap")
    row.prop(settings, "rotation_snap_degrees", text="Angle Snap")
    row.prop(settings, "scale_snap", text="Scale Snap")


def _origin_reason(context):
    reason = _scene_tool_reason(context, "select")
    if not reason and not context.selected_editable_objects:
        reason = "Select an editable object to set its origin."
    return reason


def _section_edit_origin(layout, context, settings):
    reason = _origin_reason(context)
    if reason:
        layout.label(text=wrap(reason, width=_CARD_TEXT_WIDTH, max_lines=1, placeholder="...")[0], icon="ERROR")
    origins = layout.column(align=True)
    origins.enabled = not reason
    row = origins.row(align=True)
    row.operator("object.origin_set", text="Origin to Geometry").type = "ORIGIN_GEOMETRY"
    row.operator("object.origin_set", text="Origin to 3D Cursor").type = "ORIGIN_CURSOR"
    origins.operator("view3d.snap_cursor_to_selected", text="3D Cursor to Selection")


def _section_edit_object(layout, context, settings):
    from . import live_cage
    obj = context.active_object
    row = layout.row(align=True)
    row.prop(settings, "live_cage", text="Live Cage", toggle=True)
    current = live_cage.mode_of(obj) if obj is not None else ""
    for mode in live_cage.MODES:
        cell = row.row(align=True)
        cell.enabled = obj is not None
        cell.operator("xrwb.live_cage_mode", text=mode.title(), depress=current == mode).mode = mode
    _help(layout, "Live Cage: heavy modifiers pause while you hold an edit of this object.")


def _section_sculpt_tools(layout, context, settings):
    _tool_buttons(layout, context, SCULPT_TOOLS, columns=3)
    row = layout.row(align=True)
    row.prop(settings, "sculpt_invert", text="Dig (carve)", toggle=True)
    row.prop(settings, "use_pressure", text="Trigger Pressure", toggle=True)
    _help(layout, "Off-hand trigger in the air: Brush Dial (up/down size, roll strength).")


def _section_sculpt_brush(layout, context, settings):
    row = layout.row(align=True)
    row.prop(settings, "sculpt_radius_m", text="Brush")
    row.prop(settings, "sculpt_strength", text="Strength")
    layout.prop(settings, "sculpt_layer_height_m", text="Layer Height")
    _help(layout, "Brush, spray and detail sizes are physical: shrink for finer work.")
    _size_controls(layout, context)


def _section_sculpt_symmetry(layout, context, settings):
    row = layout.row(align=True)
    row.prop(settings, "sculpt_mirror_x", text="Mirror X", toggle=True)
    row.prop(settings, "sculpt_radial", text="Radial")


def _section_sculpt_mask(layout, context, settings):
    row = layout.row(align=True)
    for action, label in (("invert", "Invert"), ("clear", "Clear"), ("grow", "Grow"), ("shrink", "Shrink")):
        row.operator(f"xrwb.mask_{action}", text=label)
    row = layout.row(align=True)
    row.operator("xrwb.mask_extract", text="Extract", icon="MOD_SOLIDIFY")
    row.prop(settings, "mask_extract_m", text="Thickness")


def _section_sculpt_detail(layout, context, settings):
    layout.prop(settings, "sculpt_detail", expand=True)
    row = layout.row(align=True)
    row.operator("xrwb.add_clay", text="Clay Ball", icon="MESH_ICOSPHERE")
    row.operator("xrwb.remesh_detail", text="Remesh for Detail")
    row.prop(settings, "remesh_detail_m", text="Detail")
    layout.operator("xrwb.sculpt_detail", text="Refine Stretched Now")
    _help(layout, "Native Sculpt Mode brushes stay on the desktop panel (Menus).")


def _section_sculpt_lab(layout, context, settings):
    from . import subdiv_lab
    _tool_buttons(layout, context, LAB_TOOLS[:1], columns=1)
    grid = layout.grid_flow(columns=3, even_columns=True, align=True)
    for kind, (label, _help_text, _name) in subdiv_lab.SCHEMES.items():
        grid.operator("xrwb.lab_add", text=label).kind = kind
    grid.operator("xrwb.lab_bake", text="Bake", icon="CHECKMARK")


def _section_sculpt_growth(layout, context, settings):
    from . import growth
    _tool_buttons(layout, context, LAB_TOOLS[1:2], columns=1)
    row = layout.row(align=True)
    for kind, label in growth.KINDS.items():
        row.operator("xrwb.grow_kind", text=label, depress=growth.STATE["kind"] == kind).kind = kind
    layout.operator("xrwb.path_array", text="Array Along Path", icon="MOD_ARRAY")


def _section_sculpt_retopo(layout, context, settings):
    _tool_buttons(layout, context, LAB_TOOLS[2:], columns=1)
    row = layout.row(align=True)
    row.prop(settings, "retopo_faces", text="Target Faces")
    row.operator("xrwb.retopo_analyze", text="Analyze", icon="VIEWZOOM")
    layout.prop(settings, "retopo_quality", expand=True)
    _help(layout, "CurioMesh makes a new quad object beside the source; one undo step.")


def _section_paint_materials(layout, context, settings):
    from . import materials_lab, node_garden
    row = layout.row(align=True)
    row.operator("xrwb.material_shelf", text="Shelf Away" if materials_lab.shelf_collection(context) else "Shelf",
                 icon="MATERIAL")
    row.operator("xrwb.show_material_logic", text="Logic", icon="NODETREE")
    row.operator("xrwb.node_garden", text="Garden Away" if node_garden.is_out(context) else "Node Garden",
                 icon="NODE").action = "TOGGLE"
    row.operator("xrwb.save_material_preset", text="Save Preset", icon="FILE_TICK")
    grid = layout.grid_flow(columns=4, even_columns=True, align=True)
    for key, (label, _inputs, _recipe) in materials_lab.PRESETS.items():
        if key not in materials_lab.UTILITY_PRESETS:
            grid.operator("xrwb.pick_material", text=label).identifier = key
    mine = materials_lab.user_presets()
    if mine:
        grid = layout.grid_flow(columns=4, even_columns=True, align=True)
        for name in mine[:4]:
            grid.operator("xrwb.pick_material", text=name, icon="USER").identifier = f"USER:{name}"
    else:
        _help(layout, "Node Garden: the real node tree as blocks. Save Preset: for every scene.")


def _section_paint_tools(layout, context, settings):
    _tool_buttons(layout, context, PAINT_TOOLS, columns=1)
    row = layout.row(align=True)
    row.label(text=f"Loaded: {settings.spray_material or 'none'}", icon="BRUSH_DATA")
    row.prop(settings, "spray_radius_m", text="Spray")
    row.operator("xrwb.apply_material", text="Apply to Selected")
    layout.prop(context.window_manager.xr_session_settings.shading, "type", expand=True)
    _help(layout, "Trigger a block to load it; hold trigger on a mesh to spray.")


def _section_animate_keys(layout, context, settings):
    tool_settings = context.scene.tool_settings
    row = layout.row(align=True)
    row.operator("xrwb.anim_key", text="Key Selected", icon="KEY_HLT")
    row.operator("xrwb.anim_key", text="Delete Key", icon="KEY_DEHLT").delete = True
    row.prop(tool_settings, "use_keyframe_insert_auto", text="Record", toggle=True)
    row = layout.row(align=True)
    for label, jump, delta in (("|<", "START", 0), ("< Key", "PREV_KEY", 0), ("-1", "STEP", -1),
                               ("+1", "STEP", 1), ("Key >", "NEXT_KEY", 0), (">|", "END", 0)):
        op = row.operator("xrwb.anim_frame", text=label)
        op.jump, op.delta = jump, delta
    row = layout.row(align=True)
    row.operator("xrwb.anim_play", text="Play / Pause", icon="PLAY")
    row.prop(context.scene, "frame_current", text="Frame")


def _section_animate_tools(layout, context, settings):
    _tool_buttons(layout, context, ANIMATE_TOOLS, columns=1)
    row = layout.row(align=True)
    row.prop(settings, "scrub_frames_per_m", text="Frames / m")
    row.prop(context.scene, "frame_start", text="Start")
    row.prop(context.scene, "frame_end", text="End")
    row = layout.row(align=True)
    for editor, label in (("DOPESHEET", "Dope Sheet"), ("FCURVES", "Graph"), ("TIMELINE", "Timeline")):
        row.operator("xrwb.workspace_editor", text=label).editor = editor
    _help(layout, "Record keys every grip move. Scrub: hold trigger, move sideways.")


def _active_label(tools, settings):
    return next((label for key, label, _d in tools if key == settings.edit_tool), "")


def _brush_summary(context, settings):
    parts = [f"Brush {polish.length(settings.sculpt_radius_m)}", f"{round(settings.sculpt_strength * 100)} %"]
    if settings.sculpt_mirror_x:
        parts.append("Mirror X")
    if settings.sculpt_radial:
        parts.append(f"Radial {settings.sculpt_radial}")
    return " · ".join(parts)


def _growth_summary(context, settings):
    from . import growth
    return f"Plants a {growth.KINDS[growth.STATE['kind']]}"


# key: (title, icon, body, summary(context, settings) -> str). A page's sections are drawn in this order.
SECTIONS = {
    "CREATE.shapes": ("Shapes", "MESH_CUBE", _section_create_shapes, lambda c, s: "Cube, Sphere, ..."),
    "CREATE.draw": ("Draw a Volume", "MOD_BUILD", _section_create_draw,
                    lambda c, s: _active_label(DRAW_TOOLS, s) or "Hold / size / release"),
    "CREATE.booleans": ("Booleans", "MOD_BOOLEAN", _section_create_booleans,
                        lambda c, s: _scene_tool_reason(c, "cut_difference") or _active_label(CUT_TOOLS, s)),
    "EDIT.tools": ("Tools", "TOOL_SETTINGS", _section_edit_tools, lambda c, s: _active_label(EDIT_TOOLS, s)),
    "EDIT.edges": ("Edges and Bevel", "MOD_BEVEL", _section_edit_edges,
                   lambda c, s: _active_label(EDGE_TOOLS, s) or f"Bevel {polish.length(s.bevel_width_m)}"),
    "EDIT.snap": ("Snap", "SNAP_ON", _section_edit_snap,
                  lambda c, s: f"{s.edit_constraint} · move {s.translation_snap:g} · {s.rotation_snap_degrees:g}°"),
    "EDIT.origin": ("Object Origin", "OBJECT_ORIGIN", _section_edit_origin, lambda c, s: _origin_reason(c)),
    "EDIT.object": ("Object", "OBJECT_DATA", _section_edit_object,
                    lambda c, s: "Live Cage " + ("on" if s.live_cage else "off")),
    "SCULPT.tools": ("Tools", "SCULPTMODE_HLT", _section_sculpt_tools,
                     lambda c, s: _scene_tool_reason(c, "sculpt_clay") or _active_label(SCULPT_TOOLS, s)),
    "SCULPT.brush": ("Brush", "BRUSH_DATA", _section_sculpt_brush, _brush_summary),
    "SCULPT.symmetry": ("Symmetry", "MOD_MIRROR", _section_sculpt_symmetry, _brush_summary),
    "SCULPT.mask": ("Mask", "MOD_MASK", _section_sculpt_mask, lambda c, s: "Invert · Grow · Extract"),
    "SCULPT.detail": ("Detail", "MOD_REMESH", _section_sculpt_detail,
                      lambda c, s: f"Stretched: {s.sculpt_detail.title()}"),
    "SCULPT.lab": ("Subdivision Lab", "MOD_SUBSURF", _section_sculpt_lab, lambda c, s: "Twist, Hard Surface, ..."),
    "SCULPT.growth": ("Growth", "OUTLINER_OB_CURVE", _section_sculpt_growth, _growth_summary),
    "SCULPT.retopo": ("Retopo", "MESH_GRID", _section_sculpt_retopo,
                      lambda c, s: f"{retopo.quads_label(s.retopo_faces)} · {s.retopo_quality}"),
    "PAINT.materials": ("Materials", "MATERIAL", _section_paint_materials, lambda c, s: "Shelf, presets, nodes"),
    "PAINT.tools": ("Spray and Paint", "BRUSH_DATA", _section_paint_tools,
                    lambda c, s: f"Loaded: {s.spray_material or 'none'}"),
    "ANIMATE.keys": ("Keys and Playback", "KEY_HLT", _section_animate_keys,
                     lambda c, s: f"Frame {c.scene.frame_current}"),
    "ANIMATE.tools": ("Scrub and Editors", "TIME", _section_animate_tools,
                      lambda c, s: _active_label(ANIMATE_TOOLS, s)),
}
SECTIONED_PAGES = ("CREATE", "EDIT", "SCULPT", "PAINT", "ANIMATE")
# Each page opens its first section until the user folds something.
DEFAULT_OPEN = tuple(next(key for key in SECTIONS if key.startswith(page + ".")) for page in SECTIONED_PAGES)


def open_sections():
    text = onboarding.prefs()["menu_open"]
    return list(DEFAULT_OPEN) if text == "" else polish.parse_keys(text, SECTIONS)


def pinned_sections():
    return polish.parse_keys(onboarding.prefs()["menu_pinned"], SECTIONS)


def section(layout, context, key):
    """Header row (fold button + pin); returns the body column when open, else None."""
    title, _icon, body, summary = SECTIONS[key]
    is_open = key in open_sections()
    settings = context.window_manager.xrwb_settings
    row = layout.row(align=True)
    text = title
    if not is_open:
        try:
            chip = summary(context, settings)
        except (AttributeError, KeyError, ReferenceError, TypeError, ValueError):
            chip = ""
        text = f"{title}:  {chip}" if chip else title
    op = row.operator("xrwb.menu_section", text=text, emboss=False,
                      icon="DISCLOSURE_TRI_DOWN" if is_open else "DISCLOSURE_TRI_RIGHT")
    op.key, op.action = key, "TOGGLE"
    op = row.operator("xrwb.menu_section", text="", emboss=False,
                      icon="PINNED" if key in pinned_sections() else "UNPINNED")
    op.key, op.action = key, "PIN"
    if not is_open:
        return None
    column = layout.column(align=True)
    body(column, context, settings)
    return column


class XRWB_OT_menu_section(bpy.types.Operator):
    bl_idname = "xrwb.menu_section"
    bl_label = "Hand Menu Section"
    bl_description = "Fold or unfold this section, or pin it to the top of every page (2 at most)"
    key: bpy.props.StringProperty(options={"SKIP_SAVE"})
    action: bpy.props.EnumProperty(items=(("TOGGLE", "Toggle", ""), ("PIN", "Pin", "")), options={"SKIP_SAVE"})

    def execute(self, context):
        from . import interaction_log
        if self.key not in SECTIONS:
            return {"CANCELLED"}
        title = SECTIONS[self.key][0]
        if self.action == "PIN":
            text, pinned, evicted = polish.pin_key(onboarding.prefs()["menu_pinned"], self.key, SECTIONS)
            onboarding.set_pref("menu_pinned", text)
            message = f"{title} pinned" if pinned else f"{title} unpinned"
            if evicted:
                message += f"; {SECTIONS[evicted][0]} unpinned (2 at most)"
            interaction_log.emit("menu", message, context)
            kind = "click"
        else:
            text, opened = polish.toggle_key(",".join(open_sections()), self.key, SECTIONS)
            onboarding.set_pref("menu_open", text or ",")  # "," = all folded, unlike "" = never touched.
            kind = "detent" if opened else "tick"
        controllers.pulse(context, controllers._pointer_hand, kind)
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


class XRWB_OT_live_cage_mode(bpy.types.Operator):
    bl_idname = "xrwb.live_cage_mode"
    bl_label = "Live Cage Mode"
    bl_description = "Auto: cage only slow objects. On: always. Off: never. One undo step"
    mode: bpy.props.EnumProperty(items=[(m, m.title(), "") for m in ("AUTO", "ON", "OFF")], options={"SKIP_SAVE"})

    def execute(self, context):
        from . import interaction_log, live_cage
        obj = context.active_object
        if obj is None or obj.library or not bpy.ops.ed.undo_push.poll():
            self.report({"WARNING"}, "Select an editable object (and keep Global Undo on)")
            return {"CANCELLED"}
        live_cage.set_mode(obj, self.mode)
        editing.push_undo(f"XR Live Cage {self.mode.title()}")
        interaction_log.emit("live_cage", f"Live Cage {self.mode.title()} for {obj.name}", context)
        return {"FINISHED"}


class XRWB_OT_retopo_analyze(bpy.types.Operator):
    bl_idname = "xrwb.retopo_analyze"
    bl_label = "Analyze for Retopo"
    bl_description = "CurioMesh reads the active mesh and says how it would retopologise it; nothing changes"

    def execute(self, context):
        from . import interaction_log
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"WARNING"}, "Select a mesh to analyze")
            return {"CANCELLED"}
        info = retopo.analyze(context, obj)
        if info.get("ok"):
            message = f"{obj.name}: {info['faces']:,} faces · {info['profile']} · {info['strategy']}"
            if info.get("warning"):
                message += f" · {info['warning']}"
        else:
            message = f"Analyze failed: {info.get('reason', 'no reason given')}"
        interaction_log.emit("retopo", message, context, level="INFO" if info.get("ok") else "WARNING")
        return {"FINISHED"} if info.get("ok") else {"CANCELLED"}


class XRWB_PT_command_deck(bpy.types.Panel):
    bl_label = "VR / BLENDER COMMANDS"
    bl_idname = "XRWB_PT_command_deck"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 24

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.xrwb_settings
        compact = _compact()
        layout.scale_y = 1.0 if compact else 1.25
        row = layout.row(align=True)
        row.label(text="HAND MENU", icon="ORIENTATION_GIMBAL")
        row.prop(settings, "menu_compact", text="Full" if compact else "Compact", toggle=True)
        row.operator("xrwb.command_menu", text="Close", icon="X")
        state = context.window_manager.xr_session_state
        desktop_visible = bool(state and state.editor_surface_info(context)[0])
        op = row.operator("xrwb.editor_surface", text="Close Desktop" if desktop_visible else "Open Desktop",
                          icon="WINDOW")
        op.hide = desktop_visible
        _help(layout, "Point + trigger, or stick up/down + click.  B: back / close.")
        active_label = next(label for identifier, label, _description in ALL_TOOLS if identifier == settings.edit_tool)
        layout.label(text=f"Active tool: {active_label}", icon="TOOL_SETTINGS")
        tabs = layout.grid_flow(columns=10 if compact else 5, even_columns=True, align=True)
        for identifier, label, _description in COMMAND_PAGES:
            tabs.operator("xrwb.command_page", text="" if compact else label, icon=PAGE_ICONS[identifier],
                          depress=settings.command_page == identifier).page = identifier
        layout.separator()
        if not editing.undo_available(context):
            # Always above the sections, never folded away.
            layout.label(text="Editing needs Global Undo and at least one undo step.", icon="ERROR")
            row = layout.row(align=True)
            row.prop(context.preferences.edit, "use_global_undo")
            row.prop(context.preferences.edit, "undo_steps")
        page = settings.command_page
        pinned = pinned_sections() if page != "GUIDE" else []
        for key in pinned:  # Pinned sections sit under the tabs on every page.
            section(layout, context, key)
        if pinned:
            layout.separator()
        if page in SECTIONED_PAGES:
            for key in SECTIONS:
                if key.startswith(page + ".") and key not in pinned:
                    section(layout, context, key)
        elif page == "MENUS":
            row = layout.row(align=True)
            row.operator("xrwb.open_hand_menu", text="Studio: every part of Blender  ›",
                         icon="PRESET").menu = "XRWB_MT_studio"
            row.operator("xrwb.open_hand_menu", text="Blender Menus  ›", icon="BLENDER").menu = "XRWB_MT_blender"
            grid = layout.grid_flow(columns=4, even_columns=True, align=True)
            from . import studio
            for menu, label, icon in studio.STUDIO_ENTRIES[:8]:
                grid.operator("xrwb.open_hand_menu", text=label, icon=icon).menu = menu
            grid = layout.grid_flow(columns=3, even_columns=True, align=True)
            for identifier, label in (("TOPBAR_MT_file", "File"), ("TOPBAR_MT_edit", "Edit"),
                                      ("TOPBAR_MT_render", "Render"), ("TOPBAR_MT_window", "Window"),
                                      ("TOPBAR_MT_help", "Help"), ("VIEW3D_MT_view", "View")):
                grid.menu(identifier, text=label)
            mode = context.mode.lower()
            for identifier, label in ((f"VIEW3D_MT_select_{mode}", "Select"),
                                      (f"VIEW3D_MT_{mode}", "Mode Tools"),
                                      ("VIEW3D_MT_add", "Add")):
                if hasattr(bpy.types, identifier):
                    grid.menu(identifier, text=label)
            layout.label(text="Submenus and installed add-on entries open here on your hand.")
            layout.label(text="Other editor menus remain on their floating windows.")
        elif page == "WORKSPACE":
            row = layout.row(align=True)
            for key, label, _description in viewer_scale.WORKFLOWS:
                row.operator("xrwb.workflow", text=label, depress=settings.workflow == key).workflow = key
            grid = layout.grid_flow(columns=4, even_columns=True, align=True)
            for item in bpy.ops.xrwb.workspace_editor.get_rna_type().properties["editor"].enum_items:
                grid.operator("xrwb.workspace_editor", text=item.name).editor = item.identifier
            row = layout.row(align=True)
            row.operator("xrwb.preferences", text="Add-ons").section = "ADDONS"
            row.operator("xrwb.preferences", text="Preferences").section = "INTERFACE"
            row.operator("xrwb.editor_surface", text="Separate Panels")
            row = layout.row(align=True)
            row.operator("xrwb.editor_surface", text="Full Desktop").full_window = True
            row.operator("xrwb.editor_surface", text="Close Desktop", icon="X").hide = True
            layout.prop(settings, "editor_width", text="Panel Width")
            layout.prop(settings, "editor_distance", text="Panel Distance")
            layout.label(text="Move panels: squeeze a grip while pointing at one.")
            layout.label(text="All panels move as one rig; left stick click shows/closes them.")
        elif page == "CONTROLS":
            layout.prop(settings, "settings_section", expand=True)
            group = settings.settings_section
            if group == "FEEL":
                from . import polish
                row = layout.row(align=True)
                current = polish.matching_preset()
                row.label(text="Feel:")
                for key, (label, _description, _values) in polish.FEEL_PRESETS.items():
                    row.operator("xrwb.feel_preset", text=label, depress=current == key).preset = key
                row = layout.row(align=True)
                row.prop(settings, "toasts", text="Toasts", toggle=True)
                row.prop(settings, "readouts", text="Readouts", toggle=True)
                row.prop(settings, "hover_outline", text="Outline", toggle=True)
                row.prop(settings, "detents", text="Detents", toggle=True)
                layout.prop(settings, "theme", text="Theme")
                row = layout.row(align=True)
                row.prop(settings, "haptic_strength", text="Haptics", slider=True)
                row.prop(settings, "ray_markers", text="Ray Rings", toggle=True)
                row.prop(settings, "button_hints", text="Button Labels", toggle=True)
                row = layout.row(align=True)
                row.prop(settings, "hand_menu_width", text="Menu Width")
                row.prop(settings, "scale_drag_distance_m", text="Scale Drag (2x)")
                layout.label(text="Scale: drag sideways. Smaller distance = faster resizing.")
                layout.prop(settings, "selection_click_slop_m", text="Selection Click Tolerance")
                row = layout.row(align=True)
                row.prop(settings, "use_pressure", text="Trigger Pressure", toggle=True)
                row.prop(settings, "multi_select", text="Multi-Select", toggle=True)
                layout.label(text="Menu size applies when the menu reopens.")
            elif group == "BUTTONS":
                layout.prop(settings, "controller_profile")
                row = layout.row(align=True)
                row.prop(settings, "left_handed", text="Left-Handed", toggle=True)
                row.prop(settings, "menu_hand", text="Menus")
                for slot, label, _description in controllers.BUTTON_SLOTS:
                    row = layout.row(align=True)
                    row.label(text=label)
                    row.prop(settings, f"slot_{slot}", text="")
                row = layout.row(align=True)
                row.prop(settings, "trigger_threshold", text="Trigger", slider=True)
                row.prop(settings, "grip_threshold", text="Grip", slider=True)
                row.prop(settings, "stick_threshold", text="Stick", slider=True)
                row = layout.row(align=True)
                row.operator("xrwb.reset_controls", text="Reset Buttons", icon="LOOP_BACK")
                row.label(text="Profile, hand, sensitivity: next VR start.")
            elif group == "TOOLS":
                row = layout.row(align=True)
                row.prop(settings, "wheel_set", text="Wheel")
                row.prop(settings, "wheel_size_m", text="Size")
                row = layout.row(align=True)
                row.prop(settings, "snap_grid_cm", text="Snap Grid cm")
                row.prop(settings, "snap_angle", text="Snap Angle")
                row = layout.row(align=True)
                row.prop(settings, "precision", text="Precision", slider=True)
                row.prop(settings, "label_scale", text="Label Size", slider=True)
                row = layout.row(align=True)
                row.prop(settings, "two_hand", text="Two Hands")
                row.prop(settings, "hover_ticks", text="Hover Ticks", toggle=True)
                row.prop(settings, "uv_island_angle", text="UV Crease")
                row = layout.row(align=True)
                row.prop(settings, "sculpt_mirror_x", text="Sculpt Mirror X", toggle=True)
                row.prop(settings, "use_pressure", text="Trigger Pressure", toggle=True)
                row = layout.row(align=True)
                row.prop(settings, "tool_props", text="Tool Props", toggle=True)
                row.prop(settings, "tool_belt", text="Tool Belt", toggle=True)
                row.prop(settings, "live_cage", text="Live Cage", toggle=True)
                layout.label(text="Wheel: stick click, flick toward a tool, click or trigger picks.")
            else:
                row = layout.row(align=True)
                row.prop(settings, "menu_at_start", text="Menu at Start", toggle=True)
                row.prop(settings, "desktop_at_start", text="Desktop at Start", toggle=True)
                row.operator("xrwb.guide", text="Replay Guide", icon="HELP").action = "OPEN"
                row = layout.row(align=True)
                row.prop(settings, "locomotion", expand=True)
                row.prop(settings, "fly", text="Fly", toggle=True)
                row = layout.row(align=True)
                row.label(text="Empty-space grip:")
                row.prop(settings, "grip_empty", text="")
                layout.prop(settings, "travel_speed", text="Maximum Travel Speed")
                navigation = getattr(context.preferences.inputs, "xr_navigation", None)  # Blender 5.0+
                if navigation is not None:
                    row = layout.row(align=True)
                    row.prop(navigation, "snap_turn", text="Snap Turn", toggle=True)
                    row.prop(navigation, "turn_amount", text="Turn Angle")
                    layout.prop(navigation, "vignette_intensity", text="Motion Vignette")
        elif page == "GUIDE":
            steps = onboarding.guide_steps(settings.controller_profile)
            step = min(settings.guide_step, len(steps) - 1)
            title, lines, attempt = steps[step]
            layout.label(text=f"GET SITUATED  {step + 1} / {len(steps)}:  {title.upper()}", icon="HELP")
            for line in lines:
                layout.label(text=line)
            layout.label(text="Try it: " + attempt, icon="HAND")
            row = layout.row(align=True)
            back = row.row(align=True)
            back.enabled = step > 0
            back.operator("xrwb.guide", text="Back").action = "BACK"
            if step < len(steps) - 1:
                row.operator("xrwb.guide", text="Next").action = "NEXT"
            else:
                row.operator("xrwb.guide", text="Finish", icon="CHECKMARK").action = "FINISH"
            row.operator("xrwb.guide", text="Skip Guide").action = "SKIP"
        else:
            row = layout.row(align=True)
            for label, speed in (("Precision", 0.35), ("Walk", 1.4), ("Travel", 3.0)):
                row.operator("xrwb.travel_preset", text=label).speed = speed
            layout.prop(settings, "travel_speed")
            navigation = getattr(context.preferences.inputs, "xr_navigation", None)  # Blender 5.0+
            if navigation is not None:
                layout.prop(navigation, "snap_turn", text="Snap Turn")
                layout.prop(navigation, "turn_amount", text="Turn Angle")
                layout.prop(navigation, "vignette_intensity", text="Motion Vignette")
            _size_controls(layout, context)
            row = layout.row(align=True)
            row.operator("xrwb.travel", text="Go to Selection", icon="VIEW_ZOOM").action = "GO_TO"
            row.operator("xrwb.travel", text="Reset View", icon="HOME").action = "RESET"
            layout.label(text="Grip empty space: move the world. Both grips: turn it and change size.")
            layout.label(text="Stick: walk and turn (Settings: Smooth / Teleport, Fly).")
        layout.separator()
        _action_feedback(layout)


class XRWB_OT_history_undo(bpy.types.Operator):
    bl_idname = "xrwb.history_undo"
    bl_label = "XR Undo"

    @classmethod
    def poll(cls, context):
        return (
            runtime.session_running(context)
            and editing.active_transaction() is None
            and bpy.ops.ed.undo.poll()
        )

    def execute(self, context):
        if editing.active_transaction() is not None:
            return {"CANCELLED"}
        return bpy.ops.ed.undo("EXEC_DEFAULT")


class XRWB_OT_history_redo(bpy.types.Operator):
    bl_idname = "xrwb.history_redo"
    bl_label = "XR Redo"

    @classmethod
    def poll(cls, context):
        return (
            runtime.session_running(context)
            and editing.active_transaction() is None
            and bpy.ops.ed.redo.poll()
        )

    def execute(self, context):
        if editing.active_transaction() is not None:
            return {"CANCELLED"}
        return bpy.ops.ed.redo("EXEC_DEFAULT")


class XRWB_OT_workspace_editor(bpy.types.Operator):
    bl_idname = "xrwb.workspace_editor"
    bl_label = "Open Native Blender Editor"
    bl_description = "Use Blender's actual editor, including installed add-on panels"

    editor: bpy.props.EnumProperty(items=(
        ("PROPERTIES", "Properties", ""),
        ("OUTLINER", "Outliner", ""),
        ("ASSETS", "Assets", ""),
        ("GeometryNodeTree", "Geometry Nodes", ""),
        ("ShaderNodeTree", "Shader Nodes", ""),
        ("CompositorNodeTree", "Compositor", ""),
        ("TextureNodeTree", "Texture Nodes", ""),
        ("UV", "UV Editor", ""),
        ("IMAGE_EDITOR", "Images", ""),
        ("DOPESHEET", "Dope Sheet", ""),
        ("TIMELINE", "Timeline", ""),
        ("FCURVES", "Graph Editor", ""),
        ("NLA_EDITOR", "NLA", ""),
        ("DRIVERS", "Drivers", ""),
        ("SEQUENCE_EDITOR", "Video Editing", ""),
        ("SPREADSHEET", "Spreadsheet", ""),
        ("TEXT_EDITOR", "Text Editor", ""),
        ("CONSOLE", "Python Console", ""),
        ("INFO", "Info Log", ""),
        ("FILES", "File Browser", ""),
    ))
    property_context: bpy.props.StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.window is not None and editing.active_transaction() is None

    def execute(self, context):
        state = getattr(context.window_manager, "xr_session_state", None)
        if state and hasattr(state, "editor_surface_info"):
            if state.editor_surface_info(context)[4]:
                self.report({"WARNING"}, "Release the current panel control first")
                return {"CANCELLED"}
        # Reuse a native side editor. Never replace the modeling viewport or split
        # a user's layout automatically; its editor selector retains every option.
        areas = [a for a in context.window.screen.areas
                 if a.type not in {"VIEW_3D", "TOPBAR", "STATUSBAR"}]
        if not areas:
            self.report({"WARNING"}, "This workspace needs a side editor")
            return {"CANCELLED"}
        area = max(areas, key=lambda item: item.width * item.height)
        try:
            area.ui_type = self.editor
            if self.editor == "PROPERTIES" and self.property_context:
                area.spaces.active.context = self.property_context
        except (TypeError, ValueError, RuntimeError):
            self.report({"WARNING"}, "That editor context is unavailable for this selection")
            return {"CANCELLED"}
        area.tag_redraw()
        return {"FINISHED"}


class XRWB_OT_preferences(bpy.types.Operator):
    bl_idname = "xrwb.preferences"
    bl_label = "Open Blender Preferences"
    section: bpy.props.EnumProperty(items=(
        ("ADDONS", "Add-ons", ""), ("INTERFACE", "All Preferences", ""),
    ))

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        context.preferences.active_section = self.section
        return bpy.ops.screen.userpref_show("INVOKE_DEFAULT")


class XRWB_PT_workspace(bpy.types.Panel):
    bl_label = "VR Workspace"
    bl_idname = "XRWB_PT_workspace"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XR Workbench"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        # Hide only our redundant desktop tab during immersion. Native and other
        # add-on sidebars, region visibility and the user's layout stay untouched.
        return not runtime.session_running(context)

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.xrwb_settings
        layout.scale_y = 1.35
        row = layout.row(align=True)
        row.operator("xrwb.start_session", text="Enter XR", icon="PLAY")
        row.operator("xrwb.stop_session", text="Exit XR", icon="PAUSE")
        box = layout.box()
        box.label(text="PANELS", icon="WINDOW")
        row = box.row(align=True)
        row.operator("xrwb.editor_surface", text="Separate Panels")
        row.operator("xrwb.editor_surface", text="Full Window").full_window = True
        row = box.row(align=True)
        row.prop(settings, "editor_width", text="Size")
        row.prop(settings, "editor_distance", text="Distance")
        box.prop(context.preferences.view, "ui_scale", text="Native UI Size")
        box.operator("xrwb.editor_surface", text="Hide Panels").hide = True
        box = layout.box()
        box.label(text="EDITORS", icon="WORKSPACE")
        grid = box.grid_flow(columns=2, even_columns=True, align=True)
        for kind, label in (("PROPERTIES", "Properties"), ("OUTLINER", "Scene"),
                            ("ASSETS", "Assets"), ("GeometryNodeTree", "Geometry"),
                            ("ShaderNodeTree", "Materials"), ("CompositorNodeTree", "Compositor"),
                            ("UV", "UVs"), ("IMAGE_EDITOR", "Images"),
                            ("DOPESHEET", "Animation"), ("SPREADSHEET", "Spreadsheet")):
            grid.operator("xrwb.workspace_editor", text=label).editor = kind
        box = layout.box()
        box.label(text="OBJECT & OUTPUT", icon="PROPERTIES")
        grid = box.grid_flow(columns=2, even_columns=True, align=True)
        for key, label in (("OBJECT", "Transform"), ("MODIFIER", "Modifiers"),
                           ("DATA", "Object Data"), ("MATERIAL", "Material"),
                           ("RENDER", "Render"), ("OUTPUT", "Output"),
                           ("SCENE", "Scene"), ("WORLD", "World")):
            op = grid.operator("xrwb.workspace_editor", text=label)
            op.editor, op.property_context = "PROPERTIES", key
        box = layout.box()
        box.label(text="ADD-ONS & PREFERENCES", icon="PREFERENCES")
        row = box.row(align=True)
        row.operator("xrwb.preferences", text="Add-ons").section = "ADDONS"
        row.operator("xrwb.preferences", text="Preferences").section = "INTERFACE"
        box.label(text="Native add-on tabs remain in the viewport sidebar")
        box.label(text="Full Window includes menus and every editor type")
        if os.environ.get("XRWB_PERSONAL_ADDONS") != "1":
            box.label(text="Diagnostic profile: personal add-ons not loaded", icon="INFO")
        elif settings.addon_status:
            _label_lines(box, settings.addon_status)
        if settings.controller_fallback:
            box = layout.box()
            box.label(text="INDEX CONTROLLERS", icon="ORIENTATION_GIMBAL")
            box.operator("xrwb.command_menu", text="Toggle Hand Menu", icon="MENU_PANEL")
            box.prop(settings, "hand_menu_width")
            box.label(text="Menu follows the opening hand; either trigger clicks.")
            box.label(text="Trigger: click UI or use selected tool on geometry")
            box.label(text="Right squeeze: move / rotate / scale")
            box.prop(settings, "edit_tool")
            row = box.row(align=True)
            row.operator("xrwb.add_cube", text="Add Cube", icon="MESH_CUBE")
            row.operator("xrwb.history_undo", text="Undo", icon="LOOP_BACK")
            row.operator("xrwb.history_redo", text="Redo", icon="LOOP_FORWARDS")
            box.prop(settings, "controller_profile")
            box.label(text="Profile changes apply next XR start; Auto supports both.")
            box.label(text="Right A: hand menu; right stick click: tool wheel")
            box.label(text="Grip while pointing at a panel moves the panels")
            box.label(text="Left stick click: show / close desktop; left B: cancel")
            box.prop(settings, "travel_speed")
            box.label(text="Index left pad: teleport; sticks: move / turn")
            navigation = getattr(context.preferences.inputs, "xr_navigation", None)  # Blender 5.0+
            if navigation is not None:
                box.prop(navigation, "vignette_intensity", text="Motion Vignette")
                box.prop(navigation, "turn_amount", text="Snap Turn Angle")
            box.label(text="SteamVR boundary is managed by Room Setup")
        else:
            box.label(text="Camera pinch currently targets Add Cube only", icon="INFO")


class XRWB_PT_panel(bpy.types.Panel):
    bl_label = "Session & Diagnostics"
    bl_idname = "XRWB_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XR Workbench"
    bl_order = 10
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return not runtime.session_running(context)

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.xrwb_settings
        layout.operator("xrwb.refresh_status", icon="FILE_REFRESH")
        layout.label(text=settings.status_summary, icon="INFO")
        if settings.status_detail:
            _label_lines(layout, settings.status_detail)
        row = layout.row(align=True)
        row.operator("xrwb.start_session", text="Start XR", icon="PLAY")
        row.operator("xrwb.stop_session", text="Stop XR", icon="PAUSE")
        box = layout.box()
        box.label(text="Index Controllers")
        row = box.row()
        row.enabled = not runtime.session_exists(context)
        row.prop(settings, "controller_fallback")
        _label_lines(box, settings.controller_status)
        box = layout.box()
        box.label(text="Native Spatial Controls")
        row = box.row(align=True)
        row.operator("xrwb.task_card", text="Show / Recall")
        row.operator("xrwb.task_card", text="Hide").hide = True
        box.label(text="Session and Arrange Objects; some tools unavailable")
        box = layout.box()
        box.label(text="Bare Optical Hands")
        _label_lines(box, settings.hand_status)
        row = box.row(align=True)
        row.operator("xrwb.hand_tracking", text="Enable").enabled = True
        row.operator("xrwb.hand_tracking", text="Stop").enabled = False
        box.prop(settings, "pinch_close")
        box.prop(settings, "pinch_open")
        box.prop(settings, "pinch_dwell")
        box = layout.box()
        box.label(text="Expert Blender Editor (Compatibility)")
        box.prop(settings, "editor_width")
        box.prop(settings, "editor_distance")
        row = box.row(align=True)
        row.operator("xrwb.editor_surface", text="Show / Recall")
        row.operator("xrwb.editor_surface", text="Hide").hide = True
        state = context.window_manager.xr_session_state
        if state is not None and hasattr(state, "editor_surface_info"):
            info = state.editor_surface_info(context)
            box.label(text=f"Native surface: {int(info[1])} x {int(info[2])}")
        box = layout.box()
        box.label(text="Hand Gesture Tools")
        box.prop(settings, "edit_tool", expand=True)
        if settings.edit_tool == "select":
            box.prop(settings, "selection_click_slop_m")
            box.label(text="Object selection; mesh components not yet supported")
        elif settings.edit_tool == "move":
            box.prop(settings, "edit_constraint")
            box.prop(settings, "translation_snap")
        elif settings.edit_tool == "scale":
            box.prop(settings, "scale_one_handed")
            if settings.scale_one_handed:
                box.prop(settings, "edit_constraint")
            box.prop(settings, "scale_snap")
        else:
            box.prop(settings, "rotation_snap_degrees")
            box.label(text="Rotate from tracked index aim; no hand roll")
        box.operator("xrwb.cancel_edit", text="Cancel Active Gesture", icon="CANCEL")
        box = layout.box()
        box.label(text="M0 diagnostic overlay")
        box.prop(settings, "panel_opacity")
        row = box.row(align=True)
        row.operator(
            "xrwb.toggle_overlay",
            text="Preview" if not settings.overlay_enabled else "Hide",
        )
        row.operator("xrwb.place_overlay", text="Recall")
        box = layout.box()
        box.label(text="Camera / optical input")
        xr_settings = getattr(context.window_manager, "xr_session_settings", None)
        if xr_settings is not None and hasattr(xr_settings, "show_passthrough"):
            box.operator(
                "xrwb.revoke_passthrough"
                if settings.passthrough_opt_in
                else "xrwb.request_passthrough",
                text="Revoke passthrough"
                if settings.passthrough_opt_in
                else "Request passthrough",
            )
            box.label(text="Requested is not camera proof.", icon="ERROR")
        else:
            box.label(text="Passthrough unsupported.", icon="ERROR")
        box.label(text="Camera view is not world hand tracking.")
        if all(
            os.environ.get(name)
            for name in (
                "XRWB_GESTURE_EXE",
                "XRWB_GESTURE_CAMERA",
                "XRWB_GESTURE_FFMPEG",
            )
        ):
            box = layout.box()
            box.label(text="Image-local Index Pinch (Simulator Card)")
            op = box.operator("xrwb.image_gesture_stream", text="Start Camera Card Pointer")
            op.executable = os.environ["XRWB_GESTURE_EXE"]
            op.camera = os.environ["XRWB_GESTURE_CAMERA"]
            op.ffmpeg = os.environ["XRWB_GESTURE_FFMPEG"]
            op.consent = True
            box.label(text="One hand in LEFT camera view")
            box.label(text="Move index tip to card button; open, pinch 0.1s, open")
            box.label(text="2D card only; no world pointing or object ray")
        layout.operator("xrwb.export_report", icon="EXPORT")


CLASSES = (
    XRWB_PG_settings,
    XRWB_OT_refresh_status,
    XRWB_OT_export_report,
    XRWB_OT_start_session,
    XRWB_OT_stop_session,
    XRWB_OT_request_passthrough,
    XRWB_OT_revoke_passthrough,
    XRWB_OT_toggle_overlay,
    XRWB_OT_place_overlay,
    XRWB_OT_editor_surface,
    XRWB_OT_task_card,
    XRWB_OT_set_edit_tool,
    XRWB_OT_bevel_object,
    XRWB_OT_add_cube,
    XRWB_OT_add_primitive,
    XRWB_OT_command_menu,
    XRWB_OT_command_page,
    XRWB_OT_travel_preset,
    XRWB_OT_reset_controls,
    XRWB_OT_feel_preset,
    XRWB_OT_workflow,
    XRWB_OT_menu_section,
    XRWB_OT_live_cage_mode,
    XRWB_OT_retopo_analyze,
    XRWB_PT_command_deck,
    XRWB_OT_history_undo,
    XRWB_OT_history_redo,
    XRWB_OT_cancel_edit,
    XRWB_OT_cancel_scene_edit,
    XRWB_OT_workspace_editor,
    XRWB_OT_preferences,
    XRWB_PT_workspace,
    XRWB_PT_panel,
)
