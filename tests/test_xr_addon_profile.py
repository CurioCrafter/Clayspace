"""Snapshot isolation checks: no Blender or user add-on code is imported."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "prepare_xr_addons_profile", Path(__file__).resolve().parents[1] / "scripts/prepare_xr_addons_profile.py"
)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class SnapshotTest(unittest.TestCase):
    def test_personal_loader_defers_services_and_restores_callbacks(self):
        helper_path = Path(__file__).resolve().parents[1] / "scripts/xr_personal_addons.py"
        for fail_read in (False, True):
            with self.subTest(fail_read=fail_read), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                private = root / ".xr_addons_profile"
                (private / "config").mkdir(parents=True)
                pref = private / "config/userpref.blend"
                pref.write_bytes(b"private-preferences")
                source = root / "original"
                (source / "config").mkdir(parents=True)
                (source / "config/userpref.blend").write_bytes(pref.read_bytes())
                marker = {"source_unchanged": True, "source_profile": str(source),
                          "destination_profile": str(private), "status": "snapshot-ready",
                          "source_preferences_sha256": profile.sha256(pref)}
                (private / profile.READY_FILE).write_text(json.dumps(marker))

                class Addons(list):
                    def get(self, name):
                        return next((item for item in self if item.module == name), None)

                addons = Addons(SimpleNamespace(module=name) for name in ["example", *profile.KNOWN_SERVICE_ADDONS])
                events = []
                def reset(**_kw):
                    events.append(("reset", [item.module for item in addons]))

                def enable(*args, **_kw):
                    events.append(("enable", args))

                def refresh(**_kw):
                    events.append(("refresh",))
                fake_addon_utils = SimpleNamespace(reset_all=reset, enable=enable)
                fake_utils = SimpleNamespace(
                    user_resource=lambda kind: str(private / {
                        "CONFIG": "config", "SCRIPTS": "scripts", "EXTENSIONS": "extensions",
                        "DATAFILES": "datafiles", "AUTOSAVE": "autosave",
                    }[kind]),
                    refresh_script_paths=refresh,
                )

                def read_preferences():
                    fake_addon_utils.reset_all()
                    fake_addon_utils.enable("blender_bridge_addon")
                    fake_utils.refresh_script_paths()
                    self.assertEqual(events, [])
                    if fail_read:
                        raise RuntimeError("simulated preferences failure")

                fake_bpy = SimpleNamespace(
                    utils=fake_utils,
                    ops=SimpleNamespace(wm=SimpleNamespace(read_userpref=read_preferences)),
                    context=SimpleNamespace(preferences=SimpleNamespace(
                        addons=addons, use_preferences_save=True,
                        filepaths=SimpleNamespace(use_scripts_auto_execute=True),
                    )),
                )
                imports = {"bpy": fake_bpy, "addon_utils": fake_addon_utils,
                           "prepare_xr_addons_profile": profile,
                           "example": SimpleNamespace(__addon_enabled__=True)}
                helper_spec = importlib.util.spec_from_file_location("personal_loader_test", helper_path)
                helper = importlib.util.module_from_spec(helper_spec)
                with patch.dict(sys.modules, imports), patch.dict(os.environ, {"BLENDER_USER_RESOURCES": str(private)}):
                    helper_spec.loader.exec_module(helper)
                    helper.__file__ = str(root / "scripts/xr_personal_addons.py")
                    with patch.object(helper, "inspect_preferences", return_value={}):
                        if fail_read:
                            with self.assertRaisesRegex(RuntimeError, "simulated preferences failure"):
                                helper.load_personal_addons()
                        else:
                            report = helper.load_personal_addons()
                            self.assertEqual(report["requested"], ["example"])
                            self.assertEqual(report["failed"], [])
                            self.assertTrue(report["original_preferences_unchanged"])
                            self.assertEqual(set(report["manual_enable_services"]), profile.KNOWN_SERVICE_ADDONS)
                            self.assertEqual(events, [("refresh",), ("reset", ["example"])])
                        original_resource = fake_utils.user_resource
                        for redirected in ("CONFIG", "SCRIPTS", "EXTENSIONS", "DATAFILES"):
                            with patch.object(fake_utils, "user_resource", side_effect=lambda kind: (
                                str(source / kind.lower()) if kind == redirected else original_resource(kind)
                            )):
                                with self.assertRaisesRegex(RuntimeError, "private .* directory"):
                                    helper.load_personal_addons()
                self.assertIs(fake_addon_utils.enable, enable)
                self.assertIs(fake_addon_utils.reset_all, reset)
                self.assertIs(fake_utils.refresh_script_paths, refresh)

    def test_isolated_snapshot_and_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "config").mkdir()
            prefs = source / "config/userpref.blend"
            prefs.write_bytes(b"private-preferences")
            (source / "config/startup.blend").write_bytes(b"never-copy-scene")
            addon = source / "scripts/addons/example"
            addon.mkdir(parents=True)
            (addon / "__init__.py").write_text("raise RuntimeError('do not import me')")
            (addon / "__pycache__").mkdir()
            (addon / "__pycache__/cached.pyc").write_bytes(b"skip")
            destination = root / "snapshot"
            inspected = {"enabled_modules": ["example"], "repositories": [], "external_asset_library_count": 0}
            with patch.object(profile, "inspect_preferences", return_value=inspected):
                result = profile.prepare_profile(source, destination, allowed_root=root)
            self.assertTrue(result["source_unchanged"])
            self.assertEqual(result["installed_modules"], ["example"])
            self.assertEqual(result["copied_files"], 2)
            self.assertEqual(prefs.read_bytes(), b"private-preferences")
            self.assertEqual((destination / "config/userpref.blend").read_bytes(), prefs.read_bytes())
            self.assertFalse((destination / "config/startup.blend").exists())
            self.assertFalse((destination / "scripts/addons/example/__pycache__").exists())
            self.assertEqual(json.loads((destination / profile.READY_FILE).read_text())["status"], "snapshot-ready")
            for rejected in (root, destination, source / "nested", root.parent / "escape"):
                with self.assertRaises(ValueError):
                    profile.prepare_profile(source, rejected, allowed_root=root)
            with patch.object(profile, "inspect_preferences", side_effect=ValueError("external path")):
                with self.assertRaises(ValueError):
                    profile.prepare_profile(source, root / "blocked", allowed_root=root)
            self.assertFalse((root / "blocked").exists())

    def test_reparse_entries_are_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "config").mkdir(parents=True)
            (source / "config/userpref.blend").write_bytes(b"private")
            (source / "scripts").mkdir()
            suspicious = source / "scripts/linked"
            suspicious.mkdir()
            (suspicious / "do-not-copy.py").write_text("secret")
            original = profile.is_link
            metadata = {"enabled_modules": [], "repositories": [], "external_asset_library_count": 0}
            with patch.object(profile, "is_link", side_effect=lambda path: path == suspicious or original(path)):
                with patch.object(profile, "inspect_preferences", return_value=metadata):
                    result = profile.prepare_profile(source, root / "snapshot", allowed_root=root)
            self.assertFalse((root / "snapshot/scripts/linked").exists())
            self.assertIn({"path": "scripts/linked", "reason": "link-or-junction"}, result["skipped_entries"])


if __name__ == "__main__":
    unittest.main()
