from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


package = types.ModuleType("xr_workbench")
package.__path__ = [str(ROOT / "xr_workbench")]
sys.modules.setdefault("xr_workbench", package)
fake_bpy = types.ModuleType("bpy")
fake_bpy.types = types.SimpleNamespace(Operator=object)
fake_bpy.props = types.SimpleNamespace(BoolProperty=lambda **_kwargs: None)
fake_bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace())
fake_bpy = sys.modules.setdefault("bpy", fake_bpy)
load_module("xr_workbench.gestures", ROOT / "xr_workbench" / "gestures.py")
hands = load_module("xr_workbench.hands", ROOT / "xr_workbench" / "hands.py")


def observation(
    hand="RIGHT",
    *,
    generation=2,
    timestamp=1.0,
    thumb=(0.0, 0.0, 0.0),
    index=(0.06, 0.0, 0.0),
    scale=1.0,
    center=(0.0, 0.0, 0.0),
):
    thumb = tuple(a + b for a, b in zip(center, thumb))
    index = tuple(a + b for a, b in zip(center, index))
    return hands.HandObservation(
        hand=hand,
        generation=generation,
        timestamp=timestamp,
        thumb_tip=thumb,
        index_proximal=(0.0, 0.0, 0.0),
        index_tip=index,
        viewer_scale=scale,
        navigation_signature=(
            scale,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ),
    )


