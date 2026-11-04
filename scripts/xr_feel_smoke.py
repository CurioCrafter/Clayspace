"""Factory-profile check of Combine Booleans, VR prefs, the Guide and ray-ring data.

Real Blender operators and undo; no XR session or headset. Run in a separate
background process with BLENDER_USER_CONFIG set to an empty temporary folder.
"""
import os
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import (  # noqa: E402
    boolean_tools, controllers, draw_editing, onboarding, physical_tools, quick_actions, ray_feedback, ui,
)
from xr_workbench.interaction import CaptureOwner, Decision, InputProvenance, InputSample, Pose  # noqa: E402
from mathutils import Quaternion  # noqa: E402


def volume(obj):
    import bmesh
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh())
        return abs(bm.calc_volume())
    finally:
        bm.free()


def check_booleans():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
    target = bpy.context.object
    target.name = "Target"
    bpy.ops.mesh.primitive_cube_add(size=2, location=(1, 0, 0))
    cutter = bpy.context.object
    cutter.name = "Cutter"
    assert bpy.ops.ed.undo_push(message="boolean base") == {"FINISHED"}
    # Target must be active and selected last.
    cutter.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    for operation, expected in (("DIFFERENCE", 4.0), ("UNION", 12.0), ("INTERSECT", 4.0)):
        assert bpy.ops.xrwb.boolean_selected(operation=operation) == {"FINISHED"}
        target = bpy.data.objects["Target"]
        assert abs(volume(target) - expected) < 1e-4, (operation, volume(target))
        cutter = bpy.data.objects["Cutter"]
        assert cutter.display_type == "WIRE" and cutter.hide_render
        assert cutter[boolean_tools.CUTTER_KEY] == "Target"
        assert bpy.ops.ed.undo() == {"FINISHED"}
        target, cutter = bpy.data.objects["Target"], bpy.data.objects["Cutter"]
        assert not target.modifiers and cutter.display_type != "WIRE", "Combine was not one undo"
        cutter.select_set(True)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
    # Cutters stay live: moving one changes the result until Apply bakes it.
    assert bpy.ops.xrwb.boolean_selected(operation="DIFFERENCE") == {"FINISHED"}
    target, cutter = bpy.data.objects["Target"], bpy.data.objects["Cutter"]
    cutter.location.x = 1.5
    bpy.context.view_layer.update()
    assert abs(volume(target) - 6.0) < 1e-4
    assert bpy.ops.xrwb.apply_booleans() == {"FINISHED"}
    target, cutter = bpy.data.objects["Target"], bpy.data.objects["Cutter"]
    assert not target.modifiers and abs(volume(target) - 6.0) < 1e-4
    assert "Cutter" in bpy.data.objects and cutter.hide_get(), "Apply must hide, never delete, cutters"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert len(bpy.data.objects["Target"].modifiers) == 1
    # Guards: no cutters, or a non-mesh active object, are refused.
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.data.objects["Target"].select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects["Target"]
    assert bpy.ops.xrwb.boolean_selected(operation="UNION") == {"CANCELLED"}
    print("XR_FEEL_BOOLEANS combine=3 exact_volumes=1 live_cutter=1 apply=1 cutter_kept=1 one_undo=1 guards=1",
          flush=True)


