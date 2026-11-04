"""Desktop companion panels for configuring and starting VR Workbench."""

from __future__ import annotations

import bpy

from .model import tools_for_category
from .runtime import session_exists, session_running


class VIEW3D_PT_vrw_workbench(bpy.types.Panel):
    bl_label = "VR Workbench"
    bl_idname = "VIEW3D_PT_vrw_workbench"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VR Workbench"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.vrw_settings
        running = session_running(context)
        exists = session_exists(context)

        status_box = layout.box()
        row = status_box.row(align=True)
        row.label(text="OpenXR", icon="XRAY")
        row.label(
            text="Running" if running else ("Starting" if exists else "Stopped"),
            icon="CHECKMARK" if running else ("TIME" if exists else "PAUSE"),
        )
        if not bpy.app.build_options.xr_openxr:
            status_box.label(
                text="This Blender build has no OpenXR support", icon="ERROR"
            )
        elif exists:
            status_box.operator("vrw.stop_session", icon="SNAP_FACE")
        else:
            status_box.operator("vrw.start_session", icon="PLAY")
        if settings.last_error:
            status_box.label(text=settings.last_error[:80], icon="ERROR")
        else:
            status_box.label(text=settings.last_status[:80], icon="INFO")

        layout.separator()
        layout.prop(settings, "active_tool")
        row = layout.row(align=True)
        row.prop(settings, "active_category", text="Palette")
        row.operator("vrw.palette_toggle", text="Show", icon="MENU_PANEL")

        grid = layout.grid_flow(
            row_major=True, columns=3, even_columns=True, even_rows=True, align=True
        )
        for tool in tools_for_category(settings.active_category):
            operator = grid.operator("vrw.set_tool", text=tool.label)
            operator.tool = tool.identifier

        box = layout.box()
        box.label(text="Index / Touch Default Controls", icon="EVENT_R")
        box.label(text="Right trigger: active tool")
        box.label(text="Right grip: direct move")
        box.label(text="Left grip: world grab")
        box.label(text="Left B/Y: palette | Left A/X: undo")
        box.label(text="Sticks: move, turn, rise | Right click: recenter")
        box.separator()
        box.label(text="SteamVR system button: 2D Desktop View fallback")


class VIEW3D_PT_vrw_precision(bpy.types.Panel):
    bl_label = "VR Precision & Sculpt"
    bl_idname = "VIEW3D_PT_vrw_precision"
    bl_parent_id = "VIEW3D_PT_vrw_workbench"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VR Workbench"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.vrw_settings
        row = layout.row(align=True)
        row.prop(settings, "snap_translation")
        row.prop(settings, "translation_step", text="Step")
        row = layout.row(align=True)
        row.prop(settings, "snap_rotation")
        row.prop(settings, "rotation_step", text="Step")
        row = layout.row(align=True)
        row.prop(settings, "snap_scale")
        row.prop(settings, "scale_step", text="Step")
        layout.prop(settings, "mesh_amount")
        layout.prop(settings, "brush_radius")
        layout.prop(settings, "brush_strength")
        layout.prop(settings, "placement_distance")
        layout.prop(settings, "ray_distance")


class VIEW3D_PT_vrw_capabilities(bpy.types.Panel):
    bl_label = "Coverage & Limits"
    bl_idname = "VIEW3D_PT_vrw_capabilities"
    bl_parent_id = "VIEW3D_PT_vrw_workbench"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VR Workbench"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Teal: Native VR")
        layout.label(text="Blue: Context bridge (coverage varies)")
        layout.label(text="Orange: Experimental")
        layout.label(text="Purple: SteamVR Desktop required")
        layout.separator()
        layout.label(text="Nodes, UVs, file dialogs, add-on panels,")
        layout.label(text="and arbitrary mouse tools use Desktop View.")
        layout.operator(
            "screen.userpref_show", text="VR Workbench Preferences", icon="PREFERENCES"
        )


CLASSES = (
    VIEW3D_PT_vrw_workbench,
    VIEW3D_PT_vrw_precision,
    VIEW3D_PT_vrw_capabilities,
)