class OpticalGestureTests(unittest.TestCase):
    def test_support_cancel_uses_the_primary_capture_thresholds(self):
        old_gate = hands._support_gate
        old_thresholds = hands._gesture_state.thresholds
        try:
            hands._gesture_state.thresholds = (.01, .02, .03)
            hands._prepare_support_cancel(object())
            self.assertEqual(
                (hands._support_gate.close_metres, hands._support_gate.open_metres,
                 hands._support_gate.dwell_seconds), (.01, .02, .03)
            )
        finally:
            hands._support_gate = old_gate
            hands._gesture_state.thresholds = old_thresholds

    def test_support_hand_loss_cancels_card_without_committing_primary(self):
        primary = hands._Capture("RIGHT", 2, "edit")
        old_capture, old_support, old_gate = (
            hands._capture, hands._support_capture, hands._support_gate
        )
        hands._capture = primary
        hands._support_capture = hands._Capture(
            "LEFT", 2, "support_cancel", surface_generation=10
        )
        hands._support_gate = hands.PinchGate()
        try:
            with patch.object(hands, "_read_observation", return_value=(None, "lost")):
                with patch.object(hands, "_task_card_event", return_value=True) as event:
                    self.assertFalse(hands._sample_support_cancel(object(), primary, 1.0))
                    event.assert_called_once()
                    self.assertEqual(event.call_args.args[2], "CANCEL")
                    self.assertTrue(event.call_args.kwargs["cancel_only"])
            self.assertIs(hands._capture, primary)
            self.assertIsNone(hands._support_capture)
        finally:
            hands._capture, hands._support_capture, hands._support_gate = (
                old_capture, old_support, old_gate
            )

    def test_support_hand_cancel_requires_neutral_and_does_not_take_scene_capture(self):
        class State:
            def task_card_info(self, _context):
                return (1, 10, 10, -1, 0, 85)

        context = types.SimpleNamespace(
            window_manager=types.SimpleNamespace(xr_session_state=State())
        )
        primary = hands._Capture(
            "RIGHT", 2, "edit", navigation_signature=observation().navigation_signature
        )
        old_capture, old_support, old_gate = (
            hands._capture, hands._support_capture, hands._support_gate
        )
        hands._capture = primary
        hands._support_capture = None
        hands._support_gate = hands.PinchGate()
        events = []

        def card_event(_context, hand, phase, generation, _ray, *, cancel_only=False):
            events.append((hand, phase, generation, cancel_only))
            if phase == "RELEASE":
                hands._capture = None
            return True

        try:
            with patch.object(hands, "_task_card_event", side_effect=card_event):
                self.assertFalse(hands._process_support_cancel(
                    context, primary, observation("LEFT", timestamp=1.0,
                                                  index=(.015, 0, 0))))
                self.assertEqual(events, [])  # Appearing pinched cannot arm.
                self.assertFalse(hands._process_support_cancel(
                    context, primary, observation("LEFT", timestamp=1.1)))
                self.assertFalse(hands._process_support_cancel(
                    context, primary, observation("LEFT", timestamp=1.2,
                                                  index=(.015, 0, 0))))
                self.assertFalse(hands._process_support_cancel(
                    context, primary, observation("LEFT", timestamp=1.26,
                                                  index=(.015, 0, 0))))
                self.assertIs(hands._capture, primary)
                self.assertTrue(hands._process_support_cancel(
                    context, primary, observation("LEFT", timestamp=1.3)))
                self.assertIsNone(hands._capture)
                self.assertEqual([event[1] for event in events],
                                 ["MOVE", "PRESS", "RELEASE"])
                self.assertTrue(all(event[3] for event in events))
        finally:
            hands._capture, hands._support_capture, hands._support_gate = (
                old_capture, old_support, old_gate
            )

    def test_distance_is_converted_from_world_units_to_physical_metres(self):
        self.assertAlmostEqual(observation(scale=2.0).pinch_metres, 0.03)

    def test_watchdog_uses_local_advance_not_xr_clock_epoch(self):
        watchdog = hands.OpticalStreamWatchdog()
        huge_xr_epoch = 9_000_000_000.0
        self.assertTrue(watchdog.observe("RIGHT", 1, huge_xr_epoch, 10.0)[0])
        self.assertTrue(watchdog.observe("RIGHT", 1, huge_xr_epoch, 10.25)[0])
        self.assertFalse(watchdog.observe("RIGHT", 1, huge_xr_epoch, 10.2501)[0])

    def test_watchdog_rejects_backward_time_until_generation_changes(self):
        watchdog = hands.OpticalStreamWatchdog()
        watchdog.observe("RIGHT", 4, 500.0, 1.0)
        self.assertFalse(watchdog.observe("RIGHT", 4, 499.0, 1.1)[0])
        self.assertTrue(watchdog.observe("RIGHT", 5, 2.0, 1.2)[0])

    def test_stable_lifecycle_epoch_accepts_advancing_sensor_frames(self):
        watchdog = hands.OpticalStreamWatchdog()
        self.assertTrue(watchdog.observe("LEFT", 22, 1000.0, 1.0)[0])
        self.assertTrue(watchdog.observe("LEFT", 22, 1000.02, 2.0)[0])
        self.assertEqual(watchdog.streams["LEFT"][0], 22)

    def test_one_hand_owns_capture_until_release(self):
        state = hands.OpticalGestureState()
        self.assertEqual(state.update(observation(timestamp=1.0)), "hover")
        self.assertEqual(
            state.update(observation(timestamp=1.1, index=(0.015, 0.0, 0.0))),
            "hover",
        )
        self.assertEqual(
            state.update(observation(timestamp=1.16, index=(0.015, 0.0, 0.0))),
            "press",
        )
        self.assertEqual(state.active_hand, "RIGHT")
        self.assertEqual(
            state.update(observation("LEFT", timestamp=1.2)),
            "ignored",
        )
        self.assertEqual(state.update(observation(timestamp=1.3)), "release")
        self.assertIsNone(state.active_hand)

    def test_generation_change_cancels_pressed_hand(self):
        state = hands.OpticalGestureState()
        state.update(observation(timestamp=1.0))
        state.update(observation(timestamp=1.1, index=(0.015, 0.0, 0.0)))
        state.update(observation(timestamp=1.16, index=(0.015, 0.0, 0.0)))
        result = state.update(
            observation(
                timestamp=1.2,
                generation=3,
                index=(0.015, 0.0, 0.0),
            )
        )
        self.assertEqual(result, "cancel")
        self.assertIsNone(state.active_hand)

    def test_thresholds_validate_and_freeze_during_capture(self):
        state = hands.OpticalGestureState()
        with self.assertRaises(ValueError):
            state.configure(0.04, 0.03, 0.05)
        state.configure(0.01, 0.02, 0.03)
        self.assertEqual(state.thresholds, (0.01, 0.02, 0.03))
        state.update(observation(timestamp=1.0, index=(0.03, 0.0, 0.0)))
        state.update(observation(timestamp=1.1, index=(0.005, 0.0, 0.0)))
        state.update(observation(timestamp=1.14, index=(0.005, 0.0, 0.0)))
        with self.assertRaises(RuntimeError):
            state.configure(0.012, 0.024, 0.04)

    def test_ray_is_normalized_and_degenerate_ray_rejected(self):
        origin, direction = observation(index=(0.0, 0.0, 2.0)).ray
        self.assertEqual(origin, (0.0, 0.0, 2.0))
        self.assertEqual(direction, (0.0, 0.0, 1.0))
        with self.assertRaises(ValueError):
            observation(index=(0.0, 0.0, 0.0)).ray


