"""Feedback for tools that touch: haptic feels, generated sounds and contact visuals.

* Haptics: four feels (CLAY, HARD, CUT, SOFT), each with its own tap and a
  continuous texture whose rate follows the sliding speed and whose strength
  follows the pressure. Sent straight to the controller (own rate cap).
* Sound: six short sounds synthesised in numpy at start (no audio files) and
  played through aud. Playing never waits. With no audio device, sound stays
  off and everything else still works.
* Visuals: plain world-space tuples (glow ring, effect previews, crumbs, the
  commit flash) that ray_feedback merges into its XR draw.

The pure parts (synth, Texture, squash_amount, the ring helpers) use numpy
only. bpy, aud and the add-on modules are imported inside functions so tests
can load this file by path.
"""
from __future__ import annotations

import math
from time import perf_counter  # Not perf_counter(): 4.5 bundles Python 3.11, where it ticks at 15.6 ms on Windows.

import numpy as np

RATE = 48000
PRESS_FULL_M = 0.02
TAP_SPEED_MPS = 0.15
LIMIT_DEPTH_M = PRESS_FULL_M * 1.5
MAX_HANDLES = 8
MAX_PER_KIND_S = 12
MAX_PARTICLES = 64
PARTICLE_LIFE_S = 0.4
FLASH_S = 0.25
PREVIEW_S = 0.25
IMPACT_SQUASH = (0.12, 0.08)  # amount, seconds
# 0.21 tuning (spec-0.21 appendix A): below this sliding speed a hand counts as still. Index/Quest
# controllers held still jitter by about 0.2 mm per frame, which reads as 0.01-0.03 m/s at 90 Hz.
STILL_MPS = 0.03
TOUCH_GAP_S = 0.15  # At most one touch tap per hand per 0.15 s: a tip resting on the surface chatters.

FEELS = {
    "CLAY": {"tap": (0.015, 0.35), "rate": (6.0, 25.0), "pulse": 0.010, "amp": (0.15, 0.50), "freq": 60.0,
             "pitch": 0.6, "touch": "squish", "commit": "thunk", "squash": 0.35},
    "HARD": {"tap": (0.008, 0.70), "rate": (10.0, 45.0), "pulse": 0.003, "amp": (0.20, 0.60), "freq": 220.0,
             "pitch": 1.0, "touch": "tap", "commit": "thunk", "squash": 0.0},
    "CUT": {"tap": (0.004, 0.50), "rate": (15.0, 45.0), "pulse": 0.003, "amp": (0.30, 0.70), "freq": 320.0,
            "pitch": 1.6, "touch": "snip", "commit": "snip", "squash": 0.0},
    "SOFT": {"tap": (0.006, 0.20), "rate": (8.0, 30.0), "pulse": 0.004, "amp": (0.08, 0.25), "freq": 120.0,
             "pitch": 1.3, "touch": "tap", "commit": "click", "squash": 0.25},
}
TOOL_FEEL = {tool: feel for feel, tools in {
    "CLAY": ("sculpt_clay", "sculpt_inflate", "sculpt_layer", "sculpt_snake", "sculpt_grab", "rolling_pin",
             "clay_gun", "sdf_blob"),
    "HARD": ("sculpt_flatten", "mallet", "place_object", "place_prefab", "measure", "lab_dial", "retopo_loom",
             "grow_seed", "eyedropper", "live_cage"),
    "CUT": ("sculpt_crease", "chisel", "rake", "knife", "drill", "uv_island"),
    "SOFT": ("sculpt_smooth", "sandpaper", "sculpt_mask", "spray_material", "paint_vertex", "draw_curve"),
}.items() for tool in tools}
CARVE_TOOLS = {"sculpt_crease", "chisel", "rake", "drill", "knife"}
SOUND_GAIN = {"tap": 0.7, "scrape": 0.35, "squish": 0.7, "snip": 0.7, "click": 0.5, "thunk": 0.9}
LENGTH_S = {"tap": 0.030, "scrape": 0.400, "squish": 0.140, "snip": 0.060, "click": 0.012, "thunk": 0.180}
SPRAY_PITCH = 2.0


# ---- pure: sound synthesis ------------------------------------------------------

def _lowpass(x, cutoff, rate):
    """64-tap exponential kernel (a soft one-pole-like low-pass)."""
    tau = max(rate / (2.0 * math.pi * cutoff), 0.5)
    kernel = np.exp(-np.arange(64) / tau)
    return np.convolve(x, kernel / kernel.sum(), mode="same")


