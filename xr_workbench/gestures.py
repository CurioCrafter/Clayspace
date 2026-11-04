"""Pinch edges with optical provenance, neutral rearm, and stale-frame rejection."""

from dataclasses import dataclass
from math import isfinite


@dataclass
class PinchGate:
    close_metres: float = 0.020
    open_metres: float = 0.030
    dwell_seconds: float = 0.050
    pressed: bool = False
    armed: bool = False
    generation: int = -1
    last_timestamp: float = -1.0
    candidate_since: float | None = None

    def __post_init__(self):
        values = self.close_metres, self.open_metres, self.dwell_seconds
        if not all(isfinite(value) for value in values):
            raise ValueError("Pinch thresholds must be finite")
        if not 0 < self.close_metres < self.open_metres or self.dwell_seconds < 0:
            raise ValueError("Pinch thresholds must be positive and ordered")

    def reset(self):
        was_pressed = self.pressed
        self.pressed = self.armed = False
        self.candidate_since = None
        return "cancel" if was_pressed else "unavailable"

    def update(self, *, distance_metres, timestamp, generation, optical, tracked):
        if (
            not optical
            or not tracked
            or not all(isfinite(value) for value in (distance_metres, timestamp))
            or distance_metres < 0
        ):
            return self.reset()
        if generation != self.generation:
            outcome = self.reset()
            self.generation = generation
            self.last_timestamp = -1.0
            if outcome == "cancel":
                return outcome
        if timestamp <= self.last_timestamp:
            return "stale"
        self.last_timestamp = timestamp
        if distance_metres >= self.open_metres:
            was_pressed = self.pressed
            self.pressed = False
            self.armed = True
            self.candidate_since = None
            return "release" if was_pressed else "hover"
        if self.pressed:
            return "hold"
        if not self.armed:
            return "neutral_required"
        if distance_metres > self.close_metres:
            self.candidate_since = None
            return "hover"
        if self.candidate_since is None:
            self.candidate_since = timestamp
        if timestamp - self.candidate_since < self.dwell_seconds:
            return "hover"
        self.pressed = True
        return "press"
