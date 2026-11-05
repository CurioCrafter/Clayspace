"""Clayspace Legacy Workbench entry point."""

from __future__ import annotations

bl_info = {
    "name": "Clayspace Legacy Workbench",
    "author": "Andrew Rainsberger (TideFront Studio)",
    "version": (0, 1, 1),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Sidebar > VR Workbench",
    "description": "SteamVR/OpenXR spatial authoring tools with Desktop View fallback",
    "category": "3D View",
    "support": "COMMUNITY",
}

import bpy  # noqa: E402

from . import operators, properties, runtime, transactions, ui  # noqa: E402


_CLASSES = (*properties.CLASSES, *operators.CLASSES, *ui.CLASSES)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vrw_settings = bpy.props.PointerProperty(
        type=properties.VRW_PG_settings
    )
    if operators.xr_session_start_pre not in bpy.app.handlers.xr_session_start_pre:
        bpy.app.handlers.xr_session_start_pre.append(operators.xr_session_start_pre)


def unregister():
    # Python callbacks must be gone before native XR teardown can invalidate
    # Blender's global context.
    runtime.shutdown_runtime()
    if not operators.stop_session_for_unregister():
        raise RuntimeError(
            "VR Workbench could not stop the live XR session; stop VR before disabling the add-on"
        )
    transactions.cancel_all_transactions()
    if operators.xr_session_start_pre in bpy.app.handlers.xr_session_start_pre:
        bpy.app.handlers.xr_session_start_pre.remove(operators.xr_session_start_pre)
    if hasattr(bpy.types.Scene, "vrw_settings"):
        del bpy.types.Scene.vrw_settings
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
