# Vendored CurioMesh

Source: github.com/CurioCrafter/CurioMesh, commit `fb4e15b130852819d44f4ce119193c1acda918e9` (0.4.0).
License: MIT (see `LICENSE`), same author as Clayspace.

Runtime files only (`__init__.py`, `atlasq.py`, `bridge.py`, `metrics.py`, `operators.py`,
`textures.py`, `ui.py`, `triadq/`, `LICENSE`, `blender_manifest.toml` for reference).
Tests, scripts and docs are left out. The files are unmodified: every import is already
relative, so none break under `xr_workbench.curiomesh`.

`xr_workbench/retopo.py` registers this copy only when `bpy.ops.curiomesh.remesh` does not already exist.
