"""Factory-profile check of Live Cage: heavy modifiers hide while a capture works,
and no undo step or saved file ever holds the hidden state.

Real Blender data, undo history and .blend files; synthetic controller poses
only, no XR session. Run in a separate factory-startup background process with
BLENDER_USER_CONFIG set to an empty temporary folder and --python-exit-code 1.
"""
from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xr_workbench  # noqa: E402
from xr_workbench import editing, live_cage, physical_tools  # noqa: E402
from xr_workbench.interaction import (  # noqa: E402
    CaptureOwner, Decision, EditRequest, InputProvenance, InputSample, Pose, TransformOptions, TransformTool,
)

OWNER = CaptureOwner(InputProvenance.SIMULATOR, "/user/hand/right", 0)
DOWN = Quaternion()
HEAVY = ("Subdivision", "Boolean", "GeometryNodes")
_clock = [10.0]


class _UndoShim:
    """Stands in for the integrator's switch of every XR undo push to live_cage.push_undo."""

    def __init__(self, original):
        self._original = original
        self._inside = False

    def __getattr__(self, name):
        return getattr(self._original, name)

    @property
    def undo_push(self):
        original = self._original.undo_push

        def push(message=""):
            if self._inside:  # live_cage.push_undo's own call.
                return original(message=message)
            self._inside = True
            try:
                return live_cage.push_undo(message)
            finally:
                self._inside = False
        push.poll = original.poll
        return push


def sample(position):
    _clock[0] += 0.01
    return InputSample(OWNER, 0, Pose(tuple(position), tuple(DOWN)), _clock[0])


def target():
    return bpy.data.objects["Target"]


def coords(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", values)
    return values


def flags(obj):
    return tuple(obj.modifiers[name].show_viewport for name in HEAVY)


def assert_real(where):
    obj = target()
    assert flags(obj) == (True, True, True) and live_cage.NOTE_KEY not in obj.keys(), (where, flags(obj))


def set_repair_handlers(on):
    for name, function in live_cage._HANDLERS:
        if name in {"undo_post", "redo_post", "load_post"}:
            handlers = getattr(bpy.app.handlers, name)
            if on and function not in handlers:
                handlers.append(function)
            elif not on and function in handlers:
                handlers.remove(function)


def evaluated_count(obj):
    return len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)


