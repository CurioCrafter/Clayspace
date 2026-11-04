"""Offline bootstrap contract; fake Blender, no XR/runtime/camera process."""

from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest import TestCase, main, mock
import os
import runpy
import sys


BOOTSTRAP = Path(__file__).parents[1] / "scripts" / "xr_launch_bootstrap.py"


class FakeOperator:
    def __init__(self, action, available=True):
        self.action = action
        self.available = available

    def poll(self):
        return self.available

    def __call__(self, *_args, **_kwargs):
        return self.action()


def run_bootstrap(
    folder: Path, *, auto: bool, simulated: bool = False,
    overlay: bool = False, hud: bool = False,
    missing_overlay_consent: bool = False,
    steamvr: bool = False, invalid_steamvr: bool = False,
    controller_failure: bool = False, delayed_surface_ticks: int = 0,
    end_before_surface: bool = False, smoke: bool = False,
    guide_done: bool = False, desktop_pref: bool = False,
):
    calls = []
    timers = []
    active = {"running": False}
    install = folder / "profile" / "scripts" / "addons" / "xr_workbench"
    install.mkdir(parents=True)
    region = SimpleNamespace(type="WINDOW")
    sidebar = SimpleNamespace(type="UI", active_panel_category="")
    view = SimpleNamespace(view_rotation=None, view_location=None, view_distance=None)
    space = SimpleNamespace(show_region_ui=False, region_3d=view)
    area = SimpleNamespace(type="VIEW_3D", regions=[region, sidebar],
                           spaces=SimpleNamespace(active=space))
    window = SimpleNamespace(screen=SimpleNamespace(areas=[area]))
    context = SimpleNamespace(
        preferences=SimpleNamespace(view=SimpleNamespace(show_splash=True, ui_scale=1.0)),
        window=window,
        window_manager=SimpleNamespace(windows=[window], xrwb_settings=SimpleNamespace(
            controller_status="not_ready" if controller_failure else "XRWB_CONTROLLERS_ACTIONMAP_PASS actions=1",
        )),
        temp_override=lambda **_kwargs: nullcontext(),
    )
    bpy = ModuleType("bpy")
    bpy.context = context
    bpy.app = SimpleNamespace(
        version=(5, 1, 2), version_string="5.1.2", background=False,
        timers=SimpleNamespace(register=lambda callback, **_kw: timers.append(callback)),
    )

    def start():
        calls.append("start")
        active["running"] = True
        return {"FINISHED"}

    def card():
        calls.append("card")
        return {"FINISHED"}

    def gesture_hud():
        calls.append("gesture_hud")
        return {"RUNNING_MODAL"}

    def read_home(**_kwargs):
        calls.append("empty_home")
        context.window = None  # Blender invalidates context during homefile load.
        context.preferences.view.show_splash = True

    bpy.ops = SimpleNamespace(
        wm=SimpleNamespace(read_homefile=read_home,
                           redraw_timer=lambda **_kw: None),
        xrwb=SimpleNamespace(
            refresh_status=lambda: None, place_overlay=lambda: None,
            toggle_overlay=lambda: None,
            start_session=FakeOperator(start), task_card=FakeOperator(card),
            camera_hud_gesture=FakeOperator(gesture_hud),
            editor_surface=FakeOperator(lambda: calls.append("editor") or {"FINISHED"}),
            stop_session=FakeOperator(lambda: calls.append("stop") or {"FINISHED"}),
            guide=FakeOperator(lambda: calls.append("guide") or {"FINISHED"}),
            command_menu=FakeOperator(lambda: calls.append("menu") or {"FINISHED"}),
        ),
    )
    addon = SimpleNamespace(
        __file__=str(install / "__init__.py"),
        runtime=SimpleNamespace(session_running=lambda _context: active["running"],
                                session_exists=lambda _context: active["running"]),
        ui=SimpleNamespace(configure_steamvr_session=lambda _context: calls.append("steamvr_config")),
        onboarding=SimpleNamespace(prefs=lambda: {"guide_done": guide_done, "desktop_at_start": desktop_pref}),
    )
    addon_utils = ModuleType("addon_utils")
    addon_utils.enable = lambda *_args, **_kw: addon
    mathutils = ModuleType("mathutils")
    mathutils.Quaternion = lambda *args: args
    status = folder / "auto-status.txt"
    environment = {
        "XRWB_EXPECTED_INSTALL": str(install),
        "XRWB_AUTO_START_XR": "1" if auto else "0",
        "XRWB_PHYSICAL_INDEX_OPT_IN": "1" if auto else "0",
        "XR_RUNTIME_JSON": str(folder / "offline-runtime.json") if auto else "",
        "XRWB_AUTO_START_STATUS_PATH": str(status) if auto else "",
        "LH_HANDTRACKING": "OFF",
        "VIVE_SLAM": "OFF",
    }
    if simulated:
        environment["SIMULATED_ENABLE"] = "1"
    if smoke:
        environment["XRWB_STEAMVR_SMOKE"] = "1"
    if steamvr:
        environment.update({"XRWB_STEAMVR": "1", "XRWB_STEAMVR_OPT_IN": "1",
                            "XRWB_PHYSICAL_INDEX_OPT_IN": "1" if invalid_steamvr else "0"})
    if overlay:
        environment.update({
            "XRWB_INDEX_CAMERA_OVERLAY": "1",
            "XRWB_INDEX_CAMERA_CONSENT": "1",
            "XRWB_INDEX_CAMERA_EVAL": str(folder / "pinned-evaluator.exe"),
            "XRWB_INDEX_CAMERA_FFMPEG": str(folder / "pinned-ffmpeg.exe"),
            "XRWB_INDEX_CAMERA_NAME": "Index Camera",
        })
        if missing_overlay_consent:
            del environment["XRWB_INDEX_CAMERA_CONSENT"]
    if hud:
        environment["XRWB_INDEX_CAMERA_GESTURE"] = "1"
        environment["XRWB_PHYSICAL_GESTURE_OPT_IN"] = "1"
    output = StringIO()
    with mock.patch.dict(sys.modules, {"bpy": bpy, "addon_utils": addon_utils,
                                       "mathutils": mathutils}), \
         mock.patch.dict(os.environ, environment, clear=True), redirect_stdout(output):
        runpy.run_path(str(BOOTSTRAP))
        assert not context.preferences.view.show_splash
        assert space.show_region_ui and sidebar.active_panel_category == "XR Workbench"
        if auto and not simulated:
            assert len(timers) == 1
            assert timers[0]() == 0.25  # UI-ready toggle request.
            if steamvr and controller_failure:
                assert timers[0]() is None
            else:
                assert timers[0]() == 0.25  # Session reaches running state.
                surface = bpy.ops.xrwb.editor_surface if steamvr else bpy.ops.xrwb.task_card
                surface.available = False
                for _ in range(delayed_surface_ticks):
                    assert timers[0]() == 0.25  # Sleeping headset must not abandon panels.
                surface.available = True
                if end_before_surface:
                    active["running"] = False
                assert timers[0]() is None  # Card request completes.
    return calls, timers, output.getvalue(), status


