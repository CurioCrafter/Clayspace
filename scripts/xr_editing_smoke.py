"""Isolated Blender 5.1.2 smoke test for XR edit transactions."""

from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import sys
import traceback

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xr_workbench import editing  # noqa: E402
from xr_workbench.interaction import (  # noqa: E402
    CaptureOwner,
    Constraint,
    Decision,
    EditRequest,
    InputProvenance,
    InputSample,
    Pose,
    SelectionRequest,
    TransformOptions,
    TransformTool,
)


EPSILON = 1.0e-5


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def matrix_close(left: Matrix, right: Matrix) -> bool:
    return all(
        abs(a - b) <= EPSILON
        for row_a, row_b in zip(left, right)
        for a, b in zip(row_a, row_b)
    )


def vector_close(left: Vector, right: Vector) -> bool:
    return (left - right).length <= EPSILON


def _ui_context_parts():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next(
                (candidate for candidate in area.regions if candidate.type == "WINDOW"),
                None,
            )
            if region is None:
                continue
            return window, area, region
    raise AssertionError("no valid UI context for Blender undo/redo")


@contextmanager
def valid_ui_context():
    if bpy.app.background:
        yield
        return
    window, area, region = _ui_context_parts()
    with bpy.context.temp_override(
        window=window,
        screen=window.screen,
        area=area,
        region=region,
    ):
        yield


def history_operator(operator):
    with valid_ui_context():
        return operator()


def select_only(obj: bpy.types.Object) -> None:
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def owner(generation: int = 7) -> CaptureOwner:
    return CaptureOwner(InputProvenance.SIMULATOR, "smoke-right-hand", generation)


def request(
    target_owner: CaptureOwner,
    *,
    options: TransformOptions | None = None,
) -> EditRequest:
    return EditRequest(
        target_owner,
        TransformTool.MOVE,
        0,
        Pose((0.0, 0.0, 0.0)),
        options or TransformOptions(),
    )


def sample(
    target_owner: CaptureOwner,
    position: tuple[float, float, float],
    timestamp: float = 1.0,
    *,
    tracking: bool = True,
    neutral: bool = False,
) -> InputSample:
    return InputSample(
        target_owner,
        0,
        Pose(position),
        timestamp,
        tracking_active=tracking,
        neutral=neutral,
    )


def selection_state() -> tuple[set[str], str | None]:
    active = bpy.context.view_layer.objects.active
    return {obj.name for obj in bpy.context.selected_objects}, (
        active.name if active is not None else None
    )


