import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location(
    "xr_polish_sections_test", Path(__file__).resolve().parents[1] / "xr_workbench/polish.py"
)
polish = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = polish
spec.loader.exec_module(polish)


class MenuSectionTests(unittest.TestCase):
    def test_toggle_round_trips(self):
        text, opened = polish.toggle_key("", "SCULPT.brush")
        self.assertTrue(opened)
        text, opened = polish.toggle_key(text, "SCULPT.brush")
        self.assertFalse(opened)
        self.assertEqual(text, "")

    def test_unknown_keys_dropped_and_deduped(self):
        known = {"A.x", "B.y"}
        self.assertEqual(polish.parse_keys("A.x,OLD.z,A.x,B.y", known), ["A.x", "B.y"])
        self.assertEqual(polish.join_keys(["A.x", "A.x"]), "A.x")

    def test_third_pin_evicts_oldest(self):
        text, _p, _e = polish.pin_key("", "A")
        text, _p, _e = polish.pin_key(text, "B")
        text, pinned, evicted = polish.pin_key(text, "C")
        self.assertTrue(pinned)
        self.assertEqual(evicted, "A")
        self.assertEqual(polish.parse_keys(text), ["B", "C"])

    def test_falloff_radii_match_weights(self):
        for radius, weight in zip(polish.falloff_radii(1.0), (0.75, 0.5, 0.25)):
            self.assertAlmostEqual((1.0 - radius * radius) ** 2, weight, places=9)


if __name__ == "__main__":
    unittest.main()
