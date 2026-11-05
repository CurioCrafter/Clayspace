import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location(
    "xr_contact_tools_test", Path(__file__).resolve().parents[1] / "xr_workbench/contact_tools.py")
ct = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ct
spec.loader.exec_module(ct)


class ContactToolMaths(unittest.TestCase):
    def test_knife_plane_flat_points_and_collinear_fallback(self):
        rng = np.random.default_rng(2)
        flat = np.c_[rng.uniform(-1, 1, (30, 2)), np.zeros(30)]
        centre, normal = ct.knife_plane(flat)
        self.assertAlmostEqual(abs(normal[2]), 1.0, places=6)
        line = np.c_[np.linspace(0, 1, 10), np.zeros(10), np.zeros(10)]
        centre, normal = ct.knife_plane(line, (0, 0, 1), (1, 0, 0))
        self.assertAlmostEqual(abs(normal[1]), 1.0, places=6)
        self.assertIsNone(ct.knife_plane(line))
        self.assertIsNone(ct.knife_plane(np.zeros((4, 3)), (0, 0, 1), (1, 0, 0)))

    def test_groove_profile_centre_and_edge(self):
        for shape in ("U", "V"):
            self.assertAlmostEqual(float(ct.groove_profile(0.0, shape)), 1.0)
            self.assertAlmostEqual(float(ct.groove_profile(1.0, shape)), 0.0)
            self.assertAlmostEqual(float(ct.groove_profile(-1.0, shape)), 0.0)

    def test_rake_profile_periodic_with_five_peaks(self):
        x = np.linspace(-0.0199, 0.0199, 4001)
        y = ct.rake_profile(x, 0.008, 5)
        peaks = np.flatnonzero((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:]) & (y[1:-1] > 0.9)) + 1
        self.assertEqual(len(peaks), 5)
        self.assertTrue(np.allclose(ct.rake_profile(0.0, 0.008), ct.rake_profile(0.008, 0.008)))
        self.assertEqual(float(ct.rake_profile(0.03, 0.008)), 0.0)

    def test_mallet_depth_rises_and_caps(self):
        self.assertLess(ct.mallet_depth(0.3), ct.mallet_depth(1.5))
        self.assertEqual(ct.mallet_depth(100.0), 0.02)
        self.assertEqual(ct.mallet_depth(0.0), 0.0005)

    def test_drill_step_accumulates_and_stops_at_limit(self):
        depth = 0.0
        for _ in range(10):
            depth = ct.drill_step(depth, 1.0, 0.1, 1.0)
        self.assertAlmostEqual(depth, 0.02)
        self.assertEqual(ct.drill_step(0.5, 1.0, 10.0, 0.51), 0.51)
        self.assertEqual(ct.drill_step(0.1, 0.0, 1.0, 1.0), 0.1)


if __name__ == "__main__":
    unittest.main()