def selection_smoke(*, defer_history: bool = False):
    original = bpy.data.objects["XRWB_Child"]
    select_only(original)
    bpy.ops.mesh.primitive_cube_add(location=(3.0, 0.0, 0.0))
    target = bpy.context.object
    target.name = "XRWB_SelectTarget"
    select_only(original)
    initial = selection_state()
    selection_owner = CaptureOwner(
        InputProvenance.SIMULATOR,
        "selection-hand",
        21,
    )
    selection_request = SelectionRequest(
        owner=selection_owner,
        target_generation=0,
        anchor_pose=Pose((0.0, 0.0, 0.0)),
        world_units_per_metre=1.0,
        click_slop_metres=0.010,
    )

    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    bpy.context.view_layer.objects.active = None
    require(selection_state() == (set(), None), "empty selection setup failed")
    transaction = editing.begin_object_selection(bpy.context, selection_request, target)
    require(
        selection_state() == ({target.name}, target.name),
        "empty selection preview did not highlight the target",
    )
    transaction.cancel(bpy.context, selection_owner)
    require(
        selection_state() == (set(), None),
        "empty selection cancel left the preview target active",
    )
    select_only(original)
    require(selection_state() == initial, "selection setup was not restored")

    transaction = editing.begin_object_selection(bpy.context, selection_request, target)
    loss_result = transaction.update(
        bpy.context,
        InputSample(
            selection_owner,
            0,
            Pose((0.0, 0.0, 0.0)),
            0.5,
            tracking_active=False,
        ),
    )
    require(
        loss_result.decision is Decision.CANCEL_REQUIRED,
        "selection tracking loss did not cancel",
    )
    require(selection_state() == initial, "tracking loss did not restore selection")

    transaction = editing.begin_object_selection(bpy.context, selection_request, target)
    changed_provider = CaptureOwner(
        InputProvenance.SIMULATOR,
        selection_owner.identifier,
        selection_owner.provider_generation + 1,
    )
    provider_result = transaction.update(
        bpy.context,
        InputSample(
            changed_provider,
            0,
            Pose((0.0, 0.0, 0.0)),
            0.6,
        ),
    )
    require(
        provider_result.decision is Decision.CANCEL_REQUIRED,
        "selection provider change did not cancel",
    )
    require(selection_state() == initial, "provider cancel did not restore selection")

    transaction = editing.begin_object_selection(bpy.context, selection_request, target)
    other_owner = CaptureOwner(
        InputProvenance.SIMULATOR,
        "other-selection-hand",
        selection_owner.provider_generation,
    )
    require(
        transaction.commit(bpy.context, other_owner).decision is Decision.REJECTED,
        "another owner committed the frozen selection target",
    )
    transaction.cancel(bpy.context, selection_owner)
    require(selection_state() == initial, "owner rejection did not restore selection")

    select_only(target)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        editing.begin_object_selection(bpy.context, selection_request, target)
    except editing.EditRejected as exc:
        require(
            "object mode only" in str(exc),
            "edit-mesh selection rejection was not explicit",
        )
    else:
        raise AssertionError("unsupported edit-mesh selection was accepted")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        select_only(original)

    transaction = editing.begin_object_selection(bpy.context, selection_request, target)
    require(
        selection_state() == ({target.name}, target.name),
        "selection preview did not use Blender selection rendering",
    )
    slop_result = transaction.update(
        bpy.context,
        InputSample(
            selection_owner,
            0,
            Pose((0.0101, 0.0, 0.0)),
            1.0,
        ),
    )
    require(
        slop_result.decision is Decision.CANCEL_REQUIRED,
        "selection beyond 10 mm click slop did not cancel",
    )
    require(selection_state() == initial, "selection cancel was not exact")

    transaction = editing.begin_object_selection(bpy.context, selection_request, target)
    require(
        transaction.update(
            bpy.context,
            InputSample(
                selection_owner,
                0,
                Pose((0.009, 0.0, 0.0)),
                1.1,
            ),
        ).decision
        is Decision.APPLY,
        "selection inside click slop was rejected",
    )
    require(
        transaction.commit(bpy.context, selection_owner).decision is Decision.APPLY,
        "object selection commit failed",
    )
    final = selection_state()
    require(final == ({target.name}, target.name), "selection commit lost target")
    if defer_history:
        return {"initial": initial, "final": final}
    require(history_operator(bpy.ops.ed.undo) == {"FINISHED"}, "selection undo failed")
    require(selection_state() == initial, "selection undo was not exact")
    require(history_operator(bpy.ops.ed.redo) == {"FINISHED"}, "selection redo failed")
    require(selection_state() == final, "selection redo was not exact")


