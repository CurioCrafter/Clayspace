import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "xr_workbench" / "curiomesh"
EXPECTED = {
    "__init__.py", "atlasq.py", "bridge.py", "metrics.py", "operators.py", "textures.py", "ui.py",
    "LICENSE", "blender_manifest.toml", "VENDORED.md",
    "triadq/__init__.py", "triadq/__main__.py", "triadq/engine.py", "triadq/io.py",
    "triadq/topology.py", "triadq/types.py",
}
spec = importlib.util.spec_from_file_location("xr_retopo_test", ROOT / "xr_workbench" / "retopo.py")
retopo = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = retopo
spec.loader.exec_module(retopo)


class VendorTests(unittest.TestCase):
    def test_files_match_archive_list(self):
        found = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts}
        self.assertEqual(found, EXPECTED)

    def test_source_commit_recorded(self):
        self.assertIn("fb4e15b", (VENDOR / "VENDORED.md").read_text())

    def test_target_snapping(self):
        self.assertEqual(retopo.snap_target(0), 500)
        self.assertEqual(retopo.snap_target(740), 500)
        self.assertEqual(retopo.snap_target(760), 1000)
        self.assertEqual(retopo.snap_target(99999), 20000)
        self.assertEqual(retopo.step_target(20000, 3), 20000)
        self.assertEqual(retopo.step_target(5000, -2), 4000)
        self.assertEqual(retopo.quads_label(8000), "~8,000 quads")


if __name__ == "__main__":
    unittest.main()
