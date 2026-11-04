"""Build and audit the manual-install Blender extension package."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "vr_blender_workbench"
DIST = ROOT / "dist"
VERSION = "0.1.1"
PACKAGE = DIST / f"vr-blender-workbench-{VERSION}.zip"

PACKAGE_FILES = (
    "__init__.py",
    "action_map.py",
    "blender_manifest.toml",
    "context_bridge.py",
    "model.py",
    "operators.py",
    "properties.py",
    "runtime.py",
    "tools.py",
    "transactions.py",
    "ui.py",
)


def build() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        PACKAGE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in PACKAGE_FILES:
            source = SOURCE / relative
            if not source.is_file():
                raise FileNotFoundError(source)
            archive.write(source, relative)
        archive.write(ROOT / "README.md", "README.md")
        archive.write(ROOT / "LICENSE", "LICENSE")
    return PACKAGE


def audit(package: Path) -> tuple[int, str]:
    forbidden = ("__pycache__", ".pyc", ".pyo", ".tmp", "~")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if any(any(token in name for token in forbidden) for name in names):
            raise AssertionError(f"Forbidden package entry: {names}")
        if len(names) != len(set(names)):
            raise AssertionError("Duplicate ZIP entries")
        if "blender_manifest.toml" not in names or "__init__.py" not in names:
            raise AssertionError("Extension entry points missing")
        for relative in PACKAGE_FILES:
            if archive.read(relative) != (SOURCE / relative).read_bytes():
                raise AssertionError(f"Packaged source mismatch: {relative}")
    digest = sha256(package.read_bytes()).hexdigest().upper()
    return len(names), digest


if __name__ == "__main__":
    output = build()
    count, digest = audit(output)
    print(f"VRW_PACKAGE_BUILD_PASS path={output} entries={count} sha256={digest}")
