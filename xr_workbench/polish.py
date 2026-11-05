"""The finishing layer: one palette, live readouts, detents, toasts and presets.

Everything here is plain Python over cached values so the XR draw callback,
the overlay timer and the tests can all share it. Nothing writes to scenes.

* Palette: the few colours every overlay uses, so rings, plates, the wheel and
  toasts read as one product.
* Readouts: what a held tool is doing right now, in real units ("Extrude
  12.4 cm", "Moved 23 cm · 15°"), shown beside the working hand.
* Detents: the step a readout is on (every centimetre, every 10 % of scale,
  every snap cell). Crossing a step gives a crisp haptic tick, like a
  physical dial.
* Toasts: the outcome of each action floats briefly by the hand that did it
  and fades, instead of only appearing on the hand menu.
* Comfort presets: one tap sets many related settings at once.
"""
from __future__ import annotations

import math
from time import monotonic

# ---- Palette -------------------------------------------------------------

# Each theme sets every key; apply_theme copies one into the module names
# below, which the draw code reads every frame. Tool ring colours stay fixed
# (they say what a tool does). LINE_WIDTH is the ring/outline width in pixels.
THEMES = {
    "DEFAULT": ("Default", "The standard dark plates with blue highlights", {
        "ACCENT": (0.30, 0.62, 1.00), "SUCCESS": (0.36, 0.90, 0.56), "WARNING": (1.00, 0.72, 0.25),
        "DANGER": (1.00, 0.36, 0.30), "TEXT": (0.96, 0.97, 1.00), "MUTED": (0.66, 0.70, 0.78),
        "PLATE": (0.035, 0.04, 0.055), "PLATE_ALPHA": 0.84, "WHEEL_CURRENT": (0.16, 0.26, 0.40),
        "LINE_WIDTH": 2.5}),
    "MIDNIGHT": ("Midnight", "Deep indigo plates with soft violet highlights", {
        "ACCENT": (0.66, 0.56, 1.00), "SUCCESS": (0.45, 0.88, 0.72), "WARNING": (1.00, 0.76, 0.40),
        "DANGER": (1.00, 0.45, 0.55), "TEXT": (0.94, 0.93, 1.00), "MUTED": (0.70, 0.68, 0.86),
        "PLATE": (0.05, 0.04, 0.12), "PLATE_ALPHA": 0.88, "WHEEL_CURRENT": (0.22, 0.17, 0.40),
        "LINE_WIDTH": 2.5}),
    "HIGH_CONTRAST": ("High Contrast", "Black plates, white text, yellow highlights, thicker lines", {
        "ACCENT": (1.00, 0.86, 0.00), "SUCCESS": (0.30, 1.00, 0.40), "WARNING": (1.00, 0.60, 0.00),
        "DANGER": (1.00, 0.30, 0.30), "TEXT": (1.00, 1.00, 1.00), "MUTED": (0.86, 0.86, 0.86),
        "PLATE": (0.0, 0.0, 0.0), "PLATE_ALPHA": 0.97, "WHEEL_CURRENT": (0.28, 0.28, 0.28),
        "LINE_WIDTH": 4.0}),
    "OCEAN": ("Ocean", "Deep teal plates with aqua highlights", {
        "ACCENT": (0.22, 0.86, 0.86), "SUCCESS": (0.40, 0.92, 0.62), "WARNING": (1.00, 0.78, 0.38),
        "DANGER": (1.00, 0.42, 0.38), "TEXT": (0.90, 0.98, 1.00), "MUTED": (0.62, 0.80, 0.84),
        "PLATE": (0.01, 0.09, 0.12), "PLATE_ALPHA": 0.86, "WHEEL_CURRENT": (0.04, 0.28, 0.34),
        "LINE_WIDTH": 2.5}),
    "LIGHT": ("Light", "Pale plates with dark text, for bright scenes", {
        "ACCENT": (0.05, 0.36, 0.78), "SUCCESS": (0.10, 0.55, 0.25), "WARNING": (0.78, 0.46, 0.00),
        "DANGER": (0.80, 0.16, 0.12), "TEXT": (0.07, 0.09, 0.13), "MUTED": (0.32, 0.36, 0.43),
        "PLATE": (0.95, 0.96, 0.98), "PLATE_ALPHA": 0.93, "WHEEL_CURRENT": (0.78, 0.86, 0.97),
        "LINE_WIDTH": 2.5}),
}
ACCENT = SUCCESS = WARNING = DANGER = TEXT = MUTED = PLATE = WHEEL_CURRENT = (1.0, 1.0, 1.0)
PLATE_ALPHA = LINE_WIDTH = 1.0
LEVEL_COLOURS: dict = {}
_theme = ""


def apply_theme(key: str) -> str:
    """Make ``key`` (or Default if unknown) the live palette; returns the key used."""
    global LEVEL_COLOURS, _theme
    key = key if key in THEMES else "DEFAULT"
    if key != _theme:
        globals().update(THEMES[key][2])
        LEVEL_COLOURS = {"INFO": SUCCESS, "WARNING": WARNING, "ERROR": DANGER}
        _theme = key
    return key


