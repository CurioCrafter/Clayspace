"""Live Cage: heavy modifiers show the plain base mesh while a hand works on an object.

While a capture holds an object, its heavy modifiers (Subdivision, Boolean,
Geometry Nodes, ...) are hidden with ``show_viewport = False`` so dragging,
sculpting and extruding cost no heavy evaluation per frame. After release the
result comes back once the hand has moved away (or after 2 s, or at once on
menu, selection, mode, save, load, undo/redo, render or session end).

``show_viewport`` is saved data, so the hidden state only lives between undo
pushes we control:

* every XR undo push goes through :func:`push_undo`, which shows the real
  modifiers, pushes, then hides them again (no extra step);
* ``obj["xrwb_live_cage"]`` is a repair note that exists only while caged;
  ``undo_post``/``redo_post``/``load_post`` repair any object that still has
  one, ``save_pre`` reveals everything and ``save_post`` hides it again.

So undo steps and saved files always hold the user's real modifier settings.

State machine (pure Python; ``bpy`` is imported lazily so tests run without it)::

    IDLE --capture--> CAGED --release--> SETTLING --away & 0.35 s | 2 s | event--> FADING --0.25 s--> IDLE
    SETTLING/FADING --capture on the same object--> CAGED

FADING is only the cage wire drawn over the restored result (``fade_lines``).
"""
from __future__ import annotations

import json
import math
import time

try:
    from bpy.app.handlers import persistent
except ImportError:  # Unit tests: no Blender.
    def persistent(function):
        return function

PREF_KEY = "live_cage"  # onboarding.DEFAULTS[PREF_KEY] = True: global on/off.
MODE_KEY = "xrwb_live_cage_mode"  # Per object: "AUTO" (missing) / "ON" / "OFF".
NOTE_KEY = "xrwb_live_cage"  # Repair note: JSON {modifier name: True}, only while caged.
MODES = ("AUTO", "ON", "OFF")
IDLE, CAGED, SETTLING, FADING = "IDLE", "CAGED", "SETTLING", "FADING"
SETTLE_MIN_S = 0.35
SETTLE_MAX_S = 2.0
AWAY_M = 0.10  # Physical metres from the object's bounding sphere.
FADE_S = 0.25
TOAST_MS = 30.0
AUTO_CHEAP_MS = 4.0  # Auto mode skips objects whose last full evaluation was faster.
WIRE_MAX_SEGMENTS = 20_000
WIRE_SKIP_EDGES = 1_000_000
SOLIDIFY_HEAVY_VERTS = 50_000
ALWAYS_HEAVY = {"SUBSURF", "MULTIRES", "BOOLEAN", "NODES", "REMESH"}


# ---- Pure part -----------------------------------------------------------

def is_heavy(modifier, vertex_count=0) -> bool:
    """Heavy = costs real time per evaluation. Hidden modifiers never count."""
    if not modifier.show_viewport:
        return False
    kind = modifier.type
    return (kind in ALWAYS_HEAVY
            or (kind == "BEVEL" and modifier.segments > 2)
            or (kind == "ARRAY" and modifier.count > 16)
            or (kind == "SOLIDIFY" and vertex_count > SOLIDIFY_HEAVY_VERTS))


def heavy_names(obj) -> list:
    data = getattr(obj, "data", None)
    vertices = getattr(data, "vertices", None)
    count = len(vertices) if vertices is not None else 0
    return [m.name for m in obj.modifiers if is_heavy(m, count)]


def settle_due(now, released_at, distance_m) -> bool:
    """Restore once the hand is away after the dwell, or after 2 s regardless."""
    age = now - released_at
    return age >= SETTLE_MAX_S or (age >= SETTLE_MIN_S and distance_m > AWAY_M)