class BootstrapTests(TestCase):
    def test_panels_wait_for_late_headset_wake_or_cancel_when_session_ends(self):
        for steamvr in (False, True):
            for ended in (False, True):
                with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
                    calls, _timers, _output, status = run_bootstrap(
                        Path(directory), auto=True, steamvr=steamvr,
                        delayed_surface_ticks=80, end_before_surface=ended,
                    )
                    log = status.read_text(encoding="utf-8")
                    self.assertEqual(log.count("WAITING reason=first_composed_view"), 1)
                    self.assertNotIn("UNAVAILABLE", log)
                    self.assertEqual(bool({"editor", "card", "guide"} & set(calls)), not ended)
                    self.assertEqual("CANCELLED stage=card reason=session_ended" in log, ended)

    def test_steamvr_uses_controllers_and_native_panels_without_camera(self):
        # Owned smoke tests and the user's "Desktop at Start" choice open the desktop.
        for options in ({"smoke": True}, {"desktop_pref": True, "guide_done": True}):
            with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
                calls, _timers, output, status = run_bootstrap(Path(directory), auto=True, steamvr=True, **options)
                self.assertEqual(calls, ["empty_home", "steamvr_config", "start", "editor"])
                self.assertIn("XRWB_STEAMVR_CONFIGURED", output)
                self.assertIn("EDITOR_REQUESTED", status.read_text(encoding="utf-8"))

    def test_steamvr_starts_in_the_scene_with_the_guide_once(self):
        for guide_done, expected in ((False, ["guide"]), (True, ["menu"])):
            with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
                calls, _timers, _output, status = run_bootstrap(
                    Path(directory), auto=True, steamvr=True, guide_done=guide_done)
                self.assertEqual(calls, ["empty_home", "steamvr_config", "start", *expected])
                log = status.read_text(encoding="utf-8")
                self.assertIn(f"PANELS_READY desktop=0 guide={int(not guide_done)} menu={int(guide_done)}", log)
                self.assertNotIn("EDITOR_REQUESTED", log)

    def test_steamvr_refuses_camera_or_mixed_runtime_contract(self):
        for args in ({"overlay": True}, {"invalid_steamvr": True}):
            with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
                with self.assertRaisesRegex(RuntimeError, "separate camera-off"):
                    run_bootstrap(Path(directory), auto=True, steamvr=True, **args)

    def test_steamvr_stops_if_controller_actionmap_did_not_attach(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            calls, _timers, _output, status = run_bootstrap(
                Path(directory), auto=True, steamvr=True, controller_failure=True,
            )
            self.assertEqual(calls, ["empty_home", "steamvr_config", "start", "stop"])
            self.assertIn("FAIL stage=controllers", status.read_text(encoding="utf-8"))

    def test_ordinary_start_stays_manual(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            calls, timers, output, status = run_bootstrap(Path(directory), auto=False)
            self.assertEqual(calls, ["empty_home"])
            self.assertEqual(timers, [])
            self.assertIn("xr_auto_start=0 camera_overlay_requested=0", output)
            self.assertFalse(status.exists())

    def test_opt_in_auto_starts_session_then_card_without_camera(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            calls, timers, output, status = run_bootstrap(Path(directory), auto=True)
            self.assertEqual(calls, ["empty_home", "start", "card"])
            self.assertEqual(len(timers), 1)
            self.assertIn("xr_auto_start=1 camera_overlay_requested=0", output)
            log = status.read_text(encoding="utf-8")
            self.assertIn("XRWB_AUTO_START_REQUESTED session_toggle=1 camera_overlay_requested=0", log)
            self.assertIn("XRWB_AUTO_START_RUNNING native_session=1 headset_frame_verified=0", log)
            self.assertIn("XRWB_AUTO_START_CARD_REQUESTED native_card=1 headset_visibility_verified=0", log)

    def test_simulator_opt_in_is_rejected_before_homefile(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            with self.assertRaisesRegex(RuntimeError, "guard or no-gesture"):
                run_bootstrap(Path(directory), auto=True, simulated=True)

    def test_overlay_request_starts_session_and_card_without_claiming_frames(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            calls, timers, output, status = run_bootstrap(
                Path(directory), auto=True, overlay=True
            )
            self.assertEqual(calls, ["empty_home", "start", "card"])
            self.assertEqual(len(timers), 1)
            self.assertIn("camera_overlay_requested=1", output)
            log = status.read_text(encoding="utf-8")
            self.assertIn("XRWB_AUTO_START_REQUESTED session_toggle=1 camera_overlay_requested=1", log)
            self.assertNotIn("camera_on=1", log)

    def test_overlay_without_explicit_consent_is_rejected(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            with self.assertRaisesRegex(RuntimeError, "overlay contract is incomplete"):
                run_bootstrap(
                    Path(directory), auto=True, overlay=True,
                    missing_overlay_consent=True,
                )

    def test_opt_in_gesture_hud_starts_after_xr_without_new_camera(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            calls, _timers, _output, status = run_bootstrap(
                Path(directory), auto=True, overlay=True, hud=True
            )
            self.assertEqual(calls, ["empty_home", "start", "gesture_hud", "card"])
            self.assertIn("GESTURE_HUD_ARMED", status.read_text(encoding="utf-8"))

    def test_gesture_hud_requires_camera_overlay(self):
        with TemporaryDirectory(prefix="xrwb-bootstrap-") as directory:
            with self.assertRaisesRegex(RuntimeError, "gesture HUD contract"):
                run_bootstrap(Path(directory), auto=True, hud=True)


if __name__ == "__main__":
    main()
