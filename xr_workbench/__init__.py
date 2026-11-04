"""XR Workbench: native editor access and reversible spatial editing."""

bl_info = {
    "name": "XR Workbench",
    "author": "Andrew Rainsberger",
    "version": (0, 18, 0),
    "blender": (5, 1, 2),
    "location": "3D Viewport > Sidebar > XR Workbench",
    "description": "Experimental source-native XR editor and gesture workbench",
    "category": "3D View",
}

import bpy  # noqa: E402
from bpy.app.handlers import persistent  # noqa: E402

from . import (  # noqa: E402
    animation_tools, boolean_tools, component_mode, controllers, edge_editing, editing, hands, image_gestures,
    creative_tools as creative_tools, materials_lab, node_garden, onboarding, physical_tools, quick_actions, ray_feedback, runtime as runtime, ui,
    navigation, prefabs, sdf_modeling, studio, tool_wheel, uv_tools, viewer_scale,
)

_MODULES = (ui, controllers, editing, hands, image_gestures, viewer_scale, physical_tools,
            materials_lab, animation_tools, boolean_tools, onboarding, quick_actions, uv_tools, node_garden, studio,
            component_mode, prefabs, sdf_modeling, navigation)


def register():
    for cls in (cls for module in _MODULES for cls in module.CLASSES):
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.xrwb_settings = bpy.props.PointerProperty(
        type=ui.XRWB_PG_settings
    )
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)
    if _xr_start_pre not in bpy.app.handlers.xr_session_start_pre:
        bpy.app.handlers.xr_session_start_pre.append(_xr_start_pre)


def unregister():
    edge_editing.stop_overlay()
    ray_feedback.stop()
    tool_wheel.close()
    ui.clear_transient_options()
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    if _xr_start_pre in bpy.app.handlers.xr_session_start_pre:
        bpy.app.handlers.xr_session_start_pre.remove(_xr_start_pre)
    if hasattr(bpy.types.WindowManager, "xrwb_settings"):
        del bpy.types.WindowManager.xrwb_settings
    for cls in reversed([cls for module in _MODULES for cls in module.CLASSES]):
        bpy.utils.unregister_class(cls)


@persistent
def _load_pre(_dummy=None):
    edge_editing.stop_overlay()
    ray_feedback.stop()
    viewer_scale.reset_memory()
    ui.clear_transient_options()


@persistent
def _xr_start_pre(_dummy=None):
    # Blender calls this before attaching OpenXR action sets, with valid context.
    # Never retain this context or read it later from an application timer.
    context = bpy.context
    # The SteamVR launcher opts into controllers; the camera route stays separate.
    if not context.window_manager.xrwb_settings.controller_fallback:
        return
    try:
        ok, message = controllers.configure_action_map(context)
        context.window_manager.xrwb_settings.controller_status = message
        print(
            message if ok else f"XRWB_CONTROLLERS_ACTIONMAP_FAIL {message}", flush=True
        )
    except Exception as exc:
        print(f"XRWB_CONTROLLERS_ACTIONMAP_FAIL {exc}", flush=True)
