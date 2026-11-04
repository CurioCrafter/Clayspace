"""Runtime-only diagnostic overlay for XR Workbench M0.

The draw callbacks intentionally consume only cached Python/math values.  They
never resolve context, scene RNA, or camera data while Blender/XR is drawing.
"""

from __future__ import annotations

from mathutils import Matrix, Vector

import bpy


_window_handle = None
_xr_handle = None
_overlay_matrix = Matrix.Identity(4)
_panel_opacity = 0.25
_overlay_enabled = False
_draw_count = 0
_xr_draw_count = 0
_last_draw_error = ""
_overlay_lines = ("XR WORKBENCH", "M0 DIAGNOST", "CUBE OPAQUE", "PANEL TEST")
_cube_geometry = ()
_panel_geometry = None
_text_geometry = ()
_FONT_5X7 = {
    " ": ("00000",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10001", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "01010", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "01010", "00100", "00100", "00100", "01010", "10001"),
    "Y": ("10001", "01010", "00100", "00100", "00100", "00100", "00100"),
}


def _cube_vertices(center: Vector, size: float) -> list[tuple[float, float, float]]:
    half = size / 2.0
    return [
        tuple(center + Vector((x * half, y * half, z * half)))
        for x in (-1, 1)
        for y in (-1, 1)
        for z in (-1, 1)
    ]


def _cube_geometry_create():
    vertices = _cube_vertices(_overlay_matrix @ Vector((-1.05, 0.0, 0.0)), 0.38)
    faces = (
        ((0, 1, 3), (0, 3, 2)),
        ((4, 6, 7), (4, 7, 5)),
        ((0, 4, 5), (0, 5, 1)),
        ((2, 3, 7), (2, 7, 6)),
        ((0, 2, 6), (0, 6, 4)),
        ((1, 5, 7), (1, 7, 3)),
    )
    return tuple(
        (vertices, indices, shade)
        for shade, indices in zip((1.0, 0.85, 0.72, 0.58, 0.45, 0.32), faces)
    )


def _panel_geometry_create():
    matrix = _overlay_matrix
    corners = [
        matrix @ Vector((-0.72, 0.0, 0.42)),
        matrix @ Vector((0.72, 0.0, 0.42)),
        matrix @ Vector((0.72, 0.0, -0.42)),
        matrix @ Vector((-0.72, 0.0, -0.42)),
    ]
    return corners, ((0, 1, 2), (0, 2, 3))


def _text_geometry_create(text: str, x: float, z: float, scale: float):
    """Build world-space 5x7 text once per cached overlay revision."""
    vertices = []
    indices = []
    cursor = x
    matrix = _overlay_matrix
    for char in text.upper():
        glyph = _FONT_5X7.get(char, _FONT_5X7[" "])
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                left = cursor + column * scale
                top = z - row * scale
                start = len(vertices)
                vertices.extend(
                    (
                        matrix @ Vector((left, -0.004, top)),
                        matrix @ Vector((left + scale * 0.8, -0.004, top)),
                        matrix
                        @ Vector((left + scale * 0.8, -0.004, top - scale * 0.8)),
                        matrix @ Vector((left, -0.004, top - scale * 0.8)),
                    )
                )
                indices.extend(
                    ((start, start + 1, start + 2), (start, start + 2, start + 3))
                )
        cursor += scale * 6
    return vertices, indices


def _invalidate_draw_cache() -> None:
    """Discard CPU geometry when cached overlay data or lifecycle changes."""
    global _cube_geometry, _panel_geometry, _text_geometry
    _cube_geometry = ()
    _panel_geometry = None
    _text_geometry = ()


def _draw_cache_ensure() -> None:
    global _cube_geometry, _panel_geometry, _text_geometry
    if _panel_geometry is not None:
        return
    _cube_geometry = _cube_geometry_create()
    _panel_geometry = _panel_geometry_create()
    _text_geometry = tuple(
        _text_geometry_create(text, -0.57, z, 0.012)
        for z, text in zip((0.23, 0.08, -0.10, -0.23), _overlay_lines)
    )


def _draw_box(shader) -> None:
    from gpu_extras.batch import batch_for_shader

    color = (0.05, 0.42, 0.95, 1.0)
    for vertices, indices, shade in _cube_geometry:
        shader.uniform_float(
            "color", tuple(component * shade for component in color[:3]) + (color[3],)
        )
        batch_for_shader(shader, "TRIS", {"pos": vertices}, indices=indices).draw(
            shader
        )


def _draw_panel(shader, opacity: float) -> None:
    from gpu_extras.batch import batch_for_shader

    shader.uniform_float("color", (0.015, 0.055, 0.085, opacity))
    vertices, indices = _panel_geometry
    batch_for_shader(shader, "TRIS", {"pos": vertices}, indices=indices).draw(shader)
    shader.uniform_float("color", (0.88, 0.96, 1.0, 1.0))
    for vertices, indices in _text_geometry:
        if vertices:
            batch_for_shader(shader, "TRIS", {"pos": vertices}, indices=indices).draw(
                shader
            )


def _draw_callback(region: str = "WINDOW") -> None:
    global _draw_count, _xr_draw_count, _last_draw_error
    if not _overlay_enabled:
        return
    previous_state = None
    try:
        import gpu

        previous_state = (
            gpu.state.blend_get(),
            gpu.state.depth_test_get(),
            gpu.state.depth_mask_get(),
        )
        _draw_cache_ensure()
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(True)
        _draw_box(shader)
        gpu.state.depth_mask_set(False)
        _draw_panel(shader, _panel_opacity)
        _draw_count += 1
        if region == "XR":
            _xr_draw_count += 1
        _last_draw_error = ""
    except Exception as exc:
        _invalidate_draw_cache()
        _last_draw_error = f"{type(exc).__name__}: {exc}"
        print(f"XR Workbench overlay draw failed: {_last_draw_error}")
    finally:
        try:
            import gpu

            if previous_state is not None:
                gpu.state.blend_set(previous_state[0])
                gpu.state.depth_test_set(previous_state[1])
                gpu.state.depth_mask_set(previous_state[2])
        except Exception:
            pass


def _add_handlers() -> None:
    global _window_handle, _xr_handle
    if _window_handle is None:
        _window_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, ("WINDOW",), "WINDOW", "POST_VIEW"
        )
    if _xr_handle is None:
        # Blender's XR custom-overlay region is separate from desktop WINDOW.
        _xr_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, ("XR",), "XR", "POST_VIEW"
        )