class TwoHandScaleTests(unittest.TestCase):
    def test_release_freezes_then_second_release_commits_once(self):
        state = hands.TwoHandScaleState()
        state.update(observation("RIGHT", timestamp=1.0, center=(-0.1, 0.0, 0.0)))
        state.update(observation("LEFT", timestamp=1.0, center=(0.1, 0.0, 0.0)))
        state.update(
            observation(
                "RIGHT", timestamp=1.1, index=(0.015, 0.0, 0.0), center=(-0.1, 0.0, 0.0)
            )
        )
        self.assertEqual(
            state.update(
                observation(
                    "RIGHT",
                    timestamp=1.16,
                    index=(0.015, 0.0, 0.0),
                    center=(-0.1, 0.0, 0.0),
                )
            ),
            "waiting_started",
        )
        state.update(
            observation(
                "LEFT", timestamp=1.17, index=(0.015, 0.0, 0.0), center=(0.1, 0.0, 0.0)
            )
        )
        state.update(
            observation(
                "RIGHT",
                timestamp=1.21,
                index=(0.015, 0.0, 0.0),
                center=(-0.1, 0.0, 0.0),
            )
        )
        self.assertEqual(
            state.update(
                observation(
                    "LEFT",
                    timestamp=1.23,
                    index=(0.015, 0.0, 0.0),
                    center=(0.1, 0.0, 0.0),
                )
            ),
            "begin",
        )
        self.assertAlmostEqual(state.scale_ratio(), 1.0)
        self.assertEqual(
            state.update(observation("RIGHT", timestamp=1.3, center=(-0.1, 0.0, 0.0))),
            "freeze",
        )
        state.update(
            observation(
                "RIGHT", timestamp=1.4, index=(0.015, 0.0, 0.0), center=(-0.1, 0.0, 0.0)
            )
        )
        self.assertEqual(
            state.update(
                observation(
                    "RIGHT",
                    timestamp=1.46,
                    index=(0.015, 0.0, 0.0),
                    center=(-0.1, 0.0, 0.0),
                )
            ),
            "finishing_no_resume",
        )
        self.assertEqual(
            state.update(observation("LEFT", timestamp=1.47, center=(0.1, 0.0, 0.0))),
            "commit",
        )
        self.assertEqual(state.phase, "cooldown")
        self.assertEqual(
            state.update(observation("RIGHT", timestamp=1.5, center=(-0.1, 0.0, 0.0))),
            "ready",
        )

    def test_incoherent_generation_and_small_separation_cancel(self):
        state = hands.TwoHandScaleState()
        for hand in hands.HANDS:
            state.update(
                observation(
                    hand,
                    timestamp=1.0,
                    center=(-0.01 if hand == "RIGHT" else 0.01, 0.0, 0.0),
                )
            )
        state.update(
            observation(
                "RIGHT",
                timestamp=1.1,
                index=(0.015, 0.0, 0.0),
                center=(-0.01, 0.0, 0.0),
            )
        )
        state.update(
            observation(
                "RIGHT",
                timestamp=1.16,
                index=(0.015, 0.0, 0.0),
                center=(-0.01, 0.0, 0.0),
            )
        )
        result = state.update(
            observation(
                "LEFT",
                timestamp=1.17,
                generation=3,
                index=(0.015, 0.0, 0.0),
                center=(0.01, 0.0, 0.0),
            )
        )
        self.assertEqual(result, "cancel")
        self.assertEqual(state.phase, "cooldown")

    def test_same_generation_but_too_close_cancels(self):
        state = hands.TwoHandScaleState()
        state.configure(0.020, 0.030, 0.0)
        state.update(observation("RIGHT", timestamp=1.0, center=(-0.01, 0.0, 0.0)))
        state.update(observation("LEFT", timestamp=1.0, center=(0.01, 0.0, 0.0)))
        self.assertEqual(
            state.update(
                observation(
                    "RIGHT",
                    timestamp=1.1,
                    index=(0.015, 0.0, 0.0),
                    center=(-0.01, 0.0, 0.0),
                )
            ),
            "waiting_started",
        )
        self.assertEqual(
            state.update(
                observation(
                    "LEFT",
                    timestamp=1.11,
                    index=(0.015, 0.0, 0.0),
                    center=(0.01, 0.0, 0.0),
                )
            ),
            "cancel",
        )

    def test_navigation_change_while_waiting_cancels(self):
        state = hands.TwoHandScaleState()
        state.configure(0.020, 0.030, 0.0)
        state.update(observation("RIGHT", timestamp=1.0))
        state.update(observation("LEFT", timestamp=1.0))
        self.assertEqual(
            state.update(observation("RIGHT", timestamp=1.1, index=(0.015, 0.0, 0.0))),
            "waiting_started",
        )
        self.assertEqual(
            state.update(
                observation(
                    "LEFT",
                    timestamp=1.11,
                    index=(0.015, 0.0, 0.0),
                    scale=2.0,
                )
            ),
            "cancel",
        )

    def test_first_hand_navigation_change_while_waiting_cancels(self):
        state = hands.TwoHandScaleState()
        state.configure(0.020, 0.030, 0.0)
        state.update(observation("RIGHT", timestamp=1.0))
        self.assertEqual(
            state.update(observation("RIGHT", timestamp=1.1, index=(0.015, 0.0, 0.0))),
            "waiting_started",
        )
        self.assertEqual(
            state.update(
                observation(
                    "RIGHT",
                    timestamp=1.2,
                    index=(0.015, 0.0, 0.0),
                    scale=2.0,
                )
            ),
            "cancel",
        )

    def test_repeated_timestamp_cannot_replace_accepted_pose(self):
        state = hands.TwoHandScaleState()
        accepted = observation("RIGHT", timestamp=2.0, center=(-0.1, 0.0, 0.0))
        state.observations["RIGHT"] = accepted
        state.gates["RIGHT"].generation = accepted.generation
        state.gates["RIGHT"].last_timestamp = accepted.timestamp
        repeated = observation("RIGHT", timestamp=2.0, center=(4.0, 0.0, 0.0))
        self.assertEqual(state.update(repeated), "stale")
        self.assertIs(state.observations["RIGHT"], accepted)

    def test_backward_timestamp_cancels_active_scale(self):
        state = hands.TwoHandScaleState(phase="active")
        current = observation("RIGHT", timestamp=4.0)
        state.generation = current.generation
        state.navigation_signature = current.navigation_signature
        state.gates["RIGHT"].generation = current.generation
        state.gates["RIGHT"].last_timestamp = current.timestamp
        self.assertEqual(
            state.update(observation("RIGHT", timestamp=3.9)),
            "cancel",
        )
        self.assertEqual(state.phase, "cooldown")

    def test_tracking_invalidation_never_commits(self):
        state = hands.TwoHandScaleState(phase="active")
        self.assertEqual(state.invalidate(), "cancel")
        self.assertEqual(state.phase, "cooldown")