def object_smoke(*, defer_history: bool = False):
    parent = bpy.data.objects.new("XRWB_Parent", None)
    bpy.context.scene.collection.objects.link(parent)
    parent.matrix_world = (
        Matrix.Translation((4.0, -2.0, 1.0))
        @ Matrix.Rotation(math.radians(31.0), 4, "Z")
        @ Matrix.Diagonal((1.7, 0.65, 1.25, 1.0))
    )

    bpy.ops.mesh.primitive_cube_add(location=(1.0, 2.0, 3.0))
    child = bpy.context.object
    child.name = "XRWB_Child"
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = Matrix.Translation((1.0, 2.0, 3.0)) @ Matrix.Rotation(
        0.2, 4, "Y"
    )

    bpy.ops.mesh.primitive_cube_add(location=(-6.0, 3.0, 2.0))
    untouched = bpy.context.object
    untouched.name = "XRWB_Untouched"
    untouched_matrix = untouched.matrix_world.copy()
    select_only(child)
    original = child.matrix_world.copy()

    simulated = owner()
    options = TransformOptions(constraint=Constraint.X, translation_snap=0.5)
    transaction = editing.begin(bpy.context, request(simulated, options=options))
    result = transaction.update(bpy.context, sample(simulated, (1.24, 8.0, -4.0)))
    require(result.decision is Decision.APPLY, "object preview did not apply")
    require(
        abs(child.matrix_world.translation.x - (original.translation.x + 1.0))
        < EPSILON,
        "X snap failed",
    )
    require(
        abs(child.matrix_world.translation.y - original.translation.y) < EPSILON,
        "X constraint leaked into Y",
    )
    require(
        matrix_close(untouched.matrix_world, untouched_matrix),
        "unselected object changed",
    )
    require(tuple(bpy.context.selected_objects) == (child,), "object selection changed")

    try:
        editing.begin(bpy.context, request(simulated))
    except editing.EditRejected:
        pass
    else:
        raise AssertionError("second capture was not rejected")
    transaction.cancel(bpy.context, simulated)
    require(matrix_close(child.matrix_world, original), "object cancel was not exact")

    pivot = child.matrix_world.translation.copy()
    rotation_options = TransformOptions(
        constraint=Constraint.Z,
        rotation_snap_radians=math.radians(15.0),
        pivot_world=tuple(pivot),
    )
    rotation_request = EditRequest(
        simulated,
        TransformTool.ROTATE,
        0,
        Pose((0.0, 0.0, 0.0)),
        rotation_options,
    )
    transaction = editing.begin(bpy.context, rotation_request)
    swing_twist = Quaternion((1.0, 0.0, 0.0), math.radians(30.0)) @ Quaternion(
        (0.0, 0.0, 1.0), math.radians(44.0)
    )
    rotation_sample = InputSample(
        simulated,
        0,
        Pose((0.0, 0.0, 0.0), tuple(swing_twist)),
        1.1,
    )
    transaction.update(bpy.context, rotation_sample)
    expected_rotation = (
        Matrix.Translation(pivot)
        @ Matrix.Rotation(math.radians(45.0), 4, "Z")
        @ Matrix.Translation(-pivot)
        @ original
    )
    require(
        matrix_close(child.matrix_world, expected_rotation),
        "axis twist extraction or rotation snap failed",
    )
    transaction.cancel(bpy.context, simulated)
    require(matrix_close(child.matrix_world, original), "rotation cancel was not exact")

    scale_options = TransformOptions(
        constraint=Constraint.X,
        scale_snap=0.25,
        pivot_world=tuple(pivot),
    )
    start_point = pivot + Vector((1.0, 0.0, 0.0))
    scale_request = EditRequest(
        simulated,
        TransformTool.SCALE,
        0,
        Pose(tuple(start_point)),
        scale_options,
    )
    transaction = editing.begin(bpy.context, scale_request)
    current_point = pivot + Vector((2.12, 0.0, 0.0))
    transaction.update(
        bpy.context,
        InputSample(simulated, 0, Pose(tuple(current_point)), 1.2),
    )
    expected_scale = (
        Matrix.Translation(pivot)
        @ Matrix.Diagonal((2.0, 1.0, 1.0, 1.0))
        @ Matrix.Translation(-pivot)
        @ original
    )
    require(
        matrix_close(child.matrix_world, expected_scale),
        "axis scale constraint or snap failed",
    )
    transaction.cancel(bpy.context, simulated)
    require(matrix_close(child.matrix_world, original), "scale cancel was not exact")

    transaction = editing.begin(bpy.context, request(simulated))
    transaction.update(
        bpy.context,
        sample(simulated, (0.5, 0.0, 0.0), tracking=False, timestamp=2.0),
    )
    require(
        transaction.commit(bpy.context, simulated).decision is Decision.REJECTED,
        "paused gesture committed",
    )
    require(
        transaction.update(
            bpy.context,
            sample(simulated, (20.0, 0.0, 0.0), timestamp=2.1),
        ).decision
        is Decision.NEUTRAL_REQUIRED,
        "tracking resumed without neutral",
    )
    require(
        transaction.update(
            bpy.context,
            sample(simulated, (20.0, 0.0, 0.0), timestamp=2.2, neutral=True),
        ).decision
        is Decision.REBASE,
        "neutral did not rebase",
    )
    rebased = child.matrix_world.copy()
    transaction.update(bpy.context, sample(simulated, (21.0, 0.0, 0.0), timestamp=2.3))
    require(
        abs(child.matrix_world.translation.x - (rebased.translation.x + 1.0)) < EPSILON,
        "rebase jumped",
    )
    transaction.cancel(bpy.context)

    transaction = editing.begin(bpy.context, request(simulated))
    changed_provider = owner(generation=8)
    generation_result = transaction.update(
        bpy.context,
        sample(changed_provider, (1.0, 0.0, 0.0)),
    )
    require(
        generation_result.decision is Decision.CANCEL_REQUIRED,
        "provider generation did not cancel",
    )
    require(
        matrix_close(child.matrix_world, original),
        "generation cancel failed to restore",
    )

    two_hand_owner = CaptureOwner(InputProvenance.OPTICAL, "two-hand-scale", 12)
    two_hand_request = EditRequest(
        two_hand_owner,
        TransformTool.SCALE,
        0,
        Pose(tuple(pivot)),
        TransformOptions(
            constraint=Constraint.FREE,
            min_scale=0.05,
            max_scale=20.0,
            pivot_world=tuple(pivot),
        ),
    )
    transaction = editing.begin(bpy.context, two_hand_request)
    transaction.update(
        bpy.context,
        InputSample(
            two_hand_owner,
            0,
            Pose(tuple(pivot)),
            3.0,
            uniform_scale=1.5,
        ),
    )
    transaction.cancel(bpy.context, two_hand_owner)
    require(
        matrix_close(child.matrix_world, original),
        "two-hand uniform scale cancel was not exact",
    )

    require(
        history_operator(lambda: bpy.ops.ed.undo_push(message="XR object baseline"))
        == {"FINISHED"},
        "could not initialize object undo baseline",
    )
    transaction = editing.begin(bpy.context, two_hand_request)
    transaction.update(
        bpy.context,
        InputSample(
            two_hand_owner,
            0,
            Pose(tuple(pivot)),
            3.1,
            uniform_scale=1.5,
        ),
    )
    final = child.matrix_world.copy()
    require(
        transaction.commit(bpy.context, two_hand_owner).decision is Decision.APPLY,
        "object commit failed",
    )
    require(
        matrix_close(child.matrix_world, final), "commit lost final object transform"
    )
    if defer_history:
        return {
            "original": original.copy(),
            "final": final.copy(),
            "untouched": untouched_matrix.copy(),
        }
    require(history_operator(bpy.ops.ed.undo) == {"FINISHED"}, "object undo failed")
    child = bpy.data.objects["XRWB_Child"]
    require(
        matrix_close(child.matrix_world, original), "one undo did not restore object"
    )
    require(history_operator(bpy.ops.ed.redo) == {"FINISHED"}, "object redo failed")
    child = bpy.data.objects["XRWB_Child"]
    require(matrix_close(child.matrix_world, final), "redo did not restore object edit")
    require(
        matrix_close(bpy.data.objects["XRWB_Untouched"].matrix_world, untouched_matrix),
        "undo touched unrelated object",
    )