def build():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=0.1, location=(0.15, 0.0, 0.0))
    cutter = bpy.context.object
    cutter.name = "Cutter"
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.12)
    obj = bpy.context.object
    obj.name = "Target"
    obj.modifiers.new("Subdivision", "SUBSURF").levels = 3
    obj.modifiers.new("Boolean", "BOOLEAN").object = cutter
    group = bpy.data.node_groups.new("Cage Smoke GN", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group.links.new(group.nodes.new("NodeGroupInput").outputs[0], group.nodes.new("NodeGroupOutput").inputs[0])
    obj.modifiers.new("GeometryNodes", "NODES").node_group = group
    obj.modifiers.new("Smooth", "SMOOTH").factor = 0.0  # Light: must stay on.
    assert bpy.ops.ed.undo_push(message="Smoke Scene") == {"FINISHED"}
    return obj


def grab_stroke(now):
    bpy.context.window_manager.xrwb_settings.sculpt_radius_m = 0.05
    start = Vector((0.0, 0.0, 1.12))
    transaction = physical_tools.begin(bpy.context, start, DOWN, tool="sculpt_grab", owner=OWNER)
    assert transaction is not None
    live_cage.refresh(bpy.context, editing.active_transaction(), None, now=now)
    result = transaction.update(bpy.context, sample(start + Vector((0.0, 0.0, 0.04))))
    assert result.decision is Decision.APPLY, result.message
    return transaction


def check_stroke_and_undo():
    obj = target()
    base_count = len(obj.data.vertices)
    assert evaluated_count(obj) > base_count * 10
    bpy.data.objects["Cutter"].location.y = 0.01
    assert bpy.ops.ed.undo_push(message="Smoke Before") == {"FINISHED"}
    before = coords(obj)

    # (a) During the stroke: heavy modifiers hidden, evaluated = base, the light Smooth stays on.
    transaction = grab_stroke(now=100.0)
    obj = target()
    assert flags(obj) == (False, False, False) and obj.modifiers["Smooth"].show_viewport
    assert live_cage.NOTE_KEY in obj.keys() and live_cage.readout_suffix() == " · cage"
    assert evaluated_count(obj) == base_count, "The cage evaluates the base mesh only"
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    live_cage.refresh(bpy.context, None, None, now=100.1)
    assert flags(target()) == (False, False, False), "Still settling after release"
    after = coords(target())
    assert not np.array_equal(after, before)

    # (b)+(e) One undo step; it and the step before it hold the real settings. The repair handlers
    # are off here so a step that recorded the cage would fail instead of being fixed on load.
    set_repair_handlers(False)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert_real("undo of stroke")
    assert np.array_equal(coords(target()), before), "One undo returns the pre-stroke mesh"
    live_cage.repair()  # What undo_post does: forget the cages.
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert abs(bpy.data.objects["Cutter"].location.y) < 1e-9, "Second undo undoes the step before the stroke"
    assert_real("undo before stroke")
    assert bpy.ops.ed.redo() == {"FINISHED"} and bpy.ops.ed.redo() == {"FINISHED"}
    assert_real("redo of stroke")
    assert np.array_equal(coords(target()), after)
    set_repair_handlers(True)
    return base_count


def check_save(tmp):
    # (c) Save while settling: the file holds the real flags; the live scene stays caged.
    transaction = grab_stroke(now=200.0)
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    live_cage.refresh(bpy.context, None, None, now=200.1)
    path = str(Path(tmp) / "cage.blend")
    assert bpy.ops.wm.save_as_mainfile(filepath=path) == {"FINISHED"}
    assert flags(target()) == (False, False, False) and live_cage.NOTE_KEY in target().keys()
    set_repair_handlers(False)  # No load_post repair: read the raw file.
    assert bpy.ops.wm.open_mainfile(filepath=path) == {"FINISHED"}
    assert_real("reloaded file")
    live_cage.repair()
    set_repair_handlers(True)

    # A file saved by a crashed session (note + hidden flag) is repaired on load.
    obj = target()
    obj.modifiers["Boolean"].show_viewport = False
    obj[live_cage.NOTE_KEY] = '{"Boolean": true}'
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert bpy.ops.wm.open_mainfile(filepath=path) == {"FINISHED"}
    assert_real("repaired on load")


def check_settle():
    assert bpy.ops.ed.undo_push(message="Smoke Reloaded") == {"FINISHED"}  # A fresh file has no step to undo to.
    transaction = grab_stroke(now=300.0)
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    near = {"/user/hand/right": (0.0, 0.0, 0.13)}
    live_cage.refresh(bpy.context, None, near, now=300.0)
    live_cage.refresh(bpy.context, None, near, now=301.0)
    assert flags(target()) == (False, False, False), "Hand still near: keep the cage"
    live_cage.refresh(bpy.context, None, {"/user/hand/right": (0.0, 0.0, 1.0)}, now=301.1)
    assert_real("settled")
    assert live_cage._EVAL_MS and live_cage.fade_lines(now=None), "Timed and fading"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert_real("undo after settle")


def check_cutter():
    # (d) Moving a boolean cutter cages its target; the cutter (no heavy modifiers) is untouched.
    cutter = bpy.data.objects["Cutter"]
    bpy.ops.object.select_all(action="DESELECT")
    cutter.select_set(True)
    bpy.context.view_layer.objects.active = cutter
    live_cage.refresh(bpy.context, None, None, now=400.0)
    start = Vector((0.15, 0.0, 0.0))
    request = EditRequest(OWNER, TransformTool.MOVE, 0, Pose(tuple(start), tuple(DOWN)), TransformOptions())
    transaction = editing.begin(bpy.context, request)
    live_cage.refresh(bpy.context, transaction, None, now=400.0)
    assert flags(target()) == (False, False, False) and not live_cage.is_caged(cutter)
    assert transaction.update(bpy.context, sample(start + Vector((0.02, 0.0, 0.0)))).decision is Decision.APPLY
    assert transaction.commit(bpy.context, OWNER).decision is Decision.APPLY
    live_cage.refresh(bpy.context, None, None, now=400.1)
    moved = bpy.data.objects["Cutter"].location.x
    assert abs(moved - 0.17) < 1e-5, moved
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert_real("undo of cutter move")
    assert abs(bpy.data.objects["Cutter"].location.x - 0.15) < 1e-5


def main():
    assert (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    xr_workbench.register()
    live_cage.register()
    bpy.ops.ed = _UndoShim(bpy.ops.ed)
    try:
        build()
        base = check_stroke_and_undo()
        with tempfile.TemporaryDirectory() as tmp:
            check_save(tmp)
            check_settle()
            check_cutter()
    finally:
        del bpy.ops.ed
        if editing.active_transaction() is not None:
            editing.active_transaction().cancel(bpy.context)
        live_cage.unregister()
        xr_workbench.unregister()
    print(f"XR_LIVE_CAGE_SMOKE_PASS base_vertices={base} heavy={len(HEAVY)} input=synthetic hardware=0",
          flush=True)


main()
