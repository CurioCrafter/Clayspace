"""Blender properties and preferences for VR Workbench."""

from __future__ import annotations

import math

import bpy

from .model import CATEGORY_ORDER, DEFAULT_FAVORITES, TOOLS


def _tool_items(_self, _context):
    return [(tool.identifier, tool.label, tool.description) for tool in TOOLS]


def _refresh_visible_palette(self, context):
    if context is None or not self.palette_visible:
        return
    try:
        from . import runtime

        runtime.refresh_palette_cache(context)
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _active_tool_changed(self, context):
    self.delete_armed_until = 0.0
    self.delete_armed_signature = ""
    self.save_armed_until = 0.0
    self.save_armed_path = ""
    _refresh_visible_palette(self, context)


def _category_changed(self, context):
    _refresh_visible_palette(self, context)


def _category_items():
    labels = {
        "ESSENTIAL": "Essential",
        "SELECTION": "Selection",
        "CREATE": "Create",
        "MESH": "Mesh Edit",
        "SCULPT": "Sculpt",
        "ANIMATE": "Animate",
        "UTILITY": "Utility",
        "FAVORITES": "Favorites",
    }
    return [(identifier, labels[identifier], "") for identifier in CATEGORY_ORDER]


class VRW_PG_settings(bpy.types.PropertyGroup):
    active_tool: bpy.props.EnumProperty(
        name="Active VR Tool",
        description="Tool used by the primary controller trigger",
        items=_tool_items,
        default=0,
        update=_active_tool_changed,
    )
    active_category: bpy.props.EnumProperty(
        name="Palette Page",
        items=_category_items(),
        default="ESSENTIAL",
        update=_category_changed,
    )
    palette_visible: bpy.props.BoolProperty(
        name="Show VR Palette",
        description="Show the world-space controller palette in the headset",
        default=False,
        options={"SKIP_SAVE"},
    )
    brush_radius: bpy.props.FloatProperty(
        name="Brush Radius",
        description="World-space radius of experimental VR sculpt tools",
        default=0.20,
        min=0.005,
        max=10.0,
        subtype="DISTANCE",
    )
    brush_strength: bpy.props.FloatProperty(
        name="Brush Strength",
        default=0.25,
        min=0.01,
        max=1.0,
        subtype="FACTOR",
    )
    mesh_amount: bpy.props.FloatProperty(
        name="Mesh Amount",
        description="Default inset, bevel, and extrusion amount",
        default=0.05,
        min=0.0001,
        max=10.0,
        subtype="DISTANCE",
    )
    snap_translation: bpy.props.BoolProperty(name="Position Snap", default=False)
    translation_step: bpy.props.FloatProperty(
        name="Position Step",
        default=0.10,
        min=0.0001,
        max=100.0,
        subtype="DISTANCE",
    )
    snap_rotation: bpy.props.BoolProperty(name="Angle Snap", default=True)
    rotation_step: bpy.props.FloatProperty(
        name="Angle Step",
        default=math.radians(15.0),
        min=math.radians(0.1),
        max=math.radians(180.0),
        subtype="ANGLE",
        unit="ROTATION",
    )
    snap_scale: bpy.props.BoolProperty(name="Scale Snap", default=False)
    scale_step: bpy.props.FloatProperty(
        name="Scale Step", default=0.10, min=0.01, max=2.0
    )
    ray_distance: bpy.props.FloatProperty(
        name="Ray Distance",
        default=1000.0,
        min=0.1,
        max=100000.0,
        subtype="DISTANCE",
    )
    placement_distance: bpy.props.FloatProperty(
        name="Empty-space Placement",
        description="Distance used when the controller ray hits no surface",
        default=1.5,
        min=0.05,
        max=100.0,
        subtype="DISTANCE",
    )
    last_status: bpy.props.StringProperty(
        name="Status", default="Ready", options={"SKIP_SAVE"}
    )
    last_error: bpy.props.StringProperty(
        name="Last Error", default="", options={"SKIP_SAVE"}
    )
    delete_armed_until: bpy.props.FloatProperty(
        default=0.0, options={"HIDDEN", "SKIP_SAVE"}
    )
    delete_armed_signature: bpy.props.StringProperty(
        default="", options={"HIDDEN", "SKIP_SAVE"}
    )
    save_armed_until: bpy.props.FloatProperty(
        default=0.0, options={"HIDDEN", "SKIP_SAVE"}
    )
    save_armed_path: bpy.props.StringProperty(
        default="", options={"HIDDEN", "SKIP_SAVE"}
    )


class VRW_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    close_palette_on_select: bpy.props.BoolProperty(
        name="Close palette after choosing a tool",
        default=True,
    )
    hover_haptics: bpy.props.BoolProperty(
        name="Palette hover haptics",
        default=True,
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "close_palette_on_select")
        layout.prop(self, "hover_haptics")
        box = layout.box()
        box.label(text="VR Favorite Operator Slots")
        box.label(text="Use operator IDs such as object.shade_smooth_by_angle")
        for index in range(1, 7):
            row = box.row(align=True)
            row.prop(self, f"favorite_{index}_label", text=f"{index}")
            row.prop(self, f"favorite_{index}_id", text="")
            row.prop(self, f"favorite_{index}_creates_undo", text="Undoable Edit")
            box.prop(self, f"favorite_{index}_args", text="JSON Args")


for _index, _favorite in enumerate(DEFAULT_FAVORITES, start=1):
    VRW_AddonPreferences.__annotations__[f"favorite_{_index}_label"] = (
        bpy.props.StringProperty(
            name=f"Favorite {_index} Label",
            default=_favorite.label,
        )
    )
    VRW_AddonPreferences.__annotations__[f"favorite_{_index}_id"] = (
        bpy.props.StringProperty(
            name=f"Favorite {_index} Operator",
            default=_favorite.operator_id,
        )
    )
    VRW_AddonPreferences.__annotations__[f"favorite_{_index}_args"] = (
        bpy.props.StringProperty(
            name=f"Favorite {_index} Arguments",
            default=_favorite.arguments,
        )
    )
    VRW_AddonPreferences.__annotations__[f"favorite_{_index}_creates_undo"] = (
        bpy.props.BoolProperty(
            name=f"Favorite {_index} Creates Undoable Data",
            description=(
                "Enable when this operator changes scene or mesh data and needs "
                "the VR trigger wrapper to create an undo snapshot; disable for "
                "selection, view, playback, and history operators"
            ),
            default=_favorite.creates_undoable_data,
        )
    )


def get_preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


CLASSES = (VRW_PG_settings, VRW_AddonPreferences)