def mesh_state(obj: bpy.types.Object) -> tuple[tuple[Vector, ...], tuple[bool, ...]]:
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return (
        tuple(vertex.co.copy() for vertex in bm.verts),
        tuple(vertex.select for vertex in bm.verts),
    )


def mesh_smoke(*, defer_history: bool = False):
    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.mode != "OBJECT" else None
    bpy.ops.mesh.primitive_cube_add(location=(0.0, -4.0, 0.0))
    obj = bpy.context.object
    obj.name = "XRWB_EditMesh"
    obj.scale = (1.8, 0.7, 1.3)  # deliberately unapplied
    select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bpy.ops.mesh.select_all(action="DESELECT")
    bm.verts.ensure_lookup_table()
    bm.verts[0].select = True
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    original_coords, original_selection = mesh_state(obj)

    simulated = owner()
    transaction = editing.begin(bpy.context, request(simulated))
    transaction.update(bpy.context, sample(simulated, (0.75, 0.0, 0.0)))
    preview_coords, preview_selection = mesh_state(obj)
    require(
        not vector_close(preview_coords[0], original_coords[0]),
        "selected vertex did not move",
    )
    require(
        all(
            vector_close(preview_coords[index], original_coords[index])
            for index in range(1, len(preview_coords))
        ),
        "unselected vertices moved",
    )
    require(preview_selection == original_selection, "mesh selection changed")
    transaction.cancel(bpy.context, simulated)
    cancelled_coords, cancelled_selection = mesh_state(obj)
    require(
        all(vector_close(a, b) for a, b in zip(cancelled_coords, original_coords)),
        "mesh cancel was not exact",
    )
    require(
        cancelled_selection == original_selection,
        f"mesh cancel lost selection: {original_selection!r} -> {cancelled_selection!r}",
    )

    transaction = editing.begin(bpy.context, request(simulated))
    bm = bmesh.from_edit_mesh(obj.data)
    added = bm.verts.new((3.0, 3.0, 3.0))
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
    invalidated = transaction.update(bpy.context, sample(simulated, (0.2, 0.0, 0.0)))
    require(
        invalidated.decision is Decision.CANCEL_REQUIRED,
        "live BMesh topology change did not invalidate capture",
    )
    require(editing.active_transaction() is None, "invalid topology retained capture")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.remove(added)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)

    require(
        history_operator(lambda: bpy.ops.ed.undo_push(message="XR mesh baseline"))
        == {"FINISHED"},
        "could not initialize mesh undo baseline",
    )
    transaction = editing.begin(bpy.context, request(simulated))
    transaction.update(bpy.context, sample(simulated, (1.0, 0.0, 0.0)))
    final_coords, _ = mesh_state(obj)
    require(
        transaction.commit(bpy.context, simulated).decision is Decision.APPLY,
        "mesh commit failed",
    )
    if defer_history:
        return {
            "original": original_coords,
            "final": final_coords,
            "selection": original_selection,
        }
    require(history_operator(bpy.ops.ed.undo) == {"FINISHED"}, "mesh undo failed")
    undo_coords, undo_selection = mesh_state(obj)
    require(
        all(vector_close(a, b) for a, b in zip(undo_coords, original_coords)),
        "mesh undo was not exact",
    )
    require(undo_selection == original_selection, "mesh undo lost selection")
    require(history_operator(bpy.ops.ed.redo) == {"FINISHED"}, "mesh redo failed")
    redo_coords, redo_selection = mesh_state(obj)
    require(
        all(vector_close(a, b) for a, b in zip(redo_coords, final_coords)),
        "mesh redo was not exact",
    )
    require(redo_selection == original_selection, "mesh redo lost selection")

    bpy.ops.object.mode_set(mode="OBJECT")
    modifier = obj.modifiers.new("XRWB_UnverifiedCage", "SUBSURF")
    modifier.show_in_editmode = True
    modifier.show_on_cage = False
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        editing.begin(bpy.context, request(simulated))
    except editing.EditRejected as exc:
        require(
            "edit-cage" in str(exc) or "edit cage" in str(exc),
            "modifier rejection was not explicit",
        )
    else:
        raise AssertionError("modifier stack was accepted without edit-cage consent")


