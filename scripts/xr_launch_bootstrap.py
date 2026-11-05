"""Open the isolated workbench; physical XR auto-start is explicit."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import addon_utils
import bpy
from mathutils import Quaternion


AUTO_START_XR = os.environ.get("XRWB_AUTO_START_XR") == "1"
STEAMVR = os.environ.get("XRWB_STEAMVR") == "1"
CAMERA_OVERLAY = os.environ.get("XRWB_INDEX_CAMERA_OVERLAY") == "1"
CAMERA_GESTURE_HUD = os.environ.get("XRWB_INDEX_CAMERA_GESTURE") == "1"
if STEAMVR and (
    os.environ.get("XRWB_STEAMVR_OPT_IN") != "1"
    or os.environ.get("XRWB_PHYSICAL_INDEX_OPT_IN") == "1"
    or CAMERA_OVERLAY or CAMERA_GESTURE_HUD
):
    raise RuntimeError("SteamVR requires its separate camera-off launch contract")
if AUTO_START_XR and (
    (not STEAMVR and os.environ.get("XRWB_PHYSICAL_INDEX_OPT_IN") != "1")
    or not os.environ.get("XR_RUNTIME_JSON")
    or not os.environ.get("XRWB_AUTO_START_STATUS_PATH")
    or os.environ.get("SIMULATED_ENABLE")
    or os.environ.get("LH_HANDTRACKING") != "OFF"
    or os.environ.get("VIVE_SLAM") != "OFF"
    or any(os.environ.get(name) for name in (
        "XRWB_GESTURE_EXE", "XRWB_GESTURE_CAMERA", "XRWB_GESTURE_FFMPEG"
    ))
):
    raise RuntimeError("Physical XR auto-start guard or no-gesture state is incomplete")
if AUTO_START_XR and CAMERA_OVERLAY and (
    os.environ.get("XRWB_INDEX_CAMERA_CONSENT") != "1"
    or not os.environ.get("XRWB_INDEX_CAMERA_EVAL")
    or not os.environ.get("XRWB_INDEX_CAMERA_FFMPEG")
    or not os.environ.get("XRWB_INDEX_CAMERA_NAME")
):
    raise RuntimeError("Opt-in camera overlay contract is incomplete")
if AUTO_START_XR and (
    CAMERA_GESTURE_HUD != (os.environ.get("XRWB_PHYSICAL_GESTURE_OPT_IN") == "1")
    or (CAMERA_GESTURE_HUD and not CAMERA_OVERLAY)
):
    raise RuntimeError("Opt-in camera gesture HUD contract is incomplete")


def _auto_status(message: str) -> None:
    line = f"XRWB_AUTO_START_{message}"
    print(line, flush=True)
    with Path(os.environ["XRWB_AUTO_START_STATUS_PATH"]).open("a", encoding="utf-8") as log:
        log.write(line + "\n")


def _desktop_at_start() -> bool:
    """Owned smoke tests need the desktop; otherwise it is the user's Settings choice."""
    if os.environ.get("XRWB_STEAMVR_SMOKE") == "1" or os.environ.get("XRWB_OPEN_DESKTOP") == "1":
        return True
    return bool(addon.onboarding.prefs()["desktop_at_start"])


