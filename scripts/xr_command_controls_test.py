"""Ray-click the real command deck, including same-open page replacement.

Called by the guarded, owned camera-off hand-menu test. No physical-input claim.
Pixel targets derive from the captured native panel and its existing layout;
they are not hidden operator calls or direct property changes.
"""
from array import array
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4

import bpy
from mathutils import Quaternion, Vector


def _read(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = array("f", [0.0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        xs, ys = [], []
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                if pixels[(y * width + x) * 4 + 3] > 0.9:
                    xs.append(x)
                    ys.append(y)
        assert xs, "Native command deck has no opaque pixels"
        return width, height, pixels, (min(xs), min(ys), max(xs), max(ys))
    finally:
        bpy.data.images.remove(image)


def run(context, addon, capture_dir):
    assert os.environ.get("XRWB_TASK_TEST_ALLOW") == "1"
    assert os.environ.get("XRWB_HAND_MENU_SIMULATOR") == "1" or os.environ.get("XRWB_STEAMVR_SMOKE") == "1"
    assert not bpy.app.background and not bpy.data.filepath and not context.scene.objects
    assert addon.controllers._active_gesture is None and not addon.editing.active_transaction()
    assert addon.runtime.session_running(context)
    assert not context.window_manager.xr_session_settings.show_passthrough
    assert not any(os.environ.get(name) == "1" for name in (
        "XRWB_INDEX_CAMERA_CONSENT", "XRWB_INDEX_CAMERA_OVERLAY", "XRWB_INDEX_CAMERA_GESTURE"))
    root = Path(__file__).resolve().parents[1]
    directory = Path(capture_dir).resolve()
    assert directory.is_relative_to(root / "reports")
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex[:8]
    original_page = context.window_manager.xrwb_settings.command_page
    original_tool = context.window_manager.xrwb_settings.edit_tool
    original_desktop = bool(context.window_manager.xr_session_state.editor_surface_info(context)[0])
    original_tooltips = context.preferences.view.show_tooltips
    cursor_before = context.scene.cursor.location.copy()
    captures = []
    clicked_tools = []
    anchor = None
    last_image = None

    def info():
        return tuple(context.window_manager.xr_session_state.hand_menu_info(context))

    def call(operator, **kwargs):
        with addon.ui._view3d_override(context):
            result = operator("EXEC_DEFAULT", **kwargs)
        assert result == {"FINISHED"}, (str(operator), kwargs, result)

    def wait(predicate, reason, limit=30):
        for _ in range(limit):
            yield
            if predicate():
                return
        raise AssertionError(reason)

    def capture(label):
        nonlocal last_image, anchor
        path = directory / f"command-click-{label}-{token}.png"
        call(bpy.ops.wm.xr_editor_surface_capture, hand_menu=True, filepath=str(path))
        yield from wait(lambda: path.is_file() and path.stat().st_size > 8, "UI capture missing")
        last_image = _read(path)
        _width, _height, _pixels, bounds = last_image
        if anchor is None:
            # Native popup regions have symmetric horizontal shadow padding;
            # vertical padding is 12px below and 10px above at standard scale.
            scale = (bounds[2] - bounds[0]) / 500.0
            anchor = ((bounds[0] + bounds[2]) * 0.5, (bounds[1] + bounds[3]) * 0.5 - scale)
        captures.append(str(path))
        return last_image

    def body_hash(image):
        width, _height, pixels, bounds = image
        scale = (bounds[2] - bounds[0]) / 500.0
        top = int(bounds[3] - 157 * scale)
        data = bytearray()
        for y in range(max(0, bounds[1]), max(0, top), 3):
            for x in range(bounds[0], bounds[2], 3):
                data.extend(int(max(0, min(1, pixels[(y * width + x) * 4 + c])) * 255) for c in range(4))
        return sha256(data).hexdigest()

    def pointer(x, y, event_type="MOUSEMOVE", value="NOTHING"):
        state = context.window_manager.xr_session_state
        pose = tuple(state.hand_menu_pose(context))
        width, height = info()[1:3]
        rotation = Quaternion(pose[3:7])
        local = Vector(((x - anchor[0]) * pose[7] / width,
                        (y - anchor[1]) * pose[8] / height, 0.3))
        call(bpy.ops.wm.xr_editor_surface_event, target="MENU", type=event_type,
             value=value, pointer_id=0, use_ray=True,
             ray_origin=tuple(Vector(pose[:3]) + rotation @ local),
             ray_direction=tuple(rotation @ Vector((0, 0, -1))))

    def click(x, y):
        pointer(x, y)
        yield
        pointer(x, y, "LEFTMOUSE", "PRESS")
        yield
        pointer(x, y, "LEFTMOUSE", "RELEASE")
        yield
        yield
        assert info()[0] and not info()[4], "Command click closed or stranded the one hand surface"

    def tab(page):
        index = [item[0] for item in addon.ui.COMMAND_PAGES].index(page)
        return point(index // 2, 113 + (index % 2) * 25, columns=5)

    def point(column, top_offset, columns=3):
        bounds = last_image[3]
        scale = (bounds[2] - bounds[0]) / 500.0
        return bounds[0] + (10 + 480 * (column + 0.5) / columns) * scale, bounds[3] - top_offset * scale

    def scene_signature():
        return tuple((obj.name, tuple(tuple(row) for row in obj.matrix_world),
                      tuple(tuple(v.co) for v in obj.data.vertices) if obj.type == "MESH" else (),
                      tuple((modifier.name, modifier.type) for modifier in obj.modifiers))
                     for obj in context.scene.objects)

    def choose_tool(tool, column, top_offset):
        assert context.window_manager.xrwb_settings.edit_tool != tool, "Tool click must change state"
        before = scene_signature()
        yield from click(*point(column, top_offset))
        yield from wait(lambda: context.window_manager.xrwb_settings.edit_tool == tool,
                        f"Real command button did not choose {tool}")
        assert scene_signature() == before, "Choosing a tool unexpectedly changed the scene"
        assert addon.controllers._active_gesture is None and not addon.editing.active_transaction()
        clicked_tools.append(tool)
        print(f"XR_COMMAND_TOOL_CLICK tool={tool} native_grid=1", flush=True)

    try:
        # Tooltip growth is tested separately; it must not contaminate the root
        # bounds used to aim at this fixed native layout.
        context.preferences.view.show_tooltips = False
        context.window_manager.xrwb_settings.command_page = "CREATE"
        call(bpy.ops.wm.xr_hand_menu, visible=True, panel="XRWB_PT_command_deck",
             width=0.48, controller=1, allow_simulated_input=True)
        yield from wait(lambda: info()[0] and info()[5] >= 2, "Command menu failed to compose")
        yield from capture("initial")
        prior_hash = body_hash(last_image)
        prior_frames = info()[5]
        # The 5 x 2 tab grid is column-major: Create/Edit, Sculpt/Materials,
        # Animate/Menus, Desktop/Travel, Settings/Guide.
        for page in ("EDIT", "SCULPT", "PAINT", "ANIMATE", "MENUS", "WORKSPACE", "MOVE", "CONTROLS", "GUIDE",
                     "CREATE"):
            yield from click(*tab(page))
            yield from wait(lambda: context.window_manager.xrwb_settings.command_page == page,
                            f"Ray click did not activate {page}")
            assert info()[5] > prior_frames, "Page click replaced the surface instead of refreshing it"
            yield from capture(page.lower())
            current_hash = body_hash(last_image)
            assert current_hash != prior_hash, f"{page} changed state but left stale visible content"
            prior_hash, prior_frames = current_hash, info()[5]
            print(f"XR_COMMAND_PAGE_CLICK page={page} same_open=1 refreshed=1", flush=True)

        for visible in (not original_desktop, original_desktop):
            yield from click(*point(2, 22))
            yield from wait(lambda: bool(context.window_manager.xr_session_state.editor_surface_info(context)[0]) == visible,
                            "Hand button did not toggle the desktop")
            yield from capture("desktop-open" if visible else "desktop-closed")

        # Cutters require a selected editable target. Create it through the real
        # rendered Cube button before arming any tool; metadata choices must not
        # add an undo step or alter this fixture.
        yield from click(*point(0, 183))
        yield from wait(lambda: len(context.scene.objects) == 1, "Real Create grid Cube did not add an object")
        cube = context.view_layer.objects.active
        assert cube and cube.type == "MESH" and len(cube.data.vertices) == 8 and len(cube.data.polygons) == 6
        assert cube.select_get() and not cube.modifiers
        yield from capture("cube-after")

        yield from click(*tab("EDIT"))
        yield from wait(lambda: context.window_manager.xrwb_settings.command_page == "EDIT", "Edit tab failed")
        yield from capture("tools-before")
        tools = [item[0] for item in addon.ui.EDIT_TOOLS]
        # Never start with the already-selected tool: a missed click must fail.
        current_tool = context.window_manager.xrwb_settings.edit_tool
        start = (tools.index(current_tool) + 1) % len(tools) if current_tool in tools else 0
        for index in [*range(start, len(tools)), *range(start)]:
            yield from choose_tool(tools[index], index // 2, 183 + (index % 2) * 25)
        # Edge selection/bevel have a dedicated two-column row; Object Bevel is
        # the separate full-width modifier action below it.
        assert len(addon.ui.EDGE_TOOLS) == 2
        for column, (tool, _label, _description) in enumerate(addon.ui.EDGE_TOOLS):
            before = scene_signature()
            yield from click(*point(column, 239, columns=2))
            yield from wait(lambda: context.window_manager.xrwb_settings.edit_tool == tool,
                            f"Real edge button did not choose {tool}")
            assert scene_signature() == before
            clicked_tools.append(tool)
        yield from capture("tools-after")

        yield from click(*tab("CREATE"))
        yield from wait(lambda: context.window_manager.xrwb_settings.command_page == "CREATE", "Create tab failed")
        yield from capture("cube-before")
        for tool_group, top_offset in ((addon.ui.DRAW_TOOLS, 311), (addon.ui.CUT_TOOLS, 431)):
            assert len(tool_group) == 3
            for column, (tool, _label, _description) in enumerate(tool_group):
                yield from choose_tool(tool, column, top_offset)
        # Physical-tool pages; offsets measured from 150% renders of each page.
        for page, group, top_offset, columns in (("SCULPT", addon.ui.SCULPT_TOOLS, 212, 4),
                                                 ("PAINT", addon.ui.PAINT_TOOLS, 327, 1),
                                                 ("ANIMATE", addon.ui.ANIMATE_TOOLS, 271, 1)):
            yield from click(*tab(page))
            yield from wait(lambda: context.window_manager.xrwb_settings.command_page == page, f"{page} tab failed")
            yield from capture(f"{page.lower()}-tools")
            for column, (tool, _label, _description) in enumerate(group):
                before = scene_signature()
                yield from click(*point(column, top_offset, columns=columns))
                yield from wait(lambda: context.window_manager.xrwb_settings.edit_tool == tool,
                                f"Real {page} button did not choose {tool}")
                assert scene_signature() == before
                clicked_tools.append(tool)
        # Place Shape is chosen from the native Add menu (Index right B / Quick),
        # not from a command-deck button.
        deck_tools = {item[0] for item in addon.ui.ALL_TOOLS} - {item[0] for item in addon.ui.QUICK_TOOLS}
        assert len(clicked_tools) == len(deck_tools) and set(clicked_tools) == deck_tools
        yield from capture("draw-cut-tools-after")
        yield from click(*tab("EDIT"))
        yield from wait(lambda: context.window_manager.xrwb_settings.command_page == "EDIT", "Bevel Edit tab failed")
        yield from capture("bevel-object-before")
        yield from click(*point(0, 270, columns=1))
        yield from wait(lambda: len(cube.modifiers) == 1 and cube.modifiers[0].type == "BEVEL",
                        "Real Bevel Object button did not add a native modifier")
        context.view_layer.update()
        assert len(cube.evaluated_get(context.evaluated_depsgraph_get()).data.vertices) > 8, \
            "Native Bevel Object modifier did not produce beveled geometry"
        yield from capture("bevel-object-after")
        call(bpy.ops.wm.xr_hand_menu, visible=False)
        yield from wait(lambda: not info()[0] and not info()[4], "Cube test menu did not close")
        call(bpy.ops.xrwb.history_undo)
        yield from wait(lambda: len(context.scene.objects) == 1 and context.object is not None
                        and not context.object.modifiers, "Bevel Object was not one undo retaining the cube")
        assert context.object.type == "MESH" and len(context.object.data.vertices) == 8
        print("XR_COMMAND_BEVEL_CLICK modifier=1 evaluated_geometry=1 one_undo=1 native_button=1", flush=True)
        call(bpy.ops.xrwb.history_undo)
        yield from wait(lambda: not context.scene.objects, "Real Create grid Cube was not one undo")
        print("XR_COMMAND_CREATE_CLICK cube=1 one_undo=1 native_grid=1", flush=True)
        # The same-open page and tool checks above are complete. Reopen after
        # undo so the next native origin checks do not retain invalid RNA UI.
        anchor = None
        call(bpy.ops.mesh.primitive_cube_add, location=(0, 0, 0))
        for vertex in context.object.data.vertices:
            vertex.co.x += 1
        context.object.data.update()
        context.scene.cursor.location = (3, 4, 5)
        call(bpy.ops.ed.undo_push, message="XR native origin fixture")
        call(bpy.ops.wm.xr_hand_menu, visible=True, panel="XRWB_PT_command_deck",
             width=0.48, controller=1, allow_simulated_input=True)
        yield from wait(lambda: info()[0] and info()[5] >= 2, "Origin menu failed to compose")
        yield from capture("origin-menu")
        if context.window_manager.xrwb_settings.command_page != "EDIT":
            yield from click(*tab("EDIT"))
            yield from wait(lambda: context.window_manager.xrwb_settings.command_page == "EDIT", "Origin Edit tab failed")
        yield from capture("origin-before")
        # Geometry fixture is selected and editable: aim in the origin row,
        # measured from the root's top so window clipping cannot target Undo.
        yield from click(*point(0, 548, columns=2))
        yield from wait(lambda: (context.object.location - Vector((1, 0, 0))).length < 1e-5,
                        "Origin to Geometry did not execute from its native hand button")
        yield from capture("origin-geometry")
        yield from click(*point(1, 548, columns=2))
        yield from wait(lambda: (context.object.location - Vector((3, 4, 5))).length < 1e-5,
                        "Origin to 3D Cursor did not execute from its native hand button")
        yield from capture("origin-cursor")
        call(bpy.ops.wm.xr_hand_menu, visible=False)
        yield from wait(lambda: not info()[0] and not info()[4], "Origin test menu did not close")
        call(bpy.ops.ed.undo)
        assert (context.object.location - Vector((1, 0, 0))).length < 1e-5, "Cursor origin was not one undo"
        call(bpy.ops.ed.undo)
        assert context.object.location.length < 1e-5, "Geometry origin was not one undo"
        obj = context.object
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        assert not context.scene.objects
    finally:
        if addon.runtime.session_running(context):
            call(bpy.ops.wm.xr_hand_menu, visible=False)
            yield
            yield
            call(bpy.ops.wm.xr_editor_surface, visible=original_desktop, recall=False)
        context.scene.cursor.location = cursor_before
        context.preferences.view.show_tooltips = original_tooltips
        context.window_manager.xrwb_settings.command_page = original_page
        context.window_manager.xrwb_settings.edit_tool = original_tool
    print(f"XR_COMMAND_CONTROLS_PASS clicked_pages=10 clicked_tools={len(clicked_tools)} edge_tools=2 "
          "draw_tools=3 cut_tools=3 sculpt_tools=4 spray_tools=1 scrub_tools=1 "
          "create_grid_cube=1 create_grid_undo=1 bevel_object=1 bevel_object_undo=1 "
          "same_open=1 desktop_toggle=2 native_origins=2 undo=2 hardware=0", flush=True)
    return {"same_open_page_clicks": 10, "desktop_button_toggles": 2, "native_origin_clicks": 2,
            "edit_tool_clicks": 6, "edge_tool_clicks": 2, "draw_tool_clicks": 3, "cut_tool_clicks": 3,
            "sculpt_tool_clicks": 4, "spray_tool_clicks": 1, "scrub_tool_clicks": 1,
            "all_tool_clicks": len(clicked_tools), "create_grid_cube": 1, "create_grid_undo": 1,
            "bevel_object_clicks": 1, "bevel_object_undos": 1,
            "origin_undos": 2, "captures": captures, "physical_controller_verified": False}