def check_prefs_and_guide():
    settings = bpy.context.window_manager.xrwb_settings
    assert not onboarding.prefs_path().exists()
    assert abs(settings.haptic_strength - onboarding.DEFAULTS["haptic_strength"]) < 1e-6
    assert settings.ray_markers and not settings.desktop_at_start
    settings.haptic_strength = 0.25
    settings.desktop_at_start = True
    onboarding.reset_cache()  # A new launch rereads the file.
    assert abs(settings.haptic_strength - 0.25) < 1e-6 and settings.desktop_at_start
    settings.desktop_at_start = False
    for profile in ("INDEX", "QUEST", "AUTO"):
        steps = onboarding.guide_steps(profile)
        assert len(steps) == 9 and all(len(line) <= 70 for _t, lines, _a in steps for line in lines)
        text = "\n".join(onboarding.controls_lines(profile))
        assert ("Quest" in text) == (profile != "INDEX")
    settings.controller_profile = "INDEX"
    assert bpy.ops.xrwb.guide(action="OPEN") == {"FINISHED"} and settings.command_page == "GUIDE"
    for _ in range(10):
        bpy.ops.xrwb.guide(action="NEXT")
    assert settings.guide_step == 8
    bpy.ops.xrwb.guide(action="BACK")
    assert settings.guide_step == 7
    assert not onboarding.prefs()["guide_done"]
    assert bpy.ops.xrwb.guide(action="FINISH") == {"FINISHED"} and settings.command_page == "CREATE"
    onboarding.reset_cache()
    assert onboarding.prefs()["guide_done"]
    print("XR_FEEL_PREFS persisted=1 guide_steps=9 guide_navigation=1 guide_done=1", flush=True)


def check_ray_and_stick():
    settings = bpy.context.window_manager.xrwb_settings
    points = ray_feedback.ring((0, 0, 1), (0, 0, 1), 0.05)
    assert len(points) == ray_feedback.SEGMENTS * 2
    assert all(abs((p - Vector((0, 0, 1))).length - 0.05) < 2e-3 for p in points)
    assert all(tool in ray_feedback.TOOL_COLOURS for tool, _l, _d in ui.ALL_TOOLS)
    settings.sculpt_radius_m = 0.04
    assert abs(ray_feedback.brush_radius(settings, "sculpt_clay", 0.25) - 0.01) < 1e-7
    gold = bpy.data.materials.new("Paint Gold")
    gold.diffuse_color = (1.0, 0.7, 0.2, 1.0)
    settings.spray_material = gold.name
    assert ray_feedback.tool_colour(settings, "spray_material") == tuple(gold.diffuse_color)[:3]
    # Right stick resizes brushes only for brush tools.
    settings.edit_tool = "sculpt_clay"
    assert controllers._adjust_brush(bpy.context, 1)[0] and abs(settings.sculpt_radius_m - 0.048) < 1e-6
    settings.edit_tool = "select"
    assert not controllers._adjust_brush(bpy.context, 1)[0]
    # Haptics never raise without a session.
    assert controllers.pulse(bpy.context, controllers.RIGHT_HAND, "commit") is False
    print("XR_FEEL_RAY ring=1 tool_colours=21 brush_scale=1 spray_colour=1 stick_brush=1 haptics_safe=1", flush=True)


class Layout:
    """Records menu entries and validates operator/property names; no pixels."""
    def __init__(self, records):
        self.records = records
        self.enabled = True

    def row(self, **_kwargs):
        return Layout(self.records)

    def label(self, **kwargs):
        self.records.append(("label", kwargs.get("text", "")))

    def separator(self):
        pass

    def menu(self, name, **_kwargs):
        assert hasattr(bpy.types, name), name
        self.records.append(("menu", name))

    def prop(self, owner, name, **_kwargs):
        assert name in owner.bl_rna.properties, name
        self.records.append(("prop", name))

    def operator(self, name, **kwargs):
        family, action = name.split(".")
        assert getattr(getattr(bpy.ops, family), action).get_rna_type()
        self.records.append(("operator", name, kwargs.get("text", "")))
        return type("Props", (), {})()


