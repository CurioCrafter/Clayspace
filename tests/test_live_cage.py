import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest


spec = importlib.util.spec_from_file_location(
    "xr_live_cage_test", Path(__file__).resolve().parents[1] / "xr_workbench/live_cage.py"
)
live_cage = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = live_cage
spec.loader.exec_module(live_cage)


def mod(name, kind, shown=True, **values):
    return NS(name=name, type=kind, show_viewport=shown, **values)


class SettleTests(unittest.TestCase):
    def released(self):
        cage = live_cage.Cage(None, ["Subdivision"])
        cage.release(10.0, "/user/hand/right")
        return cage

    def test_no_exit_before_dwell_even_when_far(self):
        cage = self.released()
        self.assertFalse(cage.due(10.34, distance_m=5.0))
        self.assertTrue(cage.due(10.36, distance_m=5.0))

    def test_exit_at_two_seconds_when_near(self):
        cage = self.released()
        self.assertFalse(cage.due(11.99, distance_m=0.0))
        self.assertTrue(cage.due(12.0, distance_m=0.0))

    def test_regrab_while_settling_is_caged_again(self):
        cage = self.released()
        cage.grab()
        self.assertEqual(cage.state, live_cage.CAGED)
        self.assertFalse(cage.due(20.0, distance_m=5.0), "A held cage never restores")

    def test_hover_and_distance_never_change_state(self):
        cage = live_cage.Cage(None, ["Boolean"])
        for distance in (0.0, 0.05, 1.0, 0.0):
            self.assertFalse(cage.due(100.0, distance))
        self.assertEqual(cage.state, live_cage.CAGED)
        cage = self.released()
        cage.due(10.1, 5.0)
        self.assertEqual(cage.state, live_cage.SETTLING)


class HeavyTests(unittest.TestCase):
    def test_only_heavy_visible_modifiers_are_picked(self):
        obj = NS(data=NS(vertices=[0] * 10), modifiers=[
            mod("Mirror", "MIRROR"), mod("Subdivision", "SUBSURF"), mod("Hidden", "BOOLEAN", shown=False),
            mod("Bevel2", "BEVEL", segments=2), mod("Bevel3", "BEVEL", segments=3),
            mod("Array16", "ARRAY", count=16), mod("Array17", "ARRAY", count=17),
            mod("Solid", "SOLIDIFY"), mod("GN", "NODES")])
        self.assertEqual(live_cage.heavy_names(obj), ["Subdivision", "Bevel3", "Array17", "GN"])
        obj.data.vertices = [0] * 50_001
        self.assertIn("Solid", live_cage.heavy_names(obj))


if __name__ == "__main__":
    unittest.main()
