"""Factory Blender UI contracts; session-state branches are stubbed, not XR proof."""
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xr_workbench as addon  # noqa: E402
from xr_workbench import interaction_log, onboarding, ui  # noqa: E402


class Layout:
    """Record draw calls and validate RNA names; this does not render pixels."""
    def __init__(self, records=None):
        self.records = [] if records is None else records
        self.enabled = True

    def row(self, **_kwargs):
        return Layout(self.records)

    column = box = grid_flow = row

    def label(self, **kwargs):
        self.records.append(("label", kwargs.get("text", ""), kwargs.get("icon", "")))

    def prop(self, owner, name, **kwargs):
        assert name in owner.bl_rna.properties, (owner, name)
        self.records.append(("prop", name, kwargs.get("text", "")))

    def operator(self, name, **kwargs):
        family, action = name.split(".")
        assert getattr(getattr(bpy.ops, family), action).get_rna_type()
        self.records.append(("operator", name, kwargs.get("text", "")))
        return SimpleNamespace()

    def menu(self, name, **_kwargs):
        assert hasattr(bpy.types, name), name
        self.records.append(("menu", name, ""))

    def separator(self):
        pass


assert bpy.app.background and not bpy.data.filepath
assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
addon.register()
settings = bpy.context.window_manager.xrwb_settings
onboarding.set_pref("menu_open", ",".join(ui.SECTIONS))  # Every section open: the draw checks see every control.
assert len(ui.COMMAND_PAGES) == 10
assert dict((key, label) for key, label, _description in ui.COMMAND_PAGES)["CONTROLS"] == "Settings"
assert abs(settings.scale_drag_distance_m-.2) < 1e-6
prop = settings.bl_rna.properties["scale_drag_distance_m"]
assert prop.subtype == "DISTANCE" and prop.unit == "LENGTH"
assert abs(prop.hard_min-.03) < 1e-6 and abs(prop.hard_max-1) < 1e-6

spaces = [area.spaces.active for window in bpy.context.window_manager.windows
          for area in window.screen.areas if area.type == "VIEW_3D"]
sidebar_before = tuple(space.show_region_ui for space in spaces)
assert ui.XRWB_PT_workspace.poll(bpy.context) and ui.XRWB_PT_panel.poll(bpy.context)
assert not bpy.ops.xrwb.set_edit_tool.poll()
actual_running = addon.runtime.session_running
try:
    addon.runtime.session_running = lambda _context: True
    assert not ui.XRWB_PT_workspace.poll(bpy.context) and not ui.XRWB_PT_panel.poll(bpy.context)
    assert tuple(space.show_region_ui for space in spaces) == sidebar_before
    assert ui.XRWB_PT_command_deck.bl_region_type == "HEADER", "Hand surface was mixed with the desktop sidebar"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 32
    assert not ui._scene_tool_reason(bpy.context, "move")
    assert bpy.ops.xrwb.set_edit_tool(tool="move") == {"FINISHED"}
    assert settings.edit_tool == "move" and "Move selected" in interaction_log.last_message()
    bpy.context.preferences.edit.use_global_undo = False
    assert "Global Undo" in ui._scene_tool_reason(bpy.context, "move")
    assert not bpy.ops.xrwb.set_edit_tool.poll()
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.object.mode_set(mode="EDIT")
    assert "Object Mode" in ui._scene_tool_reason(bpy.context, "extrude")
    # Edit Mode keeps Select, Move and Extrude Selection (vertex / edge / face work).
    assert bpy.ops.xrwb.set_edit_tool.poll()
    assert not ui._scene_tool_reason(bpy.context, "edit_extrude")
    bpy.ops.object.mode_set(mode="OBJECT")
    obj = bpy.context.object
    obj.select_set(False)
    bpy.context.view_layer.objects.active = None
    assert "Select an editable" in ui._scene_tool_reason(bpy.context, "cut_difference")
    previous = settings.edit_tool
    assert bpy.ops.xrwb.set_edit_tool(tool="cut_difference") == {"CANCELLED"}
    assert settings.edit_tool == previous
    assert interaction_log.last_level() == "WARNING" and "Select an editable" in interaction_log.last_message()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.xrwb.set_edit_tool(tool="cut_difference") == {"FINISHED"}

    for profile in ("INDEX", "QUEST", "AUTO"):
        settings.controller_profile = profile
        help_text = "\n".join(ui._controller_help(profile))
        assert ("Index pads" in help_text) == (profile in {"INDEX", "AUTO"})
        assert ("Quest X" in help_text) == (profile in {"QUEST", "AUTO"})
        assert "Left grip: move world" in help_text and "Grip a desktop panel" in help_text
        for page, _label, _description in ui.COMMAND_PAGES:
            settings.command_page = page
            layout = Layout()
            ui.XRWB_PT_command_deck.draw(SimpleNamespace(layout=layout), bpy.context)
            assert ("operator", "xrwb.command_menu", "Close") in layout.records
            assert ("operator", "xrwb.editor_surface", "Open Desktop") in layout.records
            assert any(record[0] == "label" and record[1].startswith("Last action:") for record in layout.records)
            if page == "CONTROLS":
                names = set()
                for section in ("FEEL", "BUTTONS", "START"):
                    settings.settings_section = section
                    section_layout = Layout()
                    ui.XRWB_PT_command_deck.draw(SimpleNamespace(layout=section_layout), bpy.context)
                    names |= {item[1] for item in section_layout.records if item[0] == "prop"}
                settings.settings_section = "FEEL"
                assert {"hand_menu_width", "scale_drag_distance_m", "travel_speed", "controller_profile",
                        "left_handed", "slot_RIGHT_A", "slot_LEFT_STICK", "trigger_threshold"} <= names, names
                assert not any(item[1] == "object.origin_set" for item in layout.records)
            if page == "EDIT":
                assert any(item[1] == "object.origin_set" for item in layout.records)
    layouts = []
    for message in ("Ready", "An operation was rejected. " * 30):
        interaction_log.emit("ui_test", message, level="WARNING")
        layout = Layout()
        ui._action_feedback(layout)
        layouts.append(layout.records)
    assert len(layouts[0]) == len(layouts[1]) == 2, "Status messages shifted control geometry"
    assert layouts[1][0][2] == "ERROR" and layouts[1][1][1].endswith("..."), "Long warning was not bounded"
