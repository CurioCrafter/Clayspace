"""Own-process GUI registration/draw check; never starts XR or opens a camera."""
import os
from pathlib import Path
import sys
import traceback
import runpy

import bpy

assert os.environ.get("XRWB_PERSONAL_ADDONS") == "1"
assert os.environ.get("XRWB_AUTO_START_XR") == "0"
assert not bpy.app.background
boot = runpy.run_path(str(Path(__file__).with_name("xr_launch_bootstrap.py")))
report = boot["personal_report"]
assert not report["failed"], report["failed"]
addon = sys.modules.get("xr_workbench")
assert addon is not None
assert Path(addon.__file__).resolve().parent == Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
assert bpy.context.preferences.view.ui_scale >= 1.5
steps = iter(("show_sidebar", "category", "PROPERTIES", "ShaderNodeTree",
              "GeometryNodeTree", "ASSETS", "UV", "done"))


def check():
    try:
        window = bpy.context.window_manager.windows[0]
        viewport = next(a for a in window.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in viewport.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=window, area=viewport, region=region):
            step = next(steps)
            if step == "show_sidebar":
                viewport.spaces.active.show_region_ui = True
            elif step == "category":
                sidebar = next(r for r in viewport.regions if r.type == "UI")
                sidebar.active_panel_category = "XR Workbench"
            elif step != "done":
                assert bpy.ops.xrwb.workspace_editor(editor=step) == {"FINISHED"}
            if step != "done":
                # Let Blender initialize the new editor before drawing its UI.
                return 0.5
        print(f"XRWB_PERSONAL_ADDONS_GUI_PASS loaded={len(report['loaded'])} "
              "editors=5 workspace_draw=1 xr_started=0 camera=0", flush=True)
    except Exception:
        traceback.print_exc()
        print("XRWB_PERSONAL_ADDONS_GUI_FAIL", flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(check, first_interval=2.0)
