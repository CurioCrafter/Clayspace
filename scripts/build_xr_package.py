"""Package the separate 5.1.2 capability workbench and audit source parity."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "xr_workbench"


def build():
    manifest = tomllib.loads((SOURCE / "blender_manifest.toml").read_text("utf-8"))
    version = manifest["version"]
    files = {
        file.name: file
        for file in SOURCE.iterdir()
        if file.is_file() and file.suffix in {".py", ".toml"}
    }
    assert "__init__.py" in files and "diagnostics.py" in files
    files.update(
        {"README.md": ROOT / "docs" / "xr-workbench.md", "LICENSE": ROOT / "LICENSE",
         "STEAMVR.md": ROOT / "docs" / "steamvr-index-workspace.md"}
    )
    output = ROOT / "dist" / f"blender-xr-workbench-{version}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, source in sorted(files.items()):
            archive.write(source, name)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == files.keys()
        assert len(archive.namelist()) == len(files)
        assert archive.testzip() is None
        for name, source in files.items():
            assert archive.read(name) == source.read_bytes(), name
    report = {
        "schema_version": 1,
        "product": "Blender XR Workbench experimental native control extension",
        "version": version,
        "blender_version": "5.1.2",
        "baseline_commit": "ec6e62d40fa9",
        "package": output.name,
        "sha256": sha256(output.read_bytes()).hexdigest(),
        "files": {
            name: sha256(source.read_bytes()).hexdigest()
            for name, source in sorted(files.items())
        },
        "qualification": "NOT QUALIFIED: physical stereo, passthrough, hands, and timing require hardware tests",
    }
    report_path = output.with_suffix(".manifest.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"XRWB_PACKAGE_PASS path={output} entries={len(files)} sha256={report['sha256']}"
    )
    return output


if __name__ == "__main__":
    build()
