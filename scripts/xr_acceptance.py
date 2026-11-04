"""Honest XR acceptance ledger and modal-window lifecycle soak harness.

Run inside Blender with ``XRWB_EXPECTED_INSTALL`` set. The harness never
requests passthrough or camera frames and uses no application-level timer.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Any, Mapping

try:
    import addon_utils
    import bpy
except ImportError:  # Allows the pure ledger to be unit-tested outside Blender.
    addon_utils = None
    bpy = None


VALID_STATES = frozenset({"passed", "failed", "blocked", "unverified"})
ACCEPTANCE_CASES = {
    "AT-01": "alpha ramp: independent room, mesh, panel, and text controls",
    "AT-02": "stereo order through session restart",
    "AT-03": "head-motion registration in qualified work volume",
    "AT-04": "optical provenance is never inferred from controllers",
    "AT-05": "one UI poke triggers one action and hover triggers none",
    "AT-06": "pointer capture does not transfer across panels",
    "AT-07": "object move changes only target and creates one undo step",
    "AT-08": "two-hand scale has no release/crossing jump",
    "AT-09": "view scale leaves stored model dimensions unchanged",
    "AT-10": "mesh-region edit preserves unselected geometry",
    "AT-11": "topology cancel restores topology and selection",
    "AT-12": "modifier target is edit-cage-safe or rejected",
    "AT-13": "tracking loss pauses/cancels without accidental commit",
    "AT-14": "stale result cannot overwrite newer state",
    "AT-15": "save/reopen excludes room frames and runtime helpers",
    "AT-16": "input parity across optical, controllers, and desktop",
    "AT-17": "full UI: menus, focus, scroll, numeric/text/save dialog",
    "AT-18": "runtime/camera fault retains committed work and desktop use",
    "AT-19": "100 lifecycle cycles plus 60-minute editing soak",
    "AT-20": "named fixture meets selected 90 Hz pacing tier",
    "AT-21": "one-hand, left-hand, resized UI, and keyboard cancel",
    "AT-22": "default logs/mirror/support export contain no camera images",
}
_REDACT_KEYS = {"path", "filepath", "device_id", "serial", "image", "frame"}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in _REDACT_KEYS or key.lower().endswith("_path"):
        return "<redacted>" if value not in (None, "") else None
    if isinstance(value, Mapping):
        return {str(name): _redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, Path):
        return "<redacted>"
    if isinstance(value, str) and (
        value.startswith(("/", "\\")) or re.search(r"[A-Za-z]:[\\/]", value)
    ):
        return "<redacted>"
    return (
        value
        if value is None or isinstance(value, (str, int, float, bool))
        else str(value)
    )


def acceptance_report(
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Make a complete ledger; cases remain unverified unless evidence overrides them."""
    cases = {
        case_id: {"requirement": requirement, "state": "unverified", "evidence": []}
        for case_id, requirement in ACCEPTANCE_CASES.items()
    }
    for case_id, result in (overrides or {}).items():
        if case_id not in cases:
            raise KeyError(case_id)
        state = result.get("state", "unverified")
        if state not in VALID_STATES:
            raise ValueError(f"invalid acceptance state: {state}")
        cases[case_id]["state"] = state
        cases[case_id]["evidence"] = _redact(result.get("evidence", []))
    return {
        "schema_version": "1.0.0",
        "generated_at_utc": _utc_now(),
        "milestone": {"id": "M0", "state": "incomplete"},
        "cases": cases,
    }


