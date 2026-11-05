"""Retopo Loom check: register the vendored CurioMesh, retopo a displaced sphere to 500
faces, assert a new object exists and the source is unchanged. Run headless with
BLENDER_USER_CONFIG set to an empty temporary folder."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xr_workbench import retopo  # noqa: E402

assert not retopo.has_external()
retopo.register()
assert hasattr(bpy.ops.curiomesh, "remesh")
retopo.register()  # second call must be a no-op

bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0)
src = bpy.context.object
sub = src.modifiers.new("sub", "SUBSURF")
sub.levels = 1
bpy.ops.object.modifier_apply(modifier=sub.name)
tex = bpy.data.textures.new("n", "CLOUDS")
disp = src.modifiers.new("d", "DISPLACE")
disp.texture, disp.strength = tex, 0.1
bpy.ops.object.modifier_apply(modifier=disp.name)
n_src = len(src.data.polygons)
before = retopo._fingerprint(src)

card = retopo.run(bpy.context, src, 500, "Fast")
print("CARD", card, retopo.card_lines(card))
assert card["ok"], card
assert card["source_unchanged"] and retopo._fingerprint(src) == before
assert len(src.data.polygons) == n_src and card["name"] in bpy.data.objects
assert bpy.data.objects[card["name"]] is not src

# failure path: a non-mesh leaves the scene alone
bpy.ops.object.empty_add()
names = set(bpy.data.objects.keys())
bad = retopo.run(bpy.context, bpy.context.object, 500)
assert not bad["ok"] and set(bpy.data.objects.keys()) == names

an = retopo.analyze(bpy.context, src)
assert an["ok"], an

retopo.unregister()
assert not hasattr(bpy.types.Scene, "curiomesh_settings")

# A standalone CurioMesh enabled after ours takes over; our unregister must leave it intact.
import importlib.util  # noqa: E402
retopo.register()
spec = importlib.util.spec_from_file_location(
    "curiomesh_standalone", ROOT / "xr_workbench/curiomesh/__init__.py",
    submodule_search_locations=[str(ROOT / "xr_workbench/curiomesh")])
standalone = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = standalone
spec.loader.exec_module(standalone)
standalone.register()
retopo.unregister()
assert hasattr(bpy.types.Scene, "curiomesh_settings") and hasattr(bpy.types, "CURIOMESH_OT_remesh")
standalone.unregister()
print(f"XR_CURIOMESH_SMOKE_PASS src_faces={n_src} out_faces={card['faces']} quad_pct={card['quad_pct']:.0f} passed={card['passed']}")
