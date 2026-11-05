"""Tool Props: a real 3D model in your hand for every tool.

The meshes are built offline by scripts/build_tool_props.py into
assets/tool_props.npz (positions, normals, a part index per vertex and a
squeeze offset). Nothing is added to the user's .blend: each prop is one gpu
batch drawn in the XR POST_VIEW callback.

Prop-local frame (the controller aim pose's frame): -Z is forward along the
ray, +Y is up, metres. ``tip_offset(prop)`` is where the working tip sits in
that frame; ``draw`` puts that tip exactly on ``matrix``'s origin.

Parts: 0 handle, 1 body (fixed colours stored per prop), 2 accent (the
theme's ACCENT, so props follow the theme). Lighting is plain per-vertex
lambert with a key light fixed to the viewer, computed in numpy, so the
builtin SMOOTH_COLOR shader works on every backend with no custom GLSL.
"""
from __future__ import annotations

import math
from pathlib import Path
from time import monotonic

import numpy as np

ASSET = Path(__file__).resolve().parent / "assets" / "tool_props.npz"
# Tool ids (as physical_tools / ui use them) that share another prop's model.
ALIASES = {"sculpt_snake": "snake_hook", "sculpt_inflate": "inflate", "sculpt_crease": "crease",
           "sculpt_layer": "layer", "sculpt_mask": "mask_paint", "lab_dial": "subdiv_lab"}
SPIN = {"subdiv_lab": 1.6}  # rad/s around the long axis: the helix tip twists slowly.
SWAY_SECONDS = 0.6
KEY_LIGHT = (-0.35, 0.6, 0.72)  # View space, toward the light: above-left of your eyes.
FILL_LIGHT = (0.4, -0.5, 0.3)
AMBIENT = 0.3

_props: dict | None = None
_batches: dict = {}
_switch: dict = {}  # hand -> (prop, time it was switched to)


def _load() -> dict:
    global _props
    if _props is None:
        props = {}
        with np.load(ASSET) as data:
            for prop in (str(i) for i in data["ids"]):
                handle, body = data[f"{prop}__colours"]
                props[prop] = {
                    "pos": data[f"{prop}__pos"].astype(np.float32),
                    "nrm": data[f"{prop}__nrm"].astype(np.float32),
                    "part": data[f"{prop}__part"],
                    "sqz": data[f"{prop}__sqz"].astype(np.float32),
                    "idx": data[f"{prop}__idx"].astype(np.int32),
                    "tip": tuple(float(v) for v in data[f"{prop}__tip"]),
                    "colours": (tuple(handle), tuple(body)),
                }
        _props = props
    return _props


def prop_ids() -> tuple:
    return tuple(_load())


def prop_for(tool: str) -> str | None:
    """The prop id for a tool id, or None when the tool has no prop (show the ring only)."""
    prop = ALIASES.get(tool, tool)
    return prop if prop in _load() else None


def tip_offset(prop: str) -> tuple:
    """Working tip in the prop-local frame (metres)."""
    return _load()[prop]["tip"]


def bounds(prop: str) -> tuple:
    pos = _load()[prop]["pos"]
    return tuple(pos.min(axis=0)), tuple(pos.max(axis=0))


def triangle_count(prop: str) -> int:
    return len(_load()[prop]["idx"])


def clear() -> None:
    """Drop cached gpu batches (call from unregister, before the GPU context goes away)."""
    _batches.clear()


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-12)


def shade(prop: str, key_local, fill_local, accent, squeeze: float = 0.0):
    """(positions, rgba colours) for one prop, lit by lights given in its local frame."""
    data = _load()[prop]
    pos = data["pos"] + data["sqz"] * float(max(0.0, min(1.0, squeeze)))
    nrm = data["nrm"]
    light = (AMBIENT + (1.0 - AMBIENT) * np.clip(nrm @ _unit(key_local), 0.0, 1.0)
             + 0.25 * np.clip(nrm @ _unit(fill_local), 0.0, 1.0))
    palette = np.array((*data["colours"], tuple(accent)[:3]), dtype=np.float32)
    colours = np.ones((len(pos), 4), dtype=np.float32)
    colours[:, :3] = np.clip(palette[data["part"]] * light[:, None], 0.0, 1.0)
    return pos, colours


def _accent(theme):
    from . import polish
    if theme is None:
        return polish.ACCENT
    if isinstance(theme, str):
        return polish.THEMES.get(theme, polish.THEMES["DEFAULT"])[2]["ACCENT"]
    return tuple(theme)[:3]  # An explicit colour, e.g. the vertex-paint colour.


