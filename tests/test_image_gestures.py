import importlib.util
import inspect
from contextlib import nullcontext
from io import BytesIO
import os
from pathlib import Path
from struct import pack
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
package = types.ModuleType("xr_workbench")
package.__path__ = [str(ROOT / "xr_workbench")]
sys.modules.setdefault("xr_workbench", package)
bpy = types.ModuleType("bpy")
bpy.types = types.SimpleNamespace(Operator=object)
bpy.props = types.SimpleNamespace(
    StringProperty=lambda **_kw: None, BoolProperty=lambda **_kw: None
)
calls = []
bpy.ops = types.SimpleNamespace(
    wm=types.SimpleNamespace(
        xr_task_card_cycle_opacity=lambda _mode, **kw: calls.append(kw) or {"FINISHED"}
    )
)
bpy = sys.modules.setdefault("bpy", bpy)
bpy.props.StringProperty = lambda **_kw: None
spec = importlib.util.spec_from_file_location(
    "xr_workbench.image_gestures", ROOT / "xr_workbench" / "image_gestures.py"
)
gestures = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gestures
spec.loader.exec_module(gestures)


def packet(seq, timestamp, ratio, *, world=0, count=1, candidate=0,
           center_x=480.0, center_y=480.0, presence=.95, palm=.95):
    points = [480.0, 480.0] * 21
    points[10:12] = [center_x - 20, center_y]  # index MCP, landmark 5
    points[34:36] = [center_x + 80, center_y]  # pinky MCP, landmark 17
    points[8:10] = [center_x, center_y]  # thumb tip, landmark 4
    points[16:18] = [center_x + ratio * 100, center_y]  # index tip, landmark 8
    header = pack("<9I", 0x47575258, 1, 960, 960, seq, timestamp, 0, count, world)
    return header + pack("<IIff42f", 0, candidate, presence, palm, *points) * count


def hud_observation(seq, ratio, *, valid=1, candidate=0, age_ms=20,
                    tip_x=480, tip_y=760, presence=.95, palm=.95):
    return (
        valid, seq, candidate, presence, palm,
        tip_x + ratio * 100, tip_y, tip_x, tip_y,
        390, 760, 490, 760, age_ms,
    )