class NativeContractTests(unittest.TestCase):
    class State:
        def __init__(self, source=1, flags=10, world_scale=2.0, tip_x=0.04):
            self.source = source
            self.flags = flags
            self.world_scale = world_scale
            self.tip_x = tip_x
            self.joint_calls = 0

        def hand_tracking_info(self, _context, *, hand):
            return True, True, self.source, 9, 800_000_000, 4

        def hand_tracking_world_transform(self, _context):
            return (
                self.world_scale,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            )

        def hand_joint_sample(self, _context, *, hand, joint):
            self.joint_calls += 1
            positions = {
                0: (0.0, 0.0, 0.0),
                5: (0.0, 0.0, 0.0),
                7: (0.0, 0.0, 0.0),
                10: (self.tip_x, 0.0, 0.0),
            }
            return (
                float(self.flags),
                *positions[joint],
                0.0,
                0.0,
                0.0,
                0.0,
                0.01,
            )

    @staticmethod
    def context(state):
        wm = types.SimpleNamespace(xr_session_state=state)
        return types.SimpleNamespace(window_manager=wm)

    def test_controller_source_is_never_accepted_as_optical(self):
        state = self.State(source=2)
        result, reason = hands._read_observation(self.context(state), "RIGHT", 10.0)
        self.assertIsNone(result)
        self.assertIn("not unobstructed optical", reason)
        self.assertEqual(state.joint_calls, 0)

    def test_tracked_position_flags_are_required_for_pinch_and_ray(self):
        state = self.State(flags=8)
        result, reason = hands._read_observation(self.context(state), "RIGHT", 10.0)
        self.assertIsNone(result)
        self.assertIn("incomplete", reason)

    def test_valid_native_tuple_retains_optical_generation_and_scale(self):
        state = self.State()
        result, reason = hands._read_observation(self.context(state), "RIGHT", 10.0)
        self.assertEqual(reason, "optical sample active")
        self.assertEqual(result.generation, 4)
        self.assertAlmostEqual(result.pinch_metres, 0.02)
        self.assertEqual(state.joint_calls, 3)

    def test_world_units_per_metre_includes_base_view_and_navigation_scale(self):
        state = self.State(world_scale=24.0, tip_x=0.48)
        result, _reason = hands._read_observation(self.context(state), "LEFT", 10.0)
        self.assertAlmostEqual(result.pinch_metres, 0.02)
        self.assertEqual(result.navigation_signature[0], 24.0)

    def test_current_drawn_task_card_is_probed_before_legacy_surface(self):
        calls = []

        class CardState(self.State):
            def task_card_info(self, _context):
                return 1, 8, 8, -1, 0, 100

        def card_event(_execution, **properties):
            calls.append(properties)
            return {"FINISHED"}

        fake_bpy.ops.wm.xr_task_card_event = card_event
        context = self.context(CardState())
        hit, generation = hands._task_card_hit(
            context,
            observation(timestamp=9.8),
        )
        self.assertTrue(hit)
        self.assertEqual(generation, 8)
        self.assertEqual(calls[0]["phase"], "MOVE")
        self.assertEqual(calls[0]["generation"], 8)


