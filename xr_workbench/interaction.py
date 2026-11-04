"""Pure interaction contracts for transaction-safe spatial editing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Iterable


class InputProvenance(str, Enum):
    OPTICAL = "optical"
    CONTROLLER = "controller"
    SIMULATOR = "simulator"


class TransformTool(str, Enum):
    MOVE = "move"
    ROTATE = "rotate"
    SCALE = "scale"
    GRAB = "grab"  # Rigid follow of the hand (move + rotate), optional uniform scale.


class Constraint(str, Enum):
    FREE = "free"
    X = "x"
    Y = "y"
    Z = "z"
    XY = "xy"
    XZ = "xz"
    YZ = "yz"


class CapturePhase(str, Enum):
    CAPTURED = "captured"
    PREVIEW = "preview"
    PAUSED = "paused"
    COMMITTED = "committed"
    CANCELLED = "cancelled"


class Decision(str, Enum):
    APPLY = "apply"
    PAUSED = "paused"
    REBASE = "rebase"
    NEUTRAL_REQUIRED = "neutral_required"
    CANCEL_REQUIRED = "cancel_required"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CaptureOwner:
    provenance: InputProvenance
    identifier: str
    provider_generation: int

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("Capture owner identifier must not be empty")
        if self.provider_generation < 0:
            raise ValueError("Provider generation must be non-negative")


@dataclass(frozen=True, slots=True)
class Pose:
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        if len(self.position) != 3 or len(self.rotation) != 4:
            raise ValueError("Pose requires a 3D position and WXYZ quaternion")
        if not all(math.isfinite(value) for value in (*self.position, *self.rotation)):
            raise ValueError("Pose values must be finite")
        if sum(value * value for value in self.rotation) <= 1.0e-12:
            raise ValueError("Pose quaternion must be non-zero")


@dataclass(frozen=True, slots=True)
class TransformOptions:
    constraint: Constraint = Constraint.FREE
    translation_snap: float = 0.0
    rotation_snap_radians: float = 0.0
    scale_snap: float = 0.0
    min_scale: float = 0.01
    max_scale: float = 100.0
    pivot_world: tuple[float, float, float] | None = None
    edit_cage: bool = False
    tracking_loss_grace_seconds: float = 0.25

    def __post_init__(self) -> None:
        for value in (
            self.translation_snap,
            self.rotation_snap_radians,
            self.scale_snap,
            self.tracking_loss_grace_seconds,
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    "Snap and grace values must be finite and non-negative"
                )
        if (
            not math.isfinite(self.min_scale)
            or not math.isfinite(self.max_scale)
            or self.min_scale <= 0.0
            or self.max_scale < self.min_scale
        ):
            raise ValueError("Scale bounds must be positive and ordered")
        if self.pivot_world is not None and (
            len(self.pivot_world) != 3
            or not all(math.isfinite(value) for value in self.pivot_world)
        ):
            raise ValueError("Pivot must contain three finite world coordinates")


@dataclass(frozen=True, slots=True)
class EditRequest:
    owner: CaptureOwner
    tool: TransformTool
    target_generation: int
    pose: Pose
    options: TransformOptions = field(default_factory=TransformOptions)

    def __post_init__(self) -> None:
        if self.target_generation < 0:
            raise ValueError("Target generation must be non-negative")


@dataclass(frozen=True, slots=True)
class InputSample:
    owner: CaptureOwner
    target_generation: int
    pose: Pose
    timestamp_seconds: float
    tracking_active: bool = True
    neutral: bool = False
    uniform_scale: float | None = None

    def __post_init__(self) -> None:
        if self.target_generation < 0 or not math.isfinite(self.timestamp_seconds):
            raise ValueError("Sample generation and timestamp must be valid")
        if self.uniform_scale is not None and (
            not math.isfinite(self.uniform_scale) or self.uniform_scale <= 0.0
        ):
            raise ValueError("Explicit uniform scale must be finite and positive")


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    owner: CaptureOwner
    target_generation: int
    anchor_pose: Pose
    world_units_per_metre: float
    click_slop_metres: float = 0.010

    def __post_init__(self) -> None:
        if self.target_generation < 0:
            raise ValueError("Selection target generation must be non-negative")
        if (
            not math.isfinite(self.world_units_per_metre)
            or self.world_units_per_metre <= 0.0
            or not math.isfinite(self.click_slop_metres)
            or self.click_slop_metres <= 0.0
        ):
            raise ValueError(
                "Selection scale and click slop must be finite and positive"
            )


@dataclass(frozen=True, slots=True)
class EditResult:
    decision: Decision
    message: str = ""

    @property
    def applied(self) -> bool:
        return self.decision is Decision.APPLY


class CaptureState:
    """Own one gesture and enforce generation, pause, and neutral-rearm rules."""

    def __init__(self, request: EditRequest):
        self.owner = request.owner
        self.target_generation = request.target_generation
        self.phase = CapturePhase.CAPTURED
        self.tracking_lost_at: float | None = None
        self.grace_seconds = request.options.tracking_loss_grace_seconds
        self.last_timestamp_seconds: float | None = None

    @property
    def finished(self) -> bool:
        return self.phase in {CapturePhase.COMMITTED, CapturePhase.CANCELLED}

    def evaluate(self, sample: InputSample) -> EditResult:
        if self.finished:
            return EditResult(Decision.REJECTED, "Transaction is already finished")
        if (
            self.last_timestamp_seconds is not None
            and sample.timestamp_seconds < self.last_timestamp_seconds
        ):
            return EditResult(Decision.REJECTED, "Stale input sample timestamp")
        if (
            sample.owner.provenance != self.owner.provenance
            or sample.owner.identifier != self.owner.identifier
        ):
            return EditResult(Decision.REJECTED, "Input is owned by another capture")
        if sample.owner.provider_generation != self.owner.provider_generation:
            return EditResult(
                Decision.CANCEL_REQUIRED, "Input provider generation changed"
            )
        if sample.target_generation != self.target_generation:
            return EditResult(
                Decision.CANCEL_REQUIRED, "Editable target generation changed"
            )
        self.last_timestamp_seconds = sample.timestamp_seconds
        if not sample.tracking_active:
            if self.phase is not CapturePhase.PAUSED:
                self.phase = CapturePhase.PAUSED
                self.tracking_lost_at = sample.timestamp_seconds
            return EditResult(Decision.PAUSED, "Tracking lost; preview paused")
        if self.phase is CapturePhase.PAUSED:
            if self._grace_expired(sample.timestamp_seconds):
                return EditResult(
                    Decision.CANCEL_REQUIRED, "Tracking-loss grace expired"
                )
            if not sample.neutral:
                return EditResult(
                    Decision.NEUTRAL_REQUIRED,
                    "Return input to neutral before resuming",
                )
            self.phase = CapturePhase.CAPTURED
            self.tracking_lost_at = None
            return EditResult(Decision.REBASE, "Tracking resumed from neutral")
        self.phase = CapturePhase.PREVIEW
        return EditResult(Decision.APPLY)

    def tick(self, timestamp_seconds: float) -> EditResult:
        if self.finished:
            return EditResult(Decision.REJECTED, "Transaction is already finished")
        if not math.isfinite(timestamp_seconds):
            return EditResult(Decision.REJECTED, "Tick timestamp must be finite")
        if (
            self.last_timestamp_seconds is not None
            and timestamp_seconds < self.last_timestamp_seconds
        ):
            return EditResult(Decision.REJECTED, "Stale tick timestamp")
        self.last_timestamp_seconds = timestamp_seconds
        if self.phase is CapturePhase.PAUSED and self._grace_expired(timestamp_seconds):
            return EditResult(Decision.CANCEL_REQUIRED, "Tracking-loss grace expired")
        return EditResult(
            Decision.PAUSED if self.phase is CapturePhase.PAUSED else Decision.REJECTED
        )

    def can_finish(self, owner: CaptureOwner) -> EditResult:
        if self.finished:
            return EditResult(Decision.REJECTED, "Transaction is already finished")
        if (
            owner.provenance != self.owner.provenance
            or owner.identifier != self.owner.identifier
        ):
            return EditResult(Decision.REJECTED, "Input is owned by another capture")
        if owner.provider_generation != self.owner.provider_generation:
            return EditResult(
                Decision.CANCEL_REQUIRED, "Input provider generation changed"
            )
        if self.phase is CapturePhase.PAUSED:
            return EditResult(Decision.REJECTED, "Paused tracking cannot commit")
        return EditResult(Decision.APPLY)

    def finish(self, phase: CapturePhase) -> None:
        if phase not in {CapturePhase.COMMITTED, CapturePhase.CANCELLED}:
            raise ValueError("Capture may only finish as committed or cancelled")
        self.phase = phase

    def _grace_expired(self, timestamp_seconds: float) -> bool:
        return (
            self.tracking_lost_at is not None
            and timestamp_seconds - self.tracking_lost_at >= self.grace_seconds
        )


class SelectionClickState:
    """Owner-locked object click that cancels once physical slop is exceeded."""

    def __init__(self, request: SelectionRequest):
        self.request = request
        self.phase = CapturePhase.CAPTURED
        self.last_timestamp_seconds: float | None = None

    @property
    def finished(self) -> bool:
        return self.phase in {CapturePhase.COMMITTED, CapturePhase.CANCELLED}

    def evaluate(self, sample: InputSample) -> EditResult:
        if self.finished:
            return EditResult(Decision.REJECTED, "Selection click is already finished")
        if (
            self.last_timestamp_seconds is not None
            and sample.timestamp_seconds < self.last_timestamp_seconds
        ):
            return EditResult(Decision.REJECTED, "Stale selection sample timestamp")
        if (
            sample.owner.provenance != self.request.owner.provenance
            or sample.owner.identifier != self.request.owner.identifier
        ):
            return EditResult(
                Decision.REJECTED, "Selection is owned by another capture"
            )
        if sample.owner.provider_generation != self.request.owner.provider_generation:
            return EditResult(
                Decision.CANCEL_REQUIRED, "Selection provider generation changed"
            )
        if sample.target_generation != self.request.target_generation:
            return EditResult(
                Decision.CANCEL_REQUIRED, "Selection target generation changed"
            )
        self.last_timestamp_seconds = sample.timestamp_seconds
        if not sample.tracking_active:
            return EditResult(Decision.CANCEL_REQUIRED, "Selection tracking was lost")
        distance_world = math.sqrt(
            sum(
                (current - anchor) ** 2
                for current, anchor in zip(
                    sample.pose.position,
                    self.request.anchor_pose.position,
                )
            )
        )
        distance_metres = distance_world / self.request.world_units_per_metre
        if distance_metres > self.request.click_slop_metres:
            return EditResult(
                Decision.CANCEL_REQUIRED,
                "Selection click moved beyond physical click slop",
            )
        self.phase = CapturePhase.PREVIEW
        return EditResult(Decision.APPLY, "Object selection click remains eligible")

    def can_finish(self, owner: CaptureOwner) -> EditResult:
        if self.finished:
            return EditResult(Decision.REJECTED, "Selection click is already finished")
        if owner != self.request.owner:
            return EditResult(
                Decision.REJECTED, "Selection is owned by another capture"
            )
        return EditResult(Decision.APPLY)

    def finish(self, phase: CapturePhase) -> None:
        if phase not in {CapturePhase.COMMITTED, CapturePhase.CANCELLED}:
            raise ValueError("Selection may only finish as committed or cancelled")
        self.phase = phase


def quantize(value: float, step: float) -> float:
    return value if step <= 0.0 else round(value / step) * step


def constrained_vector(
    values: Iterable[float], constraint: Constraint
) -> tuple[float, float, float]:
    x, y, z = (float(value) for value in values)
    masks = {
        Constraint.FREE: (1.0, 1.0, 1.0),
        Constraint.X: (1.0, 0.0, 0.0),
        Constraint.Y: (0.0, 1.0, 0.0),
        Constraint.Z: (0.0, 0.0, 1.0),
        Constraint.XY: (1.0, 1.0, 0.0),
        Constraint.XZ: (1.0, 0.0, 1.0),
        Constraint.YZ: (0.0, 1.0, 1.0),
    }
    mask = masks[constraint]
    return x * mask[0], y * mask[1], z * mask[2]


def snapped_translation(
    delta: Iterable[float], constraint: Constraint, step: float
) -> tuple[float, float, float]:
    return tuple(
        quantize(value, step) for value in constrained_vector(delta, constraint)
    )


def uniform_scale(
    start_distance: float,
    current_distance: float,
    options: TransformOptions,
) -> float:
    if start_distance <= 1.0e-8:
        return 1.0
    scale = current_distance / start_distance
    if options.scale_snap > 0.0:
        scale = 1.0 + quantize(scale - 1.0, options.scale_snap)
    return max(options.min_scale, min(options.max_scale, scale))


def bounded_uniform_scale(scale: float, options: TransformOptions) -> float:
    if options.scale_snap > 0.0:
        scale = 1.0 + quantize(scale - 1.0, options.scale_snap)
    return max(options.min_scale, min(options.max_scale, scale))


def scale_axes(constraint: Constraint) -> tuple[bool, bool, bool]:
    return tuple(
        value != 0.0 for value in constrained_vector((1.0, 1.0, 1.0), constraint)
    )
