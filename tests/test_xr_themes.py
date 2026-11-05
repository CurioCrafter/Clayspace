import importlib.util
from pathlib import Path
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    "xr_polish_test", Path(__file__).resolve().parents[1] / "xr_workbench/polish.py"
)
polish = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = polish
spec.loader.exec_module(polish)


def luminance(rgb):
    def channel(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class ThemeTests(unittest.TestCase):
    def test_every_theme_is_complete_and_labels_are_readable(self):
        keys = set(polish.THEMES["DEFAULT"][2])
        for name, (_label, _description, values) in polish.THEMES.items():
            with self.subTest(theme=name):
                self.assertEqual(set(values), keys)
                alpha = values["PLATE_ALPHA"]
                # The plate is translucent: check it over a black and a white scene.
                for behind in ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)):
                    plate = tuple(alpha * p + (1.0 - alpha) * s for p, s in zip(values["PLATE"], behind))
                    for label in ("TEXT", "MUTED", "ACCENT"):
                        self.assertGreaterEqual(contrast(values[label], plate), 4.5, (label, behind))
                self.assertGreaterEqual(contrast(values["TEXT"], values["WHEEL_CURRENT"]), 4.5)

    def test_apply_theme_switches_live_and_falls_back(self):
        self.assertEqual(polish.apply_theme("LIGHT"), "LIGHT")
        self.assertEqual(polish.TEXT, polish.THEMES["LIGHT"][2]["TEXT"])
        self.assertEqual(polish.LEVEL_COLOURS["ERROR"], polish.THEMES["LIGHT"][2]["DANGER"])
        self.assertEqual(polish.apply_theme("NOPE"), "DEFAULT")
        self.assertEqual(polish.PLATE_ALPHA, 0.84)


if __name__ == "__main__":
    unittest.main()