def _remove_handlers() -> None:
    global _window_handle, _xr_handle
    for handle, region in ((_window_handle, "WINDOW"), (_xr_handle, "XR")):
        if handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, region)
            except (ReferenceError, RuntimeError, ValueError):
                pass
    _window_handle = None
    _xr_handle = None


def configure_overlay(
    enabled: bool, opacity: float, matrix: Matrix | None = None
) -> None:
    """Cache overlay data at an operator/property boundary, not draw time."""
    global _overlay_enabled, _panel_opacity, _overlay_matrix
    _overlay_enabled = bool(enabled)
    _panel_opacity = max(0.0, min(1.0, float(opacity)))
    if matrix is not None:
        _overlay_matrix = matrix.copy()
        _invalidate_draw_cache()
    if _overlay_enabled:
        _add_handlers()
    else:
        _remove_handlers()


def set_overlay_status(summary: str, detail: str = "") -> None:
    """Cache report text for the draw callback without retaining report RNA."""
    global _overlay_lines
    _overlay_lines = (
        "XR WORKBENCH",
        _clean_overlay_text(summary) or "M0 STATUS",
        _clean_overlay_text(detail) or "INPUT UNKNOWN",
        "PANEL TEST",
    )
    _invalidate_draw_cache()


def set_overlay_report(m0_status: str, camera_status: str, input_status: str) -> None:
    """Cache compact capability lines from a refresh report for runtime drawing."""
    global _overlay_lines
    _overlay_lines = (
        "XR WORKBENCH",
        _clean_overlay_text(f"M0 {m0_status}") or "M0 UNKNOWN",
        _clean_overlay_text(f"CAMERA {camera_status}") or "CAMERA UNKNOWN",
        _clean_overlay_text(f"INPUT {input_status}") or "INPUT UNKNOWN",
    )
    _invalidate_draw_cache()


def _clean_overlay_text(text: str) -> str:
    return "".join(char if char in _FONT_5X7 else " " for char in str(text).upper())[
        :16
    ]


def session_exists(context) -> bool:
    try:
        return context.window_manager.xr_session_state is not None
    except (AttributeError, ReferenceError):
        return False


def session_running(context) -> bool:
    try:
        return bool(
            session_exists(context) and bpy.types.XrSessionState.is_running(context)
        )
    except (AttributeError, RuntimeError, ReferenceError):
        return False


def overlay_status() -> dict[str, object]:
    """Read-only smoke-test instrumentation; does not touch Blender RNA."""
    return {
        "enabled": _overlay_enabled,
        "window_handler": _window_handle is not None,
        "xr_handler": _xr_handle is not None,
        "draw_count": _draw_count,
        "xr_draw_count": _xr_draw_count,
        "last_draw_error": _last_draw_error,
    }


def shutdown_runtime() -> None:
    """Context-free teardown for unregister and file-load safety."""
    global _overlay_enabled
    _overlay_enabled = False
    _remove_handlers()
    _invalidate_draw_cache()