class ImageGestureTests(unittest.TestCase):
    def test_blender_operator_cancel_callback_has_required_signature(self):
        self.assertEqual(
            tuple(inspect.signature(gestures.XRWB_OT_image_gesture_stream.cancel).parameters),
            ("self", "context"),
        )
        self.assertEqual(
            tuple(inspect.signature(gestures.XRWB_OT_camera_hud_gesture.cancel).parameters),
            ("self", "context"),
        )

    def setUp(self):
        calls.clear()
        self.opacity = 85

        def cycle(_mode, **kw):
            calls.append(kw)
            self.opacity = (
                25 if self.opacity == 85 else 55 if self.opacity == 25 else 85
            )
            return {"FINISHED"}

        bpy.ops.wm.xr_task_card_cycle_opacity = cycle
        state = types.SimpleNamespace(
            task_card_info=lambda _context: (1, 8, 8, -1, 0, self.opacity)
        )
        self.context = types.SimpleNamespace(
            window_manager=types.SimpleNamespace(
                xr_session_state=state,
                xrwb_settings=types.SimpleNamespace(hand_status=""),
            )
        )

    def test_binary_stream_rejects_world_claim_and_large_record_count(self):
        with self.assertRaises(ValueError):
            gestures.read_frame(BytesIO(packet(1, 1, 0.8, world=1)))
        with self.assertRaises(ValueError):
            gestures.read_frame(BytesIO(packet(1, 1, 0.8, count=5)))

    def test_neutral_dwell_release_dispatches_once_and_loss_cancels(self):
        gate = gestures.ImagePinch()
        for seq, timestamp, ratio, now in (
            (1, 1, 0.8, 1.0),
            (2, 2, 0.2, 1.02),
            (3, 3, 0.2, 1.13),
            (4, 4, 0.8, 1.15),
        ):
            frame = gestures.read_frame(BytesIO(packet(seq, timestamp, ratio)))
            gestures.process_frame(self.context, frame, gate, now)
        self.assertEqual(calls, [{"generation": 8}])
        self.assertEqual(
            self.context.window_manager.xrwb_settings.hand_status,
            "Image pinch: card opacity 25%",
        )
        gestures.process_frame(
            self.context, gestures.read_frame(BytesIO(packet(5, 5, 0.8))), gate, 1.17
        )
        self.assertEqual(len(calls), 1)
        gate.lost(3.8)
        gestures.process_frame(
            self.context, gestures.read_frame(BytesIO(packet(6, 6, 0.2))), gate, 3.81
        )
        self.assertEqual(len(calls), 1)

    def test_rejected_native_action_does_not_report_success(self):
        bpy.ops.wm.xr_task_card_cycle_opacity = lambda _mode, **_kw: {"CANCELLED"}
        gate = gestures.ImagePinch()
        for seq, ratio, now in (
            (1, 0.8, 1.0),
            (2, 0.2, 1.02),
            (3, 0.2, 1.13),
            (4, 0.8, 1.15),
        ):
            gestures.process_frame(
                self.context,
                gestures.read_frame(BytesIO(packet(seq, seq, ratio))),
                gate,
                now,
            )
        self.assertEqual(self.context.window_manager.xrwb_settings.hand_status, "")

    def test_generation_change_or_missing_hand_never_releases(self):
        gate = gestures.ImagePinch()
        for seq, ratio, now in ((1, 0.8, 1.0), (2, 0.2, 1.02), (3, 0.2, 1.13)):
            gestures.process_frame(
                self.context,
                gestures.read_frame(BytesIO(packet(seq, seq, ratio))),
                gate,
                now,
            )
        gestures.process_frame(
            self.context,
            gestures.read_frame(BytesIO(packet(4, 4, 0.8, count=0))),
            gate,
            1.14,
        )
        self.assertEqual(calls, [])

    def test_candidate_swap_cancels_pinch(self):
        gate = gestures.ImagePinch()
        for seq, ratio, now in ((1, 0.8, 1.0), (2, 0.2, 1.02), (3, 0.2, 1.13)):
            gestures.process_frame(
                self.context,
                gestures.read_frame(BytesIO(packet(seq, seq, ratio))),
                gate,
                now,
            )
        gestures.process_frame(
            self.context,
            gestures.read_frame(BytesIO(packet(4, 4, 0.8, candidate=1))),
            gate,
            1.15,
        )
        self.assertEqual(calls, [])

    def test_one_fps_frames_require_continuous_pinch(self):
        gate = gestures.ImagePinch()
        for seq, ratio, now in (
            (1, 0.8, 1.0),
            (2, 0.2, 2.0),
            (3, 0.2, 3.0),
            (4, 0.8, 4.0),
        ):
            gestures.process_frame(
                self.context,
                gestures.read_frame(BytesIO(packet(seq, seq, ratio))),
                gate,
                now,
            )
        self.assertEqual(calls, [{"generation": 8}])
        gate.reset()
        for seq, ratio, now in (
            (5, 0.8, 5.0),
            (6, 0.2, 6.0),
            (7, 0.2, 9.0),
            (8, 0.8, 10.0),
        ):
            gestures.process_frame(
                self.context,
                gestures.read_frame(BytesIO(packet(seq, seq, ratio))),
                gate,
                now,
            )
        self.assertEqual(len(calls), 1)

    def test_physical_pinch_rejects_loss_stale_and_low_score(self):
        def feed(gate, seq, ratio, now, **kwargs):
            frame = gestures.read_frame(BytesIO(packet(seq, seq, ratio, **kwargs)))
            return gestures.process_frame(self.context, frame, gate, now)

        gate = gestures.ImagePinch(gestures.PHYSICAL_LOSS_SECONDS)
        for seq, ratio, now in ((1, .8, 1.0), (2, .2, 1.02), (3, .2, 1.13)):
            feed(gate, seq, ratio, now)
        self.assertTrue(gate.lost(1.7))
        self.assertFalse(feed(gate, 4, .8, 1.71))

        gate.reset()
        for seq, ratio, now in ((5, .8, 2.0), (6, .2, 2.02), (7, .2, 2.13)):
            feed(gate, seq, ratio, now)
        self.assertFalse(feed(gate, 7, .8, 2.15))
        self.assertFalse(feed(gate, 8, .8, 2.17))

        gate.reset()
        for seq, ratio, now in ((9, .8, 3.0), (10, .2, 3.02), (11, .2, 3.13)):
            feed(gate, seq, ratio, now)
        self.assertFalse(feed(gate, 12, .8, 3.15, palm=.7))
        self.assertFalse(feed(gate, 13, .8, 3.17))
        self.assertEqual(calls, [])

    def test_physical_operator_is_explicit_bounded_and_logs_aggregates_only(self):
        class FakeProcess:
            pid = 123
            stdout = BytesIO()
            stderr = BytesIO()
            terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

        self.context.window = object()
        wm = self.context.window_manager
        wm.xr_session_state.is_running = lambda _context: True
        wm.event_timer_add = lambda *_args, **_kwargs: object()
        wm.event_timer_remove = lambda _timer: None
        wm.modal_handler_add = lambda _operator: None
        event = types.SimpleNamespace(type="TIMER")
        operator = gestures.XRWB_OT_image_gesture_stream()
        operator.consent = True
        operator.physical_opacity_only = True
        operator.camera = "Index"
        operator.executable = str(ROOT / "xr_workbench" / "image_gestures.py")
        operator.ffmpeg = operator.executable
        with TemporaryDirectory() as temporary:
            install = Path(temporary) / ".xr_profile" / "scripts" / "addons" / "xr_workbench"
            install.mkdir(parents=True)
            variables = {
                "XRWB_PHYSICAL_INDEX_OPT_IN": "1",
                "XR_RUNTIME_JSON": "isolated-runtime",
                "XRWB_EXPECTED_INSTALL": str(install),
            }
            with patch.dict(os.environ, variables, clear=True):
                self.assertEqual(operator.execute(self.context), {"CANCELLED"})
                os.environ["XRWB_PHYSICAL_GESTURE_OPT_IN"] = "1"
                fake = FakeProcess()
                with patch.object(gestures, "Popen", return_value=fake) as launch, patch.object(
                    gestures, "Thread"
                ):
                    self.assertEqual(operator.execute(self.context), {"RUNNING_MODAL"})
                self.assertEqual(launch.call_args.args[0][1], "--consented-gesture-stream")
                operator.started_at = 1.0
                for seq, ratio, now in (
                    (1, .8, 1.0), (2, .2, 1.02), (3, .2, 1.13), (4, .8, 1.15)
                ):
                    operator.frames.put(
                        gestures.read_frame(BytesIO(packet(seq, seq, ratio)))
                    )
                    with patch.object(gestures, "monotonic", return_value=now):
                        self.assertEqual(operator.modal(self.context, event), {"RUNNING_MODAL"})
                self.assertEqual(operator.action_count, 1)
                self.assertEqual(len(calls), 1)
                operator._stop_at_deadline()
                self.assertTrue(fake.terminated)
                with patch.object(gestures, "monotonic", return_value=21.0):
                    self.assertEqual(operator.modal(self.context, event), {"CANCELLED"})
                self.assertTrue(fake.terminated)
                report = operator.report_path.read_text(encoding="utf-8")
                self.assertIn("reason=time_limit", report)
                self.assertIn("frames=4 actions=1", report)
                self.assertIn("images_saved=0 joint_traces_saved=0", report)
                self.assertNotIn("480.0", report)
                self.assertNotIn("Index", report)

    def test_card_pointer_hover_press_release_and_no_repeat(self):
        events = []
        bpy.ops.wm.xr_task_card_event = lambda _mode, **kw: events.append(kw) or {"FINISHED"}
        pointer = gestures.ImageCardPointer()
        for seq, ratio, now in ((1, .8, 1.0), (2, .2, 1.02),
                                (3, .2, 1.13), (4, .8, 1.15), (5, .8, 1.17)):
            frame = gestures.read_frame(BytesIO(packet(
                seq, seq, ratio, center_x=260, center_y=560)))
            released = gestures.process_card_frame(self.context, frame, pointer, now)
            self.assertEqual(released, seq == 4)
        self.assertEqual([event["phase"] for event in events],
                         ["MOVE", "MOVE", "MOVE", "PRESS", "RELEASE", "MOVE"])
        self.assertTrue(all(event["pointer_id"] == 2 and event["image_local"]
                            for event in events))
        self.assertTrue(all("ray_origin" not in event for event in events))
        self.assertAlmostEqual(events[0]["image_x"], 340 * 1000 / 960)
        self.assertAlmostEqual(events[0]["image_y"], 400 * 500 / 960)
        self.assertFalse(pointer.pressed)

    def test_card_pointer_loss_and_identity_swap_cancel_without_release(self):
        events = []
        bpy.ops.wm.xr_task_card_event = lambda _mode, **kw: events.append(kw) or {"FINISHED"}
        pointer = gestures.ImageCardPointer()
        for seq, ratio, now in ((1, .8, 1.0), (2, .2, 1.02), (3, .2, 1.13)):
            gestures.process_card_frame(self.context, gestures.read_frame(BytesIO(
                packet(seq, seq, ratio, center_x=260, center_y=560))), pointer, now)
        self.assertTrue(pointer.pressed)
        pointer.lost(4.0)
        self.assertEqual(events[-1]["phase"], "CANCEL")
        self.assertFalse(pointer.pressed)
        events.clear()
        for seq, ratio, now in ((4, .8, 5.0), (5, .2, 5.02), (6, .2, 5.13)):
            gestures.process_card_frame(self.context, gestures.read_frame(BytesIO(
                packet(seq, seq, ratio, center_x=260, center_y=560))), pointer, now)
        gestures.process_card_frame(self.context, gestures.read_frame(BytesIO(
            packet(7, 7, .8, candidate=1, center_x=260, center_y=560))),
            pointer, 5.15)
        self.assertEqual(events[-1]["phase"], "CANCEL")
        self.assertNotIn("RELEASE", [event["phase"] for event in events])

    def test_native_hud_add_cube_once_per_fresh_pinching_cycle(self):
        state = self.context.window_manager.xr_session_state
        state.is_running = lambda _context: True
        current = [hud_observation(1, .8)]
        state.camera_gesture_observation = lambda _context: current[0]
        gate = gestures.ImagePinch(gestures.PHYSICAL_LOSS_SECONDS)
        with TemporaryDirectory() as temporary:
            install = Path(temporary) / ".xr_profile" / "scripts" / "addons" / "xr_workbench"
            install.mkdir(parents=True)
            variables = {
                "XRWB_PHYSICAL_INDEX_OPT_IN": "1",
                "XRWB_PHYSICAL_GESTURE_OPT_IN": "1",
                "XRWB_INDEX_CAMERA_OVERLAY": "1",
                "XRWB_INDEX_CAMERA_CONSENT": "1",
                "XR_RUNTIME_JSON": "source-built-runtime",
                "XRWB_EXPECTED_INSTALL": str(install),
            }
            with patch.dict(os.environ, variables, clear=True), patch.object(
                gestures, "_add_cube_in_view3d", return_value=True
            ) as add:
                for seq, ratio, now in (
                    (1, .8, 1.0), (2, .2, 1.02),
                    (3, .2, 1.13), (4, .8, 1.15),
                ):
                    current[0] = hud_observation(seq, ratio)
                    self.assertEqual(
                        gestures.process_observation(self.context, gate, now), seq == 4
                    )
                self.assertFalse(gestures.process_observation(self.context, gate, 1.16))
                self.assertEqual(add.call_count, 1)
                current[0] = hud_observation(5, .8)
                self.assertFalse(gestures.process_observation(self.context, gate, 1.17))
                self.assertEqual(add.call_count, 1)

    def test_native_hud_rejects_unseen_stale_and_ambiguous_pinches(self):
        state = self.context.window_manager.xr_session_state
        state.is_running = lambda _context: True
        current = [hud_observation(1, .8)]
        state.camera_gesture_observation = lambda _context: current[0]
        gate = gestures.ImagePinch(gestures.PHYSICAL_LOSS_SECONDS)
        with TemporaryDirectory() as temporary:
            install = Path(temporary) / ".xr_profile" / "scripts" / "addons" / "xr_workbench"
            install.mkdir(parents=True)
            variables = {
                "XRWB_PHYSICAL_INDEX_OPT_IN": "1",
                "XRWB_PHYSICAL_GESTURE_OPT_IN": "1",
                "XRWB_INDEX_CAMERA_OVERLAY": "1",
                "XRWB_INDEX_CAMERA_CONSENT": "1",
                "XR_RUNTIME_JSON": "source-built-runtime",
                "XRWB_EXPECTED_INSTALL": str(install),
            }
            with patch.object(gestures, "_add_cube_in_view3d", return_value=True) as add:
                with patch.dict(os.environ, variables, clear=True):
                    for seq, ratio, now in ((1, .8, 1.0), (2, .2, 1.02), (3, .2, 1.13)):
                        current[0] = hud_observation(seq, ratio)
                        self.assertFalse(gestures.process_observation(self.context, gate, now))
                    for sample in (
                        hud_observation(4, .8, age_ms=501),
                        hud_observation(5, .8),
                        hud_observation(6, .2, tip_y=699),
                        hud_observation(7, .8),
                        hud_observation(8, .2),
                        hud_observation(9, .2, candidate=1),
                        hud_observation(10, .8, candidate=1),
                        hud_observation(11, .2, presence=.7),
                        hud_observation(12, .8),
                        hud_observation(13, .2),
                        hud_observation(14, .2, valid=0),
                        hud_observation(15, .8),
                    ):
                        current[0] = sample
                        self.assertFalse(gestures.process_observation(self.context, gate, 1.13 + sample[1] * .11))
                self.assertFalse(gestures.process_observation(self.context, gate, 4.0))
                self.assertEqual(add.call_count, 0)

    def test_native_hud_requires_isolated_opt_in_and_has_no_camera_child(self):
        state = self.context.window_manager.xr_session_state
        state.is_running = lambda _context: True
        state.camera_gesture_observation = lambda _context: hud_observation(1, .8)
        self.context.window = object()
        wm = self.context.window_manager
        wm.event_timer_add = lambda *_args, **_kwargs: object()
        wm.event_timer_remove = lambda _timer: None
        wm.modal_handler_add = lambda _operator: None
        operator = gestures.XRWB_OT_camera_hud_gesture()
        self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        with TemporaryDirectory() as temporary:
            install = Path(temporary) / ".xr_profile" / "scripts" / "addons" / "xr_workbench"
            install.mkdir(parents=True)
            variables = {
                "XRWB_PHYSICAL_INDEX_OPT_IN": "1",
                "XRWB_PHYSICAL_GESTURE_OPT_IN": "1",
                "XRWB_INDEX_CAMERA_OVERLAY": "1",
                "XRWB_INDEX_CAMERA_CONSENT": "1",
                "XR_RUNTIME_JSON": "source-built-runtime",
                "XRWB_EXPECTED_INSTALL": str(install),
            }
            with patch.dict(os.environ, variables, clear=True), patch.object(gestures, "Popen") as popen:
                self.assertEqual(operator.execute(self.context), {"RUNNING_MODAL"})
                self.assertEqual(operator.modal(
                    self.context, types.SimpleNamespace(type="TIMER")
                ), {"RUNNING_MODAL"})
                operator.cancel(self.context)
                popen.assert_not_called()
            personal = Path(temporary) / ".xr_addons_profile" / "scripts" / "addons" / "xr_workbench"
            personal.mkdir(parents=True)
            variables["XRWB_EXPECTED_INSTALL"] = str(personal)
            with patch.dict(os.environ, variables, clear=True):
                self.assertFalse(gestures._physical_hud_opted_in())
                with patch.dict(os.environ, {"XRWB_PERSONAL_ADDONS": "1"}):
                    self.assertTrue(gestures._physical_hud_opted_in())

    def test_native_hud_add_cube_requires_view3d_and_successful_blender_operator(self):
        self.context.window = types.SimpleNamespace(screen=types.SimpleNamespace(areas=[]))
        self.assertFalse(gestures._add_cube_in_view3d(self.context))
        region = types.SimpleNamespace(type="WINDOW")
        area = types.SimpleNamespace(type="VIEW_3D", regions=[region])
        self.context.window.screen.areas.append(area)
        self.context.temp_override = lambda **_kwargs: nullcontext()

        def add_cube(_mode):
            return {"FINISHED"}

        add_cube.poll = lambda: True
        with patch.object(bpy.ops, "xrwb", types.SimpleNamespace(add_cube=add_cube), create=True):
            self.assertTrue(gestures._add_cube_in_view3d(self.context))
            add_cube.poll = lambda: False
            self.assertFalse(gestures._add_cube_in_view3d(self.context))


if __name__ == "__main__":
    unittest.main()
