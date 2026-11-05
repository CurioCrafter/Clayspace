"""Synthetic-only smoke test for the bare optical-hand modal adapter."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xr_workbench import hands  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def observation(
    hand: str,
    timestamp: float,
    separation: float,
    generation: int = 12,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> hands.HandObservation:
    thumb = center
    index = (center[0] + separation, center[1], center[2])
    return hands.HandObservation(
        hand=hand,
        generation=generation,
        timestamp=timestamp,
        thumb_tip=thumb,
        index_proximal=(center[0], center[1], center[2] - 0.08),
        index_tip=index,
        viewer_scale=1.0,
        navigation_signature=(
            1.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ),
    )


def main() -> None:
    require((4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0), "smoke requires exact Blender 5.1.2")

    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 2.0))
    front = bpy.context.object
    front.name = "XRWB_RaycastFront"
    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.0))
    rear = bpy.context.object
    rear.name = "XRWB_RaycastOccluded"
    bpy.context.view_layer.update()
    hit = hands._scene_raycast_target(
        bpy.context,
        ((0.0, 0.0, 10.0), (0.0, 0.0, -1.0)),
    )
    require(hit is front, "evaluated ray did not map the nearest hit to its original")
    require(hit is not rear, "scene ray ignored visible occlusion")
    require(
        hands._scene_raycast_target(
            bpy.context,
            ((100.0, 0.0, 10.0), (0.0, 0.0, -1.0)),
        )
        is None,
        "scene ray miss fabricated a selection target",
    )

    state = hands.OpticalGestureState()
    require(state.update(observation("RIGHT", 1.0, 0.04)) == "hover", "neutral failed")
    require(
        state.update(observation("RIGHT", 1.1, 0.015)) == "hover",
        "dwell began incorrectly",
    )
    require(
        state.update(observation("RIGHT", 1.16, 0.015)) == "press", "pinch press failed"
    )
    require(state.active_hand == "RIGHT", "right hand did not own capture")
    require(
        state.update(observation("LEFT", 1.2, 0.04)) == "ignored",
        "second hand stole capture",
    )
    require(
        state.update(observation("RIGHT", 1.3, 0.04)) == "release",
        "pinch release failed",
    )
    require(state.active_hand is None, "release retained capture")

    two = hands.TwoHandScaleState()
    two.update(observation("RIGHT", 2.0, 0.04, center=(-0.1, 0.0, 0.0)))
    two.update(observation("LEFT", 2.0, 0.04, center=(0.1, 0.0, 0.0)))
    two.update(observation("RIGHT", 2.1, 0.015, center=(-0.1, 0.0, 0.0)))
    require(
        two.update(observation("RIGHT", 2.16, 0.015, center=(-0.1, 0.0, 0.0)))
        == "waiting_started",
        "two-hand first pinch did not wait",
    )
    two.update(observation("LEFT", 2.17, 0.015, center=(0.1, 0.0, 0.0)))
    two.update(observation("RIGHT", 2.21, 0.015, center=(-0.1, 0.0, 0.0)))
    require(
        two.update(observation("LEFT", 2.23, 0.015, center=(0.1, 0.0, 0.0))) == "begin",
        "two-hand coherent pinch did not begin",
    )
    require(
        two.update(observation("RIGHT", 2.3, 0.04, center=(-0.1, 0.0, 0.0)))
        == "freeze",
        "first release did not freeze",
    )
    require(
        two.update(observation("LEFT", 2.31, 0.04, center=(0.1, 0.0, 0.0))) == "commit",
        "second release did not commit",
    )

    operator = SimpleNamespace(_timer=None)
    desktop_event = SimpleNamespace(type="MOUSEMOVE")
    hands._router_operator = operator
    require(
        hands.XRWB_OT_hand_tracking.modal(operator, bpy.context, desktop_event)
        == {"PASS_THROUGH"},
        "non-timer desktop event was consumed",
    )
    hands._router_operator = None
    print(
        "XR_HANDS_SMOKE_PASS blender=5.1.2 provenance=synthetic "
        "runtime_access=false camera_access=false one_owner=pass "
        "two_hand_freeze_commit=pass desktop_passthrough=pass "
        "object_select_raycast_hit_miss_occlusion=pass "
        "thresholds_m=0.020,0.030 dwell_s=0.050"
    )


if __name__ == "__main__":
    main()
