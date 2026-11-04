import importlib.util
from pathlib import Path
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    "xr_gestures_test", Path(__file__).resolve().parents[1] / "xr_workbench/gestures.py"
)
gestures = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gestures
spec.loader.exec_module(gestures)


class PinchTests(unittest.TestCase):
    def test_optical_edges_loss_rearm_and_stale_frames(self):
        gate = gestures.PinchGate()

        def sample(distance, timestamp, **overrides):
            fields = dict(
                distance_metres=distance,
                timestamp=timestamp,
                generation=1,
                optical=True,
                tracked=True,
            )
            fields.update(overrides)
            return gate.update(**fields)

        self.assertEqual(sample(0.02, 1), "neutral_required")
        self.assertEqual(sample(0.06, 2), "hover")
        self.assertEqual(sample(0.02, 3), "hover")
        self.assertEqual(sample(0.02, 3), "stale")
        self.assertEqual(sample(0.02, 3.1), "press")
        self.assertEqual(sample(0.025, 3.2), "hold")
        self.assertEqual(sample(0.06, 3.3), "release")
        sample(0.02, 4)
        self.assertEqual(sample(0.02, 4.1), "press")
        self.assertEqual(sample(0.02, 4.2, tracked=False), "cancel")
        self.assertEqual(sample(0.02, 4.3), "neutral_required")
        self.assertEqual(sample(0.06, 4.4, optical=False), "unavailable")
        self.assertEqual(sample(0.06, 5), "hover")
        sample(0.02, 6)
        self.assertEqual(sample(0.02, 6.1), "press")
        self.assertEqual(sample(0.06, 6.2, generation=2), "cancel")
        self.assertFalse(gate.armed)

    def test_invalid_thresholds_and_samples_fail_closed(self):
        with self.assertRaises(ValueError):
            gestures.PinchGate(close_metres=float("nan"))
        gate = gestures.PinchGate()
        result = gate.update(
            distance_metres=float("nan"),
            timestamp=1,
            generation=0,
            optical=True,
            tracked=True,
        )
        self.assertEqual(result, "unavailable")


if __name__ == "__main__":
    unittest.main()