_STAGED_STATE: dict[str, object] = {"stage": 0, "registered": []}


def _modifier_rejection() -> None:
    obj = bpy.data.objects["XRWB_EditMesh"]
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    select_only(obj)
    shared = bpy.data.objects.new("XRWB_SharedMesh", obj.data)
    bpy.context.scene.collection.objects.link(shared)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        editing.begin(bpy.context, request(owner()))
    except editing.EditRejected as exc:
        require("Multi-user" in str(exc), "shared mesh rejection was not explicit")
    else:
        raise AssertionError("shared mesh data was accepted for component editing")
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(shared, do_unlink=True)
    select_only(obj)
    modifier = obj.modifiers.new("XRWB_StagedUnverifiedCage", "SUBSURF")
    modifier.show_in_editmode = True
    modifier.show_on_cage = False
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        editing.begin(bpy.context, request(owner()))
    except editing.EditRejected as exc:
        require(
            "edit-cage" in str(exc) or "edit cage" in str(exc),
            "modifier rejection was not explicit",
        )
    else:
        raise AssertionError("modifier stack was accepted without edit-cage consent")


def _finish_staged_smoke() -> None:
    editing.cancel_active(bpy.context)
    editing.clear_commit_payloads()
    for cls in reversed(_STAGED_STATE["registered"]):
        bpy.utils.unregister_class(cls)
    _STAGED_STATE["registered"] = []


