"""XR lifecycle, world-space palette rendering, hover, and haptics."""

from __future__ import annotations

import time
from typing import Iterable

import bpy
from mathutils import Matrix, Quaternion, Vector

from .model import (
    ACTION_MAP_NAME,
    CAP_BRIDGED,
    CAP_DESKTOP,
    CAP_EXPERIMENTAL,
    CAP_NATIVE,
    DEFAULT_PALETTE_LAYOUT,
    RIGHT_HAND,
    cycle_category,
    palette_cell_from_local_point,
    palette_entries,
    ray_plane_intersection,
    tool_by_id,
)
from .properties import get_preferences


_draw_handle = None
_palette_matrix: Matrix | None = None
_hover_entry = ""
_palette_render_entries: tuple[str, ...] = ()
_palette_render_labels: tuple[str, ...] = ()
_palette_header = ""
_active_tool_label = ""
_notice_matrix: Matrix | None = None
_notice_text = ""
_notice_error = False
_notice_until = 0.0


_FONT_5X7 = {
    " ": ("00000",) * 7,
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10011", "10101", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
}


def session_running(context) -> bool:
    try:
        return bool(
            context.window_manager.xr_session_state
            and bpy.types.XrSessionState.is_running(context)
        )
    except (AttributeError, RuntimeError):
        return False


def session_exists(context) -> bool:
    """True for both starting and fully running OpenXR sessions."""

    try:
        return context.window_manager.xr_session_state is not None
    except (AttributeError, ReferenceError):
        return False


def _controller_ray(context) -> tuple[Vector, Vector] | None:
    session_state = context.window_manager.xr_session_state
    if session_state is None:
        return None
    controller_index = 1
    try:
        origin = Vector(
            session_state.controller_aim_location_get(context, controller_index)
        )
        rotation = Quaternion(
            session_state.controller_aim_rotation_get(context, controller_index)
        )
    except (IndexError, RuntimeError, TypeError, ValueError):
        return None
    direction = (rotation @ Vector((0.0, 0.0, -1.0))).normalized()
    return origin, direction


def _make_palette_matrix(context) -> Matrix | None:
    state = context.window_manager.xr_session_state
    if state is None:
        return None
    try:
        viewer_location = Vector(state.viewer_pose_location)
        viewer_rotation = Quaternion(state.viewer_pose_rotation)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    forward = (viewer_rotation @ Vector((0.0, 0.0, -1.0))).normalized()
    up = (viewer_rotation @ Vector((0.0, 1.0, 0.0))).normalized()
    right = forward.cross(up).normalized()
    up = right.cross(forward).normalized()
    normal = -forward
    center = viewer_location + forward * 0.85
    matrix = Matrix.Identity(4)
    for row in range(3):
        matrix[row][0] = right[row]
        matrix[row][1] = normal[row]
        matrix[row][2] = up[row]
        matrix[row][3] = center[row]
    return matrix


def toggle_palette(context, visible: bool | None = None) -> bool:
    global _palette_matrix, _hover_entry
    settings = context.scene.vrw_settings
    target = not settings.palette_visible if visible is None else bool(visible)
    if target and not session_running(context):
        settings.last_error = "Start an XR session before opening the headset palette"
        return False
    settings.last_error = ""
    settings.palette_visible = target
    _hover_entry = ""
    _palette_matrix = _make_palette_matrix(context) if target else None
    if target:
        refresh_palette_cache(context)
        _add_draw_handler()
    return target


def push_notice(
    context, message: str, *, is_error: bool = False, duration: float | None = None
) -> None:
    """Show a short world-space result/safety notice inside the headset."""

    global _notice_matrix, _notice_text, _notice_error, _notice_until
    upper = message.upper()
    if "DELETE" in upper and ("ARMED" in upper or "CONFIRMATION" in upper):
        _notice_text = "DELETE AGAIN"
    elif "SAVE ARMED" in upper:
        _notice_text = "SAVE AGAIN"
    elif "INPUT STREAM" in upper:
        _notice_text = "INPUT STALL"
    elif "CONTROLLER RAY" in upper:
        _notice_text = "NO CTRL RAY"
    elif "ACTIVE VR GESTURE" in upper:
        _notice_text = "GESTURE BUSY"
    elif "STEAMVR SYSTEM" in upper:
        _notice_text = "SYSTEM/DESK"
        if duration is None:
            duration = 5.0
    elif "UNSAVED FILE" in upper:
        _notice_text = "DESKTOP SAVE"
        if duration is None:
            duration = 4.0
    else:
        _notice_text = message
    _notice_error = is_error
    _notice_until = time.monotonic() + (
        duration if duration is not None else (3.0 if is_error else 1.8)
    )
    _notice_matrix = _make_palette_matrix(context)
    _add_draw_handler()


