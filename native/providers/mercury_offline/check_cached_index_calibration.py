"""Check whether a cached Index config can calibrate a raw 1920x960 stereo stream.

Only validates static metadata. Does not access the camera, start a runtime,
or print device identifiers or calibration values.
"""

import argparse
import json
import math
from pathlib import Path


def _finite_vector(values):
    return isinstance(values, list) and len(values) == 3 and all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in values
    )


def compatible(config):
    if not isinstance(config, dict):
        return False
    if config.get("model_number") != "Utah MP":
        return False
    head = config.get("head", {})
    if not isinstance(head, dict):
        return False
    if not all(_finite_vector(head.get(key)) for key in ("position", "plus_x", "plus_z")):
        return False
    cameras = config.get("tracked_cameras", [])
    if not isinstance(cameras, list) or len(cameras) != 2:
        return False
    if not all(isinstance(camera, dict) for camera in cameras):
        return False
    if {camera.get("name") for camera in cameras} != {"left", "right"}:
        return False
    for camera in cameras:
        intrinsics = camera.get("intrinsics", {})
        if not isinstance(intrinsics, dict):
            return False
        distortion = intrinsics.get("distort", {})
        extrinsics = camera.get("extrinsics", {})
        if not isinstance(distortion, dict) or not isinstance(extrinsics, dict):
            return False
        if (intrinsics.get("width"), intrinsics.get("height")) != (960, 960):
            return False
        if distortion.get("type") != "DISTORT_EXTENDED_FTHETA":
            return False
        coeffs = distortion.get("coeffs")
        if not isinstance(coeffs, list) or len(coeffs) != 4:
            return False
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in coeffs):
            return False
        if not all(
            isinstance(intrinsics.get(key), (int, float))
            and math.isfinite(intrinsics[key])
            and (intrinsics[key] > 0 if key.startswith("focal_") else 0 <= intrinsics[key] < 960)
            for key in ("focal_x", "focal_y", "center_x", "center_y")
        ):
            return False
        if not all(_finite_vector(extrinsics.get(key)) for key in ("position", "plus_x", "plus_z")):
            return False
    return True


def _self_test():
    pose = {key: [0.0, 0.0, 0.0] for key in ("position", "plus_x", "plus_z")}
    def camera(name):
        return {
            "name": name,
            "intrinsics": {
                "width": 960, "height": 960, "focal_x": 420.0, "focal_y": 420.0,
                "center_x": 480.0, "center_y": 480.0,
                "distort": {"type": "DISTORT_EXTENDED_FTHETA", "coeffs": [0.0] * 4},
            },
            "extrinsics": pose,
        }
    valid = {"model_number": "Utah MP", "head": pose,
             "tracked_cameras": [camera("left"), camera("right")]}
    assert compatible(valid)
    valid["tracked_cameras"][1]["intrinsics"]["width"] = 640
    assert not compatible(valid)
    print("INDEX_CALIBRATION_SELFTEST_PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
    else:
        if args.config is None:
            parser.error("config path is required")
        try:
            with args.config.open(encoding="utf-8") as stream:
                result = compatible(json.load(stream))
        except (OSError, ValueError, TypeError):
            result = False
        print("INDEX_CALIBRATION_METADATA_COMPATIBLE" if result else "INDEX_CALIBRATION_METADATA_REJECTED")
        raise SystemExit(0 if result else 1)
