"""Pure-numpy tests for the sculpt brush maths (no bpy). Loads the module by path."""
import importlib.util
import math
from pathlib import Path
import unittest

import numpy as np

SPEC = importlib.util.spec_from_file_location(
    "sculpt_math", Path(__file__).resolve().parents[1] / "xr_workbench" / "sculpt_math.py")
sm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sm)


class ChunkTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(3)
        pts = rng.uniform(-1, 1, (20000, 3))
        self.pts = pts[np.argsort(pts[:, 2] * 8 + pts[:, 0])]

    def test_candidates_are_a_superset_and_smaller_than_all(self):
        chunks = sm.Chunks(self.pts)
        centre = np.array([0.1, 0.2, 0.3])
        ids = chunks.candidates(centre, 0.2)
        near = np.flatnonzero(np.linalg.norm(self.pts - centre, axis=1) < 0.2)
        self.assertTrue(set(near) <= set(ids))
        self.assertLess(len(ids), len(self.pts))

    def test_refresh_keeps_moved_points_findable(self):
        chunks = sm.Chunks(self.pts)
        moved = np.arange(100, 160)
        self.pts[moved] += np.array([2.0, 0.0, 0.0])
        chunks.refresh(self.pts, moved)
        ids = chunks.candidates(self.pts[130], 0.01)
        self.assertIn(130, ids)

    def test_empty_when_far(self):
        self.assertEqual(len(sm.Chunks(self.pts).candidates(np.array([9.0, 9.0, 9.0]), 0.1)), 0)


class SpanTests(unittest.TestCase):
    def test_spans_cover_every_point_the_candidates_do_and_merge_neighbours(self):
        pts = np.linspace(0.0, 1.0, 40_000)[:, None] * np.array([1.0, 0.0, 0.0])
        chunks = sm.Chunks(pts)
        centre = np.array([0.5, 0.0, 0.0])
        spans = chunks.spans(centre, 0.05)
        covered = np.concatenate([np.arange(a, b) for a, b in spans])
        self.assertTrue(set(chunks.candidates(centre, 0.05)) <= set(covered))
        self.assertLess(len(covered), len(pts))
        self.assertEqual(chunks.spans(np.array([9.0, 9.0, 9.0]), 0.1), [])
        wide = chunks.spans(centre, 2.0)
        self.assertEqual(wide, [(0, len(pts))])  # Adjacent chunks merge into one run.


class CopyTests(unittest.TestCase):
    def test_radial_and_mirror_counts_and_cap(self):
        eye = np.eye(4)
        self.assertEqual(len(sm.brush_copies(eye, eye, False, 0)), 1)
        self.assertEqual(len(sm.brush_copies(eye, eye, True, 0)), 2)
        self.assertEqual(len(sm.brush_copies(eye, eye, True, 6)), 12)
        self.assertEqual(len(sm.brush_copies(eye, eye, True, 99)), 24)

    def test_radial_rotates_about_local_z(self):
        copies = sm.brush_copies(np.eye(4), np.eye(4), False, 4)
        point = np.array([1.0, 0.0, 0.5, 1.0])
        np.testing.assert_allclose((copies[1] @ point)[:3], [0.0, 1.0, 0.5], atol=1e-12)

    def test_mirror_flips_x_in_object_space(self):
        world = np.eye(4)
        world[:3, 3] = (5, 0, 0)
        copies = sm.brush_copies(world, np.linalg.inv(world), True, 0)
        np.testing.assert_allclose((copies[1] @ np.array([6.0, 1.0, 2.0, 1.0]))[:3], [4.0, 1.0, 2.0], atol=1e-12)


class BrushTests(unittest.TestCase):
    def test_snake_drags_with_the_brush_and_ignores_zero_weight(self):
        pts = np.array([[0.0, 0.0, 0.0], [0.01, 0.0, 0.0], [1.0, 0.0, 0.0]])
        w = np.array([1.0, 0.5, 0.0])
        sm.snake(pts, slice(None), w, np.zeros(3), np.array([0.0, 0.0, 0.02]), 0.5, 0.05)
        self.assertAlmostEqual(pts[0, 2], 0.02)
        self.assertAlmostEqual(pts[1, 2], 0.01)
        self.assertEqual(pts[2, 2], 0.0)
        self.assertLess(pts[1, 0], 0.01)  # Pinched toward the axis.

    def test_clamp_delta(self):
        out = sm.clamp_delta(np.array([1.0, 0.0, 0.0]), 0.1)
        self.assertAlmostEqual(float(np.linalg.norm(out)), 0.1)
        small = np.array([0.1, 0.0, 0.0])
        np.testing.assert_array_equal(sm.clamp_delta(small, 0.1), small)

    def test_crease_cuts_and_pinches(self):
        pts = np.array([[0.02, 0.0, 0.0], [0.0, 0.0, 0.0]])
        sm.crease(pts, slice(None), np.ones(2), np.zeros(3), np.array([0.0, 0.0, 1.0]), 0.01, 0.5, 1.0, 0.05)
        self.assertLess(pts[1, 2], 0.0)
        self.assertLess(pts[0, 0], 0.02)
        pts = np.zeros((1, 3))
        sm.crease(pts, slice(None), np.ones(1), np.zeros(3), np.array([0.0, 0.0, 1.0]), 0.01, 0.5, -1.0, 0.05)
        self.assertGreater(pts[0, 2], 0.0)  # Dig makes a ridge.

    def test_smooth_average_matches_naive(self):
        rng = np.random.default_rng(5)
        pts = rng.uniform(size=(30, 3))
        edges = np.array([(i, (i + 1) % 30) for i in range(30)] + [(i, (i + 7) % 30) for i in range(0, 30, 3)])
        ids = np.array([2, 5, 11, 29])
        got = sm.smooth_average(pts, ids, edges)
        for row, v in enumerate(ids):
            nb = [b if a == v else a for a, b in edges if v in (a, b)]
            np.testing.assert_allclose(got[row], pts[nb].mean(axis=0))


