"""Image-local Index-camera pointer for the native simulator task card.

No image coordinate is treated as an XR ray or Blender world position.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import hypot, isfinite
import os
from pathlib import Path
from queue import Empty, Queue
from struct import Struct
from subprocess import CREATE_NO_WINDOW, PIPE, STARTF_USESHOWWINDOW, STARTUPINFO, Popen, TimeoutExpired
from threading import Thread, Timer
from time import monotonic
from uuid import uuid4

import bpy


HEADER = Struct("<9I")
RECORD = Struct("<IIff42f")
MAGIC = 0x47575258  # XRWG
MAX_RECORDS = 4
# ponytail: allows the observed ~1 fps camera; tighten after measured live cadence improves.
LOSS_SECONDS = 2.5
PHYSICAL_LOSS_SECONDS = 0.5
PHYSICAL_MAX_SECONDS = 20.0
HUD_MAX_SECONDS = 300.0
ADD_CUBE_RECT = (340.0, 700.0, 620.0, 850.0)  # Left-eye top-down 960x960 pixels.
_active_stream = None
_active_hud_input = None
_last_stats = (0, 0)
_last_diagnostics = {}


def stream_stats():
    stream = _active_stream
    return (
        (stream.frame_count, stream.action_count) if stream is not None else _last_stats
    )


def stream_diagnostics():
    stream = _active_stream
    return dict(stream.diagnostics if stream is not None else _last_diagnostics)


def stream_pid():
    return _active_stream.process.pid if _active_stream is not None else 0


def stop_stream(context):
    if _active_stream is not None:
        _active_stream.cancel(context)
    if _active_hud_input is not None:
        _active_hud_input.cancel(context)


@dataclass(frozen=True)
class GestureFrame:
    sequence: int
    timestamp_ns: int
    width: int
    height: int
    records: tuple


def _read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("gesture stream ended")
        data.extend(chunk)
    return data


def read_frame(stream):
    magic, version, width, height, seq, low, high, count, world = HEADER.unpack(
        _read_exact(stream, HEADER.size)
    )
    if (magic, version, width, height, world) != (MAGIC, 1, 960, 960, 0):
        raise ValueError("unsupported or spatially mislabeled gesture packet")
    if count > MAX_RECORDS:
        raise ValueError("too many gesture records")
    records = tuple(
        RECORD.unpack(_read_exact(stream, RECORD.size)) for _ in range(count)
    )
    return GestureFrame(seq, (high << 32) | low, width, height, records)


def classify_frame(frame: GestureFrame):
    left = [r for r in frame.records if r[0] == 0]
    if not left:
        return "empty", None
    if len(left) != 1:
        return "ambiguous", None
    record = left[0]
    if (
        not isfinite(record[2])
        or not isfinite(record[3])
        or record[2] < 0.8
        or record[3] < 0.8
    ):
        return "low_score", None
    points = record[4:]
    if not all(isfinite(v) for v in points) or any(
        points[i] < 0 or points[i] >= (frame.width if i % 2 == 0 else frame.height)
        for i in range(42)
    ):
        return "invalid", None
    palm = hypot(points[10] - points[34], points[11] - points[35])
    if palm < 20:
        return "small", None
    ratio = hypot(points[8] - points[16], points[9] - points[17]) / palm
    return (
        "open" if ratio >= 0.65 else "closed" if ratio <= 0.35 else "middle"
    ), record[1]


class ImagePinch:
    """One image-local pinch, neutral armed, with loss cancellation."""

    def __init__(self, loss_seconds=LOSS_SECONDS):
        self.loss_seconds = loss_seconds
        self.reset()

    def reset(self):
        self.phase = "neutral_required"
        self.candidate_since = None
        self.generation = 0
        self.last_sequence = -1
        self.last_timestamp = -1
        self.last_arrival = -1.0
        self.candidate = None

    def lost(self, now):
        if self.last_arrival >= 0 and now - self.last_arrival > self.loss_seconds:
            self.reset()
            return True
        return False

    def update(self, frame: GestureFrame, now: float, card_generation: int) -> int:
        kind, candidate = classify_frame(frame)
        return self.update_state(
            frame.sequence, frame.timestamp_ns, kind, candidate, now, card_generation
        )

    def update_state(self, sequence, timestamp_ns, kind, candidate, now, card_generation):
        if self.lost(now):
            return 0
        if (
            sequence <= self.last_sequence
            or timestamp_ns <= self.last_timestamp
            or card_generation <= 0
        ):
            self.reset()
            return 0
        self.last_sequence = sequence
        self.last_timestamp = timestamp_ns
        self.last_arrival = now
        # Candidate identity is not stable across occlusion; ambiguity cancels.
        if kind not in {"open", "closed", "middle"}:
            self.phase = "neutral_required"
            self.candidate_since = None
            self.candidate = None
            return 0
        if self.candidate is not None and self.candidate != candidate:
            self.phase = "neutral_required"
            self.candidate_since = None
            self.candidate = candidate
            return 0
        self.candidate = candidate
        if kind == "open":
            if self.phase == "pressed" and self.generation == card_generation:
                self.phase = "armed"
                self.candidate_since = None
                return card_generation
            self.phase = "armed"
            self.candidate_since = None
        elif kind == "closed":
            if self.phase == "armed":
                self.phase = "closing"
                self.candidate_since = now
                self.generation = card_generation
            elif self.phase == "closing" and now - self.candidate_since >= 0.10:
                self.phase = "pressed"
        elif self.phase == "closing":
            self.phase = "armed"
            self.candidate_since = None
        return 0


def _physical_hud_opted_in():
    try:
        install = Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
        isolated = (
            install.name == "xr_workbench"
            and install.parent.name == "addons"
            and install.parent.parent.name == "scripts"
            and (install.parent.parent.parent.name == ".xr_profile"
                 or (os.environ.get("XRWB_PERSONAL_ADDONS") == "1"
                     and install.parent.parent.parent.name == ".xr_addons_profile"))
        )
    except (KeyError, OSError, ValueError):
        isolated = False
    return (
        os.environ.get("XRWB_PHYSICAL_INDEX_OPT_IN") == "1"
        and os.environ.get("XRWB_PHYSICAL_GESTURE_OPT_IN") == "1"
        and os.environ.get("XRWB_INDEX_CAMERA_OVERLAY") == "1"
        and os.environ.get("XRWB_INDEX_CAMERA_CONSENT") == "1"
        and bool(os.environ.get("XR_RUNTIME_JSON"))
        and not os.environ.get("SIMULATED_ENABLE")
        and isolated
    )


def _add_cube_in_view3d(context):
    window = context.window
    screen = window.screen if window is not None else None
    area = next((area for area in screen.areas if area.type == "VIEW_3D"), None) if screen else None
    region = next((region for region in area.regions if region.type == "WINDOW"), None) if area else None
    if region is None:
        print("XRWB_CAMERA_HUD_ADD_CUBE_REJECTED reason=no_view3d", flush=True)
        return False
    try:
        with context.temp_override(window=window, screen=screen, area=area, region=region):
            if not bpy.ops.xrwb.add_cube.poll():
                print("XRWB_CAMERA_HUD_ADD_CUBE_REJECTED reason=poll", flush=True)
                return False
            changed = "FINISHED" in bpy.ops.xrwb.add_cube("EXEC_DEFAULT")
            if not changed:
                print("XRWB_CAMERA_HUD_ADD_CUBE_REJECTED reason=operator", flush=True)
            return changed
    except (AttributeError, RuntimeError, TypeError, ValueError):
        print("XRWB_CAMERA_HUD_ADD_CUBE_REJECTED reason=context", flush=True)
        return False


def process_observation(context, gate: ImagePinch, now=None) -> bool:
    """Consume one native v2 sample on the UI thread; never open another camera."""
    now = monotonic() if now is None else now
    state = getattr(context.window_manager, "xr_session_state", None)
    if not _physical_hud_opted_in() or state is None:
        gate.reset()
        return False
    try:
        if not state.is_running(context):
            gate.reset()
            return False
        sample = tuple(float(value) for value in state.camera_gesture_observation(context))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        gate.reset()
        return False
    if len(sample) != 14 or not all(isfinite(value) for value in sample):
        gate.reset()
        return False
    (valid, sequence, candidate, presence, palm_score, thumb_x, thumb_y,
     tip_x, tip_y, mcp_x, mcp_y, pinky_x, pinky_y, age_ms) = sample
    coords = (thumb_x, thumb_y, tip_x, tip_y, mcp_x, mcp_y, pinky_x, pinky_y)
    if (valid != 1.0 or sequence < 0 or int(sequence) != sequence or
            candidate < 0 or int(candidate) != candidate or
            presence < 0.8 or palm_score < 0.8 or
            age_ms < 0 or age_ms > 500 or
            any(value < 0 or value >= 960 for value in coords) or
            not (ADD_CUBE_RECT[0] <= tip_x <= ADD_CUBE_RECT[2] and
                 ADD_CUBE_RECT[1] <= tip_y <= ADD_CUBE_RECT[3])):
        gate.reset()
        return False
    # The native packet supplies real MCP landmarks, so distance is depth-normalized.
    palm = hypot(mcp_x - pinky_x, mcp_y - pinky_y)
    if palm < 20:
        gate.reset()
        return False
    ratio = hypot(thumb_x - tip_x, thumb_y - tip_y) / palm
    kind = "open" if ratio >= 0.65 else "closed" if ratio <= 0.35 else "middle"
    if sequence == gate.last_sequence:
        gate.lost(now)
        return False
    if not gate.update_state(int(sequence), int(sequence), kind, int(candidate), now, 1):
        return False
    return _add_cube_in_view3d(context)


def process_frame(context, frame: GestureFrame, gate: ImagePinch, now=None) -> bool:
    """Run on Blender's window thread. Return true only if opacity changed."""
    now = monotonic() if now is None else now
    state = getattr(context.window_manager, "xr_session_state", None)
    try:
        visible, generation, drawn, captured_pointer, captured_action, _opacity = (
            state.task_card_info(context)
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        gate.reset()
        return False
    if not visible or generation != drawn or captured_pointer != -1 or captured_action:
        gate.reset()
        return False
    activate = gate.update(frame, now, generation)
    if not activate:
        return False
    try:
        changed = "FINISHED" in bpy.ops.wm.xr_task_card_cycle_opacity(
            "EXEC_DEFAULT", generation=activate
        )
        if changed:
            settings = getattr(context.window_manager, "xrwb_settings", None)
            if settings is not None:
                opacity = state.task_card_info(context)[5]
                settings.hand_status = f"Image pinch: card opacity {opacity}%"
        return changed
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


class ImageCardPointer:
    """One simulator-only card pointer; never supplies a world or scene ray."""

    def __init__(self):
        self.gate = ImagePinch()
        self.pressed = False
        self.generation = 0

    def _event(self, phase, generation, position=None):
        properties = {
            "phase": phase, "generation": generation, "pointer_id": 2,
            "image_local": True,
        }
        if position is not None:
            properties["image_x"], properties["image_y"] = position
        try:
            return "FINISHED" in bpy.ops.wm.xr_task_card_event(
                "EXEC_DEFAULT", **properties
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    def cancel(self):
        if self.generation:
            self._event("CANCEL", self.generation)
        self.gate.reset()
        self.pressed = False
        self.generation = 0

    def lost(self, now):
        if self.gate.lost(now):
            self.cancel()


def process_card_frame(context, frame: GestureFrame, pointer: ImageCardPointer,
                       now=None) -> bool:
    """Run on Blender's window thread; return true for a consumed release."""
    now = monotonic() if now is None else now
    state = getattr(context.window_manager, "xr_session_state", None)
    try:
        visible, generation, drawn, captured_pointer, captured_action, _opacity = (
            state.task_card_info(context)
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pointer.cancel()
        return False
    if (not visible or generation <= 0 or generation != drawn or
            (captured_action and captured_pointer != 2) or
            (pointer.generation and pointer.generation != generation)):
        # Do not cancel a different pointer's native capture.
        if captured_pointer == 2 or not captured_action:
            pointer.cancel()
        else:
            pointer.gate.reset()
            pointer.pressed = False
            pointer.generation = 0
        return False
    kind, _candidate = classify_frame(frame)
    if (frame.sequence <= pointer.gate.last_sequence or
            frame.timestamp_ns <= pointer.gate.last_timestamp):
        pointer.cancel()
        return False
    if pointer.gate.lost(now):
        pointer.cancel()
        return False
    was_pressed = pointer.pressed
    release = pointer.gate.update(frame, now, generation)
    if kind not in {"open", "middle", "closed"} or (
        was_pressed and pointer.gate.phase == "neutral_required"
    ):
        pointer.cancel()
        return False
    # The validated image is always 960 square. Index tip controls the card;
    # y is flipped because camera pixels grow down and card coordinates grow up.
    left_record = next(record for record in frame.records if record[0] == 0)
    tip_x, tip_y = left_record[20:22]
    position = (tip_x * 1000.0 / frame.width,
                (frame.height - tip_y) * 500.0 / frame.height)
    if release:
        consumed = pointer._event("RELEASE", release, position)
        pointer.pressed = False
        pointer.generation = 0
        return consumed
    pointer.generation = generation
    if not pointer._event("MOVE", generation, position):
        pointer.cancel()
        return False
    if pointer.gate.phase == "pressed" and not pointer.pressed:
        pointer.pressed = pointer._event("PRESS", generation, position)
        if not pointer.pressed:
            pointer.cancel()
    return False


class XRWB_OT_image_gesture_stream(bpy.types.Operator):
    bl_idname = "xrwb.image_gesture_stream"
    bl_label = "Index Camera Pinch: Task Card Pointer"
    bl_options = {"INTERNAL"}

    executable: bpy.props.StringProperty(subtype="FILE_PATH")
    camera: bpy.props.StringProperty()
    ffmpeg: bpy.props.StringProperty(subtype="FILE_PATH")
    consent: bpy.props.BoolProperty(default=False)
    physical_opacity_only: bpy.props.BoolProperty(default=False)

    def _physical_report_path(self):
        install = Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
        if (
            install.name != "xr_workbench"
            or install.parent.name != "addons"
            or install.parent.parent.name != "scripts"
            or install.parent.parent.parent.name != ".xr_profile"
        ):
            raise ValueError("Physical gesture reports require the isolated XR profile")
        report_dir = install.parent.parent.parent.parent / "reports" / "index-physical-gesture"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return report_dir / f"gesture-{stamp}-{uuid4().hex[:8]}.txt"

    def _finish_physical_report(self, reason):
        if not self.physical_opacity_only or self.report_written:
            return
        counts = " ".join(f"{key}={value}" for key, value in self.diagnostics.items())
        duration_ms = round((monotonic() - self.started_at) * 1000)
        with self.report_path.open("a", encoding="utf-8") as report:
            report.write(
                f"XRWB_IMAGE_GESTURE_END reason={reason} duration_ms={duration_ms} "
                f"frames={self.frame_count} actions={self.action_count} {counts} "
                "images_saved=0 joint_traces_saved=0\n"
            )
        self.report_written = True

    def _stop_at_deadline(self):
        try:
            if self.process.poll() is None:
                self.process.terminate()
        except OSError:
            pass

    def execute(self, context):
        global _active_stream, _last_stats, _last_diagnostics
        state = getattr(context.window_manager, "xr_session_state", None)
        try:
            running = state is not None and bool(state.is_running(context))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            running = False
        if (
            not self.consent
            or not running
            or _active_stream is not None
            or context.window is None
            or not self.camera
            or not Path(self.executable).is_file()
            or not Path(self.ffmpeg).is_file()
        ):
            return {"CANCELLED"}
        if self.physical_opacity_only and (
            os.environ.get("XRWB_PHYSICAL_INDEX_OPT_IN") != "1"
            or os.environ.get("XRWB_PHYSICAL_GESTURE_OPT_IN") != "1"
            or not os.environ.get("XR_RUNTIME_JSON")
            or os.environ.get("SIMULATED_ENABLE")
        ):
            return {"CANCELLED"}
        try:
            self.report_path = self._physical_report_path() if self.physical_opacity_only else None
        except (KeyError, OSError, ValueError):
            return {"CANCELLED"}
        self.report_written = False
        self.started_at = monotonic()
        if self.report_path is not None:
            try:
                self.report_path.write_text(
                    "XRWB_IMAGE_GESTURE_START mode=physical_opacity max_seconds=20 "
                    "images_saved=0 joint_traces_saved=0\n", encoding="utf-8"
                )
            except OSError:
                return {"CANCELLED"}
        self.pointer = None if self.physical_opacity_only else ImageCardPointer()
        self.gate = ImagePinch(PHYSICAL_LOSS_SECONDS) if self.physical_opacity_only else None
        _last_stats = (0, 0)
        _last_diagnostics = {}
        self.frame_count = 0
        self.action_count = 0
        self.diagnostics = {
            key: 0
            for key in (
                "records",
                "empty",
                "ambiguous",
                "low_score",
                "invalid",
                "small",
                "open",
                "closed",
                "middle",
                "pressed",
            )
        }
        self.frames = Queue(maxsize=2)
        try:
            startup = STARTUPINFO()
            startup.dwFlags |= STARTF_USESHOWWINDOW
            startup.wShowWindow = 0 if self.physical_opacity_only else 1
            self.process = Popen(
                [
                    self.executable,
                    "--consented-gesture-stream"
                    if self.physical_opacity_only else "--consented-gesture-preview",
                    self.camera,
                    self.ffmpeg,
                ],
                stdout=PIPE,
                stderr=PIPE,
                startupinfo=startup,
                creationflags=CREATE_NO_WINDOW if self.physical_opacity_only else 0,
            )
        except OSError as exc:
            self._finish_physical_report("start_failed")
            self.report({"ERROR"}, f"Index gesture stream could not start: {exc}")
            return {"CANCELLED"}
        worker_state = {"error": "", "ended": False}
        self.worker_state = worker_state
        process = self.process
        frames = self.frames

        def reader():
            try:
                while True:
                    frame = read_frame(process.stdout)
                    if frames.full():
                        try:
                            frames.get_nowait()
                        except Empty:
                            pass
                    frames.put_nowait(frame)
            except (EOFError, ValueError, OSError) as exc:
                worker_state["error"] = f"gesture packet stream ended: {exc}"
                worker_state["ended"] = True

        self.thread = Thread(target=reader, daemon=True)
        self.thread.start()

        def read_errors():
            for line in process.stderr:
                worker_state["error"] = line.decode("utf-8", errors="replace").strip()

        Thread(target=read_errors, daemon=True).start()
        try:
            self.timer = context.window_manager.event_timer_add(
                0.02, window=context.window
            )
        except (AttributeError, RuntimeError, ValueError) as exc:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            self._finish_physical_report("timer_failed")
            self.report({"ERROR"}, f"Index gesture timer could not start: {exc}")
            return {"CANCELLED"}
        try:
            context.window_manager.modal_handler_add(self)
        except (AttributeError, RuntimeError, ValueError):
            self._cancel(context, reason="handler_failed")
            return {"CANCELLED"}
        self.deadline_timer = None
        if self.physical_opacity_only:
            self.deadline_timer = Timer(PHYSICAL_MAX_SECONDS, self._stop_at_deadline)
            self.deadline_timer.daemon = True
            try:
                self.deadline_timer.start()
            except RuntimeError:
                self._cancel(context, reason="watchdog_failed")
                return {"CANCELLED"}
        _active_stream = self
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        now = monotonic()
        if self.physical_opacity_only and now - self.started_at >= PHYSICAL_MAX_SECONDS:
            self._cancel(context, reason="time_limit")
            return {"CANCELLED"}
        state = getattr(context.window_manager, "xr_session_state", None)
        try:
            running = state is not None and bool(state.is_running(context))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            running = False
        if not running:
            self._cancel(context, reason="xr_stopped")
            return {"CANCELLED"}
        if self.process.poll() is not None or self.worker_state["ended"]:
            self._cancel(context, reason="stream_ended")
            self.report(
                {"WARNING"},
                self.worker_state["error"] or "Index gesture preview closed",
            )
            return {"CANCELLED"}
        try:
            frame = self.frames.get_nowait()
            while True:
                frame = self.frames.get_nowait()
        except Empty:
            if "frame" in locals():
                self.frame_count += 1
                self.diagnostics["records"] += len(frame.records)
                kind, _candidate = classify_frame(frame)
                self.diagnostics[kind] += 1
                if self.physical_opacity_only:
                    self.action_count += int(process_frame(context, frame, self.gate, now))
                    self.diagnostics["pressed"] += self.gate.phase == "pressed"
                else:
                    self.action_count += int(process_card_frame(context, frame, self.pointer, now))
                    self.diagnostics["pressed"] += self.pointer.pressed
            else:
                (self.gate if self.physical_opacity_only else self.pointer).lost(now)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        # Blender's Operator.cancel callback must have exactly (self, context).
        self._cancel(context, reason="cancelled")

    def _cancel(self, context, reason):
        global _active_stream, _last_stats, _last_diagnostics
        _last_stats = (self.frame_count, self.action_count)
        _last_diagnostics = dict(self.diagnostics)
        timer = getattr(self, "deadline_timer", None)
        if timer is not None:
            timer.cancel()
        if self.pointer is not None:
            self.pointer.cancel()
        if self.gate is not None:
            self.gate.reset()
        try:
            context.window_manager.event_timer_remove(self.timer)
        except (AttributeError, RuntimeError, ValueError):
            pass
        try:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=2)
        except TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        finally:
            _active_stream = None
            self._finish_physical_report(reason)


class XRWB_OT_camera_hud_gesture(bpy.types.Operator):
    """Poll the already-running native stereo stream; never open the camera."""

    bl_idname = "xrwb.camera_hud_gesture"
    bl_label = "Index Camera Add Cube Gesture"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        global _active_hud_input
        state = getattr(context.window_manager, "xr_session_state", None)
        try:
            ready = state is not None and state.is_running(context) and callable(
                state.camera_gesture_observation
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            ready = False
        if (not _physical_hud_opted_in() or not ready or context.window is None or
                _active_hud_input is not None or _active_stream is not None):
            return {"CANCELLED"}
        self.gate = ImagePinch(PHYSICAL_LOSS_SECONDS)
        self.started_at = monotonic()
        self.poll_count = 0
        self.action_count = 0
        try:
            self.timer = context.window_manager.event_timer_add(0.02, window=context.window)
            context.window_manager.modal_handler_add(self)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            if hasattr(self, "timer"):
                context.window_manager.event_timer_remove(self.timer)
            return {"CANCELLED"}
        _active_hud_input = self
        print("XRWB_CAMERA_HUD_START mode=native_v2 add_cube=armed images_saved=0 joint_traces_saved=0", flush=True)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        state = getattr(context.window_manager, "xr_session_state", None)
        try:
            running = state is not None and state.is_running(context)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            running = False
        if (not _physical_hud_opted_in() or not running or
                monotonic() - self.started_at >= HUD_MAX_SECONDS):
            self.cancel(context)
            return {"CANCELLED"}
        self.poll_count += 1
        if process_observation(context, self.gate):
            self.action_count += 1
            print(f"XRWB_CAMERA_HUD_ADD_CUBE actions={self.action_count}", flush=True)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        global _active_hud_input
        self.gate.reset()
        try:
            context.window_manager.event_timer_remove(self.timer)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        _active_hud_input = None
        print(
            f"XRWB_CAMERA_HUD_END polls={self.poll_count} actions={self.action_count} "
            "images_saved=0 joint_traces_saved=0",
            flush=True,
        )


CLASSES = (XRWB_OT_image_gesture_stream, XRWB_OT_camera_hud_gesture)