def sync_theme() -> None:
    """Follow the Theme preference; cheap enough to call once per frame."""
    from . import onboarding
    apply_theme(onboarding.prefs()["theme"])


apply_theme("DEFAULT")


def ease_out(t: float) -> float:
    """Cubic ease-out on 0..1: fast start, soft landing."""
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def rounded_rect(x0, y0, x1, y1, radius, segments=5):
    """TRI_FAN points (centre first) of a rounded rectangle."""
    radius = max(0.0, min(radius, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    corners = ((x1 - radius, y1 - radius, 0.0), (x0 + radius, y1 - radius, 0.5 * math.pi),
               (x0 + radius, y0 + radius, math.pi), (x1 - radius, y0 + radius, 1.5 * math.pi))
    outline = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = start + 0.5 * math.pi * step / segments
            outline.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return [((x0 + x1) / 2.0, (y0 + y1) / 2.0), *outline, outline[0]]


# ---- Units -----------------------------------------------------------------

def length(value: float) -> str:
    """Scene length (Blender metres) in the friendliest unit."""
    value = float(value)
    size = abs(value)
    if size < 0.01:
        return f"{value * 1000.0:.1f} mm"
    if size < 1.0:
        return f"{value * 100.0:.1f} cm"
    return f"{value:.2f} m"


# ---- Readouts and detents ------------------------------------------------------

def _grab_delta(transaction):
    """(distance, degrees, scale) of the first held object since it was picked up."""
    items = getattr(transaction, "original_objects", None) or ()
    if not items:
        return None
    item = items[0]
    try:
        now = item.object_ref.matrix_world
    except ReferenceError:
        return None
    before = item.matrix_world
    distance = (now.translation - before.translation).length
    degrees = math.degrees(before.to_quaternion().rotation_difference(now.to_quaternion()).angle)
    size_before = max(1.0e-12, sum(before.to_scale()) / 3.0)
    return distance, degrees, (sum(now.to_scale()) / 3.0) / size_before


def readout(transaction, gesture=None) -> str:
    """One short line describing the live edit, or "" when there is nothing to say."""
    if transaction is None:
        return ""
    from . import draw_editing, editing, face_editing, node_garden, physical_tools, uv_tools
    if isinstance(transaction, face_editing.FaceTransaction):
        name = {"extrude": "Extrude", "extrude_free": "Free extrude", "inset": "Inset"}.get(transaction.tool)
        if name is None:
            return f"Scale face ×{transaction.value:.2f}"
        extra = "  (A: next segment)" if transaction.tool != "inset" and abs(transaction.value) > 1e-6 else ""
        return f"{name} {length(transaction.value)}{extra}"
    if isinstance(transaction, draw_editing.DrawTransaction):
        obj = getattr(transaction, "obj", None)
        if obj is None:
            return "Drag to size it"
        dims = obj.dimensions
        return "Size " + " × ".join(length(value).replace(" ", " ") for value in dims)
    if isinstance(transaction, uv_tools.UVIslandStroke):
        return f"Island {len(transaction.faces)} faces · crease {transaction.angle:.0f}°"
    if isinstance(transaction, node_garden.NodeLinkStroke):
        if transaction.target is None:
            return "Drop to remove the link" if transaction.picked_link is not None else "Aim at a socket"
        node_name = node_garden.socket_key(transaction.target)[0]
        return f"Link to {node_name}"
    from . import creative_tools, prefabs, sdf_modeling
    if isinstance(transaction, prefabs.PrefabStroke):
        return f"{transaction.name} · " + " × ".join(length(v) for v in transaction.dimensions)
    if isinstance(transaction, sdf_modeling.SdfStroke):
        if transaction.moved is not None:
            return "Moving a blob"
        verb = "Carve" if transaction.carve else "Blob"
        return f"{verb} {length(transaction.radius())} · {len(transaction.added)} added"
    if isinstance(transaction, creative_tools.CurvePenStroke):
        return f"Pen {length(transaction.length())} · {len(transaction.points)} points"
    if isinstance(transaction, creative_tools.VertexPaintStroke):
        return f"Paint · {round(transaction.pressure * 100)}%"
    if isinstance(transaction, physical_tools.MeasureStroke):
        return f"Measure {length(transaction.length)}"
    if isinstance(transaction, physical_tools.SculptStroke):
        pressure = getattr(transaction, "pressure", 1.0)
        return f"{transaction.label.removeprefix('Sculpt ')} · {round(pressure * 100)}%"
    if isinstance(transaction, editing.EditTransaction) and getattr(transaction, "mode", "") == "OBJECT":
        delta = _grab_delta(transaction)
        if delta is None:
            return ""
        distance, degrees, scale = delta
        parts = [f"Moved {length(distance)}"]
        if degrees >= 0.5:
            parts.append(f"{degrees:.0f}°")
        if abs(scale - 1.0) >= 0.005:
            parts.append(f"×{scale:.2f}")
        return " · ".join(parts)
    return ""


def detent_index(transaction):
    """Which step the live edit is on; a change means one haptic tick. None: no detents."""
    if transaction is None:
        return None
    from . import editing, face_editing, uv_tools
    if isinstance(transaction, face_editing.FaceTransaction):
        if transaction.tool == "face_scale":
            return math.floor(transaction.value * 10.0 + 1e-9)  # Every 10 %.
        return math.floor(transaction.value * 100.0 + 1e-9)  # Every scene centimetre.
    if isinstance(transaction, uv_tools.UVIslandStroke):
        return len(transaction.faces)  # Each time the island grows or shrinks.
    from . import creative_tools, sdf_modeling
    if isinstance(transaction, creative_tools.CurvePenStroke):
        return math.floor(transaction.length() * 10.0)  # Every 10 cm of ink.
    if isinstance(transaction, sdf_modeling.SdfStroke):
        return len(transaction.added)  # A tick for every blob laid down.
    if isinstance(transaction, editing.EditTransaction) and getattr(transaction, "mode", "") == "OBJECT":
        delta = _grab_delta(transaction)
        if delta is None:
            return None
        distance, degrees, scale = delta
        if getattr(transaction, "snap", None) is not None:
            item = transaction.original_objects[0]
            try:
                return tuple(round(v, 4) for v in item.object_ref.matrix_world.translation)
            except ReferenceError:
                return None
        return (math.floor(scale * 10.0 + 1e-9), math.floor(degrees / 15.0))  # 10 % and 15° steps.
    return None


# ---- Toasts ---------------------------------------------------------------

TOAST_SECONDS = 2.6
TOAST_FADE = 0.6
QUIET_STAGES = {"ui_event", "begin", "hover", "two_hand", "tracking"}


def toast_alpha(age: float) -> float:
    if age < 0.0 or age > TOAST_SECONDS:
        return 0.0
    if age < 0.12:
        return max(0.05, ease_out(age / 0.12))  # A same-tick toast is not a faded one.
    return min(1.0, (TOAST_SECONDS - age) / TOAST_FADE)


def current_toast(now=None):
    """(message, level, hand, alpha) of the newest action outcome, or None when faded."""
    from . import interaction_log
    toast = interaction_log.last_toast()
    if toast is None:
        return None
    message, level, hand, stamp = toast
    alpha = toast_alpha((monotonic() if now is None else now) - stamp)
    return (message, level, hand, alpha) if alpha > 0.0 else None


# ---- Hover outline ---------------------------------------------------------

def corner_brackets(matrix, corners, fraction=0.22):
    """LINES points for short brackets at the 8 corners of a local bounding box."""
    from mathutils import Vector
    world = [matrix @ Vector(corner) for corner in corners]
    lows = [min(c[i] for c in corners) for i in range(3)]
    highs = [max(c[i] for c in corners) for i in range(3)]
    lines = []
    for corner, point in zip(corners, world):
        for axis in range(3):
            other = list(corner)
            other[axis] = highs[axis] if abs(corner[axis] - lows[axis]) < 1e-9 else lows[axis]
            target = matrix @ Vector(other)
            lines += [tuple(point), tuple(point + (target - point) * fraction)]
    return lines


# ---- Comfort presets --------------------------------------------------------

FEEL_PRESETS = {
    "GENTLE": ("Gentle", "Soft haptics, big labels and wheel, slow precise hands", {
        "haptic_strength": 0.45, "precision": 0.2, "wheel_size": 0.14, "label_scale": 1.3,
        "snap_grid_cm": 5.0, "snap_angle": 15.0, "hover_ticks": False, "detents": True,
        "toasts": True, "readouts": True, "hover_outline": True}),
    "BALANCED": ("Balanced", "The defaults", {
        "haptic_strength": 0.7, "precision": 0.25, "wheel_size": 0.11, "label_scale": 1.0,
        "snap_grid_cm": 5.0, "snap_angle": 15.0, "hover_ticks": True, "detents": True,
        "toasts": True, "readouts": True, "hover_outline": True}),
    "PRO": ("Pro", "Crisp haptics, compact labels and wheel, fine snapping", {
        "haptic_strength": 0.9, "precision": 0.35, "wheel_size": 0.08, "label_scale": 0.85,
        "snap_grid_cm": 1.0, "snap_angle": 5.0, "hover_ticks": True, "detents": True,
        "toasts": True, "readouts": True, "hover_outline": False}),
}


def apply_preset(key: str) -> str:
    from . import onboarding
    label, _description, values = FEEL_PRESETS[key]
    for name, value in values.items():
        onboarding.set_pref(name, value)
    return label


def matching_preset() -> str:
    """The preset every value still matches, or "" once anything was tuned by hand."""
    from . import onboarding
    prefs = onboarding.prefs()
    for key, (_label, _description, values) in FEEL_PRESETS.items():
        if all(abs(float(prefs[name]) - float(value)) < 1e-6 if isinstance(value, float)
               else prefs[name] == value for name, value in values.items()):
            return key
    return ""
