import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location(
    "xr_contact_test", Path(__file__).resolve().parents[1] / "xr_workbench/contact.py")
contact = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contact
spec.loader.exec_module(contact)

UP = (0.0, 0.0, 1.0)
R = 0.006


class ResolvePush(unittest.TestCase):
    def push(self, z, sink=0.0, cut=False):
        return contact.resolve_push((0, 0, z), (0, 0, 0), UP, R, sink, cut)

    def test_gap_and_touching_do_not_push(self):
        self.assertTrue(np.allclose(self.push(0.02), 0))
        self.assertTrue(np.allclose(self.push(R), 0))  # sphere just resting

    def test_hard_inside_is_pushed_onto_surface(self):
        offset = self.push(-0.01)
        self.assertAlmostEqual(-0.01 + offset[2], R)  # tip centre one radius above the surface
        self.assertAlmostEqual(offset[0], 0)

    def test_clay_sinks_6mm(self):
        offset = self.push(-0.01, sink=0.006)
        self.assertAlmostEqual(-0.01 + offset[2], R - 0.006)

    def test_cut_held_passes_through(self):
        self.assertTrue(np.allclose(self.push(-0.01, cut=True), 0))

    def test_big_push_dropped(self):
        self.assertIsNone(self.push(-0.2))


class Helpers(unittest.TestCase):
    def test_depth_pressure_clamps(self):
        self.assertEqual(contact.depth_pressure(-0.01), 0.0)
        self.assertAlmostEqual(contact.depth_pressure(0.01), 0.5)
        self.assertEqual(contact.depth_pressure(0.5), 1.0)

    def test_tangential_speed_removes_normal_part(self):
        speed, into = contact.tangential_speed((0, 0, 0), (0.003, 0.004, -0.002), 0.01, UP)
        self.assertAlmostEqual(speed, 0.5)
        self.assertAlmostEqual(into, 0.2)
        self.assertIsNone(contact.tangential_speed((0, 0, 0), (1, 0, 0), 0.5, UP))
        speed, _ = contact.tangential_speed((0, 0, 0), (0.006, 0, 0), 0.01, UP, units=2.0)
        self.assertAlmostEqual(speed, 0.3)

    def test_capsule_samples_count_and_spacing(self):
        self.assertEqual(contact.capsule_samples((0, 0, 0), UP, 0.0, R).shape, (1, 3))
        s = contact.capsule_samples((0, 0, 0), UP, 0.03, 0.006)
        self.assertEqual(len(s), 6)
        self.assertTrue(np.allclose(np.diff(s[:, 2]), 0.006))
        long = contact.capsule_samples((0, 0, 0), UP, 0.16, 0.002)
        self.assertEqual(len(long), 6)
        self.assertAlmostEqual(long[-1, 2], 0.16)

    def test_quat_axis_and_box_gap(self):
        # 90 deg about X turns +Z into -Y.
        h = 2 ** -0.5
        self.assertTrue(np.allclose(contact.quat_axis((h, h, 0, 0)), (0, -1, 0)))
        gaps = contact.box_gap(np.array([[0, 0, 0], [5, 5, 5]]), np.array([[1, 1, 1], [6, 6, 6]]),
                               np.array([0.5, 0.5, 2.0]))
        self.assertAlmostEqual(gaps[0], 1.0)
        self.assertGreater(gaps[1], 4)

    def test_nudge_step_capped(self):
        self.assertTrue(np.allclose(contact.nudge_step(UP, 0.5, 1.0), (0, 0, -0.02)))
        self.assertTrue(np.allclose(contact.nudge_step(UP, 5.0, 2.0), (0, 0, -0.1)))


if __name__ == "__main__":
    unittest.main()