finally:
    addon.runtime.session_running = actual_running
assert ui.XRWB_PT_workspace.poll(bpy.context) and ui.XRWB_PT_panel.poll(bpy.context)
assert tuple(space.show_region_ui for space in spaces) == sidebar_before

# Folding Hand Menu: fold state and pins live in vr_prefs.json and survive a restart.
onboarding.set_pref("menu_open", "")
assert ui.open_sections() == list(ui.DEFAULT_OPEN) and "SCULPT.tools" in ui.DEFAULT_OPEN
assert bpy.ops.xrwb.menu_section(key="SCULPT.brush", action="TOGGLE") == {"FINISHED"}
assert bpy.ops.xrwb.menu_section(key="SCULPT.tools", action="TOGGLE") == {"FINISHED"}
for key in ("SCULPT.brush", "EDIT.snap", "SCULPT.retopo"):
    assert bpy.ops.xrwb.menu_section(key=key, action="PIN") == {"FINISHED"}
assert "Brush unpinned" in interaction_log.last_message(), interaction_log.last_message()
onboarding.reset_cache()  # Re-read vr_prefs.json, like the next launch.
assert "SCULPT.brush" in ui.open_sections() and "SCULPT.tools" not in ui.open_sections()
assert ui.pinned_sections() == ["EDIT.snap", "SCULPT.retopo"], ui.pinned_sections()
settings.command_page = "SCULPT"
settings.menu_compact = True
layout = Layout()
ui.XRWB_PT_command_deck.draw(SimpleNamespace(layout=layout), bpy.context)
texts = [record[2] if record[0] == "operator" else record[1] for record in layout.records]
headers = [text for text in texts if text.split(":")[0] in {"Snap", "Retopo", "Tools", "Brush"}]
assert headers[:2] == ["Snap:  free · move 0 · 0°", "Retopo:  ~5,000 quads · Balanced"], headers  # Pinned first.
assert texts.count("Retopo:  ~5,000 quads · Balanced") == 1, "A pinned section is not drawn twice"
assert not any(text.startswith("Point + trigger") for text in texts), "Compact hides help lines"
assert ("prop", "sculpt_radius_m", "Brush") in layout.records, "The open Brush section draws its controls"
assert ("prop", "retopo_faces", "Target Faces") not in layout.records, "A folded section draws only its header"
settings.menu_compact = False
addon.unregister()
print("XR_UI_SETTINGS_SMOKE_PASS registration=1 settings=1 guards=1 profile_help=1 feedback=1 menu_sections=1 native_sidebar_untouched=1 session_policy=stub pixels=0 hardware=0", flush=True)
