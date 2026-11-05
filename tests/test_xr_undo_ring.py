import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

fake_bpy = types.ModuleType("bpy")
fake_bpy.types = types.SimpleNamespace(Operator=object)
fake_bpy.props = types.SimpleNamespace(EnumProperty=lambda **_kwargs: None)
spec = importlib.util.spec_from_file_location(
    "xr_undo_ring_test", Path(__file__).resolve().parents[1] / "xr_workbench/undo_ring.py"
)
undo_ring = importlib.util.module_from_spec(spec)
with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
    spec.loader.exec_module(undo_ring)

# What WindowManager.print_undo_steps() prints (Blender 5.1), Windows line endings.
PRINTED = (
    "Undo 4 Steps (*: active, #=applied, M=memfile-active, S=skip)\r\n"
    "[    ]   0 {000001654DE37C88} type='Global Undo', name='Original'\r\n"
    "[   S]   1 {000001654DE37D00} type='Global Undo', name='Hidden'\r\n"
    "[* M ]   2 {000001654DE37C08} type='Global Undo', name='XR Move 'Bevel''\r\n"
    "[ #  ]   3 {000001654DE37E00} type='Edit Mesh', name='XR Extrude'\r\n"
)


class UndoRingTests(unittest.TestCase):
    def test_parse_keeps_stack_indices_and_drops_skipped_steps(self):
        self.assertEqual(undo_ring.parse_steps(PRINTED),
                         [(0, "Original", False), (2, "XR Move 'Bevel'", True), (3, "XR Extrude", False)])
        self.assertEqual(undo_ring.parse_steps("No undo steps recorded yet.\n"), [])

    def test_neighbours_stop_at_either_end(self):
        steps = undo_ring.parse_steps(PRINTED)
        self.assertEqual(undo_ring.neighbour(steps, -1), 0)
        self.assertEqual(undo_ring.neighbour(steps, 1), 3)
        self.assertIsNone(undo_ring.neighbour(steps[:2], 1))
        self.assertIsNone(undo_ring.neighbour(steps[1:], -1))
        self.assertIsNone(undo_ring.neighbour([], -1))

    def test_ring_puts_the_current_step_on_top(self):
        placed = undo_ring.layout(undo_ring.parse_steps(PRINTED))
        self.assertEqual([kind for _angle, _name, kind in placed], ["past", "current", "undone"])
        self.assertEqual(placed[1][0], 0.0)
        self.assertLess(placed[0][0], 0.0)  # Older steps anticlockwise, undone ones clockwise.
        self.assertAlmostEqual(placed[2][0], math.tau / undo_ring.VISIBLE)


if __name__ == "__main__":
    unittest.main()