class CalibratedProviderTests(unittest.TestCase):
    def setUp(self):
        hands._capture = None
        hands._gesture_state.reset()
        hands._stream_watchdog.reset()

    def tearDown(self):
        hands._capture = None
        hands._gesture_state.reset()
        hands.set_optical_provider(None)
        if hasattr(fake_bpy.ops.wm, "xr_task_card_event"):
            del fake_bpy.ops.wm.xr_task_card_event

    class Session(NativeContractTests.State):
        def is_running(self, _context):
            return True

        def task_card_info(self, _context):
            return 1, 8, 8, -1, 0, 85

    class Provider:
        def __init__(self, signature):
            self.packet = {
                "source": "index_camera_optical",
                "space": "blender_world",
                "world_output": True,
                "active": True,
                "generation": 7,
                "timestamp": 1.0,
                "navigation_signature": signature,
                "thumb_tip": (0.0, 0.0, 0.0),
                "index_proximal": (0.0, 0.0, 0.0),
                "index_tip": (0.08, 0.0, 0.0),
            }

        def sample(self, _context, _hand):
            return self.packet

    def context(self):
        state = self.Session(world_scale=1.0)
        context = NativeContractTests.context(state)
        context.window_manager.xrwb_settings = types.SimpleNamespace(
            hand_status="", edit_tool="move"
        )
        return context

    def test_camera_relative_and_unqualified_samples_are_rejected(self):
        context = self.context()
        signature = hands._navigation_signature(
            context, context.window_manager.xr_session_state
        )
        provider = self.Provider(signature)
        hands.set_optical_provider(provider)
        provider.packet["space"] = "mercury_calibrated_stereo"
        self.assertIsNone(hands._read_observation(context, "RIGHT", 1.0)[0])
        provider.packet["space"] = "blender_world"
        provider.packet["source"] = "controller_inferred"
        self.assertIsNone(hands._read_observation(context, "RIGHT", 1.0)[0])
        provider.packet["source"] = "index_camera_optical"
        provider.packet["navigation_signature"] = (1.0,)
        self.assertIsNone(hands._read_observation(context, "RIGHT", 1.0)[0])

    def test_stalled_provider_frame_cannot_reenter_as_new(self):
        context = self.context()
        signature = hands._navigation_signature(
            context, context.window_manager.xr_session_state
        )
        provider = self.Provider(signature)
        hands.set_optical_provider(provider)
        self.assertIsNotNone(hands._read_observation(context, "RIGHT", 1.0)[0])
        self.assertIsNone(hands._read_observation(context, "RIGHT", 1.26)[0])
        self.assertIsNone(hands._read_observation(context, "RIGHT", 1.27)[0])
        provider.packet["timestamp"] = 1.28
        self.assertIsNotNone(hands._read_observation(context, "RIGHT", 1.28)[0])

    def test_qualified_pinch_routes_native_card_and_loss_cancels(self):
        context = self.context()
        signature = hands._navigation_signature(
            context, context.window_manager.xr_session_state
        )
        provider = self.Provider(signature)
        hands.set_optical_provider(provider)
        events = []
        fake_bpy.ops.wm.xr_task_card_event = lambda _mode, **kw: (
            events.append(kw["phase"]) or {"FINISHED"}
        )
        original_clock = hands.monotonic
        try:
            hands.monotonic = lambda: 1.0
            hands._sample_timer(context)
            provider.packet["timestamp"] = 1.02
            provider.packet["index_tip"] = (0.015, 0.0, 0.0)
            hands.monotonic = lambda: 1.02
            hands._sample_timer(context)
            provider.packet["timestamp"] = 1.08
            hands.monotonic = lambda: 1.08
            hands._sample_timer(context)
            self.assertEqual(hands._capture.kind, "task_card")
            self.assertIn("PRESS", events)
            provider.packet["active"] = False
            hands.monotonic = lambda: 1.10
            hands._sample_timer(context)
        finally:
            hands.monotonic = original_clock
        self.assertIsNone(hands._capture)
        self.assertEqual(events[-1], "CANCEL")
        self.assertNotIn("RELEASE", events)


