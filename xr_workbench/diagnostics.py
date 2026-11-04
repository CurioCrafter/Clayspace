"""Read-only capability inventory for the native XR Workbench M0 milestone.

The report intentionally keeps registration, advertisement, enablement, and
live activity separate.  Missing evidence is ``None``/``"unknown"`` rather
than a guessed ``False``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any, Mapping


SCHEMA_VERSION = "1.0.0"
TARGET_BLENDER_VERSION = (5, 1, 2)
_SENSITIVE_KEYS = {
    "device_id",
    "device_identifier",
    "executable_path",
    "library_path",
    "log_path",
    "manifest_path",
    "path",
    "pnp_device_id",
    "serial",
    "source_path",
    "system_name",
}


def _now_utc() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def _redacted_path(value: Any) -> str | None:
    if value in (None, ""):
        return None
    name = Path(str(value).replace("\\", "/")).name
    return f"<redacted>/{name}" if name else "<redacted>"


def redact_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy with filesystem paths and identifiers removed."""

    def clean(value: Any, key: str = "") -> Any:
        normalized = key.lower()
        if normalized in _SENSITIVE_KEYS or normalized.endswith("_path"):
            if normalized in {
                "device_id",
                "device_identifier",
                "pnp_device_id",
                "serial",
                "system_name",
            }:
                return None if value in (None, "") else "<redacted>"
            return _redacted_path(value)
        if isinstance(value, Mapping):
            return {str(k): clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [clean(item) for item in value]
        return _json_value(value)

    return clean(deepcopy(dict(report)))


def _load_manifest(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    expanded = os.path.expandvars(path)
    try:
        with open(expanded, encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _registry_values(key_path: str) -> list[tuple[str, Any]] | None:
    """Read one 64-bit HKLM OpenXR registry key, or return unknown off Windows."""

    if sys.platform != "win32":
        return None
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
            values: list[tuple[str, Any]] = []
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                values.append((name, value))
                index += 1
            return values
    except FileNotFoundError:
        return []
    except OSError:
        return None


def _manifest_identity(
    manifest: Mapping[str, Any] | None, section: str
) -> dict[str, Any]:
    details = manifest.get(section, {}) if manifest else {}
    if not isinstance(details, Mapping):
        details = {}
    return {
        "name": details.get("name"),
        "api_version": details.get("api_version"),
        "implementation_version": details.get("implementation_version"),
        "library_path": details.get("library_path"),
    }


def _native_evidence(context: Any) -> dict[str, Any]:
    candidates: list[Any] = []
    if isinstance(context, Mapping):
        candidates.append(context.get("native_xr"))
        log_text = context.get("native_xr_log")
    elif context is not None:
        candidates.append(getattr(context, "native_xr", None))
        log_text = getattr(context, "native_xr_log", None)
        window_manager = getattr(context, "window_manager", None)
        if window_manager is not None:
            try:
                candidates.append(window_manager.get("xr_workbench_native_evidence"))
            except (AttributeError, TypeError, RuntimeError):
                pass
    else:
        log_text = None
    for candidate in candidates:
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                continue
        if isinstance(candidate, Mapping):
            evidence = _sanitize_native_evidence(candidate)
            evidence.setdefault("evidence_scope", "provided_context")
            return evidence
    if log_text:
        evidence = _parse_native_log(str(log_text))
        if evidence:
            evidence["evidence_scope"] = "historical_import"
        return evidence
    if context is not None:
        return _current_process_native_evidence()
    return {}


def _current_process_native_evidence() -> dict[str, Any]:
    """Read at most 1 MiB from the launch-unique native stdout log."""

    log_path = os.environ.get("XRWB_NATIVE_LOG")
    log_session = os.environ.get("XRWB_NATIVE_LOG_SESSION")
    if not log_path or not log_session:
        return {}
    try:
        with open(log_path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 1024 * 1024))
            text = stream.read(1024 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return {}
    evidence = _parse_native_log(text)
    if evidence:
        evidence["evidence_scope"] = "current_process_log"
        evidence["current_log_session_present"] = True
    return evidence


def _parse_native_log(text: str) -> dict[str, Any]:
    """Parse only the bounded native ``XR_WORKBENCH key=value`` evidence lines."""

    repeated = {
        "enabled_extension": "enabled_extensions",
        "enabled_api_layer": "enabled_api_layers",
        "available_blend_mode": "available_blend_modes",
    }
    evidence: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("XR_WORKBENCH "):
            continue
        field = line.removeprefix("XR_WORKBENCH ")
        if "=" not in field:
            continue
        key, value = field.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "runtime_name":
            # A new instance marker invalidates every earlier instance in a
            # concatenated log. Never accumulate extensions across restarts.
            evidence = {"runtime_name": value}
        elif key in repeated:
            evidence.setdefault(repeated[key], []).append(value)
        elif key in {
            "runtime_name",
            "runtime_version",
            "graphics_binding",
            "system_name",
            "passthrough_status",
            "composition_route",
            "session_state",
        }:
            evidence[key] = value
    return evidence


def _sanitize_native_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelist the small native contract; never copy arbitrary payloads."""

    scalar_keys = {
        "runtime_name",
        "runtime_version",
        "graphics_binding",
        "system_name",
        "passthrough_status",
        "composition_route",
        "session_state",
        "input_provenance",
        "controller_availability",
        "optical_hand_availability",
        "simulator_availability",
        "evidence_scope",
        "current_log_session_present",
    }
    list_keys = {
        "advertised_extensions",
        "available_extensions",
        "enabled_extensions",
        "enabled_api_layers",
        "active_api_layers",
        "available_blend_modes",
    }
    result: dict[str, Any] = {}
    for key in scalar_keys:
        value = source.get(key)
        if value is None or isinstance(value, (bool, int, float)):
            if key in source:
                result[key] = value
        elif isinstance(value, str):
            result[key] = value[:256]
    for key in list_keys:
        value = source.get(key)
        if isinstance(value, (list, tuple, set)):
            result[key] = [str(item)[:256] for item in value if item is not None][:256]

    nested_contract = {
        "headset": {"status", "connected", "device_identifier"},
        "camera": {
            "provider",
            "availability",
            "freshness_support",
            "failure_behavior_validated",
        },
        "stereo_calibration": {"result", "method", "measured"},
        "frame_timing": {"measured", "sample_count", "mean_ms", "p95_ms", "max_ms"},
    }
    for group, allowed in nested_contract.items():
        value = source.get(group)
        if isinstance(value, Mapping):
            result[group] = {
                key: _json_value(item)
                for key, item in value.items()
                if key in allowed
                and isinstance(item, (type(None), bool, int, float, str))
            }
    return result


def _blender_report(context: Any) -> dict[str, Any]:
    try:
        import bpy
    except ImportError:
        return {
            "status": "unknown",
            "version": None,
            "version_tuple": None,
            "build_hash": None,
            "build_branch": None,
            "build_date": None,
            "openxr_compiled": None,
        }

    app = bpy.app
    version = tuple(getattr(app, "version", ())) or None
    build_options = getattr(app, "build_options", None)
    return {
        "status": "available",
        "version": getattr(app, "version_string", None),
        "version_tuple": list(version) if version else None,
        "build_hash": _json_value(getattr(app, "build_hash", None)),
        "build_branch": _json_value(getattr(app, "build_branch", None)),
        "build_date": _json_value(getattr(app, "build_date", None)),
        "openxr_compiled": (
            bool(getattr(build_options, "xr_openxr"))
            if build_options is not None and hasattr(build_options, "xr_openxr")
            else None
        ),
    }


def _graphics_report(context: Any) -> dict[str, Any]:
    result = {
        "status": "unknown",
        "backend": None,
        "vendor": None,
        "renderer": None,
        "driver": None,
        "device_type": None,
        "evidence_source": None,
    }
    try:
        import bpy
        import gpu
    except ImportError:
        return result

    # Native gpu.platform getters require a real graphics context. In background
    # mode they can assert/crash below Python, so UNKNOWN is the only safe answer.
    if bool(getattr(bpy.app, "background", False)):
        return result

    getters = {
        "backend": "backend_type_get",
        "vendor": "vendor_get",
        "renderer": "renderer_get",
        "driver": "version_get",
        "device_type": "device_type_get",
    }
    observed = False
    for field, function_name in getters.items():
        function = getattr(getattr(gpu, "platform", None), function_name, None)
        if not callable(function):
            continue
        try:
            result[field] = _json_value(function())
            observed = observed or result[field] not in (None, "")
        except (RuntimeError, SystemError):
            pass
    if observed:
        result["status"] = "available"
        result["evidence_source"] = "Blender gpu.platform"
    return result


def _session_report(context: Any, native: Mapping[str, Any]) -> dict[str, Any]:
    session = {
        "status": "unknown",
        "active": None,
        "state": native.get("session_state"),
        "evidence_source": None,
    }
    if native.get("session_state") is not None:
        session["status"] = "observed"
        session["evidence_source"] = "native XR_WORKBENCH session marker"
    if context is None or isinstance(context, Mapping):
        return session
    window_manager = getattr(context, "window_manager", None)
    state = getattr(window_manager, "xr_session_state", None)
    is_running = getattr(state, "is_running", None)
    if callable(is_running):
        try:
            session.update(
                status="observed",
                active=bool(is_running(context)),
                evidence_source="Blender XrSessionState.is_running",
            )
        except (RuntimeError, SystemError, TypeError):
            pass
    return session


def _openxr_report(context: Any, native: Mapping[str, Any]) -> dict[str, Any]:
    runtime_values = _registry_values(r"SOFTWARE\Khronos\OpenXR\1")
    runtime_path = None
    if runtime_values is not None:
        runtime_path = dict(runtime_values).get("ActiveRuntime")
    runtime_manifest = _load_manifest(runtime_path)
    runtime_identity = _manifest_identity(runtime_manifest, "runtime")
    if native.get("runtime_name"):
        runtime_identity["name"] = native["runtime_name"]
        runtime_identity["name_evidence_source"] = "native XR_WORKBENCH runtime marker"
    elif runtime_identity.get("name"):
        runtime_identity["name_evidence_source"] = "registered runtime manifest"
    if native.get("runtime_version"):
        runtime_identity["version"] = native["runtime_version"]
        runtime_identity["version_evidence_source"] = (
            "native XR_WORKBENCH runtime marker"
        )

    layers: list[dict[str, Any]] = []
    active_names = set(
        native.get("enabled_api_layers") or native.get("active_api_layers") or []
    )
    for kind in ("Implicit", "Explicit"):
        values = _registry_values(rf"SOFTWARE\Khronos\OpenXR\1\ApiLayers\{kind}")
        if values is None:
            continue
        for manifest_path, disabled_flag in values:
            manifest = _load_manifest(manifest_path)
            identity = _manifest_identity(manifest, "api_layer")
            name = identity.get("name")
            loader_enabled = None
            if kind == "Implicit" and isinstance(disabled_flag, int):
                loader_enabled = disabled_flag == 0
            layers.append(
                {
                    **identity,
                    "kind": kind.lower(),
                    "manifest_path": manifest_path,
                    "registered": True,
                    "loader_registry_enabled": loader_enabled,
                    "active": True if name in active_names else None,
                    "active_evidence_source": (
                        "native XR_WORKBENCH enabled_api_layer marker"
                        if active_names
                        else None
                    ),
                }
            )
    registered_names = {layer.get("name") for layer in layers}
    for name in sorted(active_names - registered_names):
        layers.append(
            {
                "name": name,
                "kind": "unknown",
                "manifest_path": None,
                "registered": None,
                "loader_registry_enabled": None,
                "active": True,
                "active_evidence_source": "native XR_WORKBENCH enabled_api_layer marker",
            }
        )

    advertised = set(
        native.get("advertised_extensions") or native.get("available_extensions") or []
    )
    enabled_present = "enabled_extensions" in native
    enabled = set(native.get("enabled_extensions") or [])
    extensions = []
    for name in sorted(advertised | enabled):
        extensions.append(
            {
                "name": name,
                "advertised": True if name in advertised else None,
                "enabled": (
                    True
                    if name in enabled
                    else False
                    if enabled_present and name in advertised
                    else None
                ),
                "active": None,
            }
        )

    registry_status = (
        "unknown"
        if runtime_values is None
        else ("registered" if runtime_path else "not_registered")
    )
    return {
        "runtime": {
            **runtime_identity,
            "registration_status": registry_status,
            "manifest_path": runtime_path,
            "manifest_readable": bool(runtime_manifest) if runtime_path else None,
            "registration_evidence_source": (
                "HKLM 64-bit OpenXR ActiveRuntime"
                if runtime_values is not None
                else None
            ),
        },
        "api_layers": layers,
        "extensions": extensions,
        "session": _session_report(context, native),
        "composition_route": native.get("composition_route"),
        "passthrough_status": native.get("passthrough_status", "unknown"),
    }


def _passthrough_requested(context: Any) -> bool | None:
    if context is None or isinstance(context, Mapping):
        return None
    window_manager = getattr(context, "window_manager", None)
    settings = getattr(window_manager, "xr_session_settings", None)
    value = getattr(settings, "show_passthrough", None)
    return bool(value) if value is not None else None


def _sensors_report(
    native: Mapping[str, Any], session_active: bool | None, context: Any
) -> dict[str, Any]:
    headset = (
        native.get("headset") if isinstance(native.get("headset"), Mapping) else {}
    )
    camera = native.get("camera") if isinstance(native.get("camera"), Mapping) else {}
    return {
        "headset": {
            "status": headset.get(
                "status",
                "observed"
                if native.get("system_name") and session_active is True
                else "unknown",
            ),
            "connected": headset.get(
                "connected",
                True if native.get("system_name") and session_active is True else None,
            ),
            "device_identifier": headset.get(
                "device_identifier", native.get("system_name")
            ),
            "evidence_source": headset.get(
                "evidence_source",
                "native xrGetSystem system properties"
                if native.get("system_name")
                else None,
            ),
        },
        "camera": {
            "provider": camera.get("provider"),
            "availability": camera.get("availability", "unknown"),
            "freshness_support": camera.get("freshness_support", "unknown"),
            "passthrough_requested": _passthrough_requested(context),
            "runtime_passthrough_status": native.get("passthrough_status", "unknown"),
            "failure_behavior_validated": camera.get("failure_behavior_validated"),
            "collector_access_attempted": False,
            "pixels_in_report": False,
            "evidence_source": camera.get("evidence_source"),
        },
        "input_sources": {
            "controller": {
                "availability": native.get("controller_availability", "unknown"),
                "provenance": "controller",
            },
            "optical_hands": {
                "availability": native.get("optical_hand_availability", "unknown"),
                "provenance": "optical",
            },
            "simulator": {
                "availability": native.get("simulator_availability", "unknown"),
                "provenance": "simulator",
            },
            "active_provenance": native.get("input_provenance"),
        },
    }


def _requirement(state: str, evidence: Any = None) -> dict[str, Any]:
    return {"state": state, "evidence": evidence}


def _m0_report(
    blender: Mapping[str, Any],
    graphics: Mapping[str, Any],
    openxr: Mapping[str, Any],
    sensors: Mapping[str, Any],
    native: Mapping[str, Any],
) -> dict[str, Any]:
    version = tuple(blender.get("version_tuple") or ())
    target_state = (
        "pass"
        if version == TARGET_BLENDER_VERSION
        else ("fail" if version else "unknown")
    )
    graphics_complete = all(
        graphics.get(key) for key in ("backend", "vendor", "renderer", "driver")
    )
    session_active = openxr["session"].get("active") is True
    headset_measured = (
        session_active
        and sensors["headset"].get("connected") is True
        and bool(sensors["headset"].get("evidence_source"))
    )
    calibration = native.get("stereo_calibration")
    calibration_pass = (
        isinstance(calibration, Mapping)
        and calibration.get("result") == "pass"
        and calibration.get("measured") is True
        and bool(calibration.get("method"))
    )
    frame_timing = native.get("frame_timing")
    timing_measured = (
        isinstance(frame_timing, Mapping)
        and frame_timing.get("measured") is True
        and isinstance(frame_timing.get("sample_count"), int)
        and frame_timing["sample_count"] > 0
        and isinstance(frame_timing.get("mean_ms"), (int, float))
        and isinstance(frame_timing.get("p95_ms"), (int, float))
    )
    current_native = native.get("evidence_scope") in {
        "current_process_log",
        "provided_context",
    }
    passthrough_active = (
        current_native
        and session_active
        and openxr.get("composition_route") == "fb_passthrough"
        and openxr.get("passthrough_status") == "active"
    )
    requirements = {
        "target_blender_5_1_2": _requirement(target_state, blender.get("version")),
        "native_diagnostic_build": _requirement(
            "pass" if current_native and native.get("runtime_name") else "unknown"
        ),
        "graphics_inventory": _requirement("pass" if graphics_complete else "unknown"),
        "live_openxr_session": _requirement("pass" if session_active else "unknown"),
        "real_headset_evidence": _requirement(
            "pass" if headset_measured else "unknown"
        ),
        "composition_route": _requirement(
            "pass" if passthrough_active else "unknown",
            openxr.get("composition_route"),
        ),
        "input_provenance": _requirement(
            "pass"
            if current_native
            and session_active
            and sensors["input_sources"].get("active_provenance")
            in {"controller", "optical", "simulator"}
            else "unknown",
            sensors["input_sources"].get("active_provenance"),
        ),
        "stereo_calibration": _requirement(
            "pass" if calibration_pass else "unknown", calibration
        ),
        "frame_timing": _requirement(
            "pass" if timing_measured else "unknown", frame_timing
        ),
        "camera_failure_behavior": _requirement(
            "pass"
            if sensors["camera"].get("failure_behavior_validated") is True
            else "unknown"
        ),
    }
    blockers = [
        name for name, result in requirements.items() if result["state"] != "pass"
    ]
    if not blockers:
        status = "pass"
    elif any(result["state"] == "fail" for result in requirements.values()):
        status = "blocked"
    else:
        status = "incomplete"
    return {"status": status, "requirements": requirements, "blockers": blockers}


def collect_report(context: Any = None, *, redact: bool = True) -> dict[str, Any]:
    """Collect a bounded, read-only and JSON-serializable capability report."""

    native = _native_evidence(context)
    blender = _blender_report(context)
    graphics = _graphics_report(context)
    openxr = _openxr_report(context, native)
    sensors = _sensors_report(native, openxr["session"].get("active"), context)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _now_utc(),
        "target": {"blender_version": ".".join(map(str, TARGET_BLENDER_VERSION))},
        "host": {
            "platform": platform.system() or None,
            "platform_release": platform.release() or None,
            "python_version": platform.python_version(),
        },
        "blender": blender,
        "graphics": graphics,
        "openxr": openxr,
        "sensors": sensors,
        "native_xr": native,
        "evidence": {
            "native_markers_present": bool(native),
            "native_scope": native.get("evidence_scope"),
            "sources": [
                "Windows OpenXR loader registry",
                "Blender runtime APIs when safely available",
                *(["native XR_WORKBENCH markers"] if native else []),
            ],
            "limits": [
                "Registry entries prove registration, not live activation",
                "Camera/provider freshness remains unknown until measured",
                "No camera device or pixel access is performed",
            ],
        },
        "privacy": {
            "paths_redacted": redact,
            "device_identifiers_redacted": redact,
            "collector_camera_access_attempted": False,
            "camera_pixels_included": False,
        },
    }
    report["m0"] = _m0_report(blender, graphics, openxr, sensors, native)
    report = _json_value(report)
    return redact_report(report) if redact else report


def write_report(
    report: Mapping[str, Any], path: str | os.PathLike[str], *, redact: bool = True
) -> Path:
    """Write one deterministic JSON report; exports are redacted by default."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_report(report) if redact else _json_value(report)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination
