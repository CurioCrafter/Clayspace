"""Load a private profile while deferring the known remote-control add-ons."""

import json
import os
from pathlib import Path
import sys

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_xr_addons_profile import (  # noqa: E402
    inspect_preferences, KNOWN_SERVICE_ADDONS, _no_link_ancestors, sha256,
)


def load_personal_addons():
    root = Path(os.environ["BLENDER_USER_RESOURCES"]).resolve()
    expected = Path(__file__).resolve().parents[1] / ".xr_addons_profile"
    if root != expected:
        raise RuntimeError("Personal add-ons require the private XR profile")
    for resource in ("CONFIG", "SCRIPTS", "EXTENSIONS", "DATAFILES"):
        if Path(bpy.utils.user_resource(resource)).resolve() != root / resource.lower():
            raise RuntimeError(f"Personal add-ons require a private {resource} directory")
    _no_link_ancestors(root / "config/userpref.blend")
    marker = json.loads((root / "xr_addons_profile_ready.json").read_text(encoding="utf-8"))
    if (not marker.get("source_unchanged") or marker.get("status") != "snapshot-ready"
            or Path(marker["destination_profile"]).resolve() != root):
        raise RuntimeError("Private profile snapshot has not passed its integrity check")
    source = Path(marker["source_profile"]).resolve()
    if source.is_relative_to(root) or root.is_relative_to(source):
        raise RuntimeError("Original and private profiles must not overlap")
    original_preferences = source / "config/userpref.blend"
    _no_link_ancestors(original_preferences)
    original_hash = sha256(original_preferences)
    inspect_preferences(root / "config/userpref.blend")
    # read_userpref normally registers add-ons immediately. Defer registration so
    # saved service auto-start preferences cannot open listeners during startup.
    reset, enable = addon_utils.reset_all, addon_utils.enable
    refresh = bpy.utils.refresh_script_paths
    try:
        addon_utils.reset_all = lambda **_kw: None
        addon_utils.enable = lambda *_args, **_kw: None
        bpy.utils.refresh_script_paths = lambda **_kw: None
        bpy.ops.wm.read_userpref()
    finally:
        addon_utils.reset_all, addon_utils.enable = reset, enable
        bpy.utils.refresh_script_paths = refresh
    preferences = bpy.context.preferences
    preferences.use_preferences_save = False
    preferences.filepaths.use_scripts_auto_execute = False
    deferred = []
    for name in sorted(KNOWN_SERVICE_ADDONS):
        item = preferences.addons.get(name)
        if item is not None:
            preferences.addons.remove(item)
            deferred.append(name)
    requested = [item.module for item in preferences.addons]
    bpy.utils.refresh_script_paths()
    addon_utils.reset_all()
    loaded, failed = [], []
    for name in requested:
        module = sys.modules.get(name)
        (loaded if module and getattr(module, "__addon_enabled__", False) else failed).append(name)
    original_unchanged = sha256(original_preferences) == original_hash
    report = {"requested": requested, "loaded": loaded, "failed": failed,
              "manual_enable_services": deferred, "camera_requested": False,
              "headset_verified": False, "original_preferences_unchanged": original_unchanged,
              "original_preferences_sha256": original_hash,
              "original_profile_other_files_audited": False}
    (root / "addon-load-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"XRWB_PERSONAL_ADDONS loaded={len(loaded)} failed={len(failed)} "
          f"manual_services={len(deferred)} original_preferences_unchanged={int(original_unchanged)}", flush=True)
    if not original_unchanged:
        raise RuntimeError("Original preferences changed during loading; check for concurrent Blender activity")
    return report


if __name__ == "__main__":
    report = load_personal_addons()
    assert not report["failed"], "See the private addon-load-report.json for incompatible add-ons"
    print("XRWB_PERSONAL_ADDONS_SMOKE_PASS", flush=True)
