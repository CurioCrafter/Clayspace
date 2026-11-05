import importlib.util
import math
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location(
    "xr_growth_test", Path(__file__).resolve().parents[1] / "xr_workbench/growth.py")
growth = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = growth
spec.loader.exec_module(growth)


def all_points(lines):
    return [p for points, _radii in lines for p in points]


class GrowthGenerators(unittest.TestCase):
    def test_same_seed_same_shape_different_seed_differs(self):
        a = growth.grow((0, 0, 0), (0, 0, 1), 1.0, curl=0.6, seed=3, radius=0.02)
        b = growth.grow((0, 0, 0), (0, 0, 1), 1.0, curl=0.6, seed=3, radius=0.02)
        c = growth.grow((0, 0, 0), (0, 0, 1), 1.0, curl=0.6, seed=4, radius=0.02)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_finite_capped_and_radii_match_points(self):
        for cap in (50, 400, 2000):
            lines = growth.grow((0, 0, 0), (1, 0, 0), 2.0, curl=1.0, seed=1, radius=0.03, max_points=cap, split=0.3)
            self.assertLessEqual(len(all_points(lines)), cap)
            self.assertTrue(all(math.isfinite(v) for p in all_points(lines) for v in p))
            self.assertTrue(all(len(points) == len(radii) for points, radii in lines))
        self.assertGreater(len(lines), 1, "a high split rate makes branches")

    def test_vine_projection_keeps_points_at_radius_from_a_sphere(self):
        def project(p):  # Unit sphere.
            n = growth._norm(p)
            return n, n
        lines = growth.grow((0, 0, 1), (1, 0, 0), 2.0, curl=0.5, seed=2, radius=0.02, project=project, max_points=600)
        for points, radii in lines:
            for p, r in zip(points, radii):
                self.assertAlmostEqual(math.sqrt(growth._dot(p, p)), 1.0 + r, places=6)

    def test_spiral_ends_at_requested_height(self):
        (points, radii), = growth.spiral((1, 2, 3), (0, 0, 2), 0.5, turns=3, size=0.1, thickness=0.01)
        axis_end = growth._dot(growth._sub(points[-1], (1, 2, 3)), (0, 0, 1))
        self.assertAlmostEqual(axis_end, 0.5, places=9)
        self.assertEqual(len(points), len(radii))
        shell, = growth.spiral((0, 0, 0), (0, 0, 1), 1.0, 4, 0.05, 0.01, shell=True)
        self.assertGreater(math.hypot(*shell[0][-1][:2]), math.hypot(*shell[0][0][:2]))


if __name__ == "__main__":
    unittest.main()
