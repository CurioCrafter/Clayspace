"""Snapshot the user's add-ons without importing their code or modifying their profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
READY_FILE = "xr_addons_profile_ready.json"
KNOWN_SERVICE_ADDONS = {"blender_bridge_addon", "bl_ext.user_default.mcp"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _no_link_ancestors(path: Path) -> None:
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            if is_link(item):
                raise ValueError("Profile paths must not traverse symbolic links or junctions")


def inspect_preferences(path: Path) -> dict:
    # Blender's own binary reader reads DNA fields; it does not start Blender,
    # load a scene, import an add-on, or expose any license/token/preferences values.
    module_dir = PROJECT_ROOT / "native/blender-5.1.2/tools/modules"
    sys.path.insert(0, str(module_dir))
    try:
        import blendfile
    finally:
        sys.path.remove(str(module_dir))
    enabled = []
    repositories = []
    asset_count = 0
    with blendfile.open_blend(str(path), access="rb") as blend:
        for block in blend.blocks:
            if block.code in (b"ENDB", b"DNA1"):
                continue
            kind = block.dna_type_name
            if kind == "bAddon":
                enabled.append(block.get(b"module"))
            elif kind == "bUserScriptDirectory" and block.get(b"dir_path"):
                raise ValueError("External script directories require a separately reviewed snapshot")
            elif kind == "bUserExtensionRepo":
                if block.get(b"custom_dirpath"):
                    raise ValueError("Custom extension directories require a separately reviewed snapshot")
                module = block.get(b"module")
                if not module.isidentifier():
                    raise ValueError("Invalid extension repository module identifier")
                source = block.get(b"source")
                if source not in (0, 1):
                    raise ValueError("Unknown extension repository source")
                repositories.append({"module": module, "source": "USER" if source == 0 else "SYSTEM"})
            elif kind == "bUserAssetLibrary":
                asset_count += 1
    return {
        "enabled_modules": sorted(enabled),
        "repositories": repositories,
        "external_asset_library_count": asset_count,
        "service_addons": sorted(KNOWN_SERVICE_ADDONS.intersection(enabled)),
    }


def prepare_profile(source: Path, destination: Path, *, allowed_root: Path = PROJECT_ROOT) -> dict:
    source = Path(os.path.abspath(source))
    destination = Path(os.path.abspath(destination))
    allowed_root = Path(os.path.abspath(allowed_root))
    _no_link_ancestors(source)
    _no_link_ancestors(destination)
    _no_link_ancestors(allowed_root)
    if not source.is_dir():
        raise ValueError("Source Blender profile is missing")
    if destination == allowed_root or not destination.is_relative_to(allowed_root):
        raise ValueError("Destination must be a new child directory of the workspace")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Source and destination profiles must not overlap")
    if destination.exists():
        raise ValueError("Destination already exists; choose a new profile path to preserve existing changes")
    pref_source = source / "config/userpref.blend"
    _no_link_ancestors(pref_source)
    if not pref_source.is_file():
        raise ValueError("Source user preferences are missing")
    source_hash = sha256(pref_source)
    metadata = inspect_preferences(pref_source)
    metadata.update({
        "status": "snapshot-ready",
        "source_profile": str(source),
        "destination_profile": str(destination),
        "source_preferences_sha256": source_hash,
        "source_unchanged": False,
        "user_addon_code_executed": False,
        "startup_scene_copied": False,
        "copied_files": 0,
        "copied_bytes": 0,
        "skipped_entries": [],
        "installed_modules": [],
    })
    destination.mkdir(parents=True, exist_ok=False)

    def copy_entry(entry: Path, target: Path) -> None:
        relative = entry.relative_to(source).as_posix()
        if is_link(entry):
            metadata["skipped_entries"].append({"path": relative, "reason": "link-or-junction"})
            return
        if entry.name in ("__pycache__", ".cache") or entry.suffix in (".pyc", ".pyo"):
            metadata["skipped_entries"].append({"path": relative, "reason": "cache"})
            return
        if entry.is_dir():
            target.mkdir(exist_ok=False)
            for child in sorted(entry.iterdir()):
                copy_entry(child, target / child.name)
        elif entry.is_file():
            shutil.copy2(entry, target)
            metadata["copied_files"] += 1
            metadata["copied_bytes"] += target.stat().st_size
        else:
            metadata["skipped_entries"].append({"path": relative, "reason": "not-regular-file"})

    (destination / "config").mkdir()
    copy_entry(pref_source, destination / "config/userpref.blend")
    for entry in sorted((source / "config").iterdir()):
        if entry == pref_source:
            continue
        if is_link(entry) or entry.is_dir():
            copy_entry(entry, destination / "config" / entry.name)
        else:
            metadata["skipped_entries"].append({
                "path": entry.relative_to(source).as_posix(),
                "reason": "startup-history-or-unselected-config-file",
            })
    for name in ("scripts", "extensions", "datafiles"):
        path = source / name
        if path.exists() or path.is_symlink():
            copy_entry(path, destination / name)

    addon_root = destination / "scripts/addons"
    if addon_root.is_dir():
        metadata["installed_modules"].extend(
            item.stem if item.is_file() else item.name
            for item in addon_root.iterdir()
            if (item.is_file() and item.suffix == ".py") or (item / "__init__.py").is_file()
        )
    for repo in metadata["repositories"]:
        if repo["source"] != "USER":
            continue
        repo_root = destination / "extensions" / repo["module"]
        if repo_root.is_dir():
            metadata["installed_modules"].extend(
                f"bl_ext.{repo['module']}.{item.name}"
                for item in repo_root.iterdir()
                if (item / "blender_manifest.toml").is_file()
            )
    metadata["installed_modules"].sort()
    if sha256(pref_source) != source_hash or sha256(destination / "config/userpref.blend") != source_hash:
        raise RuntimeError("Preferences changed during copying; incomplete snapshot retained without ready marker")
    metadata["source_unchanged"] = True
    (destination / READY_FILE).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    options = parser.parse_args(args)
    result = prepare_profile(options.source, options.destination)
    print(
        f"XR_ADDONS_PROFILE_READY files={result['copied_files']} "
        f"installed={len(result['installed_modules'])} enabled={len(result['enabled_modules'])} "
        "source_unchanged=1 addon_code_executed=0 startup_scene_copied=0"
    )


if __name__ == "__main__":
    main()