def check_quick():
    settings = bpy.context.window_manager.xrwb_settings
    for workflow, _label, _description in xr_workbench.viewer_scale.WORKFLOWS:
        settings.workflow = workflow
        records = []
        quick_actions.XRWB_MT_quick.draw(type("Menu", (), {"layout": Layout(records)})(), bpy.context)
        operators = [item[1] for item in records if item[0] == "operator"]
        assert {"xrwb.quick_duplicate", "xrwb.quick_delete"} <= set(operators)
        drill = {item[2] for item in records if item[0] == "operator" and item[1] == "xrwb.open_hand_menu"}
        assert {"Blender Menus  ›", "Add Shape  ›", "Arrange  ›"} <= drill, drill
        tools = {tool for tool, _t, _i in quick_actions.WORKFLOW_TOOLS[workflow]}
        assert tools <= {tool for tool, _l, _d in ui.ALL_TOOLS}
        assert (sum(item[1] == "xrwb.workflow" for item in records if item[0] == "operator")
                == len(xr_workbench.viewer_scale.WORKFLOWS) - 1)
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(5, 5, 5))
    cube = bpy.context.object
    cube.name = "Quick"
    assert bpy.ops.ed.undo_push(message="quick base") == {"FINISHED"}
    assert bpy.ops.xrwb.quick_duplicate() == {"FINISHED"}
    copy = bpy.context.active_object
    assert copy.name != "Quick" and copy.data != bpy.data.objects["Quick"].data and copy.select_get()
    assert abs((copy.location - bpy.data.objects["Quick"].location).length - 0.1) < 1e-5
    assert bpy.ops.xrwb.quick_bring_here() == {"FINISHED"}
    assert (bpy.context.active_object.location - Vector((5, 5, 5))).length > 1.0
    assert bpy.ops.xrwb.quick_delete() == {"FINISHED"} and len(bpy.data.objects) == 1
    for _ in range(3):
        assert bpy.ops.ed.undo() == {"FINISHED"}
    assert set(bpy.data.objects.keys()) == {"Quick"}, "Quick actions were not one undo each"
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    assert bpy.ops.xrwb.quick_delete() == {"CANCELLED"}
    print("XR_FEEL_QUICK menus=4 duplicate=1 bring_here=1 delete=1 one_undo_each=1 guards=1", flush=True)