def _norm(x, peak=0.8):
    top = float(np.max(np.abs(x)))
    return (x * (peak / top) if top > 0 else x).astype(np.float32)


def synth(kind, rate=RATE):
    """One generated sound as float32 mono samples, peak 0.8."""
    rng = np.random.default_rng(sum(map(ord, kind)))
    n = int(round(LENGTH_S[kind] * rate))
    t = np.arange(n) / rate
    if kind == "tap":
        noise = _lowpass(rng.standard_normal(n) * np.exp(-t / 0.004), 2500.0, rate)
        x = noise / max(np.max(np.abs(noise)), 1e-9) + 0.5 * np.sin(2 * math.pi * 1800 * t) * np.exp(-t / 0.006)
    elif kind == "scrape":
        fade = int(0.010 * rate)
        noise = rng.standard_normal(n + fade)
        band = _lowpass(noise, 3000.0, rate) - _lowpass(noise, 400.0, rate)
        tt = np.arange(n + fade) / rate
        band *= 0.6 + 0.4 * np.abs(np.sin(2 * math.pi * 30 * tt))  # 12 whole grains per loop
        ramp = np.linspace(0.0, 1.0, fade)
        x = band[:n].copy()
        x[:fade] = band[:fade] * ramp + band[n:n + fade] * (1.0 - ramp)  # loop seam: end flows into start
    elif kind == "squish":
        freq = 180.0 + (70.0 - 180.0) * t / t[-1]
        env = np.minimum(t / 0.010, 1.0) * np.exp(-np.maximum(t - 0.010, 0.0) / 0.040)
        x = np.sin(2 * math.pi * np.cumsum(freq) / rate) * env
        x += 0.3 * env * _lowpass(rng.standard_normal(n), 800.0, rate) * 3.0
    elif kind == "snip":
        click = int(0.002 * rate)
        x = 0.6 * np.sin(2 * math.pi * 4500 * t) * np.exp(-t / 0.008)
        for start in (0, int(0.025 * rate)):
            burst = rng.standard_normal(click)
            x[start:start + click] += burst - _lowpass(burst, 2000.0, rate)
    elif kind == "click":
        x = np.sin(2 * math.pi * 3000 * t) * np.exp(-t / 0.002)
    elif kind == "thunk":
        freq = 70.0 + (50.0 - 70.0) * t / t[-1]
        x = np.sin(2 * math.pi * np.cumsum(freq) / rate) * np.exp(-t / 0.050)
        burst = int(0.020 * rate)
        x[:burst] += 0.5 * _lowpass(rng.standard_normal(burst), 600.0, rate) * 3.0 * np.linspace(1, 0, burst)
    else:
        raise KeyError(kind)
    if kind != "scrape":
        tail = min(n, int(0.002 * rate))
        x[-tail:] *= np.linspace(1.0, 0.0, tail)  # no click at the end
    return _norm(x)


# ---- pure: haptic texture and squash --------------------------------------------

def _clamp01(v):
    return min(1.0, max(0.0, float(v)))


class Texture:
    """Pulse train while touching: rate by sliding speed, amplitude by pressure."""

    def __init__(self, feel="HARD"):
        self.feel = feel
        self.phase = 0.0
        self.count = 0

    def step(self, dt, speed_mps, pressure, working=False):
        """[(duration, amplitude), ...] pulses due in this dt."""
        if speed_mps < STILL_MPS and (pressure <= 0.0 or not working):
            self.phase = 0.0  # Resting still: no buzz. Still and working (press-to-build): the slow pulse.
            return []
        f = FEELS[self.feel]
        lo, hi = f["rate"]
        rate = min(45.0, lo + (hi - lo) * _clamp01(speed_mps / 0.5))
        amp = min(1.0, f["amp"][0] + (f["amp"][1] - f["amp"][0]) * _clamp01(pressure) + (0.1 if working else 0.0))
        self.phase += rate * max(0.0, dt)
        pulses = []
        while self.phase >= 1.0:
            self.phase -= 1.0
            self.count += 1
            double = self.feel == "CUT" and self.count % 4 == 0
            pulses.append((f["pulse"] * (2.0 if double else 1.0), amp))
        return pulses