def _staged_tick():
    try:
        stage = int(_STAGED_STATE["stage"])
        with valid_ui_context():
            if stage == 0:
                _STAGED_STATE["object"] = object_smoke(defer_history=True)
            elif stage == 1:
                require(
                    history_operator(bpy.ops.ed.undo) == {"FINISHED"},
                    "object undo failed",
                )
                expected = _STAGED_STATE["object"]
                require(
                    matrix_close(
                        bpy.data.objects["XRWB_Child"].matrix_world,
                        expected["original"],
                    ),
                    "one undo did not restore object",
                )
            elif stage == 2:
                require(
                    history_operator(bpy.ops.ed.redo) == {"FINISHED"},
                    "object redo failed",
                )
                expected = _STAGED_STATE["object"]
                require(
                    matrix_close(
                        bpy.data.objects["XRWB_Child"].matrix_world,
                        expected["final"],
                    ),
                    "redo did not restore object edit",
                )
                require(
                    matrix_close(
                        bpy.data.objects["XRWB_Untouched"].matrix_world,
                        expected["untouched"],
                    ),
                    "undo touched unrelated object",
                )
                child = bpy.data.objects["XRWB_Child"]
                select_only(child)
                pivot = tuple(child.matrix_world.translation)
                no_change_owner = CaptureOwner(
                    InputProvenance.OPTICAL, "two-hand-scale", 13
                )
                no_change_request = EditRequest(
                    no_change_owner,
                    TransformTool.SCALE,
                    0,
                    Pose(pivot),
                    TransformOptions(
                        min_scale=0.05,
                        max_scale=20.0,
                        pivot_world=pivot,
                    ),
                )
                no_change = editing.begin(bpy.context, no_change_request)
                no_change.update(
                    bpy.context,
                    InputSample(
                        no_change_owner,
                        0,
                        Pose(pivot),
                        4.0,
                        uniform_scale=1.0,
                    ),
                )
                no_change_result = no_change.commit(bpy.context, no_change_owner)
                require(
                    "undo history unchanged" in no_change_result.message,
                    "zero-change commit was not identified",
                )
            elif stage == 3:
                require(
                    history_operator(bpy.ops.ed.undo) == {"FINISHED"},
                    "undo after zero-change commit failed",
                )
                expected = _STAGED_STATE["object"]
                require(
                    matrix_close(
                        bpy.data.objects["XRWB_Child"].matrix_world,
                        expected["original"],
                    ),
                    "zero-change commit created an undo entry",
                )
                require(
                    history_operator(bpy.ops.ed.redo) == {"FINISHED"},
                    "object redo after no-op proof failed",
                )
                _STAGED_STATE["selection"] = selection_smoke(defer_history=True)
            elif stage == 4:
                require(
                    history_operator(bpy.ops.ed.undo) == {"FINISHED"},
                    "selection undo failed",
                )
                expected = _STAGED_STATE["selection"]
                require(
                    selection_state() == expected["initial"],
                    "selection undo was not exact",
                )
            elif stage == 5:
                require(
                    history_operator(bpy.ops.ed.redo) == {"FINISHED"},
                    "selection redo failed",
                )
                expected = _STAGED_STATE["selection"]
                require(
                    selection_state() == expected["final"],
                    "selection redo was not exact",
                )
                selection_owner = CaptureOwner(
                    InputProvenance.SIMULATOR,
                    "selection-hand-noop",
                    22,
                )
                selection_request = SelectionRequest(
                    owner=selection_owner,
                    target_generation=0,
                    anchor_pose=Pose((0.0, 0.0, 0.0)),
                    world_units_per_metre=1.0,
                    click_slop_metres=0.010,
                )
                no_change = editing.begin_object_selection(
                    bpy.context,
                    selection_request,
                    bpy.data.objects["XRWB_SelectTarget"],
                )
                no_change.update(
                    bpy.context,
                    InputSample(
                        selection_owner,
                        0,
                        Pose((0.0, 0.0, 0.0)),
                        2.0,
                    ),
                )
                no_change_result = no_change.commit(bpy.context, selection_owner)
                require(
                    "undo history unchanged" in no_change_result.message,
                    "no-op object selection created history",
                )
            elif stage == 6:
                require(
                    history_operator(bpy.ops.ed.undo) == {"FINISHED"},
                    "undo after no-op selection failed",
                )
                expected = _STAGED_STATE["selection"]
                require(
                    selection_state() == expected["initial"],
                    "no-op selection created an undo entry",
                )
            elif stage == 7:
                require(
                    history_operator(bpy.ops.ed.redo) == {"FINISHED"},
                    "selection redo after no-op proof failed",
                )
                expected = _STAGED_STATE["selection"]
                require(
                    selection_state() == expected["final"],
                    "selection redo after no-op proof was not exact",
                )
                _STAGED_STATE["mesh"] = mesh_smoke(defer_history=True)
            elif stage == 8:
                require(
                    history_operator(bpy.ops.ed.undo) == {"FINISHED"},
                    "mesh undo failed",
                )
                expected = _STAGED_STATE["mesh"]
                coords, selection = mesh_state(bpy.data.objects["XRWB_EditMesh"])
                require(
                    all(
                        vector_close(a, b) for a, b in zip(coords, expected["original"])
                    ),
                    "mesh undo was not exact",
                )
                require(selection == expected["selection"], "mesh undo lost selection")
            elif stage == 9:
                require(
                    history_operator(bpy.ops.ed.redo) == {"FINISHED"},
                    "mesh redo failed",
                )
                expected = _STAGED_STATE["mesh"]
                coords, selection = mesh_state(bpy.data.objects["XRWB_EditMesh"])
                require(
                    all(vector_close(a, b) for a, b in zip(coords, expected["final"])),
                    "mesh redo was not exact",
                )
                require(selection == expected["selection"], "mesh redo lost selection")
                _modifier_rejection()
                print(
                    "XR_EDITING_SMOKE_PASS "
                    "blender=5.1.2 provenance=synthetic "
                    "object_cancel=exact object_undo_redo=pass "
                    "mesh_cancel=exact mesh_undo_redo=pass noop_undo=pass "
                    "two_hand_scale_cancel=exact two_hand_scale_undo_redo=pass "
                    "object_select_hit_slop_loss_provider_owner_undo_noop=pass "
                    "modifier_cage=rejected"
                )
                _finish_staged_smoke()
                bpy.ops.wm.quit_blender()
                return None
        _STAGED_STATE["stage"] = stage + 1
        return 0.05
    except Exception:
        traceback.print_exc()
        _finish_staged_smoke()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


