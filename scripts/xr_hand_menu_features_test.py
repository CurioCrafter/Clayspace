"""Real native hand-UI checks in an owned, empty, camera-off SteamVR process.

The caller drives this generator on its main-thread timer and owns XR/process
lifetime. Synthetic rays verify UI routing, never physical controller tracking.
Only explicit Blender UI PNGs are captured; no room imagery is read or saved.
"""
from array import array
from math import isfinite
import os
import runpy
from pathlib import Path
from uuid import uuid4

import bpy
from mathutils import Quaternion, Vector


def _alpha_bounds(path):
    """Inspect a native UI capture, including its transparent-background contract."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        assert image.channels == 4 and width > 0 and height > 0
        pixels = array("f", [0.0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        xmin, ymin, xmax, ymax = width, height, -1, -1
        foreground = transparent = 0
        # Every fourth pixel is enough for broad bounds and avoids a costly
        # full-frame Python image loop; button activation is tested separately.
        for y in range(0, height, 4):
            row = y * width * 4
            for x in range(0, width, 4):
                alpha = pixels[row + x * 4 + 3]
                if alpha > 0.1:
                    xmin, ymin = min(xmin, x), min(ymin, y)
                    xmax, ymax = max(xmax, x), max(ymax, y)
                    foreground += 1
                elif alpha < 0.01:
                    transparent += 1
        assert foreground > 20, "Hand capture contains no visible UI"
        assert transparent > foreground, "Hand capture is not an isolated transparent UI layer"
        return {"width": width, "height": height,
                "alpha_bounds": [xmin, ymin, xmax, ymax],
                "sampled_foreground": foreground, "sampled_transparent": transparent}
    finally:
        bpy.data.images.remove(image)


def run(context, addon, output_dir=None):
    """Yield between queued UI events; return assertions and capture paths on success."""
    root = Path(__file__).resolve().parents[1]
    simulated = os.environ.get("XRWB_HAND_MENU_SIMULATOR") == "1"
    if simulated:
        assert os.environ.get("XRWB_EDITOR_TEST_ALLOW") == "1"
        assert os.environ.get("SIMULATED_ENABLE") == "1"
        assert Path(os.environ["XR_RUNTIME_JSON"]).resolve() == (
            root / "native/steamvr_free/monado-inproc-build/openxr_monado-dev.json"
        ).resolve(), "Only the camera-free simulator is allowed"
    else:
        assert os.environ.get("XRWB_STEAMVR_SMOKE") == "1", "Owned smoke opt-in required"
        assert os.environ.get("XRWB_STEAMVR") == "1" and not os.environ.get("SIMULATED_ENABLE")
    assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1", "Synthetic menu opt-in required"
    profile = Path(os.environ["BLENDER_USER_CONFIG"]).resolve().parent
    if simulated:
        assert profile.parent == root and profile.name.startswith(".tmp_xr_probe_"), "Probe profile required"
    else:
        assert profile in (root / ".xr_profile", root / ".xr_addons_profile"), "Private profile required"
    assert not bpy.app.background and context.mode == "OBJECT"
    assert not context.scene.objects, "Refusing to modify a nonempty scene"
    assert not addon.editing.active_transaction() and addon.controllers._active_gesture is None
    assert not addon.controllers._release_required, "Controller input is still held"
    assert addon.runtime.session_running(context)
    assert not context.window_manager.xr_session_settings.show_passthrough
    assert not any(os.environ.get(key) == "1" for key in (
        "XRWB_INDEX_CAMERA_OVERLAY", "XRWB_INDEX_CAMERA_GESTURE", "XRWB_INDEX_CAMERA_CONSENT",
    )), "Camera mode cannot capture UI test images"
    assert "target" in bpy.ops.wm.xr_editor_surface_event.get_rna_type().properties
    assert "hand_menu" in bpy.ops.wm.xr_editor_surface_capture.get_rna_type().properties
    capture_dir = Path(output_dir or root / "reports" / "hand-menu-features").resolve()
    assert capture_dir.is_relative_to(root / "reports"), "Captures must stay in project reports"
    capture_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex[:8]
    captures = []
    original_page = context.window_manager.xrwb_settings.command_page
    original_editor = tuple(context.window_manager.xr_session_state.editor_surface_info(context))
    original_pose = tuple(context.window_manager.xr_session_state.editor_surface_pose(context))
    undo_enabled = context.preferences.edit.use_global_undo
    undo_steps = context.preferences.edit.undo_steps
    show_tooltips = context.preferences.view.show_tooltips
    registered = []

    class XRWB_PT_hand_test(bpy.types.Panel):
        bl_idname = "XRWB_PT_hand_test"
        bl_label = "Synthetic native button test"
        bl_space_type = "VIEW_3D"
        bl_region_type = "HEADER"
        bl_ui_units_x = 12

        def draw(self, _context):
            self.layout.scale_y = 8.0
            self.layout.operator("xrwb.add_primitive", text="Add Test Cube", icon="MESH_CUBE").kind = "CUBE"

    class XRWB_PT_hand_nested_test(bpy.types.Panel):
        bl_idname = "XRWB_PT_hand_nested_test"
        bl_label = "Synthetic native submenu test"
        bl_space_type = "VIEW_3D"
        bl_region_type = "HEADER"
        bl_ui_units_x = 12

        def draw(self, _context):
            self.layout.scale_y = 8.0
            self.layout.menu("VIEW3D_MT_mesh_add", text="Native Mesh Add", icon="MESH_CUBE")

    def call(operator, **properties):
        with addon.ui._view3d_override(context):
            result = operator("EXEC_DEFAULT", **properties)
        assert result == {"FINISHED"}, (str(operator), properties, result)

    def info():
        return tuple(context.window_manager.xr_session_state.hand_menu_info(context))

    def pose():
        result = tuple(context.window_manager.xr_session_state.hand_menu_pose(context))
        assert len(result) == 9 and all(isfinite(value) for value in result)
        return result

    def wait_for(predicate, reason, steps=40):
        for _ in range(steps):
            yield
            if predicate():
                return
        raise AssertionError(reason)

    def open_menu(panel, width=0.48):
        call(bpy.ops.wm.xr_hand_menu, visible=True, panel=panel, width=width,
             controller=0, allow_simulated_input=True)
        yield from wait_for(lambda: info()[0] and info()[5] >= 2, "Hand UI did not render")
        assert info()[1] > 0 and info()[2] > 0 and pose()[7] > 0 and pose()[8] > 0

    def close_menu():
        call(bpy.ops.wm.xr_hand_menu, visible=False)
        # Native cancellation queues ESC; wait for popup-handler teardown too.
        yield
        yield
        yield from wait_for(lambda: not info()[0] and not info()[4], "Hand UI did not release capture")
        assert info()[3] == -1

    def ray():
        current = pose()
        rotation = Quaternion(current[3:7])
        return {"ray_origin": tuple(Vector(current[:3]) + rotation @ Vector((0, 0, 0.3))),
                "ray_direction": tuple(rotation @ Vector((0, 0, -1)))}

    def pointer(event_type, value):
        call(bpy.ops.wm.xr_editor_surface_event, target="MENU", type=event_type,
             value=value, pointer_id=0, use_ray=True, **ray())

    def capture(label, hand=True):
        path = capture_dir / f"{label}-{token}.png"
        assert not path.exists(), "Refusing to overwrite UI evidence"
        call(bpy.ops.wm.xr_editor_surface_capture, filepath=str(path), hand_menu=hand)
        yield from wait_for(lambda: path.is_file() and path.stat().st_size > 8,
                            f"Native UI capture did not arrive: {label}")
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "Invalid PNG capture"
        record = {"label": label, "path": str(path), "hand_menu": hand}
        if hand:
            record.update(_alpha_bounds(path))
        captures.append(record)
        return record

    addon.controllers.stop_router(context)
    try:
        for cls in (XRWB_PT_hand_test, XRWB_PT_hand_nested_test):
            assert not hasattr(bpy.types, cls.bl_idname), "Diagnostic panel already registered"
            bpy.utils.register_class(cls)
            registered.append(cls)
        context.preferences.edit.use_global_undo = True
        context.preferences.edit.undo_steps = max(undo_steps, 32)
        context.preferences.view.show_tooltips = True
        scene_check = runpy.run_path(str(Path(__file__).with_name("xr_scene_trigger_features_test.py")))
        yield from scene_check["run"](context, addon)
        call(bpy.ops.ed.undo_push, message="XR Hand Menu Empty Baseline")

        # No desktop surface is required to draw or operate the hand host.
        call(bpy.ops.wm.xr_editor_surface, visible=False)
        yield from open_menu("XRWB_PT_hand_test")
        assert not context.window_manager.xr_session_state.editor_surface_info(context)[0]
        tooltip_before = yield from capture("hand-only-add-button")
        pointer("MOUSEMOVE", "NOTHING")
        for _ in range(12):
            yield
            # Match the continuous controller-hover router. Native popovers only
            # enable help after a follow-up motion event on the highlighted button.
            pointer("MOUSEMOVE", "NOTHING")
        tooltip_after = yield from capture("hand-button-tooltip")
        assert tooltip_after["sampled_foreground"] > tooltip_before["sampled_foreground"] + 20, (
            "Native button help did not appear in the hand layer"
        )
        pointer("LEFTMOUSE", "PRESS")
        assert info()[3] == 0 and info()[4], "Native MENU pointer did not capture press"
        yield
        assert not context.scene.objects, "Button created an object before its release"
        pointer("LEFTMOUSE", "RELEASE")
        yield from wait_for(lambda: len(context.scene.objects) == 1, "Hand button did not create a cube")
        assert not info()[4] and info()[3] == -1
        obj = context.view_layer.objects.active
        assert obj and obj.type == "MESH" and len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
        assert obj.select_get()
        yield from close_menu()
        call(bpy.ops.xrwb.history_undo)
        yield from wait_for(lambda: not context.scene.objects, "Hand-created cube did not undo")
        print("XRWB_HAND_MENU_CLICK_PASS created=1 undo=1 desktop_hidden=1 input=synthetic", flush=True)

        # Width calibration preserves geometry by scaling its pixel-to-world ratio.
        yield from open_menu("XRWB_PT_hand_test", width=0.30)
        width_small = pose()[7]
        yield from close_menu()
        yield from open_menu("XRWB_PT_hand_test", width=0.60)
        assert abs(pose()[7] / width_small - 2.0) < 0.02, "Physical menu width did not scale"
        yield from close_menu()

        # A native child menu must expand only the isolated hand layer.
        call(bpy.ops.wm.xr_editor_surface, visible=True, recall=False, separated=False)
        yield from open_menu("XRWB_PT_hand_nested_test")
        nested_before = yield from capture("hand-nested-before")
        pointer("MOUSEMOVE", "NOTHING")
        yield
        pointer("LEFTMOUSE", "PRESS")
        yield
        pointer("LEFTMOUSE", "RELEASE")
        for _ in range(5):
            yield
        assert info()[0] and not info()[4]
        nested_after = yield from capture("hand-nested-after")
        before, after = nested_before["alpha_bounds"], nested_after["alpha_bounds"]
        assert (after[0] < before[0] - 8 or after[1] < before[1] - 8 or
                after[2] > before[2] + 8 or after[3] > before[3] + 8), "Native submenu did not expand hand UI"
        yield from capture("desktop-with-hand-submenu", hand=False)
        yield from close_menu()
        assert not context.scene.objects, "Submenu navigation unexpectedly created an object"

        # Capture real command pages and the independent editor for visual review.
        for page, _label, _description in addon.ui.COMMAND_PAGES:
            context.window_manager.xrwb_settings.command_page = page
            yield from open_menu("XRWB_PT_command_deck")
            yield from capture(f"hand-command-{page.lower()}")
            yield from capture(f"desktop-with-hand-{page.lower()}", hand=False)
            if page == "CREATE":
                eye_path = capture_dir / f"hand-in-xr-eye-{token}.png"
                assert not eye_path.exists()
                previous_eye_path = os.environ.get("XRWB_EDITOR_EYE_CAPTURE_PATH")
                try:
                    os.environ["XRWB_EDITOR_EYE_CAPTURE_PATH"] = str(eye_path)
                    yield from wait_for(lambda: eye_path.is_file() and eye_path.stat().st_size > 8,
                                        "Composed XR eye did not arrive")
                    captures.append({"label": "hand-in-xr-eye", "path": str(eye_path), "xr_eye": True})
                finally:
                    if previous_eye_path is None:
                        os.environ.pop("XRWB_EDITOR_EYE_CAPTURE_PATH", None)
                    else:
                        os.environ["XRWB_EDITOR_EYE_CAPTURE_PATH"] = previous_eye_path
            yield from close_menu()
        command_check = runpy.run_path(str(Path(__file__).with_name("xr_command_controls_test.py")))
        yield from command_check["run"](context, addon, capture_dir)
        assert not context.scene.objects and addon.runtime.session_running(context)
        assert not info()[0] and not info()[4] and info()[3] == -1
        result = {"hand_click_create": 1, "hand_click_undo": 1, "desktop_hidden": 1,
                  "menu_pages": len(addon.ui.COMMAND_PAGES), "nested_menu_growth": 1, "width_calibration": 1, "tooltip": 1,
                  "captures": captures, "desktop_popup_visual_review_required": True,
                  "physical_controller_verified": False}
    finally:
        if addon.runtime.session_running(context):
            # Explicit native teardown before unregistering a panel still in use.
            yield from close_menu()
            call(bpy.ops.wm.xr_editor_surface, visible=bool(original_editor[0]), recall=False)
            if original_editor[0] and original_pose[7] > 0:
                call(bpy.ops.wm.xr_editor_surface_transform, position=original_pose[:3],
                     rotation=original_pose[3:7], width=original_pose[7])
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        context.preferences.edit.use_global_undo = undo_enabled
        context.preferences.edit.undo_steps = undo_steps
        context.preferences.view.show_tooltips = show_tooltips
        context.window_manager.xrwb_settings.command_page = original_page
        if addon.runtime.session_running(context):
            addon.controllers.start_router(context)
    print(f"XRWB_HAND_MENU_FEATURES_PASS created=1 undo=1 desktop_hidden=1 menu_pages={len(addon.ui.COMMAND_PAGES)} "
          "nested_menu_growth=1 width_calibration=1 tooltip=1 input=synthetic physical_controller_verified=0", flush=True)
    return result