def squash_amount(depth_m, feel):
    return FEELS.get(feel, FEELS["HARD"])["squash"] * _clamp01(depth_m / PRESS_FULL_M)


# ---- pure: draw helpers ----------------------------------------------------------

def _basis(normal):
    """Unit normal and two tangents (plain floats: np.cross is slow on 3-vectors)."""
    x, y, z = map(float, normal)
    length = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / length, y / length, z / length
    ax, ay, az = (1.0, 0.0, 0.0) if abs(x) < 0.9 else (0.0, 1.0, 0.0)
    ux, uy, uz = y * az - z * ay, z * ax - x * az, x * ay - y * ax
    ul = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux, uy, uz = ux / ul, uy / ul, uz / ul
    return (np.array((x, y, z)), np.array((ux, uy, uz)),
            np.array((y * uz - z * uy, z * ux - x * uz, x * uy - y * ux)))


_circles: dict = {}


def _circle(segments, closed=False):
    key = (segments, closed)
    if key not in _circles:
        a = np.linspace(0.0, 2 * math.pi, segments + 1 if closed else segments, endpoint=closed)
        _circles[key] = (np.cos(a)[:, None], np.sin(a)[:, None])
    return _circles[key]


def ring_points(centre, normal, radius, segments=24, lift=0.0):
    """Closed ring as LINES pairs (tuples)."""
    n, u, v = _basis(normal)
    cos, sin = _circle(segments, True)
    pts = np.asarray(centre, dtype=np.float64) + n * lift + radius * (cos * u + sin * v)
    pairs = np.empty((segments, 2, 3))
    pairs[:, 0], pairs[:, 1] = pts[:-1], pts[1:]
    return list(map(tuple, pairs.reshape(-1, 3).tolist()))


def disc_tris(centre, normal, radius, segments=24, lift=0.0):
    n, u, v = _basis(normal)
    c = np.asarray(centre, dtype=np.float64) + n * lift
    cos, sin = _circle(segments, True)
    rim = c + radius * (cos * u + sin * v)
    tris = np.empty((segments, 3, 3))
    tris[:, 0], tris[:, 1], tris[:, 2] = c, rim[:-1], rim[1:]
    return list(map(tuple, tris.reshape(-1, 3).tolist()))


def _tetra(points, sizes):
    """4 triangles per point (N x 12 x 3) for crumbs."""
    corners = np.array(((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)), dtype=np.float64) / math.sqrt(3)
    faces = np.array(((0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2))).ravel()
    return points[:, None, :] + sizes[:, None, None] * corners[faces][None]


def _crosses(points, size):
    offsets = np.eye(3) * size
    segs = np.stack((points[:, None, :] - offsets, points[:, None, :] + offsets), axis=2)
    return [tuple(p) for p in segs.reshape(-1, 3).tolist()]


# ---- state ------------------------------------------------------------------------

_audio = {"device": None, "sounds": {}, "handles": [], "played": {}, "buffers": {}}
_loops: dict = {}      # hand -> aud handle (scrape)
_textures: dict = {}   # hand -> Texture
_hand_feel: dict = {}
_squash: dict = {}     # hand -> (normal, amount)
_impact: dict = {}     # hand -> (time, normal)
_limit: dict = {}      # hand -> last limit buzz time
_touched: dict = {}    # hand -> last time the tip entered (touch-tap debounce)
_path: dict = {}       # hand -> carved metres since the last crumbs
_last_now = [None]
_frame: dict = {"lines": [], "fills": [], "tris": []}   # rebuilt every update
_particles = {"p0": np.empty((0, 3)), "v": np.empty((0, 3)), "born": np.empty(0), "size": np.empty(0),
              "colour": [], "gravity": np.zeros(3)}
_flash: list = []      # (start, lines)
_previews: dict = {}   # hand -> (time, lines, fills)
_rng = np.random.default_rng(7)
stats = {"synth_ms": 0.0, "played": 0, "audio": False}


def _log(context, message, level="INFO"):
    try:
        from . import interaction_log
        interaction_log.emit("feel", message, context, level=level)
    except Exception:
        print(f"XR feel: {message}", flush=True)


def _pref(key, default):
    try:
        from . import onboarding
        return onboarding.prefs().get(key, default)
    except Exception:
        return default