def check_place_and_previews():
    settings = bpy.context.window_manager.xrwb_settings
    owner = CaptureOwner(InputProvenance.SIMULATOR, "feel-place", 0)
    down = Quaternion()  # Aims along -Z.
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "Floor"
    assert bpy.ops.ed.undo_push(message="place base") == {"FINISHED"}
    # Unit outlines: 12 cube edges, closed rings for round shapes.
    assert len(ray_feedback.shape_lines("CUBE")) == 24 and len(ray_feedback.shape_lines("PLANE")) == 8
    for kind in physical_tools.PLACE_SHAPES:
        assert ray_feedback.shape_lines(kind) and len(ray_feedback.shape_lines(kind)) % 2 == 0
    # The Add menu lists every shape plus hand drawing, with valid operators.
    records = []
    quick_actions.XRWB_MT_add.draw(type("Menu", (), {"layout": Layout(records)})(), bpy.context)
    picks = [item for item in records if item[0] == "operator" and item[1] == "xrwb.pick_place"]
    assert len(picks) == len(physical_tools.PLACE_SHAPES)
    # Ghost before pressing: sits on the floor where the ray lands.
    settings.place_kind = "CUBE"
    controllers._pointer_hand = controllers.RIGHT_HAND
    pose = (Vector((1.0, 2.0, 3.0)), down, Vector((0.0, 0.0, -1.0)))
    ghosts = ray_feedback._preview_shapes(bpy.context, settings, "place_object", 1.0,
                                          {controllers.RIGHT_HAND: pose})
    assert len(ghosts) == 1
    zs = [p[2] for p in ghosts[0][0]]
    assert 0 < min(zs) < 1e-3 and abs(max(zs) - min(zs) - 0.2) < 1e-5, (min(zs), max(zs))
    draw_ghost = ray_feedback._preview_shapes(bpy.context, settings, "draw_cube", 1.0,
                                              {controllers.RIGHT_HAND: pose})
    centre = sum((Vector(p) for p in draw_ghost[0][0]), Vector()) / len(draw_ghost[0][0])
    assert (centre - Vector((1.0, 2.0, 2.8))).length < 1e-5, "Draw ghost must sit at the draw start"
    # Place: trigger drops the shape on the floor, hold slides it, release keeps it (one undo).
    assert bpy.ops.xrwb.pick_place(kind="CUBE") == {"CANCELLED"}, "Tools need a running XR session"
    running = xr_workbench.runtime.session_running
    xr_workbench.runtime.session_running = lambda _context: True  # Stub, as in the UI smoke.
    try:
        assert bpy.ops.xrwb.pick_place(kind="CUBE") == {"FINISHED"} and settings.edit_tool == "place_object"
    finally:
        xr_workbench.runtime.session_running = running
    transaction = physical_tools.begin(bpy.context, (1, 2, 3), down, tool="place_object", owner=owner)
    cube = transaction.obj
    bottom = min((cube.matrix_world @ Vector(c)).z for c in cube.bound_box)
    assert 0 < bottom < 1e-3 and abs(cube.matrix_world.translation.x - 1) < 1e-5
    # Sliding across its own resting spot must keep it on the floor (no coplanar flicker).
    transaction.update(bpy.context, InputSample(owner, 0, Pose((1.0, 2.0, 3.0), tuple(down)), 4.0))
    assert 0 < min((cube.matrix_world @ Vector(c)).z for c in cube.bound_box) < 1e-3, "Place flickered"
    transaction.update(bpy.context, InputSample(owner, 0, Pose((3.0, -1.0, 3.0), tuple(down)), 5.0))
    assert abs(cube.matrix_world.translation.x - 3) < 1e-5 and abs(cube.matrix_world.translation.y + 1) < 1e-5
    name, mesh_name = cube.name, cube.data.name
    transaction.cancel(bpy.context, owner)
    assert name not in bpy.data.objects and mesh_name not in bpy.data.meshes
    transaction = physical_tools.begin(bpy.context, (0, 0, 3), down, tool="place_object", owner=owner)
    name = transaction.obj.name
    assert transaction.commit(bpy.context, owner).decision is Decision.APPLY
    assert name in bpy.data.objects and bpy.context.active_object.name == name
    assert bpy.ops.ed.undo() == {"FINISHED"} and name not in bpy.data.objects
    # Every place shape builds a real mesh of the stated size.
    for kind, (_label, _icon, size) in physical_tools.PLACE_SHAPES.items():
        mesh = physical_tools.build_shape_mesh(kind, size, "probe")
        xs = [v.co.x for v in mesh.vertices]
        assert abs((max(xs) - min(xs)) - size[0]) < 1e-4, (kind, max(xs) - min(xs))
        bpy.data.meshes.remove(mesh)
    # Drawing preview is the solid shape (cutters stay wire) with a live outline.
    settings.edit_tool = "draw_cube"
    drawing = draw_editing.begin(bpy.context, (0, 0, 3), down, tool="draw_cube", owner=owner)
    drawing.update(bpy.context, InputSample(owner, 0, Pose((0.3, 0.2, 2.9), tuple(down)), 6.0))
    assert drawing.obj.display_type == "TEXTURED"
    previous = controllers._active_gesture
    controllers._active_gesture = controllers._ActiveGesture("move", "pointer", controllers.RIGHT_HAND,
                                                             owner, drawing)
    try:
        outline = ray_feedback._preview_shapes(bpy.context, settings, "draw_cube", 1.0, {})
    finally:
        controllers._active_gesture = previous
    corners = {tuple(round(v, 4) for v in p) for p in outline[0][0]}
    expected = {tuple(round(v, 4) for v in drawing.obj.matrix_world @ Vector(c)) for c in drawing.obj.bound_box}
    assert corners == expected, "Live outline must match the drawn preview exactly"
    drawing.cancel(bpy.context, owner)
    print("XR_FEEL_PREVIEW outlines=6 add_menu=1 place_ghost_on_floor=1 draw_ghost_at_tip=1 place=1 slide=1 "
          "place_cancel=1 place_undo=1 shape_sizes=6 solid_draw_preview=1 live_outline=1", flush=True)