def cycle_palette(context, direction: int) -> str:
    settings = context.scene.vrw_settings
    settings.active_category = cycle_category(settings.active_category, direction)
    settings.last_status = f"Palette: {settings.active_category.title()}"
    refresh_palette_cache(context)
    return settings.active_category


def _palette_hit(ray: tuple[Vector, Vector]) -> str:
    if _palette_matrix is None:
        return ""
    origin, direction = ray
    plane_origin = _palette_matrix.translation
    plane_normal = Vector(
        (_palette_matrix[0][1], _palette_matrix[1][1], _palette_matrix[2][1])
    )
    hit = ray_plane_intersection(origin, direction, plane_origin, plane_normal)
    if hit is None:
        return ""
    local = _palette_matrix.inverted_safe() @ Vector(hit)
    index = palette_cell_from_local_point(local)
    if index is None:
        return ""
    return (
        _palette_render_entries[index] if index < len(_palette_render_entries) else ""
    )


def palette_hit_from_event(context, event) -> str:
    xr = getattr(event, "xr", None)
    if xr is not None:
        try:
            origin = Vector(xr.controller_location)
            rotation = Quaternion(xr.controller_rotation)
            direction = (rotation @ Vector((0.0, 0.0, -1.0))).normalized()
            return _palette_hit((origin, direction))
        except (AttributeError, TypeError, ValueError):
            pass
    ray = _controller_ray(context)
    return _palette_hit(ray) if ray else ""


def choose_palette_entry(context, entry: str) -> tuple[bool, str]:
    if not entry:
        return False, "Point at a palette button"
    if entry == "__PREVIOUS__":
        return True, f"Palette: {cycle_palette(context, -1)}"
    if entry == "__NEXT__":
        return True, f"Palette: {cycle_palette(context, 1)}"
    if entry == "__CLOSE__":
        toggle_palette(context, False)
        return True, "Palette closed"
    settings = context.scene.vrw_settings
    settings.delete_armed_until = 0.0
    settings.delete_armed_signature = ""
    settings.save_armed_until = 0.0
    settings.save_armed_path = ""
    settings.active_tool = entry
    label = entry_label(entry, context)
    refresh_palette_cache(context)
    preferences = get_preferences(context)
    if preferences is None or preferences.close_palette_on_select:
        toggle_palette(context, False)
    context.scene.vrw_settings.last_status = f"Active VR tool: {label}"
    return True, f"Selected {label}"


def entry_label(entry: str, context) -> str:
    if entry == "__PREVIOUS__":
        return "Prev"
    if entry == "__NEXT__":
        return "Next"
    if entry == "__CLOSE__":
        return "Close"
    if entry.startswith("FAVORITE_"):
        preferences = get_preferences(context)
        if preferences:
            index = int(entry.rsplit("_", 1)[1])
            return (
                getattr(preferences, f"favorite_{index}_label") or f"Favorite {index}"
            )
    try:
        return tool_by_id(entry).label
    except KeyError:
        return entry


def refresh_palette_cache(context) -> None:
    """Cache all RNA-derived palette text outside the XR draw callback."""

    global \
        _palette_render_entries, \
        _palette_render_labels, \
        _palette_header, \
        _active_tool_label
    settings = context.scene.vrw_settings
    _palette_render_entries = palette_entries(settings.active_category)
    _palette_render_labels = tuple(
        entry_label(entry, context) for entry in _palette_render_entries
    )
    _palette_header = settings.active_category
    _active_tool_label = entry_label(settings.active_tool, context)