def _auto_start_timer():
    """UI-thread, bounded startup; no keyboard prompt or file save."""
    state = {"stage": "window", "ticks": 0}

    def tick():
        state["ticks"] += 1
        try:
            context = bpy.context
            window = context.window or next(iter(context.window_manager.windows), None)
            area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None) if window else None
            region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
            if state["stage"] == "window":
                if not region:
                    if state["ticks"] >= 40:
                        _auto_status("FAIL stage=window reason=no_view3d")
                        return None
                    return 0.25
                with context.temp_override(window=window, area=area, region=region):
                    if not bpy.ops.xrwb.start_session.poll():
                        _auto_status("FAIL stage=start reason=operator_unavailable")
                        return None
                    result = bpy.ops.xrwb.start_session("EXEC_DEFAULT")
                if "FINISHED" not in result:
                    _auto_status("FAIL stage=start reason=operator_cancelled")
                    return None
                _auto_status(f"REQUESTED session_toggle=1 camera_overlay_requested={int(CAMERA_OVERLAY)}")
                state["stage"] = "running"
                state["ticks"] = 0
                return 0.25
            if state["stage"] == "running":
                if addon.runtime.session_running(context):
                    if STEAMVR and not context.window_manager.xrwb_settings.controller_status.startswith("XRWB_CONTROLLERS_ACTIONMAP_PASS"):
                        with context.temp_override(window=window, area=area, region=region):
                            bpy.ops.xrwb.stop_session("EXEC_DEFAULT")
                        _auto_status("FAIL stage=controllers reason=actionmap_not_ready")
                        return None
                    _auto_status("RUNNING native_session=1 headset_frame_verified=0")
                    if CAMERA_GESTURE_HUD:
                        with context.temp_override(window=window, area=area, region=region):
                            if "RUNNING_MODAL" not in bpy.ops.xrwb.camera_hud_gesture("EXEC_DEFAULT"):
                                _auto_status("FAIL stage=gesture_hud reason=operator_cancelled")
                                return None
                        _auto_status("GESTURE_HUD_ARMED image_local_add_cube=1 physical_action_verified=0")
                    state["stage"] = "card"
                    state["ticks"] = 0
                    return 0.25
                if state["ticks"] >= 120:
                    _auto_status("FAIL stage=running reason=session_timeout")
                    return None
                return 0.25
            # A running OpenXR session can remain SYNCHRONIZED while the headset
            # sleeps. Do not abandon panels after ten seconds without a view.
            if not addon.runtime.session_exists(context):
                _auto_status("CANCELLED stage=card reason=session_ended")
                return None
            if state["ticks"] == 40:
                _auto_status("WAITING reason=first_composed_view timeout_seconds=120")
            if not region:
                if state["ticks"] >= 480:
                    _auto_status("CARD_UNAVAILABLE reason=no_view3d")
                    return None
                return 0.25
            with context.temp_override(window=window, area=area, region=region):
                if STEAMVR or os.environ.get("XRWB_PERSONAL_ADDONS") == "1":
                    if not bpy.ops.xrwb.editor_surface.poll():
                        pass  # Not composed yet: keep waiting below.
                    elif _desktop_at_start():
                        if "FINISHED" in bpy.ops.xrwb.editor_surface("EXEC_DEFAULT"):
                            _auto_status("EDITOR_REQUESTED separated_panels=1 headset_visibility_verified=0")
                            return None
                    else:
                        # Start in the scene: desktop hidden (left stick click shows
                        # it). First launch opens the Guide on the right hand.
                        guide = menu = 0
                        prefs = addon.onboarding.prefs()
                        if not prefs["guide_done"] and bpy.ops.xrwb.guide.poll():
                            guide = int("FINISHED" in bpy.ops.xrwb.guide("EXEC_DEFAULT", action="OPEN"))
                        elif prefs.get("menu_at_start", True) and bpy.ops.xrwb.command_menu.poll():
                            # A real menu is there when you arrive: the hand menu on Create.
                            menu = int("FINISHED" in bpy.ops.xrwb.command_menu("EXEC_DEFAULT", page="CREATE"))
                        _auto_status(f"PANELS_READY desktop=0 guide={guide} menu={menu} "
                                     "headset_visibility_verified=0")
                        return None
                    if state["ticks"] >= 480:
                        _auto_status("EDITOR_UNAVAILABLE reason=not_composed")
                        return None
                    return 0.25
                if bpy.ops.xrwb.task_card.poll() and "FINISHED" in bpy.ops.xrwb.task_card("EXEC_DEFAULT"):
                    _auto_status("CARD_REQUESTED native_card=1 headset_visibility_verified=0")
                    return None
            if state["ticks"] >= 480:
                _auto_status("CARD_UNAVAILABLE reason=not_composed")
                return None
            return 0.25
        except Exception as exc:
            _auto_status(f"FAIL stage={state['stage']} reason={type(exc).__name__}")
            return None

    return tick


