"""Factory-profile check of the finishing layer: readouts, detents, toasts, presets, wheel motion.

Real Blender and the real add-on; no XR session or headset. Run in a separate
background process with BLENDER_USER_CONFIG set to an empty temporary folder.
"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (  # noqa: E402
    controllers, editing, face_editing, interaction_log, onboarding, polish, ray_feedback, tool_wheel,
)
from xr_workbench.interaction import CaptureOwner, InputProvenance, InputSample, Pose  # noqa: E402

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "premium-smoke", 0)
RIGHT = controllers.RIGHT_HAND


def check_units_and_shapes():
    assert polish.length(0.004) == "4.0 mm"
    assert polish.length(0.124) == "12.4 cm"
    assert polish.length(2.5) == "2.50 m"
    points = polish.rounded_rect(0, 0, 10, 4, 1, 4)
    assert points[0] == (5.0, 2.0) and points[1] == points[-1]
    assert all(-1e-9 <= x <= 10 + 1e-9 and -1e-9 <= y <= 4 + 1e-9 for x, y in points)
    assert polish.ease_out(0) == 0 and polish.ease_out(1) == 1 and polish.ease_out(0.5) > 0.5
    corners = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    lines = polish.corner_brackets(Matrix.Identity(4), corners, 0.25)
    assert len(lines) == 48
    assert all(abs((Vector(a) - Vector(b)).length - 0.5) < 1e-6 for a, b in zip(lines[::2], lines[1::2]))
    return 1


def check_toasts():
    interaction_log.emit("commit", "Extrude committed", hand=RIGHT)
    message, level, hand, alpha = polish.current_toast()
    assert message == "Extrude committed" and level == "INFO" and hand == RIGHT and alpha > 0.0
    interaction_log.emit("ui_event", "LEFTMOUSE PRESS accepted")
    assert polish.current_toast()[0] == "Extrude committed", "Noisy stages never replace a toast"
    stamp = interaction_log.last_toast()[3]
    assert polish.current_toast(stamp + polish.TOAST_SECONDS + 0.1) is None, "Toasts fade out"
    assert 0.0 < polish.toast_alpha(polish.TOAST_SECONDS - 0.3) < 1.0
    interaction_log.emit("rejected", "Point at a face", level="WARNING")
    assert polish.current_toast()[1] == "WARNING"
    assert polish.LEVEL_COLOURS["WARNING"] == polish.WARNING
    return 1


def check_readouts_and_detents():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2)
    bpy.ops.ed.undo_push(message="Premium baseline")
    origin = Vector((0.2, 0.1, 3.0))
    rotation = Quaternion()
    transaction = face_editing.begin(bpy.context, origin, rotation, tool="extrude", owner=OWNER)
    ticks = []
    real_pulse = controllers.pulse
    controllers.pulse = lambda _context, path, kind="click": ticks.append(kind) or True
    try:
        gesture = NS(transaction=transaction, user_path=RIGHT, readout="", detent=None)
        for step, height in enumerate((0.0, 0.004, 0.012, 0.025, 0.0351)):
            result = transaction.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin + Vector((0, 0, height)))),
                                                                  1.0 + step))
            controllers._polish_feedback(bpy.context, gesture, result)
        assert ticks.count("detent") == 3, ticks  # Crossed 1, 2 and 3 cm.
        assert gesture.readout.startswith("Extrude 3.5 cm"), gesture.readout
        onboarding.set_pref("detents", False)
        transaction.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin + Vector((0, 0, 0.08)))), 9.0))
        controllers._polish_feedback(bpy.context, gesture, None)
        assert ticks.count("detent") == 3, "Detents can be turned off"
        onboarding.set_pref("detents", True)
    finally:
        controllers.pulse = real_pulse
        transaction.cancel(bpy.context)
    transaction = face_editing.begin(bpy.context, origin, rotation, tool="inset", owner=OWNER)
    transaction.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin + Vector((0.15, 0, 0)))), 1.0))
    assert polish.readout(transaction) == "Inset 15.0 cm"
    transaction.cancel(bpy.context)
    transaction = face_editing.begin(bpy.context, origin, rotation, tool="face_scale", owner=OWNER)
    transaction.update(bpy.context, InputSample(OWNER, 0, Pose(tuple(origin)), 1.0, uniform_scale=1.5))
    assert polish.readout(transaction) == "Scale face ×1.50" and polish.detent_index(transaction) == 15
    transaction.cancel(bpy.context)
    assert editing.active_transaction() is None
    return 1


def check_status_plates():
    settings = bpy.context.window_manager.xrwb_settings
    poses = {RIGHT: (Vector((0.3, 0, 1.2)), Quaternion(), Vector((0, 0, -1)))}
    viewer = tuple(Quaternion((1, 0, 0), 1.5708))
    controllers._active_gesture = NS(user_path=RIGHT, readout="Moved 12.0 cm · 15°", primary_position=None)
    interaction_log.emit("commit", "Committed as one Blender undo step", hand=RIGHT)
    try:
        plates = ray_feedback._status_plates(bpy.context, settings, "select", poses, viewer, 1.0)
        assert [plate[1][0] for plate in plates] == ["Moved 12.0 cm · 15°", "Committed as one Blender undo step"]
        assert all(plate[4] for plate in plates), "Readout and toast plates are centred"
        assert plates[1][0].translation.z > plates[0][0].translation.z, "The toast floats above the readout"
        onboarding.set_pref("readouts", False)
        onboarding.set_pref("toasts", False)
        assert ray_feedback._status_plates(bpy.context, settings, "select", poses, viewer, 1.0) == []
    finally:
        onboarding.set_pref("readouts", True)
        onboarding.set_pref("toasts", True)
        controllers._active_gesture = None
    return 1


def check_presets():
    settings = bpy.context.window_manager.xrwb_settings
    assert polish.matching_preset() == "BALANCED", "Defaults are the Balanced preset"
    assert bpy.ops.xrwb.feel_preset(preset="PRO") == {"FINISHED"}
    assert polish.matching_preset() == "PRO"
    assert abs(settings.snap_grid_cm - 1.0) < 1e-6 and abs(settings.wheel_size_m - 0.08) < 1e-6
    assert controllers.hold_mode_name(2) == "Snap 1 cm / 5°"
    settings.label_scale = 1.1
    assert polish.matching_preset() == "", "Hand tuning leaves every preset unlit"
    assert bpy.ops.xrwb.feel_preset(preset="BALANCED") == {"FINISHED"}
    onboarding.reset_cache()
    assert polish.matching_preset() == "BALANCED", "Presets persist in vr_prefs.json"
    return len(polish.FEEL_PRESETS)


def check_wheel_motion():
    import math
    upright = Quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    tool_wheel.open_wheel(bpy.context, RIGHT, (0.0, 0.0, 1.0), upright, 1.0)
    opened = tool_wheel._state["opened"]
    assert tool_wheel.opening(opened) == 0.0 and tool_wheel.opening(opened + 1.0) == 1.0
    assert 0.5 < tool_wheel.opening(opened + tool_wheel.OPEN_SECONDS / 2) < 1.0
    tool_wheel.follow((0.0, 0.0, 1.09))
    assert tool_wheel.highlighted()[1] == "select"
    plain = tool_wheel.geometry()
    lifted = tool_wheel.geometry(highlight=0)
    assert max(y for _x, y in lifted[0]) > max(y for _x, y in plain[0]) + 0.005, "The chosen wedge lifts out"
    assert lifted[1] == plain[1]
    assert tool_wheel.label_position(0)[1] > tool_wheel.label_position(0, 1.0)[1] - 1e-9
    tool_wheel.close()
    return 1


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        shapes = check_units_and_shapes()
        toasts = check_toasts()
        detents = check_readouts_and_detents()
        plates = check_status_plates()
        presets = check_presets()
        wheel = check_wheel_motion()
    finally:
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        xr_workbench.unregister()
    print(f"XR_PREMIUM_SMOKE_PASS shapes={shapes} toasts={toasts} readouts_detents={detents} plates={plates} "
          f"presets={presets} wheel_motion={wheel} input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