def update_palette_hover_from_event(context, event) -> str:
    """Update hover feedback only from an operator-supplied valid context."""

    global _hover_entry
    entry = palette_hit_from_event(context, event)
    if entry != _hover_entry:
        _hover_entry = entry
        if entry:
            _apply_hover_haptic(context)
    return entry


def clear_palette_hover() -> None:
    global _hover_entry
    _hover_entry = ""


def _entry_color(entry: str, hovered: bool) -> tuple[float, float, float, float]:
    if entry.startswith("__"):
        base = (0.13, 0.18, 0.24, 0.94)
    elif not entry:
        base = (0.06, 0.07, 0.08, 0.72)
    else:
        capability = tool_by_id(entry).capability
        base = {
            CAP_NATIVE: (0.04, 0.30, 0.34, 0.94),
            CAP_BRIDGED: (0.18, 0.22, 0.38, 0.94),
            CAP_EXPERIMENTAL: (0.42, 0.20, 0.05, 0.94),
            CAP_DESKTOP: (0.25, 0.10, 0.30, 0.94),
        }[capability]
    if not hovered:
        return base
    return tuple(min(1.0, channel * 1.65 + 0.08) for channel in base[:3]) + (1.0,)


def _button_rect(index: int):
    layout = DEFAULT_PALETTE_LAYOUT
    row = index // layout.columns
    column = index % layout.columns
    width = layout.cell_width - layout.gap
    height = layout.cell_height - layout.gap
    x = -layout.width * 0.5 + layout.cell_width * (column + 0.5)
    z = layout.height * 0.5 - layout.cell_height * (row + 0.5)
    return x, z, width, height


def _world_points(
    local_points: Iterable[tuple[float, float, float]],
    matrix: Matrix | None = None,
) -> list[tuple[float, float, float]]:
    matrix = _palette_matrix if matrix is None else matrix
    assert matrix is not None
    return [tuple(matrix @ Vector(point)) for point in local_points]


def _draw_rect(
    shader,
    x: float,
    z: float,
    width: float,
    height: float,
    color,
    matrix: Matrix | None = None,
):
    from gpu_extras.batch import batch_for_shader

    y = 0.0
    x0, x1 = x - width * 0.5, x + width * 0.5
    z0, z1 = z - height * 0.5, z + height * 0.5
    vertices = _world_points(
        ((x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)), matrix
    )
    batch = batch_for_shader(
        shader, "TRIS", {"pos": vertices}, indices=((0, 1, 2), (0, 2, 3))
    )
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_text(
    shader,
    text: str,
    x: float,
    z: float,
    max_width: float,
    max_height: float,
    matrix: Matrix | None = None,
):
    from gpu_extras.batch import batch_for_shader

    text = text.upper().replace("_", " ")[:12]
    if not text:
        return
    columns = len(text) * 6 - 1
    pixel = min(max_width / max(columns, 1), max_height / 7.0)
    left = x - columns * pixel * 0.5
    top = z + 3.5 * pixel
    vertices = []
    indices = []
    for char_index, character in enumerate(text):
        glyph = _FONT_5X7.get(character, _FONT_5X7[" "])
        for row, bits in enumerate(glyph):
            for column, enabled in enumerate(bits):
                if enabled != "1":
                    continue
                cx = left + (char_index * 6 + column + 0.5) * pixel
                cz = top - (row + 0.5) * pixel
                half = pixel * 0.38
                start = len(vertices)
                vertices.extend(
                    _world_points(
                        (
                            (cx - half, 0.003, cz - half),
                            (cx + half, 0.003, cz - half),
                            (cx + half, 0.003, cz + half),
                            (cx - half, 0.003, cz + half),
                        ),
                        matrix,
                    )
                )
                indices.extend(
                    ((start, start + 1, start + 2), (start, start + 2, start + 3))
                )
    if not vertices:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": vertices}, indices=indices)
    shader.uniform_float("color", (0.92, 0.98, 1.0, 1.0))
    batch.draw(shader)