def start(context=None):
    """Synthesise every sound and open the aud device. Never raises; sound stays off on failure."""
    began = perf_counter()
    try:
        _audio["buffers"] = {kind: synth(kind) for kind in LENGTH_S}
    except Exception as exc:  # numpy failure: no sound, haptics and visuals still work
        _log(context, f"Tool sounds off: {exc}", "WARNING")
        return False
    stats["synth_ms"] = (perf_counter() - began) * 1000.0
    try:
        import aud
        device = aud.Device()
        sounds = {kind: aud.Sound.buffer(data.reshape(-1, 1), RATE) for kind, data in _audio["buffers"].items()}
    except Exception as exc:
        _audio.update(device=None, sounds={})
        stats["audio"] = False
        _log(context, f"Tool sounds off (no audio device): {exc}", "WARNING")
        return False
    _audio.update(device=device, sounds=sounds)
    stats["audio"] = True
    return True


def stop():
    for handle in list(_audio["handles"]) + list(_loops.values()):
        try:
            handle.stop()
        except Exception:
            pass
    _audio.update(device=None, sounds={}, handles=[], played={})
    _loops.clear()
    _textures.clear()
    _hand_feel.clear()
    _squash.clear()
    _impact.clear()
    _limit.clear()
    _touched.clear()
    _path.clear()
    _flash.clear()
    _previews.clear()
    _frame.update(lines=[], fills=[], tris=[])
    _particles.update(p0=np.empty((0, 3)), v=np.empty((0, 3)), born=np.empty(0), size=np.empty(0), colour=[])
    _last_now[0] = None


def _gain(kind, strength=1.0):
    if _pref("tool_sound_mute", False):
        return 0.0
    return float(_pref("tool_sound_volume", 0.6)) * SOUND_GAIN.get(kind, 0.6) * _clamp01(strength)


def play(kind, strength=1.0, pitch=1.0, loop=False, now=None):
    """Fire-and-forget sound; returns the aud handle or None. Never waits on a handle."""
    device, sound = _audio["device"], _audio["sounds"].get(kind)
    gain = _gain(kind, strength)
    if device is None or sound is None or (gain <= 0.0 and not loop):
        return None
    now = perf_counter() if now is None else now
    handles = [h for h in _audio["handles"] if _alive(h)]
    _audio["handles"] = handles
    recent = [t for t in _audio["played"].get(kind, []) if now - t < 1.0]
    if not loop and (len(handles) >= MAX_HANDLES or len(recent) >= MAX_PER_KIND_S):
        _audio["played"][kind] = recent
        return None
    try:
        handle = device.play(sound)
        handle.volume = gain
        handle.pitch = pitch
        if loop:
            handle.loop_count = -1
        else:
            handles.append(handle)
            _audio["played"][kind] = recent + [now]
        stats["played"] += 1
        return handle
    except Exception:
        return None


def _alive(handle):
    try:
        return handle.status in (1, 2)  # aud.STATUS_PLAYING, STATUS_PAUSED
    except Exception:
        return False


def _haptic(context, hand, duration, amplitude, frequency=0.0):
    try:
        from . import controllers
        strength = float(context.window_manager.xrwb_settings.haptic_strength)
        if strength <= 0.0:
            return False
        state = context.window_manager.xr_session_state
        return bool(state.haptic_action_apply(context, controllers.ACTION_SET_NAME, controllers.HAPTIC_ACTION, hand,
                                              duration, frequency, min(1.0, amplitude * strength)))
    except Exception:
        return False


def _tail(context, hand, delay, duration, amplitude):
    try:
        from . import controllers
        strength = float(context.window_manager.xrwb_settings.haptic_strength)
        controllers._schedule_tail(hand, delay, duration, min(1.0, amplitude * strength))
    except Exception:
        pass


def event(context, hand, kind, strength=1.0, at=None, normal=None):
    """One feedback moment: haptic and/or sound for a hand (see spec 2.1/2.2)."""
    feel = _hand_feel.get(hand, "HARD")
    f = FEELS[feel]
    if kind in {"touch", "press"}:
        duration, amp = f["tap"]
        _haptic(context, hand, duration, amp * strength, f["freq"])
        if feel == "CUT":
            _tail(context, hand, 0.020, duration, amp * strength)
        play(f["touch"], strength)
    elif kind == "leave":
        handle = _loops.pop(hand, None)
        if handle is not None:
            try:
                handle.stop()
            except Exception:
                pass
    elif kind == "impact":
        _haptic(context, hand, 0.012, min(1.0, 0.3 + strength), FEELS["HARD"]["freq"])
        play("thunk", min(1.0, 0.3 + strength))
        _impact[hand] = (perf_counter(), normal)
    elif kind == "snip":
        duration, amp = FEELS["CUT"]["tap"]
        _haptic(context, hand, duration, amp * strength, FEELS["CUT"]["freq"])
        play("snip", strength)
    elif kind == "commit":
        play(f["commit"], strength)
    elif kind == "reject":
        play("click", 0.6 * strength, pitch=0.5)
    elif kind == "chip":
        play("click", 0.4 * strength, pitch=1.4)
    elif kind in {"click", "detent"}:
        play("click", strength)
    elif kind == "thunk":
        play("thunk", strength)
    # "cancel": nothing (the existing cancel haptic is enough).