def _motion(prop: str, now: float, hand: str):
    """Sway after a tool switch, and the helix spin, about the prop's own origin."""
    from mathutils import Matrix
    if _switch.get(hand, ("",))[0] != prop:
        _switch[hand] = (prop, now)
    t = now - _switch[hand][1]
    matrix = Matrix.Identity(4)
    if t < SWAY_SECONDS:
        matrix = Matrix.Rotation(0.3 * math.exp(-6.0 * t) * math.sin(t * 22.0), 4, "X")
    if prop in SPIN:
        matrix = matrix @ Matrix.Rotation(now * SPIN[prop], 4, "Z")
    return matrix


def _batch(prop, model, accent, squeeze):
    """Cached batch; rebuilt only when the light direction, squeeze or accent really changes."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    view = gpu.matrix.get_model_view_matrix() @ model
    inverse = view.to_3x3().inverted_safe()
    key_local = inverse @ _vec(KEY_LIGHT)
    fill_local = inverse @ _vec(FILL_LIGHT)
    # ponytail: CPU lighting rebuilds the batch while the hand turns (~0.3 ms for 2k tris);
    # a small GLSL lit shader would make it free if the frame budget ever needs it.
    key = (prop, tuple(round(v, 1) for v in _unit(key_local)), tuple(round(v, 1) for v in _unit(fill_local)),
           round(squeeze, 2), tuple(round(c, 3) for c in accent))
    batch = _batches.get(key)
    if batch is None:
        if len(_batches) > 96:
            _batches.clear()
        shader = gpu.shader.from_builtin("SMOOTH_COLOR")
        pos, colours = shade(prop, key_local, fill_local, accent, squeeze)
        batch = _batches[key] = batch_for_shader(shader, "TRIS", {"pos": pos, "color": colours},
                                                 indices=_load()[prop]["idx"])
    return batch


def _vec(v):
    from mathutils import Vector
    return Vector(v).normalized()


def _draw_model(prop, model, accent, squeeze):
    import gpu
    previous = gpu.state.depth_test_get(), gpu.state.depth_mask_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set("NONE")
        batch = _batch(prop, model, accent, squeeze)
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(model)
            batch.draw(gpu.shader.from_builtin("SMOOTH_COLOR"))
    finally:
        gpu.state.depth_test_set(previous[0])
        gpu.state.depth_mask_set(previous[1])
        gpu.state.blend_set(previous[2])


def draw(context, prop_id, matrix, squeeze=0.0, theme=None, hand="") -> bool:
    """Draw a prop with its tip on ``matrix``'s origin (call inside the XR POST_VIEW callback).

    ``prop_id`` may be a tool id (aliases resolve). ``matrix`` is the aim pose,
    scaled by world units per metre. ``squeeze`` is the trigger value 0..1.
    ``theme`` is a polish theme key, an RGB accent, or None for the live theme.
    ``hand`` keeps each hand's switch sway apart (the off hand may show the Live Cage glove).
    ``context`` is unused (XR draw has no usable bContext); kept for the call shape.
    Returns False when there is no prop for this id.
    """
    from mathutils import Matrix
    prop = prop_for(prop_id)
    if prop is None:
        return False
    tip = tip_offset(prop)
    model = Matrix(matrix) @ Matrix.Translation((-tip[0], -tip[1], -tip[2]))
    model = model @ _motion(prop, monotonic(), hand)
    _draw_model(prop, model, _accent(theme), float(squeeze))
    return True


def wheel_icon(prop_id, matrix, size, theme=None, now=None) -> bool:
    """A small prop turning slowly in a Tool Wheel slot.

    ``matrix`` is the slot centre in the wheel plane (x right, y up, z toward
    you), ``size`` the icon's largest dimension in the same units.
    """
    from mathutils import Matrix
    prop = prop_for(prop_id)
    if prop is None:
        return False
    low, high = (np.array(b) for b in bounds(prop))
    centre = (low + high) / 2.0
    scale = size / max(float(np.max(high - low)), 1e-6)
    spin = (monotonic() if now is None else now) * 0.9
    model = (Matrix(matrix) @ Matrix.Rotation(math.radians(-35.0), 4, "Z") @ Matrix.Rotation(spin, 4, "Y")
             @ Matrix.Rotation(math.radians(90.0), 4, "X")  # Prop forward (-Z) points up the wheel.
             @ Matrix.Scale(scale, 4) @ Matrix.Translation(tuple(-centre)))
    _draw_model(prop, model, _accent(theme), 0.0)
    return True