class Cage:
    """One caged object: the modifiers we hid and where it is in the state machine."""

    __slots__ = ("obj", "names", "state", "released_at", "hand")

    def __init__(self, obj, names):
        self.obj = obj
        self.names = list(names)
        self.state = CAGED
        self.released_at = None
        self.hand = ""

    def grab(self):
        """Capture again while settling: stay caged, no restore, no evaluation."""
        self.state = CAGED
        self.released_at = None

    def release(self, now, hand=""):
        if self.state == CAGED:
            self.state, self.released_at, self.hand = SETTLING, now, hand

    def due(self, now, distance_m) -> bool:
        return self.state == SETTLING and settle_due(now, self.released_at, distance_m)


# ---- Blender part --------------------------------------------------------

_CAGES: dict = {}  # object pointer -> Cage
_EVAL_MS: dict = {}  # object pointer -> last measured full evaluation (memory only)
_FADES: list = []  # [pointer, start time, segments (N, 2, 3) float32 world]
_last = {"transaction": None, "hand": "", "selection": None}


def _pointer(obj):
    try:
        return obj.as_pointer()
    except ReferenceError:
        return None


def mode_of(obj) -> str:
    value = obj.get(MODE_KEY, "AUTO") if hasattr(obj, "get") else "AUTO"
    return value if value in MODES else "AUTO"


def set_mode(obj, mode) -> None:
    """Writes the per-object toggle; the caller pushes one undo step "XR Live Cage <Mode>"."""
    if mode not in MODES:
        raise ValueError(mode)
    obj[MODE_KEY] = mode


def enabled() -> bool:
    try:
        from . import onboarding
        return bool(onboarding.prefs().get(PREF_KEY, True))
    except (ImportError, AttributeError):
        return True


def _cageable(obj) -> bool:
    if obj.library is not None or obj.override_library is not None or not obj.modifiers:
        return False
    mode = mode_of(obj)
    if mode == "OFF":
        return False
    return mode == "ON" or _EVAL_MS.get(_pointer(obj), math.inf) >= AUTO_CHEAP_MS


def _references(modifier, obj) -> bool:
    if getattr(modifier, "object", None) == obj:
        return True
    if modifier.type == "NODES":
        return any(modifier.get(key) == obj for key in modifier.keys())
    return False


def dependents(objects) -> list:
    """Objects whose modifiers use one of ``objects`` (boolean cutters, GN Object Info)."""
    import bpy
    found = []
    for used, users in bpy.data.user_map(subset=set(objects)).items():
        for user in users:
            if (isinstance(user, bpy.types.Object) and user not in objects and user not in found
                    and any(_references(m, used) for m in user.modifiers)):
                found.append(user)
    return found


def objects_of(transaction) -> list:
    """Objects a capture edits. Unknown kinds give nothing (no cage, never wrong)."""
    import bpy
    found = [getattr(transaction, "obj", None)]
    found += [item.object_ref for item in getattr(transaction, "original_objects", ()) or ()]
    mesh = getattr(transaction, "original_mesh", None)
    if mesh is not None:
        found.append(mesh.object_ref)
    # ponytail: `target` is skipped: on select clicks it would cage then restore at once (a hitch per
    # click), and draw cuts compare the target's whole modifier signature and would cancel. Dependents
    # still cage a moved cutter's target.
    result = []
    for obj in found:
        try:
            if isinstance(obj, bpy.types.Object) and obj not in result:
                result.append(obj)
        except ReferenceError:
            pass
    return result


def _write_note(obj, names):
    obj[NOTE_KEY] = json.dumps({name: True for name in names})


def _drop_note(obj):
    if NOTE_KEY in obj.keys():
        del obj[NOTE_KEY]


def _hide(cage):
    modifiers = cage.obj.modifiers
    cage.names = [name for name in cage.names if modifiers.get(name) is not None]
    for name in cage.names:
        modifiers[name].show_viewport = False
    _write_note(cage.obj, cage.names)


