"""Pure data model and geometry helpers for VR Blender Workbench.

This module deliberately has no ``bpy`` import so its behavior can be tested
with the system Python as well as Blender's bundled Python.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable, Sequence


ADDON_VERSION = (0, 1, 1)
ACTION_MAP_NAME = "vr_workbench"
LEFT_HAND = "/user/hand/left"
RIGHT_HAND = "/user/hand/right"
RUNTIME_PREFIX = "VRW_RUNTIME_"

CAP_NATIVE = "NATIVE_VR"
CAP_BRIDGED = "CONTEXT_BRIDGED"
CAP_EXPERIMENTAL = "EXPERIMENTAL"
CAP_DESKTOP = "DESKTOP_REQUIRED"

BEHAVIOR_CONTINUOUS = "CONTINUOUS"
BEHAVIOR_PRESS = "PRESS"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    identifier: str
    label: str
    category: str
    capability: str
    behavior: str = BEHAVIOR_PRESS
    description: str = ""


@dataclass(frozen=True, slots=True)
class FavoriteSpec:
    label: str
    operator_id: str
    arguments: str
    creates_undoable_data: bool


DEFAULT_FAVORITES: tuple[FavoriteSpec, ...] = (
    FavoriteSpec("Smooth", "object.shade_smooth_by_angle", "{}", True),
    FavoriteSpec("Flat", "object.shade_flat", "{}", True),
    FavoriteSpec(
        "Apply Xform",
        "object.transform_apply",
        '{"location": true, "rotation": true, "scale": true}',
        True,
    ),
    FavoriteSpec(
        "Recalc Out",
        "mesh.normals_make_consistent",
        '{"inside": false}',
        True,
    ),
    FavoriteSpec("Select All", "object.select_all", '{"action": "SELECT"}', False),
    FavoriteSpec("Frame All", "view3d.view_all", '{"center": false}', False),
)


_HISTORY_OPERATOR_IDS = frozenset({"ed.undo", "ed.redo", "ed.undo_history"})


def favorite_needs_outer_undo(operator_id: str, creates_undoable_data: bool) -> bool:
    """Return whether a Favorite needs Primary's outer undo snapshot.

    History operators must never finish the UNDO-enabled wrapper because that
    would immediately add a new state and destroy the Redo branch they manage.
    Other arbitrary operators use explicit user-owned metadata because Blender
    exposes no reliable pre-invocation test for whether an operator mutates data.
    """

    if operator_id.strip() in _HISTORY_OPERATOR_IDS:
        return False
    return bool(creates_undoable_data)


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "SELECT",
        "Select",
        "ESSENTIAL",
        CAP_NATIVE,
        description="Ray select objects or mesh faces",
    ),
    ToolSpec(
        "MOVE",
        "Move",
        "ESSENTIAL",
        CAP_NATIVE,
        BEHAVIOR_CONTINUOUS,
        "Move selection with the controller",
    ),
    ToolSpec(
        "ROTATE",
        "Rotate",
        "ESSENTIAL",
        CAP_NATIVE,
        BEHAVIOR_CONTINUOUS,
        "Rotate selection from controller rotation",
    ),
    ToolSpec(
        "SCALE",
        "Scale",
        "ESSENTIAL",
        CAP_NATIVE,
        BEHAVIOR_CONTINUOUS,
        "Uniformly scale selection around its pivot",
    ),
    ToolSpec(
        "DUPLICATE",
        "Duplicate",
        "ESSENTIAL",
        CAP_BRIDGED,
        description="Duplicate selected objects or mesh elements",
    ),
    ToolSpec(
        "DELETE",
        "Delete",
        "ESSENTIAL",
        CAP_BRIDGED,
        description="Arm and confirm deletion of the selection",
    ),
    ToolSpec(
        "HIDE",
        "Hide",
        "ESSENTIAL",
        CAP_BRIDGED,
        description="Hide the current selection",
    ),
    ToolSpec(
        "UNDO",
        "Undo",
        "ESSENTIAL",
        CAP_BRIDGED,
        description="Undo the last committed action",
    ),
    ToolSpec(
        "REDO",
        "Redo",
        "ESSENTIAL",
        CAP_BRIDGED,
        description="Redo the last undone action",
    ),
    ToolSpec(
        "SELECT_REPLACE",
        "Replace",
        "SELECTION",
        CAP_NATIVE,
        description="Replace the selection with the ray target",
    ),
    ToolSpec(
        "SELECT_ADD",
        "Add",
        "SELECTION",
        CAP_NATIVE,
        description="Add the ray target to the selection",
    ),
    ToolSpec(
        "SELECT_SUBTRACT",
        "Subtract",
        "SELECTION",
        CAP_NATIVE,
        description="Remove the ray target from the selection",
    ),
    ToolSpec(
        "SELECT_TOGGLE",
        "Toggle",
        "SELECTION",
        CAP_NATIVE,
        description="Toggle the ray target in the selection",
    ),
    ToolSpec(
        "SELECT_ALL",
        "Select All",
        "SELECTION",
        CAP_BRIDGED,
        description="Select all available objects or mesh elements",
    ),
    ToolSpec(
        "DESELECT_ALL",
        "Deselect",
        "SELECTION",
        CAP_BRIDGED,
        description="Clear the current selection",
    ),
    ToolSpec(
        "SELECT_INVERT",
        "Invert",
        "SELECTION",
        CAP_BRIDGED,
        description="Invert the current selection",
    ),
    ToolSpec(
        "UNHIDE",
        "Reveal",
        "SELECTION",
        CAP_BRIDGED,
        description="Reveal hidden objects or mesh elements",
    ),
    ToolSpec(
        "ADD_CUBE",
        "Cube",
        "CREATE",
        CAP_NATIVE,
        description="Add a cube at the ray target",
    ),
    ToolSpec(
        "ADD_UV_SPHERE",
        "Sphere",
        "CREATE",
        CAP_NATIVE,
        description="Add a UV sphere at the ray target",
    ),
    ToolSpec(
        "ADD_CYLINDER",
        "Cylinder",
        "CREATE",
        CAP_NATIVE,
        description="Add a cylinder at the ray target",
    ),
    ToolSpec(
        "ADD_CONE",
        "Cone",
        "CREATE",
        CAP_NATIVE,
        description="Add a cone at the ray target",
    ),
    ToolSpec(
        "ADD_PLANE",
        "Plane",
        "CREATE",
        CAP_NATIVE,
        description="Add a plane at the ray target",
    ),
    ToolSpec(
        "ADD_TORUS",
        "Torus",
        "CREATE",
        CAP_NATIVE,
        description="Add a torus at the ray target",
    ),
    ToolSpec(
        "ADD_EMPTY",
        "Empty",
        "CREATE",
        CAP_NATIVE,
        description="Add an empty at the ray target",
    ),
    ToolSpec(
        "ADD_LIGHT",
        "Light",
        "CREATE",
        CAP_NATIVE,
        description="Add a point light at the ray target",
    ),
    ToolSpec(
        "ADD_CAMERA",
        "Camera",
        "CREATE",
        CAP_NATIVE,
        description="Add a camera at the controller pose",
    ),
    ToolSpec(
        "MODE_EDIT",
        "Edit Mode",
        "MESH",
        CAP_BRIDGED,
        description="Enter mesh Edit Mode",
    ),
    ToolSpec(
        "SELECT_VERTEX",
        "Vertex",
        "MESH",
        CAP_BRIDGED,
        description="Use vertex selection",
    ),
    ToolSpec(
        "SELECT_EDGE", "Edge", "MESH", CAP_BRIDGED, description="Use edge selection"
    ),
    ToolSpec(
        "SELECT_FACE", "Face", "MESH", CAP_BRIDGED, description="Use face selection"
    ),
    ToolSpec(
        "EXTRUDE",
        "Extrude",
        "MESH",
        CAP_BRIDGED,
        description="Extrude selected mesh region",
    ),
    ToolSpec("INSET", "Inset", "MESH", CAP_BRIDGED, description="Inset selected faces"),
    ToolSpec(
        "BEVEL",
        "Bevel",
        "MESH",
        CAP_BRIDGED,
        description="Bevel selected mesh elements",
    ),
    ToolSpec(
        "SUBDIVIDE",
        "Subdivide",
        "MESH",
        CAP_BRIDGED,
        description="Subdivide selected mesh elements",
    ),
    ToolSpec(
        "MERGE_CENTER",
        "Merge",
        "MESH",
        CAP_BRIDGED,
        description="Merge selected vertices at center",
    ),
    ToolSpec(
        "MODE_SCULPT",
        "Sculpt",
        "SCULPT",
        CAP_EXPERIMENTAL,
        description="Enter Sculpt Mode",
    ),
    ToolSpec(
        "SCULPT_DRAW",
        "Draw",
        "SCULPT",
        CAP_EXPERIMENTAL,
        BEHAVIOR_CONTINUOUS,
        "Direct radial mesh deformation",
    ),
    ToolSpec(
        "SCULPT_SMOOTH",
        "Smooth",
        "SCULPT",
        CAP_EXPERIMENTAL,
        BEHAVIOR_CONTINUOUS,
        "Smooth vertices under the controller ray",
    ),
    ToolSpec(
        "SCULPT_INFLATE",
        "Inflate",
        "SCULPT",
        CAP_EXPERIMENTAL,
        BEHAVIOR_CONTINUOUS,
        "Inflate vertices under the controller ray",
    ),
    ToolSpec(
        "SCULPT_DEFLATE",
        "Deflate",
        "SCULPT",
        CAP_EXPERIMENTAL,
        BEHAVIOR_CONTINUOUS,
        "Deflate vertices under the controller ray",
    ),
    ToolSpec(
        "BRUSH_SMALLER",
        "Radius -",
        "SCULPT",
        CAP_NATIVE,
        description="Reduce the VR brush radius",
    ),
    ToolSpec(
        "BRUSH_LARGER",
        "Radius +",
        "SCULPT",
        CAP_NATIVE,
        description="Increase the VR brush radius",
    ),
    ToolSpec(
        "STRENGTH_DOWN",
        "Strength -",
        "SCULPT",
        CAP_NATIVE,
        description="Reduce VR brush strength",
    ),
    ToolSpec(
        "STRENGTH_UP",
        "Strength +",
        "SCULPT",
        CAP_NATIVE,
        description="Increase VR brush strength",
    ),
    ToolSpec(
        "INSERT_KEY",
        "Insert Key",
        "ANIMATE",
        CAP_BRIDGED,
        description="Insert location, rotation and scale keys",
    ),
    ToolSpec(
        "DELETE_KEY",
        "Delete Key",
        "ANIMATE",
        CAP_BRIDGED,
        description="Delete keys on the current frame",
    ),
    ToolSpec(
        "PLAY_PAUSE",
        "Play/Pause",
        "ANIMATE",
        CAP_BRIDGED,
        description="Toggle animation playback",
    ),
    ToolSpec(
        "FRAME_PREV",
        "Prev Frame",
        "ANIMATE",
        CAP_NATIVE,
        description="Move one frame backward",
    ),
    ToolSpec(
        "FRAME_NEXT",
        "Next Frame",
        "ANIMATE",
        CAP_NATIVE,
        description="Move one frame forward",
    ),
    ToolSpec(
        "KEY_PREV",
        "Prev Key",
        "ANIMATE",
        CAP_BRIDGED,
        description="Jump to the previous keyframe",
    ),
    ToolSpec(
        "KEY_NEXT",
        "Next Key",
        "ANIMATE",
        CAP_BRIDGED,
        description="Jump to the next keyframe",
    ),
    ToolSpec(
        "AUTO_KEY",
        "Auto Key",
        "ANIMATE",
        CAP_NATIVE,
        description="Toggle automatic keying",
    ),
    ToolSpec(
        "MODE_POSE",
        "Pose Mode",
        "ANIMATE",
        CAP_BRIDGED,
        description="Enter armature Pose Mode",
    ),
    ToolSpec(
        "MODE_OBJECT",
        "Object",
        "UTILITY",
        CAP_BRIDGED,
        description="Return to Object Mode",
    ),
    ToolSpec(
        "SHADE_SMOOTH",
        "Smooth",
        "UTILITY",
        CAP_BRIDGED,
        description="Shade selected objects smooth by angle",
    ),
    ToolSpec(
        "SHADE_FLAT",
        "Flat",
        "UTILITY",
        CAP_BRIDGED,
        description="Shade selected objects flat",
    ),
    ToolSpec(
        "APPLY_TRANSFORMS",
        "Apply Xform",
        "UTILITY",
        CAP_BRIDGED,
        description="Apply location, rotation and scale",
    ),
    ToolSpec(
        "JOIN", "Join", "UTILITY", CAP_BRIDGED, description="Join selected objects"
    ),
    ToolSpec(
        "SEPARATE",
        "Separate",
        "UTILITY",
        CAP_BRIDGED,
        description="Separate selected mesh elements",
    ),
    ToolSpec(
        "ORIGIN_GEOMETRY",
        "Set Origin",
        "UTILITY",
        CAP_BRIDGED,
        description="Set origin to geometry",
    ),
    ToolSpec(
        "SAVE",
        "Save",
        "UTILITY",
        CAP_BRIDGED,
        description="Two-step save of the current Blender file",
    ),
    ToolSpec(
        "DESKTOP",
        "Desktop Help",
        "UTILITY",
        CAP_DESKTOP,
        description="Show how to open SteamVR Desktop View",
    ),
    ToolSpec("FAVORITE_1", "Favorite 1", "FAVORITES", CAP_BRIDGED),
    ToolSpec("FAVORITE_2", "Favorite 2", "FAVORITES", CAP_BRIDGED),
    ToolSpec("FAVORITE_3", "Favorite 3", "FAVORITES", CAP_BRIDGED),
    ToolSpec("FAVORITE_4", "Favorite 4", "FAVORITES", CAP_BRIDGED),
    ToolSpec("FAVORITE_5", "Favorite 5", "FAVORITES", CAP_BRIDGED),
    ToolSpec("FAVORITE_6", "Favorite 6", "FAVORITES", CAP_BRIDGED),
)

CATEGORY_ORDER = (
    "ESSENTIAL",
    "SELECTION",
    "CREATE",
    "MESH",
    "SCULPT",
    "ANIMATE",
    "UTILITY",
    "FAVORITES",
)
_TOOL_BY_ID = {tool.identifier: tool for tool in TOOLS}
_OPERATOR_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def tool_by_id(identifier: str) -> ToolSpec:
    return _TOOL_BY_ID[identifier]


def tools_for_category(category: str) -> tuple[ToolSpec, ...]:
    return tuple(tool for tool in TOOLS if tool.category == category)


def cycle_category(category: str, direction: int) -> str:
    try:
        index = CATEGORY_ORDER.index(category)
    except ValueError:
        index = 0
    return CATEGORY_ORDER[(index + (1 if direction >= 0 else -1)) % len(CATEGORY_ORDER)]


def operator_id_is_valid(operator_id: str) -> bool:
    return bool(_OPERATOR_ID_RE.fullmatch(operator_id.strip()))


def xr_route_matches(
    captured_action_set: str,
    captured_action: str,
    captured_user_path: str,
    event_action_set: str,
    event_action: str,
    event_user_path: str,
) -> bool:
    """True when an XR modal event belongs to its initiating action route."""

    return (
        (not captured_action_set or captured_action_set == event_action_set)
        and (not captured_action or captured_action == event_action)
        and (not captured_user_path or captured_user_path == event_user_path)
    )


def quantize(value: float, step: float) -> float:
    if step <= 0.0:
        return value
    return round(value / step) * step


def quantize_vector(values: Iterable[float], step: float) -> tuple[float, ...]:
    return tuple(quantize(float(value), step) for value in values)


@dataclass(frozen=True, slots=True)
class PaletteLayout:
    columns: int = 3
    rows: int = 4
    width: float = 0.72
    height: float = 0.48
    gap: float = 0.012

    @property
    def cell_width(self) -> float:
        return self.width / self.columns

    @property
    def cell_height(self) -> float:
        return self.height / self.rows


DEFAULT_PALETTE_LAYOUT = PaletteLayout()


def ray_plane_intersection(
    ray_origin: Sequence[float],
    ray_direction: Sequence[float],
    plane_origin: Sequence[float],
    plane_normal: Sequence[float],
    *,
    epsilon: float = 1.0e-7,
) -> tuple[float, float, float] | None:
    """Return the forward ray/plane hit or ``None`` when parallel/behind."""

    denominator = sum(float(a) * float(b) for a, b in zip(ray_direction, plane_normal))
    if abs(denominator) <= epsilon:
        return None
    offset = tuple(float(b) - float(a) for a, b in zip(ray_origin, plane_origin))
    distance = sum(a * float(b) for a, b in zip(offset, plane_normal)) / denominator
    if distance < 0.0:
        return None
    return tuple(
        float(o) + distance * float(d) for o, d in zip(ray_origin, ray_direction)
    )


def palette_cell_from_local_point(
    point: Sequence[float],
    layout: PaletteLayout = DEFAULT_PALETTE_LAYOUT,
) -> int | None:
    """Map a palette-local X/Z point to a row-major button index."""

    x, _y, z = (float(value) for value in point)
    half_width = layout.width * 0.5
    half_height = layout.height * 0.5
    if not (-half_width <= x <= half_width and -half_height <= z <= half_height):
        return None
    column = min(layout.columns - 1, int((x + half_width) / layout.cell_width))
    row_from_bottom = min(layout.rows - 1, int((z + half_height) / layout.cell_height))
    row = layout.rows - 1 - row_from_bottom
    return row * layout.columns + column


def palette_entries(category: str) -> tuple[str, ...]:
    """Return twelve entries: nine tools plus previous/close/next controls."""

    tool_ids = [tool.identifier for tool in tools_for_category(category)[:9]]
    tool_ids.extend(["" for _ in range(9 - len(tool_ids))])
    return tuple(tool_ids + ["__PREVIOUS__", "__CLOSE__", "__NEXT__"])


def uniform_scale_from_distances(start: float, current: float) -> float:
    if start <= 1.0e-6:
        return 1.0
    return max(0.01, min(100.0, current / start))


def snapped_angle_radians(angle: float, step_degrees: float) -> float:
    if step_degrees <= 0.0:
        return angle
    return quantize(angle, math.radians(step_degrees))