def check_measure_and_arrange():
    settings = bpy.context.window_manager.xrwb_settings
    owner = CaptureOwner(InputProvenance.SIMULATOR, "feel-measure", 0)
    down = Quaternion()
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
    # Tape measure: surface to surface, nothing in the scene changes, no undo step.
    before = tuple(bpy.data.objects.keys())
    measure = physical_tools.begin(bpy.context, (0, 0, 2), down, tool="measure", owner=owner)
    result = measure.update(bpy.context, InputSample(owner, 0, Pose((0.3, 0.4, 2.0), tuple(down)), 1.0))
    assert result.decision is Decision.APPLY and abs(measure.length - 0.5) < 1e-6, measure.length
    assert "50.0 cm" in result.message and measure.ticks == 5
    previous = controllers._active_gesture
    controllers._active_gesture = controllers._ActiveGesture("move", "pointer", controllers.RIGHT_HAND, owner, measure)
    try:
        lines = ray_feedback._preview_shapes(bpy.context, settings, "measure", 1.0, {})
    finally:
        controllers._active_gesture = previous
    start_point, end_point = (Vector(point) for point in lines[0][0][:2])
    assert start_point.length < 1e-6 and (end_point - Vector((0.3, 0.4, 0.0))).length < 1e-6
    commit = measure.commit(bpy.context, owner)
    assert commit.decision is Decision.APPLY and commit.message.startswith("Measured 0.500")
    assert tuple(bpy.data.objects.keys()) == before
    # Arrange: drop onto the floor, face the viewer upright, reset, apply scale; one undo each.
    bpy.ops.mesh.primitive_cube_add(size=1, location=(2, 0, 3), rotation=(0.4, 0.2, 0.9))
    cube = bpy.context.object
    cube.name = "Arrange"
    cube.scale = (2, 2, 2)
    bpy.context.view_layer.update()
    assert bpy.ops.ed.undo_push(message="arrange base") == {"FINISHED"}
    assert bpy.ops.xrwb.quick_arrange(action="DROP") == {"FINISHED"}
    cube = bpy.data.objects["Arrange"]
    assert abs(min((cube.matrix_world @ Vector(c)).z for c in cube.bound_box)) < 1e-4
    assert bpy.ops.xrwb.quick_arrange(action="FACE") == {"FINISHED"}
    cube = bpy.data.objects["Arrange"]
    front = cube.matrix_world.to_3x3() @ Vector((0, -1, 0))
    viewer = xr_workbench.viewer_scale.viewer_frame(bpy.context)[0] - cube.matrix_world.translation
    viewer.z = 0
    assert abs(front.normalized().z) < 1e-6 and front.normalized().dot(viewer.normalized()) > 0.9999
    assert bpy.ops.xrwb.quick_arrange(action="RESET") == {"FINISHED"}
    assert bpy.data.objects["Arrange"].matrix_world.to_quaternion().angle < 1e-5
    assert bpy.ops.xrwb.quick_arrange(action="APPLY_SCALE") == {"FINISHED"}
    cube = bpy.data.objects["Arrange"]
    assert tuple(round(v, 5) for v in cube.scale) == (1, 1, 1) and abs(cube.dimensions.x - 2) < 1e-5
    for _ in range(4):
        assert bpy.ops.ed.undo() == {"FINISHED"}
    cube = bpy.data.objects["Arrange"]
    assert abs(cube.location.z - 3) < 1e-5 and abs(cube.scale.x - 2) < 1e-5, "Arrange steps were not one undo each"
    print("XR_FEEL_MEASURE_ARRANGE measure=1 ticks=5 measure_line=1 no_scene_change=1 drop=1 face_me=1 "
          "reset=1 apply_scale=1 one_undo_each=1", flush=True)


