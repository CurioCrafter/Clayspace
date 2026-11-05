"""XR Workbench: native editor access and reversible spatial editing."""

bl_info = {
    "name": "Clayspace",
    "author": "Andrew Rainsberger",
    "version": (0, 21, 0),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Sidebar > XR Workbench",
    "description": "Experimental source-native XR editor and gesture workbench",
    "category": "3D View",
}

import bpy  # noqa: E402
from bpy.app.handlers import persistent  # noqa: E402

from . import (  # noqa: E402
    animation_tools, boolean_tools, component_mode, controllers, edge_editing, editing, hands, image_gestures,
    creative_tools as creative_tools, materials_lab, modifier_tower, node_garden, onboarding, physical_tools, quick_actions, ray_feedback, runtime as runtime, ui,
    navigation, prefabs, sdf_modeling, studio, tool_wheel, undo_ring, uv_tools, viewer_scale,
    growth, live_cage, retopo, subdiv_lab, tool_props,
    contact, contact_tools as contact_tools, feel,  # 0.21: physical_tools.begin dispatches to contact_tools.
)

_MODULES = (ui, controllers, editing, hands, image_gestures, viewer_scale, physical_tools,
            materials_lab, animation_tools, boolean_tools, onboarding, quick_actions, uv_tools, node_garden, studio,
            component_mode, prefabs, sdf_modeling, navigation, modifier_tower, undo_ring, subdiv_lab, growth)


def _require_vrblender_build():
    state = getattr(bpy.types, "XrSessionState", None)
    if state is None or "hand_menu_pose" not in state.bl_rna.functions:
        raise RuntimeError(
            f"Clayspace needs its patched Blender build; this Blender {bpy.app.version_string} "
            "is missing its native XR hand menu and editor surfaces. Download a Clayspace build from "
            "https://github.com/CurioCrafter/Clayspace/releases or build it from source (see README)."
        )


def register():
    _require_vrblender_build()
    for cls in (cls for module in _MODULES for cls in module.CLASSES):
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.xrwb_settings = bpy.props.PointerProperty(
        type=ui.XRWB_PG_settings
    )
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)
    if _xr_start_pre not in bpy.app.handlers.xr_session_start_pre:
        bpy.app.handlers.xr_session_start_pre.append(_xr_start_pre)
    modifier_tower.register_handlers()
    if _depsgraph_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_post)
    live_cage.register()
    retopo.register()


def unregister():
    live_cage.unregister()  # Restores every caged modifier first.
    retopo.unregister()
    tool_props.clear()  # GPU batches must go before Blender exits, or it can crash.
    edge_editing.stop_overlay()
    ray_feedback.stop()  # Also stops the feel layer's sounds and clears the contact cache.
    feel.stop()
    contact.clear()
    if _depsgraph_post in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_post)
    tool_wheel.close()
    undo_ring.close()
    modifier_tower.unregister_handlers()
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
def _depsgraph_post(scene, depsgraph):
    # Contact's BVH cache: geometry marks a tree dirty, a transform only refreshes its matrices.
    try:
        contact.on_depsgraph_update(scene, depsgraph)
    except Exception as exc:  # Never break Blender's update loop.
        print(f"XR contact update failed: {exc}", flush=True)


@persistent
def _load_pre(_dummy=None):
    edge_editing.stop_overlay()
    ray_feedback.stop()
    viewer_scale.reset_memory()
    undo_ring.close()  # Another file has another history.
    tool_props.clear()
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