def write_acceptance_report(report: Mapping[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(_redact(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def process_working_set_bytes(*, committed: bool = False) -> int | None:
    """Optional process-only memory sample; no headset/runtime probing occurs."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.pagefile_usage if committed else counters.working_set_size)
    except Exception:
        return None


if bpy is not None:

    class XRWB_OT_acceptance_soak(bpy.types.Operator):
        bl_idname = "xrwb.acceptance_soak"
        bl_label = "XR Workbench Acceptance Soak"

        def _view3d_override(self, context):
            window = context.window
            area = next(
                (area for area in window.screen.areas if area.type == "VIEW_3D"), None
            )
            region = (
                next(
                    (region for region in area.regions if region.type == "WINDOW"), None
                )
                if area
                else None
            )
            if area is None or region is None:
                raise RuntimeError(
                    "A VIEW_3D window region is required for XR lifecycle testing"
                )
            return context.temp_override(window=window, area=area, region=region)

        def _record(self, outcome: str, detail: str = "") -> None:
            overlay = self.addon.runtime.overlay_status()
            xr_draw_count = int(overlay["xr_draw_count"])
            self.cycles.append(
                {
                    "cycle": self.cycle,
                    "outcome": outcome,
                    "detail": detail,
                    "draw_count": int(overlay["draw_count"]),
                    "xr_draw_count": xr_draw_count,
                    "xr_draw_delta": max(0, xr_draw_count - self.draw_before_cycle),
                    "draw_error": str(overlay["last_draw_error"]),
                    "memory_bytes": process_working_set_bytes(),
                    "committed_bytes": process_working_set_bytes(committed=True),
                    # A callback count shows Blender invoked a callback, not
                    # that a headset user saw a correctly composed frame.
                    "visible_frame_qualified": False,
                }
            )
            # Retain bounded metadata evidence even if the native runtime crashes.
            write_acceptance_report(
                {
                    "run_state": "in_progress",
                    "cycles_requested": self.limit,
                    "cycles": self.cycles,
                    "camera_requested": False,
                },
                self.output,
            )
            item = self.cycles[-1]
            print(
                f"XRWB_CYCLE cycle={self.cycle} outcome={outcome} "
                f"committed_bytes={item['committed_bytes']} "
                f"xr_draw_delta={item['xr_draw_delta']}",
                flush=True,
            )

        def _session_running(self, context) -> bool:
            try:
                return bool(self.addon.runtime.session_running(context))
            except (AttributeError, ReferenceError, RuntimeError):
                return False

        def _session_exists(self, context) -> bool:
            try:
                return bool(self.addon.runtime.session_exists(context))
            except (AttributeError, ReferenceError, RuntimeError):
                return False

        def _remove_timer(self, context) -> None:
            if self.timer is not None:
                context.window_manager.event_timer_remove(self.timer)
                self.timer = None

        def _advance_cycle(self, context) -> set[str]:
            self.completed += 1
            committed = process_working_set_bytes(committed=True)
            # XR/driver/shader startup is a one-time allocation. Compare later
            # cycles against the first completed cycle, not the pre-XR process.
            if self.initial_commit is None:
                self.initial_commit = committed
            if committed is not None and self.initial_commit is not None:
                if committed - self.initial_commit > self.max_commit_growth:
                    self.failed += 1
                    self._record(
                        "memory_growth_limit",
                        "Stopped safely after 1 GiB process commit growth beyond first-cycle warm-up.",
                    )
                    return self._finish(context, True)
            if self.cycle >= self.limit:
                return self._finish(context, self.failed > 0)
            self.cycle += 1
            self.cycle_was_running = False
            self.cycle_outcome_recorded = False
            self.phase = "request_start"
            self.due = time.monotonic() + 0.25
            return {"RUNNING_MODAL"}

        def _request_stop(self, context) -> None:
            if not self._session_exists(context):
                if not self.cycle_outcome_recorded:
                    self.failed += 1
                    self._record(
                        "session_ended_before_stop",
                        "XR session ended before the requested stop could be observed.",
                    )
                    self.cycle_outcome_recorded = True
                self.phase = "next"
                return
            self.stop_requests += 1
            with self._view3d_override(context):
                result = bpy.ops.xrwb.stop_session()
            if "CANCELLED" in result:
                self.failed += 1
                self._record("stop_rejected", "The stop operator rejected the request.")
                self.phase = "next"
                return
            self.phase = "wait_stopped"
            self.deadline = time.monotonic() + self.stop_timeout

        def _finish(self, context, failed: bool = False) -> set[str]:
            settings = context.window_manager.xr_session_settings
            settings.show_passthrough = False
            try:
                settings_owner = context.window_manager.xrwb_settings
                settings_owner.overlay_enabled = False
                self.addon.runtime.configure_overlay(
                    False, settings_owner.panel_opacity
                )
            except (AttributeError, ReferenceError, RuntimeError):
                pass
            if self._session_exists(context):
                try:
                    with self._view3d_override(context):
                        result = bpy.ops.xrwb.stop_session()
                    self.stop_requests += 1
                    if "CANCELLED" in result:
                        self.failed += 1
                        failed = True
                except Exception:
                    self.failed += 1
                    failed = True
            self._remove_timer(context)
            overrides = {
                "AT-19": {
                    "state": "failed" if failed else "unverified",
                    "evidence": [
                        "Lifecycle cycles recorded; 60-minute editing soak is not part of this harness."
                    ],
                }
            }
            report = acceptance_report(overrides)
            report["harness"] = {
                "run_state": "failed" if failed else "completed",
                "cycles_requested": self.limit,
                "cycles_completed": self.completed,
                "start_requests": self.start_requests,
                "cycles_started": self.started,
                "stop_requests": self.stop_requests,
                "cycles_stopped": self.stopped,
                "cycles_inactive": self.inactive,
                "cycles_failed": self.failed,
                "camera_requested": False,
                "visible_frame_qualified": False,
                "rendered_xr_callback_cycles": sum(
                    item["xr_draw_delta"] > 0 for item in self.cycles
                ),
                "cycles": self.cycles,
            }
            write_acceptance_report(report, self.output)
            addon_utils.disable("xr_workbench", default_set=False)
            marker = "FAIL" if failed else "COMPLETE"
            print(
                f"XRWB_ACCEPTANCE_{marker} completed={self.completed} started={self.started} "
                f"inactive={self.inactive} failed={self.failed} visible_frames=0 camera_requested=0",
                flush=True,
            )
            bpy.ops.wm.quit_blender()
            return {"CANCELLED" if failed else "FINISHED"}

        def invoke(self, context, _event):
            self.limit = max(
                1, min(1000, int(os.environ.get("XRWB_SOAK_CYCLES", "100")))
            )
            self.dwell = max(
                0.05,
                min(30.0, float(os.environ.get("XRWB_SOAK_DWELL_SECONDS", "0.25"))),
            )
            self.start_timeout = max(
                0.5,
                min(
                    30.0,
                    float(os.environ.get("XRWB_SOAK_STARTUP_TIMEOUT_SECONDS", "5")),
                ),
            )
            self.stop_timeout = max(
                0.5,
                min(
                    30.0,
                    float(os.environ.get("XRWB_SOAK_STOP_TIMEOUT_SECONDS", "5")),
                ),
            )
            self.output = Path(os.environ.get("XRWB_SOAK_OUTPUT", "xr-acceptance.json"))
            self.addon = addon_utils.enable(
                "xr_workbench", default_set=False, persistent=True
            )
            expected = Path(os.environ["XRWB_EXPECTED_INSTALL"]).resolve()
            if (
                self.addon is None
                or expected not in Path(self.addon.__file__).resolve().parents
            ):
                raise RuntimeError(
                    "XR Workbench did not load from the expected isolated install"
                )
            context.window_manager.xr_session_settings.show_passthrough = False
            settings = context.window_manager.xrwb_settings
            settings.overlay_enabled = True
            self.addon.runtime.configure_overlay(True, settings.panel_opacity)
            self.cycle = 1
            self.completed = 0
            self.started = 0
            self.stopped = 0
            self.inactive = 0
            self.failed = 0
            self.start_requests = self.stop_requests = 0
            self.cycles = []
            self.initial_commit = None
            self.max_commit_growth = 1024**3
            self.cycle_was_running = False
            self.cycle_outcome_recorded = False
            self.draw_before_cycle = int(
                self.addon.runtime.overlay_status()["xr_draw_count"]
            )
            self.phase = "request_start"
            self.due = time.monotonic()
            self.timer = context.window_manager.event_timer_add(
                0.05, window=context.window
            )
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}

        def modal(self, context, event):
            if event.type != "TIMER" or time.monotonic() < self.due:
                return {"PASS_THROUGH"}
            try:
                settings = context.window_manager.xr_session_settings
                settings.show_passthrough = False
                if self.phase == "request_start":
                    self.draw_before_cycle = int(
                        self.addon.runtime.overlay_status()["xr_draw_count"]
                    )
                    self.start_requests += 1
                    with self._view3d_override(context):
                        result = bpy.ops.xrwb.start_session()
                    if "CANCELLED" in result or not self._session_exists(context):
                        self.inactive += 1
                        self._record(
                            "headset_inactive",
                            "Session request was not accepted; no visible-frame claim.",
                        )
                        self.phase = "next"
                    else:
                        self.phase = "wait_running"
                        self.deadline = time.monotonic() + self.start_timeout
                    return {"RUNNING_MODAL"}
                if self.phase == "wait_running":
                    if self._session_running(context):
                        self.started += 1
                        self.cycle_was_running = True
                        self.phase = "dwell"
                        self.due = time.monotonic() + self.dwell
                    elif time.monotonic() >= self.deadline:
                        self.inactive += 1
                        self._record(
                            "headset_inactive",
                            "XR did not become running before bounded startup timeout.",
                        )
                        self.cycle_outcome_recorded = True
                        self.phase = "request_stop"
                    return {"RUNNING_MODAL"}
                if self.phase == "dwell":
                    # The counter is recorded below after stop. It is evidence
                    # of callback invocation only, never a visible-frame pass.
                    self.phase = "request_stop"
                    return {"RUNNING_MODAL"}
                if self.phase == "request_stop":
                    self._request_stop(context)
                    return {"RUNNING_MODAL"}
                if self.phase == "wait_stopped":
                    if not self._session_exists(context):
                        self.stopped += 1
                        outcome = (
                            "started_and_stopped"
                            if self.cycle_was_running
                            else "stopped_after_running"
                        )
                        if not self.cycle_outcome_recorded:
                            self._record(
                                outcome,
                                "XR lifecycle completed; visible frame remains unqualified.",
                            )
                            self.cycle_outcome_recorded = True
                        self.phase = "next"
                    elif time.monotonic() >= self.deadline:
                        self.failed += 1
                        self._record(
                            "stop_timeout", "XR session did not stop before timeout."
                        )
                        self.cycle_outcome_recorded = True
                        self.phase = "next"
                    return {"RUNNING_MODAL"}
                return self._advance_cycle(context)
            except Exception as exc:
                traceback.print_exc()
                self.failed += 1
                self._record("exception", f"{type(exc).__name__}: {exc}")
                return self._finish(context, True)

    bpy.utils.register_class(XRWB_OT_acceptance_soak)
    if __name__ == "__main__":
        bpy.ops.xrwb.acceptance_soak("INVOKE_DEFAULT")