if not (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0):
    raise RuntimeError(f"Expected Blender 4.5 to 5.2, found {bpy.app.version_string}")
# This launcher starts a dedicated, unsaved diagnostic workspace.
bpy.ops.wm.read_homefile(use_empty=True)
personal_report = None
if os.environ.get("XRWB_PERSONAL_ADDONS") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from xr_personal_addons import load_personal_addons
    personal_report = load_personal_addons()

# Loading copied preferences resets this flag; apply after both load operations.
# Runtime-only: never save this change into the user's original preferences.
bpy.context.preferences.view.show_splash = False

expected = Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
addon = addon_utils.enable("xr_workbench", default_set=False, persistent=True)
if addon is None:
    raise RuntimeError("XR Workbench could not be enabled in the isolated profile")
if expected not in Path(addon.__file__).resolve().parents:
    raise RuntimeError("XR Workbench did not load from the isolated profile")
if STEAMVR:
    profile = os.environ.get("XRWB_CONTROLLER_PROFILE", "AUTO")
    if profile not in {"AUTO", "INDEX", "QUEST"}:
        raise RuntimeError("Unsupported controller profile")
    bpy.context.window_manager.xrwb_settings.controller_profile = profile
    addon.ui.configure_steamvr_session(bpy.context)
    bpy.context.preferences.view.ui_scale = max(bpy.context.preferences.view.ui_scale, 1.5)
    print(f"XRWB_STEAMVR_CONFIGURED controllers={profile.lower()} positional=1 absolute=1 base_scale=1 camera_requested=0 physical_tracking_verified=0", flush=True)
if personal_report:
    # Only the private profile: larger native labels, still adjustable in VR.
    bpy.context.preferences.view.ui_scale = max(bpy.context.preferences.view.ui_scale, 1.5)
    bpy.context.window_manager.xrwb_settings.addon_status = (
        f"{len(personal_report['loaded'])} enabled add-ons loaded; "
        f"{len(personal_report['failed'])} failed; "
        f"{len(personal_report['manual_enable_services'])} services available for manual enable"
    )

bpy.ops.xrwb.refresh_status()
context = bpy.context
window = context.window or next(iter(context.window_manager.windows), None)
if window:
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next(
            (region for region in area.regions if region.type == "WINDOW"), None
        )
        if region is None:
            continue
        with context.temp_override(window=window, area=area, region=region):
            # Region visibility invokes a native update that requires an owner window.
            area.spaces.active.show_region_ui = True
            view = area.spaces.active.region_3d
            view.view_rotation = Quaternion((1.0, 0.0, 0.0), 1.57079632679)
            view.view_location = (-0.35, 0.0, 1.2)
            view.view_distance = 5.0
            if not STEAMVR:
                bpy.ops.xrwb.place_overlay()
                bpy.ops.xrwb.toggle_overlay()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            sidebar = next((item for item in area.regions if item.type == "UI"), None)
            if sidebar is not None:
                sidebar.active_panel_category = "XR Workbench"
        break

report_path = os.environ.get("XRWB_REPORT_PATH")
if report_path:
    addon.diagnostics.write_report(
        addon.diagnostics.collect_report(context), report_path
    )
if AUTO_START_XR and bpy.app.background:
    raise RuntimeError("Physical XR auto-start requires a visible Blender window")
print(
    f"XRWB_BOOTSTRAP_PASS module={Path(addon.__file__).resolve()} xr_auto_start={int(AUTO_START_XR)} camera_overlay_requested={int(CAMERA_OVERLAY)}",
    flush=True,
)
if AUTO_START_XR:
    _auto_status(f"ARMED ui_timer=1 camera_overlay_requested={int(CAMERA_OVERLAY)}")
    try:
        bpy.app.timers.register(_auto_start_timer(), first_interval=0.5)
    except Exception as exc:
        _auto_status(f"FAIL stage=timer reason={type(exc).__name__}")
        raise
