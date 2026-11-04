"""Opt-in bare optical-hand routing for XR Workbench.

Only WINDOW modal timer events query the native hand API.  Other desktop events
always pass through so Blender's native editor queue remains authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite, radians, sqrt
from time import monotonic
from typing import Any

import bpy

from .gestures import PinchGate


HANDS = ("RIGHT", "LEFT")
REQUIRED_JOINTS = (5, 7, 10)
POSITION_FLAGS = 10
OPTICAL_SOURCE = 1
WATCHDOG_SECONDS = 0.25
TIMER_SECONDS = 0.02
TWO_HAND_COHERENCE_SECONDS = 0.05
TWO_HAND_MIN_SEPARATION_METRES = 0.08


@dataclass(frozen=True, slots=True)
class HandObservation:
    hand: str
    generation: int
    timestamp: float
    thumb_tip: tuple[float, float, float]
    index_proximal: tuple[float, float, float]
    index_tip: tuple[float, float, float]
    viewer_scale: float
    navigation_signature: tuple[float, ...]

    def __post_init__(self) -> None:
        values = (
            self.timestamp,
            self.viewer_scale,
            *self.thumb_tip,
            *self.index_proximal,
            *self.index_tip,
            *self.navigation_signature,
        )
        if self.hand not in HANDS or not all(isfinite(value) for value in values):
            raise ValueError("Hand observation values must be finite and valid")
        if self.generation < 0 or self.viewer_scale <= 0.0:
            raise ValueError("Hand generation and viewer scale must be valid")

    @property
    def pinch_metres(self) -> float:
        return _distance(self.thumb_tip, self.index_tip) / self.viewer_scale

    @property
    def pinch_position(self) -> tuple[float, float, float]:
        return tuple(
            (left + right) * 0.5 for left, right in zip(self.thumb_tip, self.index_tip)
        )

    @property
    def ray(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        direction = _subtract(self.index_tip, self.index_proximal)
        length = _length(direction)
        if length <= 1.0e-8:
            raise ValueError("Index pointing ray is degenerate")
        return self.index_tip, tuple(value / length for value in direction)


@dataclass(slots=True)
class OpticalGestureState:
    gates: dict[str, PinchGate] = field(
        default_factory=lambda: {
            hand: PinchGate(
                close_metres=0.020,
                open_metres=0.030,
                dwell_seconds=0.050,
            )
            for hand in HANDS
        }
    )
    active_hand: str | None = None
    thresholds: tuple[float, float, float] = (0.020, 0.030, 0.050)

    def configure(self, close: float, open_: float, dwell: float) -> None:
        values = close, open_, dwell
        if not all(isfinite(value) for value in values):
            raise ValueError("Pinch settings must be finite")
        if not 0.0 < close < open_ or dwell < 0.0:
            raise ValueError("Pinch close/open/dwell settings are invalid")
        if self.active_hand is not None:
            raise RuntimeError("Pinch thresholds are frozen during capture")
        if values == self.thresholds:
            return
        self.thresholds = values
        self.gates = {
            hand: PinchGate(
                close_metres=close,
                open_metres=open_,
                dwell_seconds=dwell,
            )
            for hand in HANDS
        }

    def update(self, observation: HandObservation) -> str:
        if self.active_hand is not None and observation.hand != self.active_hand:
            return "ignored"
        outcome = self.gates[observation.hand].update(
            distance_metres=observation.pinch_metres,
            timestamp=observation.timestamp,
            generation=observation.generation,
            optical=True,
            tracked=True,
        )
        if outcome == "press" and self.active_hand is None:
            self.active_hand = observation.hand
        if outcome in {"release", "cancel"} and self.active_hand == observation.hand:
            self.active_hand = None
        return outcome

    def invalidate(self, hand: str) -> str:
        outcome = self.gates[hand].reset()
        if self.active_hand == hand:
            self.active_hand = None
            return "cancel"
        return outcome

    def reset(self) -> None:
        for gate in self.gates.values():
            gate.reset()
        self.active_hand = None


@dataclass(slots=True)
class TwoHandScaleState:
    gates: dict[str, PinchGate] = field(
        default_factory=lambda: {hand: PinchGate(0.020, 0.030, 0.050) for hand in HANDS}
    )
    phase: str = "idle"
    first_hand: str | None = None
    released_hand: str | None = None
    observations: dict[str, HandObservation] = field(default_factory=dict)
    initial_separation_metres: float = 0.0
    pivot_world: tuple[float, float, float] | None = None
    generation: int = -1
    navigation_signature: tuple[float, ...] = ()

    def configure(self, close: float, open_: float, dwell: float) -> None:
        if self.phase != "idle":
            raise RuntimeError("Two-hand thresholds are frozen during capture")
        probe = PinchGate(close, open_, dwell)
        current = next(iter(self.gates.values()))
        if (
            current.close_metres,
            current.open_metres,
            current.dwell_seconds,
        ) == (close, open_, dwell):
            return
        self.gates = {hand: PinchGate(close, open_, dwell) for hand in HANDS}
        del probe

    def update(self, observation: HandObservation) -> str:
        gate = self.gates[observation.hand]
        previous_timestamp = gate.last_timestamp
        if (
            previous_timestamp >= 0.0
            and observation.timestamp < previous_timestamp
            and self.phase in {"waiting", "active", "frozen"}
        ):
            self._clear("cooldown")
            return "cancel"
        outcome = gate.update(
            distance_metres=observation.pinch_metres,
            timestamp=observation.timestamp,
            generation=observation.generation,
            optical=True,
            tracked=True,
        )
        # Equal source timestamps are valid briefly, but they are not a new
        # sensor frame and must not replace the last accepted pose.
        if outcome == "stale":
            return outcome
        if self.phase in {"waiting", "active", "frozen"} and (
            observation.generation != self.generation
            or not _navigation_matches(
                observation.navigation_signature,
                self.navigation_signature,
            )
        ):
            self._clear("cooldown")
            return "cancel"
        self.observations[observation.hand] = observation
        if self.phase == "cooldown":
            if all(gate.armed and not gate.pressed for gate in self.gates.values()):
                self._clear("idle")
                return "ready"
            return "cooldown"
        if self.phase == "idle":
            if outcome == "press":
                self.phase = "waiting"
                self.first_hand = observation.hand
                self.generation = observation.generation
                self.navigation_signature = observation.navigation_signature
                return "waiting_started"
            return outcome
        if self.phase == "waiting":
            if observation.hand == self.first_hand and outcome in {"release", "cancel"}:
                self._clear("cooldown")
                return "cancel"
            first = self.observations.get(self.first_hand)
            if (
                observation.hand != self.first_hand
                and first is not None
                and (
                    observation.generation != first.generation
                    or not _navigation_matches(
                        observation.navigation_signature,
                        first.navigation_signature,
                    )
                )
            ):
                self._clear("cooldown")
                return "cancel"
            if observation.hand != self.first_hand and outcome == "press":
                if first is None or not self._coherent(first, observation):
                    self._clear("cooldown")
                    return "cancel"
                separation = _distance(first.pinch_position, observation.pinch_position)
                separation /= first.viewer_scale
                if separation < TWO_HAND_MIN_SEPARATION_METRES:
                    self._clear("cooldown")
                    return "cancel"
                self.phase = "active"
                self.initial_separation_metres = separation
                self.pivot_world = tuple(
                    (a + b) * 0.5
                    for a, b in zip(first.pinch_position, observation.pinch_position)
                )
                self.generation = first.generation
                self.navigation_signature = first.navigation_signature
                return "begin"
            return "waiting"
        if self.phase == "active":
            if outcome == "cancel":
                self._clear("cooldown")
                return "cancel"
            if outcome == "release":
                self.phase = "frozen"
                self.released_hand = observation.hand
                return "freeze"
            return "preview" if outcome == "hold" else outcome
        if self.phase == "frozen":
            if outcome == "cancel":
                self._clear("cooldown")
                return "cancel"
            if observation.hand != self.released_hand and outcome == "release":
                self._clear("cooldown")
                return "commit"
            if observation.hand == self.released_hand and outcome == "press":
                return "finishing_no_resume"
            return "frozen"
        return outcome

    def scale_ratio(self) -> float | None:
        if self.phase != "active" or self.initial_separation_metres <= 0.0:
            return None
        left = self.observations.get("LEFT")
        right = self.observations.get("RIGHT")
        if left is None or right is None or not self._coherent(left, right):
            return None
        current = (
            _distance(left.pinch_position, right.pinch_position) / left.viewer_scale
        )
        return max(0.05, min(20.0, current / self.initial_separation_metres))

    def invalidate(self) -> str:
        was_captured = self.phase in {"active", "frozen"}
        self._clear("cooldown")
        return "cancel" if was_captured else "cancel_wait"

    def reset(self) -> None:
        self.gates = {hand: PinchGate(0.020, 0.030, 0.050) for hand in HANDS}
        self._clear("idle")

    def _coherent(self, first: HandObservation, second: HandObservation) -> bool:
        return (
            first.generation == second.generation
            and abs(first.timestamp - second.timestamp) <= TWO_HAND_COHERENCE_SECONDS
            and _navigation_matches(
                first.navigation_signature,
                second.navigation_signature,
            )
        )

    def _clear(self, phase: str) -> None:
        self.phase = phase
        self.first_hand = None
        self.released_hand = None
        self.observations.clear()
        self.initial_separation_metres = 0.0
        self.pivot_world = None
        self.generation = -1
        self.navigation_signature = ()


@dataclass(slots=True)
class OpticalStreamWatchdog:
    streams: dict[str, tuple[int, float, float]] = field(default_factory=dict)

    def observe(
        self,
        hand: str,
        generation: int,
        source_timestamp: float,
        local_now: float,
    ) -> tuple[bool, str]:
        if not all(isfinite(value) for value in (source_timestamp, local_now)):
            return False, "hand stream timestamp is not finite"
        previous = self.streams.get(hand)
        if previous is None or previous[0] != generation:
            self.streams[hand] = (generation, source_timestamp, local_now)
            return True, "advanced"
        _old_generation, old_timestamp, last_advance = previous
        if source_timestamp < old_timestamp:
            return False, "hand stream timestamp moved backward"
        if source_timestamp > old_timestamp:
            self.streams[hand] = (generation, source_timestamp, local_now)
            return True, "advanced"
        if local_now - last_advance > WATCHDOG_SECONDS:
            return False, "hand stream timestamp stalled beyond 250 ms"
        return True, "repeated"

    def invalidate(self, hand: str) -> None:
        self.streams.pop(hand, None)

    def reset(self) -> None:
        self.streams.clear()


@dataclass(slots=True)
class _Capture:
    hand: str
    generation: int
    kind: str
    owner: Any | None = None
    transaction: Any | None = None
    last_ray: tuple[tuple[float, float, float], tuple[float, float, float]] | None = (
        None
    )
    navigation_signature: tuple[float, ...] = ()
    surface_generation: int = 0


@dataclass(slots=True)
class _TwoHandCapture:
    owner: Any
    transaction: Any


_gesture_state = OpticalGestureState()
_two_hand_state = TwoHandScaleState()
_stream_watchdog = OpticalStreamWatchdog()
_optical_provider = None
_capture: _Capture | None = None
_support_capture: _Capture | None = None
_support_gate = PinchGate()
_two_hand_capture: _TwoHandCapture | None = None
_router_operator: Any | None = None
_last_status = "Optical hands are off"


def _subtract(left, right):
    return tuple(float(a) - float(b) for a, b in zip(left, right))


def _length(value) -> float:
    return sqrt(sum(component * component for component in value))


def _distance(left, right) -> float:
    return _length(_subtract(left, right))


def _set_status(context, message: str) -> None:
    global _last_status
    _last_status = message
    settings = getattr(getattr(context, "window_manager", None), "xrwb_settings", None)
    if settings is not None and hasattr(settings, "hand_status"):
        settings.hand_status = message


def status() -> str:
    return _last_status


def _pinch_settings(context) -> tuple[float, float, float]:
    settings = getattr(context.window_manager, "xrwb_settings", None)
    return (
        float(getattr(settings, "pinch_close", 0.020)),
        float(getattr(settings, "pinch_open", 0.030)),
        float(getattr(settings, "pinch_dwell", 0.050)),
    )


def _session_state(context):
    return getattr(context.window_manager, "xr_session_state", None)


def set_optical_provider(provider) -> None:
    """Install a calibrated Index-camera provider while the router is stopped.

    The provider must implement start(context), sample(context, hand), and
    stop(context). Camera-relative diagnostics must never be registered here.
    """
    global _optical_provider
    if _router_operator is not None:
        raise RuntimeError("Stop optical hands before changing providers")
    _optical_provider = provider
    _stream_watchdog.reset()


def _read_provider_observation(context, hand: str, now: float):
    try:
        sample = _optical_provider.sample(context, hand)
        if (
            sample["source"] != "index_camera_optical"
            or sample["space"] != "blender_world"
            or sample["world_output"] is not True
            or sample["active"] is not True
        ):
            raise ValueError("provider sample lacks qualified optical world provenance")
        state = _session_state(context)
        navigation = _navigation_signature(context, state)
        if navigation is None or not _navigation_matches(
            tuple(sample["navigation_signature"]), navigation
        ):
            raise ValueError("provider world mapping does not match XR navigation")
        generation = int(sample["generation"])
        timestamp = float(sample["timestamp"])
        if generation <= 0 or not isfinite(timestamp) or timestamp < 0:
            raise ValueError("provider generation or timestamp is invalid")
        fresh, reason = _stream_watchdog.observe(hand, generation, timestamp, now)
        if not fresh:
            raise ValueError(reason)
        observation = HandObservation(
            hand=hand,
            generation=generation,
            timestamp=timestamp,
            thumb_tip=tuple(sample["thumb_tip"]),
            index_proximal=tuple(sample["index_proximal"]),
            index_tip=tuple(sample["index_tip"]),
            viewer_scale=navigation[0],
            navigation_signature=navigation,
        )
        observation.ray
        return observation, "qualified optical world sample active"
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        # Keep the last timestamp after a fault. The same stale frame must not
        # become a fresh first frame on the next timer tick.
        return None, f"optical provider sample rejected: {exc}"


def _navigation_signature(context, state) -> tuple[float, ...] | None:
    try:
        signature = tuple(
            float(value) for value in state.hand_tracking_world_transform(context)
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return (
        signature
        if len(signature) == 17
        and signature[0] > 1.0e-8
        and all(isfinite(value) for value in signature)
        else None
    )


def _navigation_matches(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return len(left) == len(right) and all(
        abs(a - b) <= 1.0e-6 for a, b in zip(left, right)
    )


def _read_observation(
    context, hand: str, now: float
) -> tuple[HandObservation | None, str]:
    if _optical_provider is not None:
        return _read_provider_observation(context, hand, now)
    state = _session_state(context)
    if state is None:
        return None, "native XR session state is unavailable"
    try:
        available, active, source, seconds, nanoseconds, generation = (
            state.hand_tracking_info(context, hand=hand)
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return None, f"native hand info unavailable: {exc}"
    if not available or not active:
        _stream_watchdog.invalidate(hand)
        return None, f"{hand.lower()} optical hand is not active"
    if int(source) != OPTICAL_SOURCE:
        _stream_watchdog.invalidate(hand)
        return None, f"{hand.lower()} hand source is not unobstructed optical"
    timestamp = float(seconds) + float(nanoseconds) * 1.0e-9
    fresh, freshness = _stream_watchdog.observe(
        hand,
        int(generation),
        timestamp,
        now,
    )
    if not fresh:
        return None, freshness
    navigation = _navigation_signature(context, state)
    if navigation is None:
        return None, "XR viewer navigation transform is unavailable"
    scale = navigation[0]
    joints: dict[int, tuple[Any, ...]] = {}
    try:
        for joint in REQUIRED_JOINTS:
            sample = state.hand_joint_sample(context, hand=hand, joint=joint)
            if len(sample) != 9 or int(sample[0]) & POSITION_FLAGS != POSITION_FLAGS:
                return None, f"{hand.lower()} required joint tracking is incomplete"
            joints[joint] = sample
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return None, f"native hand joint sample unavailable: {exc}"
    try:
        observation = HandObservation(
            hand=hand,
            generation=int(generation),
            timestamp=timestamp,
            thumb_tip=tuple(float(value) for value in joints[5][1:4]),
            index_proximal=tuple(float(value) for value in joints[7][1:4]),
            index_tip=tuple(float(value) for value in joints[10][1:4]),
            viewer_scale=scale,
            navigation_signature=navigation,
        )
    except (TypeError, ValueError) as exc:
        return None, f"native hand sample was invalid: {exc}"
    try:
        observation.ray
    except ValueError as exc:
        return None, str(exc)
    return observation, "optical sample active"


def _surface_event(hand: str, event_type: str, value: str, ray=None) -> bool:
    operator = getattr(getattr(bpy.ops, "wm", None), "xr_editor_surface_event", None)
    if operator is None:
        return False
    properties: dict[str, Any] = {
        "type": event_type,
        "value": value,
        "pointer_id": 0 if hand == "LEFT" else 1,
        "unicode": "",
        "shift": False,
        "ctrl": False,
        "alt": False,
        "use_ray": ray is not None,
    }
    if ray is not None:
        properties["ray_origin"], properties["ray_direction"] = ray
    try:
        return "FINISHED" in operator("EXEC_DEFAULT", **properties)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _task_card_event(
    context,
    hand: str,
    phase: str,
    generation: int,
    ray,
    *,
    cancel_only: bool = False,
) -> bool:
    state = _session_state(context)
    operator = getattr(getattr(bpy.ops, "wm", None), "xr_task_card_event", None)
    if state is None or not hasattr(state, "task_card_info") or operator is None:
        return False
    properties: dict[str, Any] = {
        "phase": phase,
        "generation": int(generation),
        "pointer_id": 0 if hand == "LEFT" else 1,
        "cancel_only": cancel_only,
    }
    if ray is not None:
        properties["ray_origin"], properties["ray_direction"] = ray
    try:
        return "FINISHED" in operator("EXEC_DEFAULT", **properties)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _task_card_hit(
    context, observation: HandObservation, *, cancel_only: bool = False
) -> tuple[bool, int]:
    state = _session_state(context)
    if state is None or not hasattr(state, "task_card_info"):
        return False, 0
    try:
        info = state.task_card_info(context)
        visible, layout_generation, drawn_generation = info[:3]
        generation = int(drawn_generation)
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return False, 0
    if not visible or generation <= 0 or generation != int(layout_generation):
        return False, generation
    return (
        _task_card_event(
            context, observation.hand, "MOVE", generation, observation.ray,
            cancel_only=cancel_only,
        ),
        generation,
    )


def _editing_contract():
    try:
        from . import editing, interaction
    except ImportError:
        return None, None
    return editing, interaction


def _pointing_rotation(
    observation: HandObservation,
) -> tuple[float, float, float, float]:
    """Use tracked index direction for pitch/yaw; this has no hand roll."""
    from mathutils import Vector

    rotation = Vector((0.0, 0.0, -1.0)).rotation_difference(Vector(observation.ray[1]))
    return rotation.w, rotation.x, rotation.y, rotation.z


def _begin_edit(context, observation: HandObservation) -> tuple[Any, Any] | None:
    editing, interaction = _editing_contract()
    if editing is None or interaction is None:
        return None
    settings = getattr(context.window_manager, "xrwb_settings", None)
    try:
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.OPTICAL,
            observation.hand.lower(),
            observation.generation,
        )
        tool = interaction.TransformTool(
            str(getattr(settings, "edit_tool", interaction.TransformTool.MOVE.value))
        )
        request = interaction.EditRequest(
            owner=owner,
            tool=tool,
            target_generation=0,
            pose=interaction.Pose(
                observation.pinch_position,
                _pointing_rotation(observation)
                if tool is interaction.TransformTool.ROTATE
                else (1.0, 0.0, 0.0, 0.0),
            ),
            options=interaction.TransformOptions(
                constraint=(
                    interaction.Constraint.FREE
                    if tool is interaction.TransformTool.ROTATE
                    else interaction.Constraint(
                        str(getattr(settings, "edit_constraint", "free"))
                    )
                ),
                translation_snap=float(getattr(settings, "translation_snap", 0.0)),
                rotation_snap_radians=radians(
                    float(getattr(settings, "rotation_snap_degrees", 0.0))
                ),
                scale_snap=float(getattr(settings, "scale_snap", 0.0)),
            ),
        )
        return owner, editing.begin(context, request)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _selection_requested(context) -> bool:
    settings = getattr(context.window_manager, "xrwb_settings", None)
    return str(getattr(settings, "edit_tool", "select")) == "select"


def _scene_raycast_target(context, ray):
    """Return the original object behind the nearest evaluated-scene hit."""

    try:
        from mathutils import Vector

        origin, direction = ray
        result = context.scene.ray_cast(
            context.evaluated_depsgraph_get(),
            Vector(origin),
            Vector(direction),
        )
        hit, _location, _normal, _face_index, evaluated, _matrix = result
        if not hit or evaluated is None:
            return None
        original = getattr(evaluated, "original", None)
        return original if original is not None else evaluated
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"evaluated scene ray cast unavailable: {exc}") from exc


def _begin_selection(
    context,
    observation: HandObservation,
) -> tuple[tuple[Any, Any] | None, str]:
    editing, interaction = _editing_contract()
    if editing is None or interaction is None:
        return None, "Object selection adapter is unavailable"
    if str(getattr(context, "mode", "")) != "OBJECT":
        return (
            None,
            "Select currently supports object mode only; mesh component selection is pending",
        )
    try:
        target = _scene_raycast_target(context, observation.ray)
    except RuntimeError as exc:
        return None, str(exc)
    if target is None:
        return None, "Optical selection ray did not hit an object"
    settings = getattr(context.window_manager, "xrwb_settings", None)
    try:
        slop = float(getattr(settings, "selection_click_slop_m", 0.010))
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.OPTICAL,
            observation.hand.lower(),
            observation.generation,
        )
        request = interaction.SelectionRequest(
            owner=owner,
            target_generation=0,
            anchor_pose=interaction.Pose(observation.pinch_position),
            world_units_per_metre=observation.viewer_scale,
            click_slop_metres=slop,
        )
        transaction = editing.begin_object_selection(context, request, target)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return None, f"Object selection could not begin: {exc}"
    return (owner, transaction), f"Object target frozen: {target.name}"


def _selection_sample(capture: _Capture, observation: HandObservation):
    _editing, interaction = _editing_contract()
    if interaction is None:
        return None
    return interaction.InputSample(
        owner=capture.owner,
        target_generation=0,
        pose=interaction.Pose(observation.pinch_position),
        timestamp_seconds=monotonic(),
    )


def _two_hand_scale_requested(context) -> bool:
    settings = getattr(context.window_manager, "xrwb_settings", None)
    return str(getattr(settings, "edit_tool", "move")) == "scale" and not bool(
        getattr(settings, "scale_one_handed", False)
    )


def _begin_two_hand_edit(context) -> bool:
    global _two_hand_capture
    editing, interaction = _editing_contract()
    pivot = _two_hand_state.pivot_world
    if editing is None or interaction is None or pivot is None:
        return False
    settings = getattr(context.window_manager, "xrwb_settings", None)
    try:
        owner = interaction.CaptureOwner(
            interaction.InputProvenance.OPTICAL,
            "two-hand-scale",
            _two_hand_state.generation,
        )
        request = interaction.EditRequest(
            owner=owner,
            tool=interaction.TransformTool.SCALE,
            target_generation=0,
            pose=interaction.Pose(pivot, (1.0, 0.0, 0.0, 0.0)),
            options=interaction.TransformOptions(
                constraint=interaction.Constraint.FREE,
                scale_snap=float(getattr(settings, "scale_snap", 0.0)),
                min_scale=0.05,
                max_scale=20.0,
                pivot_world=pivot,
            ),
        )
        _two_hand_capture = _TwoHandCapture(owner, editing.begin(context, request))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        _two_hand_capture = None
        return False
    return True


def _preview_two_hand_edit(context) -> bool:
    capture = _two_hand_capture
    ratio = _two_hand_state.scale_ratio()
    pivot = _two_hand_state.pivot_world
    if capture is None or ratio is None or pivot is None:
        return False
    _editing, interaction = _editing_contract()
    if interaction is None:
        return False
    try:
        result = capture.transaction.update(
            context,
            interaction.InputSample(
                owner=capture.owner,
                target_generation=0,
                pose=interaction.Pose(pivot, (1.0, 0.0, 0.0, 0.0)),
                timestamp_seconds=monotonic(),
                uniform_scale=ratio,
            ),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return getattr(getattr(result, "decision", None), "value", "") == "apply"


def _cancel_two_hand(context, reason: str) -> None:
    global _two_hand_capture
    capture = _two_hand_capture
    if capture is not None:
        try:
            capture.transaction.cancel(context, capture.owner)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    _two_hand_capture = None
    _two_hand_state.invalidate()
    _set_status(context, reason)


def _commit_two_hand(context) -> None:
    global _two_hand_capture
    capture = _two_hand_capture
    if capture is None:
        _set_status(context, "Two-hand scale had no active edit to commit")
        return
    try:
        result = capture.transaction.commit(context, capture.owner)
        decision = getattr(getattr(result, "decision", None), "value", "")
        if decision != "apply":
            capture.transaction.cancel(context, capture.owner)
            _set_status(context, "Two-hand scale commit was rejected; cancelled")
        else:
            _set_status(context, "Two-hand scale committed")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        try:
            capture.transaction.cancel(context, capture.owner)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        _set_status(context, "Two-hand scale commit failed; cancelled")
    _two_hand_capture = None


def _update_edit(context, capture: _Capture, observation: HandObservation) -> bool:
    _editing, interaction = _editing_contract()
    if interaction is None:
        return False
    try:
        tool = capture.transaction.request.tool
        result = capture.transaction.update(
            context,
            interaction.InputSample(
                owner=capture.owner,
                target_generation=0,
                pose=interaction.Pose(
                    observation.pinch_position,
                    _pointing_rotation(observation)
                    if tool is interaction.TransformTool.ROTATE
                    else (1.0, 0.0, 0.0, 0.0),
                ),
                timestamp_seconds=monotonic(),
            ),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return getattr(getattr(result, "decision", None), "value", "") not in {
        "cancel_required",
        "rejected",
    }


def _reset_support_cancel(context) -> None:
    global _support_capture
    capture = _support_capture
    _support_capture = None
    if capture is not None:
        _task_card_event(
            context, capture.hand, "CANCEL", capture.surface_generation,
            capture.last_ray, cancel_only=True,
        )
    _support_gate.reset()


def _prepare_support_cancel(context) -> None:
    global _support_gate
    _reset_support_cancel(context)
    _support_gate = PinchGate(*_gesture_state.thresholds)


def _process_support_cancel(
    context, primary: _Capture, observation: HandObservation
) -> bool:
    """Route the free hand only to Session or Cancel while a scene edit owns capture."""

    global _support_capture
    if observation.hand == primary.hand or observation.generation != primary.generation or not _navigation_matches(
        observation.navigation_signature, primary.navigation_signature
    ):
        _reset_support_cancel(context)
        return False
    outcome = _support_gate.update(
        distance_metres=observation.pinch_metres,
        timestamp=observation.timestamp,
        generation=observation.generation,
        optical=True,
        tracked=True,
    )
    if outcome == "cancel":
        _reset_support_cancel(context)
        return False
    if outcome in {"stale", "neutral_required"}:
        return False
    capture = _support_capture
    if capture is not None:
        state = _session_state(context)
        try:
            visible, generation, drawn = state.task_card_info(context)[:3]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            visible = generation = drawn = 0
        if not visible or generation != drawn or generation != capture.surface_generation:
            _reset_support_cancel(context)
            return False
        if outcome == "release":
            _support_capture = None
            _task_card_event(
                context, observation.hand, "RELEASE", capture.surface_generation,
                observation.ray, cancel_only=True,
            )
            return _capture is not primary
        _task_card_event(
            context, observation.hand, "MOVE", capture.surface_generation,
            observation.ray, cancel_only=True,
        )
        return False
    if outcome in {"hover", "release"} and observation.pinch_metres >= _support_gate.open_metres:
        _task_card_hit(context, observation, cancel_only=True)
    elif outcome == "press":
        state = _session_state(context)
        try:
            visible, generation, drawn = state.task_card_info(context)[:3]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            visible = generation = drawn = 0
        if visible and generation > 0 and generation == drawn and _task_card_event(
            context, observation.hand, "PRESS", generation, observation.ray,
            cancel_only=True,
        ):
            _support_capture = _Capture(
                observation.hand, observation.generation, "support_cancel",
                last_ray=observation.ray,
                navigation_signature=observation.navigation_signature,
                surface_generation=generation,
            )
        else:
            _support_gate.reset()
    return False


def _sample_support_cancel(context, primary: _Capture, now: float) -> bool:
    if primary.kind not in {"edit", "selection"}:
        _reset_support_cancel(context)
        return False
    other_hand = "LEFT" if primary.hand == "RIGHT" else "RIGHT"
    observation, _reason = _read_observation(context, other_hand, now)
    if observation is None:
        _reset_support_cancel(context)
        return False
    return _process_support_cancel(context, primary, observation)


def _cancel_capture(context, reason: str) -> None:
    global _capture
    _reset_support_cancel(context)
    capture = _capture
    if capture is not None:
        if capture.kind == "task_card":
            _task_card_event(
                context,
                capture.hand,
                "CANCEL",
                capture.surface_generation,
                capture.last_ray,
            )
        elif capture.kind == "pointer":
            _surface_event(capture.hand, "ESC", "PRESS")
            _surface_event(capture.hand, "LEFTMOUSE", "RELEASE", capture.last_ray)
        elif capture.transaction is not None:
            try:
                capture.transaction.cancel(context, capture.owner)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        _gesture_state.invalidate(capture.hand)
    _capture = None
    _set_status(context, reason)


def _process_observation(context, observation: HandObservation) -> None:
    global _capture
    outcome = _gesture_state.update(observation)
    ray = observation.ray
    if outcome in {"stale", "ignored"}:
        return
    if _capture is not None:
        _capture.last_ray = ray
    if outcome in {"hover", "neutral_required"}:
        card_hit, _generation = _task_card_hit(context, observation)
        if card_hit:
            return
        _surface_event(observation.hand, "MOUSEMOVE", "NOTHING", ray)
        return
    if outcome == "press":
        card_hit, card_generation = _task_card_hit(context, observation)
        if card_hit:
            if _task_card_event(
                context,
                observation.hand,
                "PRESS",
                card_generation,
                ray,
            ):
                _capture = _Capture(
                    observation.hand,
                    observation.generation,
                    "task_card",
                    last_ray=ray,
                    navigation_signature=observation.navigation_signature,
                    surface_generation=card_generation,
                )
                _set_status(
                    context, f"{observation.hand.title()} optical pinch: task card"
                )
            else:
                _gesture_state.invalidate(observation.hand)
                _set_status(context, "Native task card rejected pinch capture")
            return
        _surface_event(observation.hand, "MOUSEMOVE", "NOTHING", ray)
        if _surface_event(observation.hand, "LEFTMOUSE", "PRESS", ray):
            _capture = _Capture(
                observation.hand,
                observation.generation,
                "pointer",
                last_ray=ray,
                navigation_signature=observation.navigation_signature,
            )
            _set_status(context, f"{observation.hand.title()} optical pinch: editor")
            return
        if _selection_requested(context):
            selection, message = _begin_selection(context, observation)
            if selection is None:
                _gesture_state.invalidate(observation.hand)
                _set_status(context, message)
                return
            owner, transaction = selection
            _capture = _Capture(
                observation.hand,
                observation.generation,
                "selection",
                owner,
                transaction,
                ray,
                observation.navigation_signature,
            )
            _prepare_support_cancel(context)
            _set_status(context, message)
            return
        edit = _begin_edit(context, observation)
        if edit is None:
            _gesture_state.invalidate(observation.hand)
            _set_status(
                context, "Optical pinch hit no editor card or editable selection"
            )
            return
        owner, transaction = edit
        _capture = _Capture(
            observation.hand,
            observation.generation,
            "edit",
            owner,
            transaction,
            ray,
            observation.navigation_signature,
        )
        _prepare_support_cancel(context)
        tool_name = str(
            getattr(getattr(transaction, "request", None), "tool", "transform")
        )
        suffix = (
            " (one-hand accessibility route)" if "scale" in tool_name.lower() else ""
        )
        _set_status(
            context, f"{observation.hand.title()} optical pinch: transform{suffix}"
        )
        return
    if _capture is None or observation.hand != _capture.hand:
        return
    if outcome == "hold":
        if _capture.kind == "task_card":
            _task_card_event(
                context,
                observation.hand,
                "MOVE",
                _capture.surface_generation,
                ray,
            )
        elif _capture.kind == "pointer":
            _surface_event(observation.hand, "MOUSEMOVE", "NOTHING", ray)
        elif _capture.kind == "selection":
            sample = _selection_sample(_capture, observation)
            try:
                result = (
                    _capture.transaction.update(context, sample)
                    if sample is not None
                    else None
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                result = None
            decision = getattr(getattr(result, "decision", None), "value", "")
            if decision != "apply":
                _cancel_capture(
                    context,
                    getattr(result, "message", "Selection click was cancelled"),
                )
        elif not _update_edit(context, _capture, observation):
            _cancel_capture(context, "Optical edit update was rejected; cancelled")
        return
    if outcome == "release":
        capture = _capture
        if capture.kind == "task_card":
            _task_card_event(
                context,
                observation.hand,
                "RELEASE",
                capture.surface_generation,
                ray,
            )
        elif capture.kind == "pointer":
            _surface_event(observation.hand, "MOUSEMOVE", "NOTHING", ray)
            _surface_event(observation.hand, "LEFTMOUSE", "RELEASE", ray)
        elif capture.kind == "selection":
            sample = _selection_sample(capture, observation)
            try:
                result = (
                    capture.transaction.update(context, sample)
                    if sample is not None
                    else None
                )
                if getattr(getattr(result, "decision", None), "value", "") != "apply":
                    raise RuntimeError(
                        getattr(result, "message", "Selection release was rejected")
                    )
                committed = capture.transaction.commit(context, capture.owner)
                if (
                    getattr(getattr(committed, "decision", None), "value", "")
                    != "apply"
                ):
                    raise RuntimeError(
                        getattr(committed, "message", "Selection commit was rejected")
                    )
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                try:
                    capture.transaction.cancel(context, capture.owner)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
                _capture = None
                _set_status(context, f"Object selection cancelled: {exc}")
                return
        else:
            try:
                capture.transaction.commit(context, capture.owner)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                capture.transaction.cancel(context, capture.owner)
        _capture = None
        _set_status(context, "Optical pinch released")
    elif outcome == "cancel":
        _cancel_capture(context, "Optical generation changed; gesture cancelled")


def _adopt_pointer_pinch(observation: HandObservation) -> None:
    gate = _gesture_state.gates[observation.hand]
    gate.pressed = True
    gate.armed = True
    gate.generation = observation.generation
    gate.last_timestamp = observation.timestamp
    gate.candidate_since = None
    _gesture_state.active_hand = observation.hand


def _sample_two_hand(context, now: float) -> None:
    global _capture
    observations: list[HandObservation] = []
    unavailable_reasons: list[str] = []
    for hand in HANDS:
        observation, reason = _read_observation(context, hand, now)
        if observation is None:
            _two_hand_state.gates[hand].reset()
            unavailable_reasons.append(reason)
            if _two_hand_state.phase in {"active", "frozen"}:
                _cancel_two_hand(context, f"{reason}; two-hand scale cancelled")
                return
            if (
                _two_hand_state.phase == "waiting"
                and hand == _two_hand_state.first_hand
            ):
                _two_hand_state.invalidate()
                _set_status(
                    context,
                    f"{reason}; first scale hand lost, return both hands neutral",
                )
                return
            # A single visible hand must remain able to operate the native
            # presentation card while scale is idle or waiting for hand two.
            continue
        if _two_hand_state.phase in {"active", "frozen"} and (
            observation.generation != _two_hand_state.generation
            or not _navigation_matches(
                observation.navigation_signature,
                _two_hand_state.navigation_signature,
            )
        ):
            _cancel_two_hand(
                context,
                "Two-hand provider generation or viewer transform changed; cancelled",
            )
            return
        observations.append(observation)

    if not observations:
        if unavailable_reasons:
            _set_status(context, unavailable_reasons[0])
        return

    preview = False
    for observation in observations:
        event = _two_hand_state.update(observation)
        if event == "waiting_started":
            card_hit, card_generation = _task_card_hit(context, observation)
            if card_hit:
                if _task_card_event(
                    context,
                    observation.hand,
                    "PRESS",
                    card_generation,
                    observation.ray,
                ):
                    _capture = _Capture(
                        observation.hand,
                        observation.generation,
                        "task_card",
                        last_ray=observation.ray,
                        navigation_signature=observation.navigation_signature,
                        surface_generation=card_generation,
                    )
                    _adopt_pointer_pinch(observation)
                    _two_hand_state.reset()
                    _set_status(context, "Optical pinch: task card")
                else:
                    _two_hand_state.invalidate()
                    _set_status(context, "Native task card rejected pinch capture")
                return
            _surface_event(observation.hand, "MOUSEMOVE", "NOTHING", observation.ray)
            if _surface_event(
                observation.hand,
                "LEFTMOUSE",
                "PRESS",
                observation.ray,
            ):
                _capture = _Capture(
                    observation.hand,
                    observation.generation,
                    "pointer",
                    last_ray=observation.ray,
                    navigation_signature=observation.navigation_signature,
                )
                _adopt_pointer_pinch(observation)
                _two_hand_state.reset()
                _set_status(context, "Optical pinch: editor card")
                return
            _set_status(context, "Two-hand scale: waiting for second pinch")
        elif event == "waiting":
            _set_status(context, "Two-hand scale: waiting for second pinch")
        elif event == "begin":
            if not _begin_two_hand_edit(context):
                _two_hand_state.invalidate()
                _set_status(
                    context, "Two-hand scale could not begin; return both hands neutral"
                )
                return
            _set_status(context, "Two-hand scale active")
        elif event == "preview":
            preview = True
        elif event == "freeze":
            _set_status(
                context, "Two-hand scale frozen; release the remaining hand to commit"
            )
            preview = False
        elif event in {"cancel", "cancel_wait"}:
            _cancel_two_hand(
                context, "Two-hand scale cancelled; return both hands neutral"
            )
            return
        elif event == "commit":
            _commit_two_hand(context)
            return
        elif event == "finishing_no_resume":
            # While an edit transaction is frozen, only the native session
            # card is safe.  Forwarding to the legacy editor here could mutate
            # the captured selection or its undo history before commit/cancel.
            card_hit, card_generation = _task_card_hit(context, observation)
            if card_hit:
                if _task_card_event(
                    context,
                    observation.hand,
                    "PRESS",
                    card_generation,
                    observation.ray,
                ):
                    _capture = _Capture(
                        observation.hand,
                        observation.generation,
                        "task_card",
                        last_ray=observation.ray,
                        navigation_signature=observation.navigation_signature,
                        surface_generation=card_generation,
                    )
                    _adopt_pointer_pinch(observation)
                    _set_status(context, "Scale frozen; one-hand task card active")
                else:
                    _set_status(context, "Scale frozen; task card rejected pinch")
                return
            _set_status(
                context,
                "Scale remains frozen; use the native session card or Cancel",
            )
    if preview and _two_hand_state.phase == "active":
        if not _preview_two_hand_edit(context):
            _cancel_two_hand(context, "Two-hand scale preview was rejected; cancelled")


def _sample_timer(context) -> None:
    now = monotonic()
    state = _session_state(context)
    try:
        running = state is not None and bool(state.is_running(context))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        running = False
    if not running:
        stop_router(context)
        return
    if _capture is not None:
        if _two_hand_capture is not None and _two_hand_state.phase == "frozen":
            remaining = next(
                hand for hand in HANDS if hand != _two_hand_state.released_hand
            )
            remaining_observation, remaining_reason = _read_observation(
                context, remaining, now
            )
            if (
                remaining_observation is None
                or remaining_observation.generation != _two_hand_state.generation
                or not _navigation_matches(
                    remaining_observation.navigation_signature,
                    _two_hand_state.navigation_signature,
                )
            ):
                _cancel_two_hand(
                    context,
                    f"{remaining_reason}; frozen two-hand scale cancelled",
                )
            elif _two_hand_state.update(remaining_observation) == "commit":
                _commit_two_hand(context)
        observation, reason = _read_observation(context, _capture.hand, now)
        if observation is None:
            _cancel_capture(context, f"{reason}; active gesture cancelled")
            return
        if observation.generation != _capture.generation:
            _cancel_capture(
                context, "Optical provider generation changed; gesture cancelled"
            )
            return
        if not _navigation_matches(
            observation.navigation_signature,
            _capture.navigation_signature,
        ):
            _cancel_capture(
                context, "XR viewer recentered or rescaled; gesture cancelled"
            )
            return
        if _sample_support_cancel(context, _capture, now):
            return
        _process_observation(context, observation)
        return
    _reset_support_cancel(context)
    if _two_hand_state.phase != "idle" or _two_hand_scale_requested(context):
        if _two_hand_state.phase == "idle":
            try:
                _two_hand_state.configure(*_pinch_settings(context))
            except (RuntimeError, TypeError, ValueError) as exc:
                _set_status(context, f"Invalid two-hand pinch settings: {exc}")
                return
        _sample_two_hand(context, now)
        return
    try:
        _gesture_state.configure(*_pinch_settings(context))
    except (RuntimeError, TypeError, ValueError) as exc:
        _set_status(context, f"Invalid optical pinch settings: {exc}")
        return
    reasons = []
    for hand in HANDS:
        observation, reason = _read_observation(context, hand, now)
        if observation is None:
            _gesture_state.invalidate(hand)
            reasons.append(reason)
            continue
        _process_observation(context, observation)
        if _capture is not None:
            return
    if reasons:
        _set_status(context, reasons[0])


def stop_router(context) -> None:
    """Cancel capture, remove the WINDOW timer, and disable native hands."""

    global _router_operator
    _cancel_capture(context, "Optical hands stopped")
    if _two_hand_state.phase in {"waiting", "active", "frozen"}:
        _cancel_two_hand(context, "Two-hand scale cancelled")
    _two_hand_state.reset()
    _gesture_state.reset()
    _stream_watchdog.reset()
    operator = _router_operator
    if operator is not None:
        operator._remove_timer(context)
    _router_operator = None
    state = _session_state(context)
    if _optical_provider is not None:
        try:
            _optical_provider.stop(context)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    elif state is not None:
        try:
            state.hand_tracking_enable(context, enabled=False)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass


def cancel_scene_edit(context) -> None:
    """Rollback an edit while keeping qualified hand input available."""

    if _capture is not None and _capture.kind in {"edit", "selection"}:
        _cancel_capture(context, "Optical edit cancelled")
    if _two_hand_state.phase in {"waiting", "active", "frozen"}:
        _cancel_two_hand(context, "Two-hand scale cancelled")


class XRWB_OT_hand_tracking(bpy.types.Operator):
    bl_idname = "xrwb.hand_tracking"
    bl_label = "XR Workbench Optical Hands"
    bl_options = {"INTERNAL"}

    enabled: bpy.props.BoolProperty(default=True)
    _timer = None

    def _remove_timer(self, context) -> None:
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except (ReferenceError, RuntimeError, ValueError):
                pass
            self._timer = None

    def execute(self, context):
        global _router_operator
        if not self.enabled:
            stop_router(context)
            return {"FINISHED"}
        if _router_operator is not None:
            _set_status(context, "Optical hands are already running")
            return {"CANCELLED"}
        state = _session_state(context)
        if state is None or context.window is None:
            _set_status(context, "Optical hands require an active XR window")
            return {"CANCELLED"}
        try:
            _gesture_state.configure(*_pinch_settings(context))
        except (RuntimeError, TypeError, ValueError) as exc:
            _set_status(context, f"Invalid optical pinch settings: {exc}")
            return {"CANCELLED"}
        try:
            accepted = bool(
                _optical_provider.start(context)
                if _optical_provider is not None
                else state.hand_tracking_enable(context, enabled=True)
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            _set_status(context, f"Native optical hands unavailable: {exc}")
            return {"CANCELLED"}
        if not accepted:
            _set_status(context, "Native runtime rejected optical hand opt-in")
            return {"CANCELLED"}
        try:
            self._timer = context.window_manager.event_timer_add(
                TIMER_SECONDS, window=context.window
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            try:
                if _optical_provider is not None:
                    _optical_provider.stop(context)
                else:
                    state.hand_tracking_enable(context, enabled=False)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            _set_status(context, f"Could not start optical hand timer: {exc}")
            return {"CANCELLED"}
        _router_operator = self
        context.window_manager.modal_handler_add(self)
        _set_status(context, "Optical hands enabled; waiting for neutral open hand")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if _router_operator is not self:
            self._remove_timer(context)
            return {"CANCELLED"}
        if event.type == "ESC":
            if _capture is None and _two_hand_capture is None:
                return {"PASS_THROUGH"}
            stop_router(context)
            return {"CANCELLED"}
        if event.type != "TIMER" or getattr(event, "timer", None) != self._timer:
            return {"PASS_THROUGH"}
        _sample_timer(context)
        if _router_operator is not self:
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        stop_router(context)


CLASSES = (XRWB_OT_hand_tracking,)