def _draw_callback():
    """Draw only cached Python/math data; never dereference global RNA context."""

    notice_active = bool(
        _notice_text and _notice_matrix is not None and time.monotonic() < _notice_until
    )
    if _palette_matrix is None and not notice_active:
        return
    try:
        import gpu

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        if _palette_matrix is not None:
            _draw_rect(shader, 0.0, 0.285, 0.72, 0.055, (0.025, 0.075, 0.10, 0.96))
            _draw_text(
                shader,
                _palette_header,
                0.0,
                0.285,
                0.64,
                0.028,
            )
            _draw_rect(shader, 0.0, -0.285, 0.72, 0.055, (0.025, 0.075, 0.10, 0.96))
            _draw_text(
                shader,
                "ACTIVE " + _active_tool_label,
                0.0,
                -0.285,
                0.64,
                0.028,
            )
            for index, entry in enumerate(_palette_render_entries):
                x, z, width, height = _button_rect(index)
                _draw_rect(
                    shader,
                    x,
                    z,
                    width,
                    height,
                    _entry_color(entry, entry == _hover_entry),
                )
                _draw_text(
                    shader,
                    _palette_render_labels[index],
                    x,
                    z,
                    width * 0.88,
                    height * 0.46,
                )
        if notice_active:
            # Destructive confirmations must stay legible even when scene
            # geometry sits between the viewer and the world-space strip.
            gpu.state.depth_test_set("NONE")
            notice_matrix = (
                _palette_matrix if _palette_matrix is not None else _notice_matrix
            )
            notice_z = 0.355 if _palette_matrix is not None else 0.0
            notice_color = (
                (0.62, 0.08, 0.045, 0.97) if _notice_error else (0.02, 0.34, 0.24, 0.97)
            )
            _draw_rect(
                shader,
                0.0,
                notice_z,
                0.76,
                0.075,
                notice_color,
                notice_matrix,
            )
            _draw_text(
                shader,
                _notice_text,
                0.0,
                notice_z,
                0.68,
                0.038,
                notice_matrix,
            )
    except Exception as exc:
        # There is deliberately no RNA/context access in this error path.
        print(f"VR Workbench XR overlay: {exc}")
    finally:
        try:
            import gpu

            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")
        except Exception:
            pass


def _add_draw_handler():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), "XR", "POST_VIEW"
        )


def _remove_draw_handler():
    global _draw_handle
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "XR")
        except (ReferenceError, RuntimeError, ValueError):
            pass
        _draw_handle = None


def _apply_hover_haptic(context):
    preferences = get_preferences(context)
    if preferences and not preferences.hover_haptics:
        return
    state = context.window_manager.xr_session_state
    if state is None:
        return
    try:
        state.haptic_action_apply(
            context,
            ACTION_MAP_NAME,
            "haptic",
            RIGHT_HAND,
            0.025,
            0.0,
            0.16,
        )
    except (AttributeError, RuntimeError):
        pass


def prepare_xr_session(context) -> None:
    """Prepare overlay state while Blender supplies a valid start context."""

    end_xr_session(context, remove_draw_handler=False)
    settings = context.window_manager.xr_session_settings
    settings.show_custom_overlays = True
    settings.show_controllers = True
    settings.controller_draw_style = "DARK_RAY"
    refresh_palette_cache(context)
    _add_draw_handler()


def end_xr_session(context=None, *, remove_draw_handler: bool = True) -> None:
    """Reset runtime state without ever falling back to global bpy.context."""

    global \
        _hover_entry, \
        _palette_matrix, \
        _palette_render_entries, \
        _palette_render_labels, \
        _palette_header, \
        _active_tool_label, \
        _notice_matrix, \
        _notice_text, \
        _notice_error, \
        _notice_until
    _palette_matrix = None
    _hover_entry = ""
    _palette_render_entries = ()
    _palette_render_labels = ()
    _palette_header = ""
    _active_tool_label = ""
    _notice_matrix = None
    _notice_text = ""
    _notice_error = False
    _notice_until = 0.0
    if context is not None:
        try:
            context.scene.vrw_settings.palette_visible = False
        except (AttributeError, ReferenceError, RuntimeError):
            pass

    # Transaction cleanup must not be skipped merely because the active scene
    # disappeared during file load or XR teardown.
    from .transactions import cancel_all_transactions

    cancel_all_transactions()
    if remove_draw_handler:
        _remove_draw_handler()


def shutdown_runtime() -> None:
    """Remove global callbacks during add-on teardown, context-free."""

    end_xr_session(None, remove_draw_handler=True)
