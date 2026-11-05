"""Retopo Loom: CurioMesh quad retopology as a VR tool.

Point at a mesh, set the target faces (steps of 500, 500..20,000), pull the
trigger. `curiomesh.remesh` makes a NEW object beside the source; the source is
never replaced. The operator carries Blender's UNDO flag, so one run is one
undo step (named "CurioMesh Remesh" by Blender).

Pure helpers (snap_target, quads_label, card_lines) need no bpy. The VR
tool/stroke class, hand-menu section and tool-wheel entry are wired by the
integrator; see the integration notes.
"""
from __future__ import annotations

import importlib.util

STEP, MIN_FACES, MAX_FACES, DEFAULT_FACES = 500, 500, 20000, 5000
# Menu labels -> CurioMesh quality enum (this CurioMesh has DRAFT/BALANCED/HERO).
QUALITY = {"Fast": "DRAFT", "Balanced": "BALANCED", "Best": "HERO"}
_registered_vendored = False


def snap_target(value) -> int:
    """Snap a dial/thumbstick value to the 500-step grid inside 500..20,000."""
    return max(MIN_FACES, min(MAX_FACES, int(round(float(value) / STEP)) * STEP))


def step_target(current, ticks) -> int:
    """Thumbstick/dial detents: move `ticks` steps from `current`."""
    return snap_target(snap_target(current) + int(ticks) * STEP)


def quads_label(target) -> str:
    return f"~{snap_target(target):,} quads"


def card_lines(card: dict) -> list[str]:
    """Text lines for the task/result card (plain data, safe for the XR draw callback)."""
    if card.get("state") == "running":
        return ["Retopologising...", "the view will pause for a few seconds"]
    if not card.get("ok"):
        return ["Retopo failed", card.get("reason") or "CurioMesh gave no reason", "The scene is unchanged"]
    return [
        f"{card['faces']:,} faces, {card['quad_pct']:.0f}% quads",
        ("PASS" if card.get("passed") else "FAIL") + ": " + (card.get("reason") or ""),
        f"New object: {card['name']}",
    ]


def has_external() -> bool:
    """True when CurioMesh is already installed separately."""
    import bpy
    # bpy.ops resolves any name lazily, so ask bpy.types whether the operator class is registered.
    return hasattr(bpy.types, "CURIOMESH_OT_remesh")


def register():
    """Register the vendored CurioMesh unless the user's own copy exists."""
    global _registered_vendored
    if _registered_vendored or has_external():
        return
    from . import curiomesh
    curiomesh.register()
    _registered_vendored = True


def unregister():
    global _registered_vendored
    if not _registered_vendored:
        return
    import bpy
    from . import curiomesh
    _registered_vendored = False
    # A standalone CurioMesh enabled after us replaced our classes and owns Scene.curiomesh_settings now:
    # unregistering "ours" would fail and delete its settings.
    if getattr(bpy.types, "CURIOMESH_OT_remesh", None) is curiomesh.CURIOMESH_OT_remesh:
        curiomesh.unregister()


def face_count(obj) -> int:
    """Face count to show when pointing at a mesh (the stored mesh, not modifiers)."""
    return len(obj.data.polygons) if obj is not None and obj.type == "MESH" else 0


def _fingerprint(obj):
    me = obj.data
    co = [0.0] * (len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    return (len(me.vertices), len(me.polygons), round(sum(co), 6), tuple(round(v, 6) for row in obj.matrix_world for v in row))


def run(context, obj, target_faces, quality="Balanced") -> dict:
    """Run curiomesh.remesh on `obj`; return the result-card dict.

    # ponytail: blocks the VR frame (CurioMesh is synchronous). Upgrade path:
    # run the solver in a background Blender process and import the result.
    """
    import bpy
    if obj is None or obj.type != "MESH":
        return {"state": "done", "ok": False, "reason": "Point at a mesh object."}
    target = snap_target(target_faces)
    from . import live_cage
    # CurioMesh pushes its own undo step: never let it record a cage's hidden modifiers.
    live_cage.restore_all(context, include_caged=True)
    before = set(bpy.data.objects.keys())
    fingerprint = _fingerprint(obj)
    view_layer = context.view_layer
    old_active, old_selected = view_layer.objects.active, list(context.selected_objects)
    for other in old_selected:
        other.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj
    reason, result = "", None
    try:
        result = bpy.ops.curiomesh.remesh(
            "EXEC_DEFAULT", target_faces=target, engine="ATLAS_Q",
            quality=QUALITY.get(quality, quality), output_mode="NEW",
        )
    except Exception as error:  # operator errors surface as RuntimeError
        reason = str(error).strip().splitlines()[-1] if str(error).strip() else "CurioMesh raised an error"
    settings = context.scene.curiomesh_settings
    new = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
    if result != {"FINISHED"} or not new:
        for extra in new:  # a failed run must leave the scene as it was
            bpy.data.objects.remove(extra, do_unlink=True)
        for other in context.selected_objects:
            other.select_set(False)
        for other in old_selected:
            if other.name in bpy.data.objects:
                other.select_set(True)
        if old_active is not None and old_active.name in bpy.data.objects:
            view_layer.objects.active = old_active
        return {"state": "done", "ok": False, "reason": reason or settings.metrics_message or "CurioMesh remesh failed"}
    out = new[0]
    return {
        "state": "done", "ok": True, "name": out.name, "faces": len(out.data.polygons),
        "quad_pct": 100.0 * float(settings.metrics_quad_ratio),
        "passed": settings.metrics_status == "OK" and bool(settings.metrics_quad_only),
        "reason": settings.metrics_message, "source_unchanged": _fingerprint(obj) == fingerprint,
    }


def analyze(context, obj) -> dict:
    """curiomesh.analyze for the hand-menu card; the source is not changed."""
    import bpy
    view_layer = context.view_layer
    old = view_layer.objects.active
    view_layer.objects.active = obj
    try:
        ok = bpy.ops.curiomesh.analyze("EXEC_DEFAULT") == {"FINISHED"}
    except Exception as error:
        return {"ok": False, "reason": str(error).strip().splitlines()[-1]}
    finally:
        if old is not None and old.name in bpy.data.objects:
            view_layer.objects.active = old
    s = context.scene.curiomesh_settings
    return {"ok": ok, "profile": s.analysis_profile, "strategy": s.analysis_strategy,
            "faces": s.analysis_faces, "warning": s.analysis_warning}