class ScaleRoutingTests(unittest.TestCase):
    def setUp(self):
        hands._capture = None
        hands._two_hand_capture = None
        hands._two_hand_state.reset()
        hands._gesture_state.reset()

    def tearDown(self):
        hands._capture = None
        hands._two_hand_capture = None
        hands._two_hand_state.reset()
        hands._gesture_state.reset()

    @staticmethod
    def context():
        settings = types.SimpleNamespace(hand_status="")
        wm = types.SimpleNamespace(xrwb_settings=settings)
        return types.SimpleNamespace(window_manager=wm)

    def test_one_visible_hand_can_capture_native_card_in_scale_mode(self):
        current = {"RIGHT": observation("RIGHT", timestamp=1.0)}
        calls = []
        original_read = hands._read_observation
        original_hit = hands._task_card_hit
        original_event = hands._task_card_event
        original_surface = hands._surface_event
        try:
            hands._read_observation = lambda _context, hand, _now: (
                (current[hand], "active")
                if hand in current
                else (None, "left unavailable")
            )
            hands._task_card_hit = lambda _context, _observation: (True, 12)
            hands._task_card_event = (
                lambda _context, hand, phase, generation, _ray: calls.append(
                    (hand, phase, generation)
                )
                or True
            )
            hands._surface_event = lambda *_args, **_kwargs: self.fail(
                "native task-card capture must not fall through to legacy editor"
            )
            context = self.context()
            hands._sample_two_hand(context, 10.0)
            current["RIGHT"] = observation(
                "RIGHT", timestamp=1.1, index=(0.015, 0.0, 0.0)
            )
            hands._sample_two_hand(context, 10.1)
            current["RIGHT"] = observation(
                "RIGHT", timestamp=1.16, index=(0.015, 0.0, 0.0)
            )
            hands._sample_two_hand(context, 10.16)
        finally:
            hands._read_observation = original_read
            hands._task_card_hit = original_hit
            hands._task_card_event = original_event
            hands._surface_event = original_surface
        self.assertEqual(calls, [("RIGHT", "PRESS", 12)])
        self.assertEqual(hands._capture.kind, "task_card")

    def test_frozen_repinch_never_reaches_legacy_editor(self):
        right = observation(
            "RIGHT", timestamp=2.0, index=(0.015, 0.0, 0.0), center=(-0.1, 0, 0)
        )
        left = observation(
            "LEFT", timestamp=2.0, index=(0.015, 0.0, 0.0), center=(0.1, 0, 0)
        )
        state = hands._two_hand_state
        state.phase = "frozen"
        state.released_hand = "RIGHT"
        state.generation = 2
        state.navigation_signature = right.navigation_signature
        state.observations = {"RIGHT": right, "LEFT": left}
        state.gates["RIGHT"].generation = 2
        state.gates["RIGHT"].armed = True
        state.gates["RIGHT"].candidate_since = 1.9
        state.gates["RIGHT"].last_timestamp = 1.8
        state.gates["LEFT"].generation = 2
        state.gates["LEFT"].armed = True
        state.gates["LEFT"].pressed = True
        state.gates["LEFT"].last_timestamp = 1.8
        original_read = hands._read_observation
        original_hit = hands._task_card_hit
        original_surface = hands._surface_event
        legacy_calls = []
        try:
            hands._read_observation = lambda _context, hand, _now: (
                (right if hand == "RIGHT" else left),
                "active",
            )
            hands._task_card_hit = lambda _context, _observation: (False, 0)
            hands._surface_event = (
                lambda *args, **kwargs: legacy_calls.append((args, kwargs)) or True
            )
            hands._sample_two_hand(self.context(), 20.0)
        finally:
            hands._read_observation = original_read
            hands._task_card_hit = original_hit
            hands._surface_event = original_surface
        self.assertEqual(legacy_calls, [])
        self.assertIsNone(hands._capture)
        self.assertEqual(state.phase, "frozen")

    def test_stopped_native_session_cancels_active_two_hand_edit(self):
        class Transaction:
            cancelled = 0

            def cancel(self, _context, _owner):
                self.cancelled += 1

        class SessionState:
            disabled = False

            def is_running(self, _context):
                return False

            def hand_tracking_enable(self, _context, *, enabled):
                self.disabled = not enabled

        transaction = Transaction()
        session = SessionState()
        context = self.context()
        context.window_manager.xr_session_state = session
        hands._two_hand_capture = hands._TwoHandCapture(object(), transaction)
        hands._two_hand_state.phase = "active"
        hands._sample_timer(context)
        self.assertEqual(transaction.cancelled, 1)
        self.assertIsNone(hands._two_hand_capture)
        self.assertTrue(session.disabled)


if __name__ == "__main__":
    unittest.main()
