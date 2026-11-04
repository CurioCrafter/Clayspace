"""Exercise real drawing, file persistence, reload and teardown in an owned GUI.

Only this test harness has a timer. The shipped add-on must have none.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import traceback

import addon_utils
import bpy
import gpu
from mathutils import Quaternion


expected = Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
output = Path(os.environ["XRWB_TEST_OUTPUT"]).resolve()
output.mkdir(parents=True, exist_ok=True)
addon = addon_utils.enable("xr_workbench", default_set=False, persistent=True)
assert addon is not None
assert expected in Path(addon.__file__).resolve().parents
assert bpy.app.version[:3] == (5, 1, 2)
bpy.context.preferences.view.show_splash = False
# Keep a real fixture object for persistence checks, outside the panel's view.
bpy.context.scene.objects["Cube"].location.x = -4.0
bpy.context.view_layer.update()
phase = 0
attempts = 0
draw_count = 0
backend = gpu.platform.backend_type_get()


def scene_signature():
    return tuple(
        (obj.name, obj.type, tuple(value for row in obj.matrix_world for value in row))
        for obj in bpy.context.scene.objects
    )


baseline = scene_signature()
images = set(bpy.data.images.keys())


def view_context():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    return window, area, region


def check_no_addon_timers():
    for module in (addon.runtime, addon.ui):
        for value in vars(module).values():
            if inspect.isfunction(value) and value.__module__ == module.__name__:
                assert not bpy.app.timers.is_registered(value), value.__name__


def advance():
    global phase, attempts, draw_count
    try:
        check_no_addon_timers()
        if phase == 0:
            window, area, region = view_context()
            area.spaces.active.show_region_ui = True
            area.spaces.active.overlay.show_overlays = False
            view = area.spaces.active.region_3d
            view.view_rotation = Quaternion((1.0, 0.0, 0.0), 1.57079632679)
            view.view_location = (-0.35, 0.0, 1.2)
            view.view_distance = 5.0
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.xrwb.refresh_status()
                bpy.ops.xrwb.place_overlay()
                bpy.ops.xrwb.toggle_overlay()
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            sidebar = next(item for item in area.regions if item.type == "UI")
            sidebar.active_panel_category = "XR Workbench"
            phase = 1
            return 0.5
        if phase == 1:
            state = addon.runtime.overlay_status()
            assert not state["last_draw_error"], state
            attempts += 1
            if not state["draw_count"] and attempts < 10:
                window, area, region = view_context()
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
                return 0.5
            assert state["draw_count"] > 0, state
            draw_count = state["draw_count"]
            assert scene_signature() == baseline
            assert set(bpy.data.images.keys()) == images
            bpy.ops.screen.screenshot(
                filepath=str(output / f"xr-workbench-{backend.lower()}.png")
            )
            bpy.ops.wm.save_as_mainfile(filepath=str(output / "fixture.blend"))
            phase = 2
            bpy.ops.wm.read_homefile(use_empty=True)
            return 0.5
        if phase == 2:
            state = addon.runtime.overlay_status()
            assert (
                not state["enabled"]
                and not state["window_handler"]
                and not state["xr_handler"]
            ), state
            assert not bpy.context.window_manager.xrwb_settings.passthrough_opt_in
            phase = 3
            bpy.ops.wm.open_mainfile(filepath=str(output / "fixture.blend"))
            return 0.5
        assert scene_signature() == baseline
        assert set(bpy.data.images.keys()) == images
        settings = bpy.context.window_manager.xrwb_settings
        assert not settings.overlay_enabled and not settings.passthrough_opt_in
        addon_utils.disable("xr_workbench", default_set=False)
        assert not hasattr(bpy.types.WindowManager, "xrwb_settings")
        assert not addon.runtime.overlay_status()["window_handler"]
        assert not addon.runtime.overlay_status()["xr_handler"]
        assert addon._load_pre not in bpy.app.handlers.load_pre
        print(
            f"XRWB_GUI_LIFECYCLE_PASS backend={backend} draw_count={draw_count} scene_unchanged=1 image_data_unchanged=1 save_reload=1 teardown=1",
            flush=True,
        )
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        traceback.print_exc()
        print("XRWB_GUI_LIFECYCLE_FAIL", flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(advance, first_interval=0.5, persistent=True)