def start_staged_smoke() -> None:
    require(
        (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0),
        f"expected Blender 4.5 to 5.2, got {bpy.app.version_string}",
    )
    bpy.context.preferences.edit.use_global_undo = True
    for cls in editing.CLASSES:
        bpy.utils.register_class(cls)
        _STAGED_STATE["registered"].append(cls)
    bpy.app.timers.register(_staged_tick, first_interval=0.05)


def main() -> None:
    require(
        (4, 5, 0) <= bpy.app.version[:3] < (5, 3, 0),
        f"expected Blender 4.5 to 5.2, got {bpy.app.version_string}",
    )
    bpy.context.preferences.edit.use_global_undo = True
    registered = []
    try:
        for cls in editing.CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        with valid_ui_context():
            object_smoke()
            mesh_smoke()
        print(
            "XR_EDITING_SMOKE_PASS "
            "blender=5.1.2 provenance=simulator "
            "object_cancel=exact object_undo_redo=pass "
            "mesh_cancel=exact mesh_undo_redo=pass modifier_cage=rejected"
        )
    finally:
        editing.cancel_active(bpy.context)
        editing.clear_commit_payloads()
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    if "--staged-smoke" in sys.argv:
        start_staged_smoke()
    else:
        main()
        if "--quit-after-smoke" in sys.argv:
            bpy.ops.wm.quit_blender()
