"""Read/change SteamVR camera permission or Direct Mode through its settings API.

ABI: ValveSoftware/openvr headers/openvr_capi.h, IVRSettings_003, checked
2026-09-21. This background client never retrieves camera frames.
"""

import argparse
import ctypes as ct
import json
from pathlib import Path


def bool_setting(section, key, change=None):
    dll_path = Path(
        r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\openvr_api.dll"
    )
    api = ct.CDLL(str(dll_path))
    error_type = ct.POINTER(ct.c_int)
    api.VR_InitInternal.argtypes = [error_type, ct.c_int]
    api.VR_InitInternal.restype = ct.c_size_t
    api.VR_GetGenericInterface.argtypes = [ct.c_char_p, error_type]
    api.VR_GetGenericInterface.restype = ct.c_void_p
    api.VR_ShutdownInternal.argtypes = []
    api.VR_ShutdownInternal.restype = None
    error = ct.c_int()
    api.VR_InitInternal(ct.byref(error), 3)  # VRApplication_Background, not a scene.
    if error.value:
        raise RuntimeError(f"OpenVR background initialization failed: {error.value}")
    try:
        pointer = api.VR_GetGenericInterface(
            b"FnTable:IVRSettings_003", ct.byref(error)
        )
        if error.value or not pointer:
            raise RuntimeError(f"IVRSettings_003 unavailable: {error.value}")
        table = ct.cast(pointer, ct.POINTER(ct.c_void_p))
        get_bool = ct.WINFUNCTYPE(ct.c_bool, ct.c_char_p, ct.c_char_p, error_type)(
            table[5]
        )
        set_bool = ct.WINFUNCTYPE(
            None, ct.c_char_p, ct.c_char_p, ct.c_bool, error_type
        )(table[1])

        def read():
            error.value = 0
            value = get_bool(section, key, ct.byref(error))
            if error.value:
                raise RuntimeError(f"SteamVR setting read failed: {error.value}")
            return bool(value)

        before = read()
        if change is not None:
            error.value = 0
            set_bool(section, key, bool(change), ct.byref(error))
            if error.value:
                raise RuntimeError(f"SteamVR setting write failed: {error.value}")
        after = read()
        if change is not None and after != bool(change):
            raise RuntimeError("SteamVR setting did not persist")
        return before, after
    finally:
        api.VR_ShutdownInternal()


def camera_setting(change=None):
    before, after = bool_setting(b"camera", b"enableCamera", change)
    return {"camera_enabled_before": before, "camera_enabled_after": after,
            "camera_frames_accessed": False, "api": "IVRSettings_003"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--camera", choices=("status", "on", "off"), default="status")
    target.add_argument("--direct-mode", choices=("status", "on", "off"))
    parser.add_argument("--consented", action="store_true")
    args = parser.parse_args()
    requested = args.direct_mode or args.camera
    if requested != "status" and not args.consented:
        parser.error(
            "Setting changes require explicit user consent and --consented"
        )
    change = None if requested == "status" else requested == "on"
    if args.direct_mode:
        before, after = bool_setting(b"direct_mode", b"enable", change)
        result = {"direct_mode_before": before, "direct_mode_after": after,
                  "camera_frames_accessed": False, "restart_required": before != after,
                  "api": "IVRSettings_003"}
    else:
        result = camera_setting(change)
    print(json.dumps(result))