def check_labels_and_menus():
    # Button labels: every profile, hand and state has short, specific lines.
    for profile in ("INDEX", "QUEST", "AUTO"):
        for hand in ("LEFT", "RIGHT"):
            for state in ("idle", "holding", "menu", "extruding", "edit"):
                lines = onboarding.button_hints(profile, hand, state, "Place Shape", "-> Surface")
                assert 2 <= len(lines) <= 4 and all(len(line) <= 36 for line in lines), (profile, hand, state, lines)
    assert "B: Add Shapes" in " ".join(onboarding.button_hints("INDEX", "RIGHT", "idle"))
    # Labels follow reassigned buttons and handedness.
    custom = onboarding.button_hints("INDEX", "RIGHT", "idle", slots={"RIGHT_A": "Undo"}, left_handed=True)
    assert "A: Undo" in " ".join(custom) and "Grip: world" in " ".join(custom)
    left = onboarding.button_hints("INDEX", "LEFT", "idle", left_handed=True)
    assert "Grip: pick up" in " ".join(left) and "Stick: turn" in " ".join(left)
    assert "B: Add Shapes" in " ".join(onboarding.button_hints("QUEST", "RIGHT", "idle")), "Quest B = Add, like Index"
    assert "Y: back" in onboarding.button_hints("QUEST", "LEFT", "menu")[0]
    assert "A: stamp a copy" in onboarding.button_hints("INDEX", "RIGHT", "holding")
    # Label plate faces the viewer and scales with your size.
    rotation = Quaternion((0, 0, 1), 0.7)
    matrix = ray_feedback.hint_matrix((0, 0, 0), tuple(rotation), 1, 2.0)
    normal = (matrix.to_3x3() @ Vector((0, 0, 1))).normalized()
    assert (normal - rotation @ Vector((0, 0, 1))).length < 1e-6
    assert abs(matrix.to_scale()[0] - ray_feedback.HINT_LINE_M * 2.0 / ray_feedback.HINT_FONT_PX) < 1e-9
    # Blender Menus: every entry resolves to a real menu for the current mode.
    ids = quick_actions.blender_menu_ids(bpy.context)
    assert len(ids) >= 9 and all(hasattr(bpy.types, idname) for idname, _l, _i in ids)
    records = []
    quick_actions.XRWB_MT_blender.draw(type("Menu", (), {"layout": Layout(records)})(), bpy.context)
    assert sum(item[0] == "operator" for item in records) == len(ids)
    # Drill-down stack: deeper levels push, B pops one level, then closes.
    calls = []
    original = controllers._native_call
    controllers._native_call = lambda name, **props: calls.append((name, props)) or (True, "ok")
    try:
        controllers._open_hand_native_menu(bpy.context, "XRWB_MT_quick", controllers.RIGHT_HAND)
        controllers._open_hand_native_menu(bpy.context, "XRWB_MT_blender", None, push=True)
        controllers._open_hand_native_menu(bpy.context, "TOPBAR_MT_file", None, push=True)
        assert controllers._menu_stack == ["XRWB_MT_quick", "XRWB_MT_blender", "TOPBAR_MT_file"]
        assert controllers.menu_back(bpy.context)[0] and calls[-1][1]["menu"] == "XRWB_MT_blender"
        assert controllers.menu_back(bpy.context)[0] and calls[-1][1]["menu"] == "XRWB_MT_quick"
        assert controllers.menu_back(bpy.context)[0] and calls[-1][1] == {"visible": False}
        assert controllers._menu_stack == []
        # A new top-level menu (a button press) starts a fresh stack.
        controllers._open_hand_native_menu(bpy.context, "XRWB_MT_add", controllers.LEFT_HAND)
        assert controllers._menu_stack == ["XRWB_MT_add"] and calls[-1][1]["controller"] == 0
    finally:
        controllers._native_call = original
        controllers._menu_stack.clear()
        controllers._menu_hand = controllers.RIGHT_HAND
    print("XR_FEEL_LABELS_MENUS labels=18 plate_faces_viewer=1 blender_menus=1 drill_down=1 back=2 close=1",
          flush=True)


def main():
    assert bpy.app.version[:3] == (5, 1, 2) and bpy.app.background and not bpy.data.filepath
    assert os.environ.get("BLENDER_USER_CONFIG"), "Set BLENDER_USER_CONFIG to an empty temporary folder"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    try:
        onboarding.reset_cache()
        check_booleans()
        check_prefs_and_guide()
        check_ray_and_stick()
        check_quick()
        check_place_and_previews()
        check_measure_and_arrange()
        check_labels_and_menus()
    finally:
        xr_workbench.unregister()
    print("XR_FEEL_SMOKE_PASS booleans=1 prefs=1 guide=1 ray=1 quick=1 preview=1 measure=1 arrange=1 labels=1 menus=1 input=synthetic hardware=0", flush=True)


if __name__ == "__main__":
    main()
