"""Real Blender UI: the Add menu is walkable with arrow keys and Enter.

The right stick sends exactly these key events to hand list menus. Run in a
GUI Blender (a virtual display is fine) with --enable-event-simulate and
--factory-startup; it quits by itself. XR is not started: the tool guard's
session check is stubbed, as in the UI settings smoke.
"""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xr_workbench as addon  # noqa: E402

assert not bpy.app.background and not bpy.data.filepath
addon.register()
addon.runtime.session_running = lambda _context: True
state = {"tick": 0}


def step():
    state["tick"] += 1
    context = bpy.context
    window = context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    tick = state["tick"]
    if tick == 1:
        context.window_manager.xrwb_settings.place_kind = "CUBE"
        window.event_simulate(type="MOUSEMOVE", value="NOTHING",
                              x=region.x + region.width // 2, y=region.y + region.height // 2)
        with context.temp_override(window=window, area=area, region=region):
            bpy.ops.wm.call_menu(name="XRWB_MT_add")
    elif tick in (3, 4):
        window.event_simulate(type="DOWN_ARROW", value="PRESS")
        window.event_simulate(type="DOWN_ARROW", value="RELEASE")
    elif tick == 6:
        window.event_simulate(type="RET", value="PRESS")
        window.event_simulate(type="RET", value="RELEASE")
    elif tick == 9:
        settings = context.window_manager.xrwb_settings
        passed = settings.edit_tool == "place_object" and settings.place_kind == "CYLINDER"
        print(f"XR_MENU_STICK_GUI_{'PASS' if passed else 'FAIL'} down=2 enter=1 picked={settings.place_kind} "
              "native_menu=1 hardware=0", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return 0.4


bpy.app.timers.register(step, first_interval=1.0)