def chips(at, normal, count, colour, units=1.0, now=None, tiny=False):
    """Launch crumbs along normal +-30 deg at 0.3-0.6 m/s; they fall with scene gravity and fade."""
    now = perf_counter() if now is None else now
    count = int(min(count, MAX_PARTICLES))
    if count <= 0:
        return
    if colour is None:  # contact_tools._chips has no object to hand; fall back to the theme grey.
        from . import polish
        colour = polish.MUTED
    n, u, v = _basis(normal)
    tilt = np.tan(np.radians(30.0)) * np.sqrt(_rng.random(count))
    spin = _rng.random(count) * 2 * math.pi
    direction = n + tilt[:, None] * (np.cos(spin)[:, None] * u + np.sin(spin)[:, None] * v)
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    speed = _rng.uniform(0.3, 0.6, count) * units
    size = _rng.uniform(0.0015, 0.003, count) * units * (0.4 if tiny else 1.0)
    p = _particles
    alive = now - p["born"] < PARTICLE_LIFE_S
    keep = np.flatnonzero(alive)[-(MAX_PARTICLES - count):] if MAX_PARTICLES > count else np.empty(0, int)
    p["p0"] = np.vstack((p["p0"][keep], np.repeat(np.asarray(at, dtype=np.float64)[None], count, 0)))
    p["v"] = np.vstack((p["v"][keep], direction * speed[:, None]))
    p["born"] = np.concatenate((p["born"][keep], np.full(count, now)))
    p["size"] = np.concatenate((p["size"][keep], size))
    p["colour"] = [p["colour"][k] for k in keep] + [tuple(colour)[:3]] * count
    p["gravity"] = np.array((0.0, 0.0, -9.8 * units))


def flash(points=None, corners=None, size=0.002, now=None):
    """Commit flash for 0.25 s: crosses at up to 2,000 points, or ready-made bracket LINES (corners)."""
    now = perf_counter() if now is None else now
    lines = []
    if points is not None and len(points):
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if len(pts) > 2000:
            pts = pts[np.linspace(0, len(pts) - 1, 2000).astype(int)]
        lines += _crosses(pts, size)
    if corners:
        lines += [tuple(p) for p in corners]
    if lines:
        _flash[:] = [item for item in _flash if now - item[0] < FLASH_S] + [(now, lines)]