def _show(cage):
    """Show the hidden modifiers. A name the user switched on (or removed) meanwhile is dropped."""
    modifiers = cage.obj.modifiers
    kept = []
    for name in cage.names:
        modifier = modifiers.get(name)
        if modifier is not None and not modifier.show_viewport:
            modifier.show_viewport = True
            kept.append(name)
    cage.names = kept
    _drop_note(cage.obj)


def enter(context, objects) -> list:
    """Capture start: cage the objects (and the targets of cutters among them). Returns names caged."""
    if not enabled():
        return []
    objects = list(objects)
    caged = []
    for obj in objects + dependents(objects):
        pointer = _pointer(obj)
        if pointer is None:
            continue
        _FADES[:] = [fade for fade in _FADES if fade[0] != pointer]
        cage = _CAGES.get(pointer)
        if cage is not None:
            cage.grab()
            caged.append(obj.name)
            continue
        if not _cageable(obj):
            continue
        names = heavy_names(obj)
        if names:
            cage = _CAGES[pointer] = Cage(obj, names)
            _hide(cage)
            caged.append(obj.name)
    return caged


def release(now=None, hand="") -> None:
    now = time.monotonic() if now is None else now
    for cage in _CAGES.values():
        cage.release(now, hand)


def _wire(obj):
    import numpy as np
    mesh = obj.data
    count = len(getattr(mesh, "edges", ()))
    if not count or count > WIRE_SKIP_EDGES:
        return None
    edges = np.empty(count * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)[::max(1, -(-count // WIRE_MAX_SEGMENTS))]
    local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", local)
    world = np.array(obj.matrix_world, dtype=np.float32)
    points = local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
    return points[edges]


def _restore(context, pointer, timed=True):
    """End a cage: real modifiers back, note gone; time the one heavy evaluation."""
    cage = _CAGES.pop(pointer)
    try:
        _show(cage)
        if not timed:
            return
        start = time.perf_counter()
        context.view_layer.update()
        ms = (time.perf_counter() - start) * 1000.0
        _EVAL_MS[pointer] = ms
        wire = _wire(cage.obj)
    except ReferenceError:
        return
    if wire is not None:
        _FADES.append([pointer, time.monotonic(), wire])
    if ms > TOAST_MS:
        from . import controllers, interaction_log
        interaction_log.emit("live_cage", f"Refined in {ms:.0f} ms", context, hand=cage.hand)
        controllers.pulse(context, cage.hand, "tick")


def restore_all(context, include_caged=True, timed=False) -> None:
    """Event exits: hand menu open, VR session end, render. Settling only when include_caged is False."""
    for pointer in [p for p, cage in _CAGES.items() if include_caged or cage.state != CAGED]:
        _restore(context, pointer, timed)


def _distance_m(context, cage, hand_positions):
    if not hand_positions or cage.hand not in hand_positions:
        return 0.0  # Unknown hand: only the 2 s rule applies.
    from mathutils import Vector
    from . import viewer_scale
    obj = cage.obj
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    centre = sum(corners, Vector()) / 8.0
    radius = max((corner - centre).length for corner in corners)
    gap = max(0.0, (Vector(hand_positions[cage.hand]) - centre).length - radius)
    return gap / max(1.0e-9, viewer_scale.world_units_per_metre(context))


def _selection(context):
    try:
        active = context.view_layer.objects.active
        return (str(context.mode), _pointer(active) if active else None,
                frozenset(_pointer(obj) for obj in context.selected_objects))
    except AttributeError:
        return None


def refresh(context, transaction, hand_positions=None, now=None) -> None:
    """Call once per main-thread tick with ``editing.active_transaction()`` and
    ``{user_path: world position}`` of the hands (None when unknown)."""
    now = time.monotonic() if now is None else now
    if transaction is not _last["transaction"]:
        if _last["transaction"] is not None:
            release(now, _last["hand"])
        if transaction is not None:
            owner = getattr(getattr(transaction, "request", None), "owner", None)
            _last["hand"] = str(getattr(owner, "identifier", ""))
            enter(context, objects_of(transaction))
        _last["transaction"] = transaction
    selection = _selection(context)
    if selection != _last["selection"]:
        _last["selection"] = selection
        restore_all(context, include_caged=False, timed=True)
    # ponytail: settling pauses while any capture is live (one at a time today): a restore mid-capture
    # could fail a capture that guards modifier state. Per-capture ownership if two captures ever coexist.
    if transaction is None:
        for pointer, cage in list(_CAGES.items()):
            try:
                due = cage.due(now, _distance_m(context, cage, hand_positions))
            except ReferenceError:
                _CAGES.pop(pointer, None)
                continue
            if due:
                _restore(context, pointer)
    _FADES[:] = [fade for fade in _FADES if now - fade[1] < FADE_S]


def readout_suffix() -> str:
    return " · cage" if _CAGES else ""


def is_caged(obj) -> bool:
    return _pointer(obj) in _CAGES


def fade_lines(now=None) -> list:
    """Plain data for the XR draw: [(segments (N, 2, 3) world float32, alpha)] in ``polish.MUTED``."""
    from . import polish
    now = time.monotonic() if now is None else now
    return [(segments, 1.0 - polish.ease_out((now - start) / FADE_S))
            for _pointer_, start, segments in _FADES if now - start < FADE_S]


# ---- Undo, save and repair -------------------------------------------------

def reveal_for_push() -> None:
    """Show every caged modifier and drop the notes, keeping the cages."""
    for pointer, cage in list(_CAGES.items()):
        try:
            _show(cage)
        except ReferenceError:
            _CAGES.pop(pointer, None)


def rehide_after_push() -> None:
    for pointer, cage in list(_CAGES.items()):
        try:
            _hide(cage)
        except ReferenceError:
            _CAGES.pop(pointer, None)


def push_undo(message):
    """``bpy.ops.ed.undo_push`` that never records a cage. Returns the operator result."""
    import bpy
    if _pushing[0]:  # Nested push (a wrapped ed.undo_push): already revealed; a second reveal would forget the cage.
        return bpy.ops.ed.undo_push(message=message)
    _pushing[0] = True
    reveal_for_push()
    try:
        return bpy.ops.ed.undo_push(message=message)
    finally:
        rehide_after_push()
        _pushing[0] = False


_pushing = [False]


def repair(_scene=None, *_args) -> int:
    """Show modifiers named in any leftover note and drop it; forget all cages. Returns objects fixed."""
    import bpy
    _CAGES.clear()
    _FADES.clear()
    _last.update(transaction=None, hand="", selection=None)
    fixed = 0
    for obj in bpy.data.objects:
        if NOTE_KEY not in obj.keys() or obj.library is not None:
            continue
        try:
            names = json.loads(obj[NOTE_KEY])
        except (TypeError, ValueError):
            names = {}
        for name in names:
            modifier = obj.modifiers.get(name)
            if modifier is not None:
                modifier.show_viewport = True
        del obj[NOTE_KEY]
        fixed += 1
    return fixed


@persistent
def _on_undo_redo_load(*_args):
    repair()


@persistent
def _on_save_pre(*_args):
    reveal_for_push()


@persistent
def _on_save_post(*_args):
    rehide_after_push()


@persistent
def _on_render_init(*_args):
    import bpy
    restore_all(bpy.context, include_caged=True)


_HANDLERS = (("undo_post", _on_undo_redo_load), ("redo_post", _on_undo_redo_load),
             ("load_post", _on_undo_redo_load), ("save_pre", _on_save_pre),
             ("save_post", _on_save_post), ("render_init", _on_render_init))


def register():
    import bpy
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)


def unregister():
    import bpy
    try:
        restore_all(bpy.context, include_caged=True)
    except (AttributeError, ReferenceError):
        _CAGES.clear()
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