class TouchToolMathTests(unittest.TestCase):
    """0.21 helpers: press-to-build, cone spray, stamp footprints, extract thickness, seam paths."""

    def test_press_floor_scales_with_time_and_units(self):
        self.assertAlmostEqual(sm.press_floor(0.1, 1.0), 0.002)
        self.assertAlmostEqual(sm.press_floor(0.1, 2.0), 0.004)
        self.assertEqual(sm.press_floor(-1.0, 1.0), 0.0)

    def test_rub_speed_raises_smoothing_from_half_to_full(self):
        self.assertAlmostEqual(sm.smooth_speed_factor(0.0, 0.3), 0.5)
        self.assertAlmostEqual(sm.smooth_speed_factor(0.3, 0.3), 1.0)
        self.assertAlmostEqual(sm.smooth_speed_factor(9.0, 0.3), 1.0)

    def test_cone_rays_are_unit_inside_the_angle_and_rotate_with_phase(self):
        d = np.array([0.0, 0.0, -1.0])
        rays = sm.cone_directions(d, 0.3, 12, 0.0)
        np.testing.assert_allclose(np.linalg.norm(rays, axis=1), 1.0)
        self.assertTrue(np.all(rays @ d >= np.cos(0.3) - 1e-9))
        self.assertGreater(np.linalg.norm(sm.cone_directions(d, 0.3, 12, 0.7) - rays), 0.1)
        # Spread across the cone, not bunched on the axis.
        self.assertGreater((rays @ d).min(), 0.9)
        self.assertLess((rays @ d).min(), 0.99)

    def test_spray_spot_grows_with_distance_and_has_a_floor(self):
        half = math.atan(0.2)
        self.assertAlmostEqual(sm.spray_spot(0.5, half, 0.03), 0.1)
        self.assertAlmostEqual(sm.spray_spot(0.0, half, 0.03), 0.006)

    def test_stamp_footprints_are_positive_inside(self):
        pts = np.array([[0.0, 0.0], [0.09, 0.0], [0.11, 0.0], [0.0, 0.04]])
        square = sm.stamp_footprint("SQUARE", pts, (0.1, 0.05))
        self.assertTrue(np.all(square[[0, 1, 3]] >= 0.0) and square[2] < 0.0)
        circle = sm.stamp_footprint("CIRCLE", pts, (0.1, 0.1))
        self.assertAlmostEqual(circle[0], 0.1)
        self.assertLess(circle[2], 0.0)
        ring = sm.stamp_footprint("RING", pts, (0.1, 0.1), inner=0.5)
        self.assertLess(ring[0], 0.0)  # The hole in the middle.
        self.assertGreater(ring[1], 0.0)

    def test_edge_soft_is_a_smoothstep(self):
        np.testing.assert_allclose(sm.edge_soft(np.array([-1.0, 0.0, 0.5, 1.0, 2.0])), [0, 0, 0.5, 1, 1])

    def test_extract_thickness_is_one_mm_per_cm_whole_mm_and_clamped(self):
        self.assertEqual(sm.extract_thickness_mm(4.0, 0.0), 4.0)
        self.assertEqual(sm.extract_thickness_mm(4.0, 0.03), 7.0)  # 3 cm of hand = 3 mm more.
        self.assertEqual(sm.extract_thickness_mm(4.0, -0.2), 0.5)
        self.assertEqual(sm.extract_thickness_mm(4.0, 2.0), 50.0)

    def test_shortest_path_prefers_light_edges_and_respects_the_edge_limit(self):
        graph = {0: [(1, 0, 1.0), (2, 1, 5.0)], 1: [(0, 0, 1.0), (2, 2, 1.0)], 2: [(1, 2, 1.0), (0, 1, 5.0)]}
        near = lambda v: graph[v]  # noqa: E731
        self.assertEqual(sm.shortest_path(near, 0, 2), [0, 2])
        self.assertEqual(sm.shortest_path(near, 0, 0), [])
        line = {i: [(i + 1, i, 1.0)] + ([(i - 1, i - 1, 1.0)] if i else []) for i in range(100)}
        self.assertEqual(len(sm.shortest_path(lambda v: line[v], 0, 60)), 60)
        self.assertIsNone(sm.shortest_path(lambda v: line[v], 0, 70, max_edges=64))


if __name__ == "__main__":
    unittest.main()