def preview(hand, kind, data, colour=(1.0, 1.0, 1.0), now=None):
    """Live effect preview for the next draws (kept 0.25 s unless refreshed). data is a dict of world values."""
    now = perf_counter() if now is None else now
    lines, fills = [], []
    if kind == "bowl":
        from .sculpt_math import falloff
        n, u, v = _basis(data["normal"])
        c, r, step = np.asarray(data["point"], dtype=np.float64), float(data["radius"]), float(data["step"])
        t = np.linspace(0.0, 1.0, 7)
        for a in np.linspace(0.0, 2 * math.pi, 8, endpoint=False):
            d = np.cos(a) * u + np.sin(a) * v
            pts = c + (t * r)[:, None] * d + (step * falloff(t * r, r))[:, None] * n
            lines += [tuple(p) for p in np.stack((pts[:-1], pts[1:]), 1).reshape(-1, 3).tolist()]
    elif kind == "plateau":
        lines += ring_points(data["point"], data["normal"], data["radius"], lift=float(data["height"]))
    elif kind == "plane":
        n, u, v = _basis(data["normal"])
        c, s = np.asarray(data["point"], dtype=np.float64), float(data["size"])
        # ponytail: a square of the given size, not clipped to the object's AABB.
        q = [c + s * (i * u + j * v) for i, j in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        fills += [tuple(p) for p in (q[0], q[1], q[2], q[0], q[2], q[3])]
        lines += [tuple(p) for k in range(4) for p in (q[k], q[(k + 1) % 4])]
    elif kind in {"groove", "tape"}:
        pts = [tuple(p) for p in data["points"]] if kind == "groove" else [tuple(data["start"]), tuple(data["end"])]
        lines += [p for k in range(len(pts) - 1) for p in (pts[k], pts[k + 1])]
    elif kind == "cone":
        n, u, v = _basis(data["normal"])
        c, r = np.asarray(data["point"], dtype=np.float64), float(data["radius"])
        for a in np.linspace(0.0, 2 * math.pi, 6, endpoint=False):
            lines += [tuple(data["nozzle"]), tuple(c + r * (np.cos(a) * u + np.sin(a) * v))]
        lines += ring_points(c, n, r)
    elif kind == "swatch":
        colour = tuple(data["colour"])[:3]
        fills += disc_tris(data["point"], data["normal"], data["radius"])
    else:
        raise KeyError(kind)
    _previews[hand] = (now, lines, fills, colour)


def squash_for(hand):
    return _squash.get(hand)


def _brush_radius(context, tool, units, fallback):
    try:
        from . import ray_feedback
        if tool.startswith("sculpt_") or tool in {"spray_material", "paint_vertex"}:
            return ray_feedback.brush_radius(context.window_manager.xrwb_settings, tool, units)
    except Exception:
        pass
    return max(fallback, 0.004 * units)


def _object_colour(name):
    from . import polish
    try:
        import bpy
        obj = bpy.data.objects.get(name)
        material = obj.active_material if obj is not None else None
        if material is not None:
            return tuple(material.diffuse_color)[:3]
    except Exception:
        pass
    return polish.MUTED


def _working(active, hand):
    if active is None:
        return False
    owner = getattr(active, "hand", None) or getattr(getattr(active, "owner", None), "identifier", hand)
    return owner == hand


def update(context, contacts, tools, active=None, now=None):
    """Per frame: textures, scrape loops, crumbs, squash and the glow-ring draw data."""
    from . import polish
    now = perf_counter() if now is None else now
    dt = 0.0 if _last_now[0] is None else min(0.1, max(0.0, now - _last_now[0]))
    _last_now[0] = now
    try:
        from . import viewer_scale
        units = viewer_scale.world_units_per_metre(context)
    except Exception:
        units = 1.0
    texture_on = bool(_pref("contact_texture", True))
    lines, fills, tris = [], [], []
    # The scrape loop only plays while pressed: lifted but still in reach, it would hold its last volume.
    for hand in [h for h in list(_loops) if h not in contacts or not contacts[h].pressed]:
        event(context, hand, "leave")
    for hand, c in contacts.items():
        tool = tools.get(hand, "")
        feel = c.feel if c.feel in FEELS else TOOL_FEEL.get(tool, "HARD")
        _hand_feel[hand] = feel
        working = _working(active, hand) and c.pressed
        if c.entered:
            if feel == "HARD" and c.normal_speed_mps >= TAP_SPEED_MPS:
                event(context, hand, "impact", c.normal_speed_mps, c.point, c.normal)
            elif now - _touched.get(hand, -1.0) >= TOUCH_GAP_S:
                event(context, hand, "touch")
            _touched[hand] = now
        if c.left:
            event(context, hand, "leave")
            _textures.pop(hand, None)
        if not c.touching:
            _squash.pop(hand, None)
            continue
        if c.pressed and texture_on:
            texture = _textures.get(hand)
            if texture is None or texture.feel != feel:
                texture = _textures[hand] = Texture(feel)
            pulses = texture.step(dt, c.speed_mps, c.pressure, working)
            if pulses:  # A new pulse replaces the running one, so send only the last.
                duration, amp = pulses[-1]
                _haptic(context, hand, duration, amp, FEELS[feel]["freq"])
        over = c.depth_m > LIMIT_DEPTH_M
        if over and now - _limit.get(hand, -1.0) >= 0.5:
            _haptic(context, hand, 0.04, 0.3)
            _limit[hand] = now
        if c.pressed and _audio["device"] is not None:
            handle = _loops.get(hand)
            if handle is None or not _alive(handle):
                handle = _loops[hand] = play("scrape", 1.0, loop=True, now=now)
            if handle is not None:
                pitch = SPRAY_PITCH if tool == "spray_material" and working else FEELS[feel]["pitch"]
                try:
                    rub = _clamp01((c.speed_mps - STILL_MPS) / 0.4)  # Jitter while still stays silent.
                    handle.volume = _gain("scrape") * rub * (0.4 + 0.6 * c.pressure)
                    handle.pitch = pitch * (0.8 + 0.6 * _clamp01(c.speed_mps / 0.5))
                except Exception:
                    pass
        hit = _impact.get(hand)
        amount = squash_amount(c.depth_m, feel)
        if hit is not None and now - hit[0] < IMPACT_SQUASH[1]:
            amount = max(amount, IMPACT_SQUASH[0])
        _squash[hand] = (c.normal, amount) if amount > 0.0 else None
        if working:
            dig = tool == "sculpt_clay" and bool(getattr(getattr(context.window_manager, "xrwb_settings", None),
                                                         "sculpt_invert", False))
            if tool in CARVE_TOOLS or dig:
                _path[hand] = _path.get(hand, 0.0) + c.speed_mps * dt
                while _path[hand] >= 0.01:
                    _path[hand] -= 0.01
                    chips(c.point, c.normal, int(_rng.integers(2, 5)), _object_colour(c.obj_name), units, now)
            elif tool == "spray_material":
                chips(c.point, c.normal, 1, _object_colour(c.obj_name), units, now, tiny=True)
        # Glow ring: replaces the ray ring for this hand while touching.
        try:
            from .ray_feedback import TOOL_COLOURS
            colour = TOOL_COLOURS.get(tool, polish.ACCENT)
        except Exception:
            colour = polish.ACCENT
        radius = _brush_radius(context, tool, units, c.radius)
        alpha = 0.35 + 0.6 * c.pressure if c.pressed else 0.25
        lift = radius * 0.02
        lines.append((ring_points(c.point, c.normal, radius, 32, lift), colour, alpha))
        lines.append((ring_points(c.point, c.normal, radius * 0.6, 24, lift), colour, alpha))
        fills.append((disc_tris(c.point, c.normal, radius, 24, lift), colour, alpha * 0.3))
        if over:
            lines.append((ring_points(c.point, c.normal, radius * 1.06, 32, lift), polish.WARNING, 0.9))
        if c.push_dropped:
            try:
                import bpy
                obj = bpy.data.objects.get(c.obj_name)
                if obj is not None:
                    lines.append((polish.corner_brackets(obj.matrix_world, [tuple(v) for v in obj.bound_box]),
                                  polish.WARNING, 0.9))
            except Exception:
                pass
    for hand in [h for h in _hand_feel if h not in contacts]:
        _hand_feel.pop(hand, None)
        _squash.pop(hand, None)
        _textures.pop(hand, None)
    _frame.update(lines=lines, fills=fills, tris=tris)


def draw_data(now=None):
    """(lines, fills, tris): lists of (points, rgb, alpha). Lines are LINES pairs, the rest TRIS.

    Lines and fills draw depth-off; tris (crumbs) draw depth-tested after the props.
    """
    from . import polish
    now = perf_counter() if now is None else now
    lines, fills, tris = list(_frame["lines"]), list(_frame["fills"]), list(_frame["tris"])
    for hand, (born, plines, pfills, colour) in list(_previews.items()):
        if now - born > PREVIEW_S:
            del _previews[hand]
            continue
        if plines:
            lines.append((plines, colour, 0.5))
        if pfills:
            fills.append((pfills, colour, 0.5))
    for born, flines in _flash:
        age = now - born
        if 0.0 <= age < FLASH_S:
            lines.append((flines, polish.SUCCESS, 0.8 * (1.0 - age / FLASH_S)))
    p = _particles
    if len(p["born"]):
        age = now - p["born"]
        live = np.flatnonzero((age >= 0.0) & (age < PARTICLE_LIFE_S))
        if live.size:
            a = age[live][:, None]
            pos = p["p0"][live] + p["v"][live] * a + 0.5 * p["gravity"] * a * a
            shapes = _tetra(pos, p["size"][live])
            fade = np.clip((PARTICLE_LIFE_S - age[live]) / 0.1, 0.0, 1.0)
            for k, idx in enumerate(live):
                tris.append(([tuple(q) for q in shapes[k].tolist()], p["colour"][idx], float(fade[k])))
    return lines, fills, tris
