"""CLI consent checks only: never initialize SteamVR or touch a setting."""
from pathlib import Path
import subprocess
import sys
import unittest


class SteamVRSettingTests(unittest.TestCase):
    def test_mutations_require_consent_and_targets_are_exclusive(self):
        script = Path(__file__).parents[1] / "scripts" / "steamvr_camera_setting.py"
        for args in (("--camera", "on"), ("--direct-mode", "off"),
                     ("--camera", "status", "--direct-mode", "status")):
            result = subprocess.run([sys.executable, str(script), *args],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("OpenVR", result.stderr)
            self.assertTrue("--consented" in result.stderr or "not allowed" in result.stderr)


if __name__ == "__main__":
    unittest.main()
