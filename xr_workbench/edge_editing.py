"""Project-owned edge selection and native BMesh bevel; no external add-ons.

Object-mode selections live in an explicit edge attribute, not the native mesh
select bits inherited from primitive creation. Preview, capture and undo reuse
the source-face transaction. Source editing deliberately rejects modifier stacks.
"""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Quaternion, Vector

from . import editing, face_editing
from .interaction import CaptureOwner, Decision, EditResult, InputProvenance, Pose

SELECTION_ATTRIBUTE = "xrwb_edge_selected"
_draw_handles = {}
_last_draw_error = ""
_draw_counts = {"WINDOW": 0, "XR": 0}
_draw_positions = ()
_CACHE_CLEAR_HANDLERS = ("undo_pre", "undo_post", "redo_pre", "redo_post", "load_pre")


def selected_indices(mesh):
    attribute = mesh.attributes.get(SELECTION_ATTRIBUTE)
    if attribute is None:
        return ()
    if attribute.domain != "EDGE" or attribute.data_type != "BOOLEAN":
        raise editing.EditRejected("XR edge selection attribute has an incompatible type")
    return tuple(index for index, item in enumerate(attribute.data) if item.value)


def _selection_state(context):
    return (editing._safe_pointer(context.view_layer.objects.active),
            frozenset(editing._safe_pointer(obj) for obj in context.selected_objects))


def _nearest_edge(obj, face_index, location):
    mesh = obj.data
    if not 0 <= face_index < len(mesh.polygons) or mesh.polygons[face_index].hide:
        raise editing.EditRejected("Ray has no visible source face")
    best = None
    for loop_index in mesh.polygons[face_index].loop_indices:
        edge = mesh.edges[mesh.loops[loop_index].edge_index]
        if edge.hide:
            continue
        a, b = (obj.matrix_world @ mesh.vertices[index].co for index in edge.vertices)
        delta = b - a
        if delta.length_squared < 1.0e-16:
            continue
        nearest = a + delta * max(0.0, min(1.0, (location - a).dot(delta) / delta.length_squared))
        key = ((nearest - location).length_squared, edge.index)
        if best is None or key < best:
            best = key
    if best is None:
        raise editing.EditRejected("Hit face has no visible nondegenerate edge")
    return best[1]


class EdgeTransaction(face_editing.FaceTransaction):
    def __init__(self, context, obj, face_index, edge_index, pose, tool, owner,
                 generation, segments, world_units_per_metre):
        self.edge_indices = (edge_index,) if tool == "select_edge" else (
            selected_indices(obj.data) or (edge_index,))
        self.segments = segments
        self.world_units_per_metre = world_units_per_metre
        self.object_selection = _selection_state(context)
        self.view_layer_pointer = editing._safe_pointer(context.view_layer)
        self.drag_axis = None
        super().__init__(context, obj, face_index, pose, tool, owner, generation)
        self.value = self.anchor_value = 0.0
        try:
            self.base.edges.ensure_lookup_table()
            for index in self.edge_indices:
                edge = self.base.edges[index]
                if edge.hide or edge.calc_length() < 1.0e-8:
                    raise editing.EditRejected("Cannot edit a hidden or degenerate source edge")
                if tool == "bevel_edge" and not edge.is_manifold:
                    raise editing.EditRejected("Selected-edge bevel currently requires manifold edges")
            if tool == "select_edge":
                layer = self.work.edges.layers.bool.get(SELECTION_ATTRIBUTE)
                if layer is None:
                    layer = self.work.edges.layers.bool.new(SELECTION_ATTRIBUTE)
                edge = self.work.edges[edge_index]
                edge[layer] = not edge[layer]
                self._write_preview()
        except Exception:
            self.cancel(context)
            raise

    def _reset_work(self):
        if self.work is not None:
            self.work.free()
        self.work = self.base.copy()
        self.work.edges.ensure_lookup_table()

    def _valid(self, context):
        return (super()._valid(context)
                and editing._safe_pointer(context.view_layer) == self.view_layer_pointer
                and _selection_state(context) == self.object_selection)

    def _is_unchanged(self):
        return self.tool == "bevel_edge" and self.value < 1.0e-7

    def _write_preview(self):
        self.work.normal_update()
        self.work.to_mesh(self.preview)
        self.preview.update()
        self.preview_signature = face_editing._signature(self.preview)

    def commit(self, context, owner=None):
        unchanged = self._is_unchanged()
        result = super().commit(context, owner)
        if result.decision is Decision.APPLY:
            return EditResult(Decision.APPLY, "No bevel change" if unchanged else
                              ("Edge selection" if self.tool == "select_edge" else "Edge bevel")
                              + " committed as one undo step")
        return result

    def update(self, context, sample):
        editing._require_main_thread()
        if editing.active_transaction() is not self or self.finished:
            return EditResult(Decision.REJECTED, "Edge tool does not own capture")
        if not self._valid(context) or not editing.undo_available(context):
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, "Edge target, selection or undo state changed")
        result = self.capture.evaluate(sample)
        if result.decision is Decision.CANCEL_REQUIRED:
            self.cancel(context)
            return result
        if result.decision not in {Decision.APPLY, Decision.REBASE}:
            if self.tool == "select_edge" and result.decision is Decision.PAUSED:
                return self.cancel(context)
            return result
        position = Vector(sample.pose.position)
        if not all(math.isfinite(value) for value in position):
            return self.cancel(context)
        if self.tool == "select_edge":
            if (position - self.anchor_origin).length > .02 * self.world_units_per_metre:
                return self.cancel(context)
            return EditResult(Decision.APPLY, "Edge selection preview; release to keep it")
        if result.decision is Decision.REBASE:
            self.anchor_origin = position
            self.anchor_value = self.value
            return result
        delta = position - self.anchor_origin
        if self.drag_axis is None:
            if delta.length < 1.0e-6:
                return EditResult(Decision.APPLY, "Move the hand to set bevel width")
            self.drag_axis = delta.normalized()
        self.value = max(0.0, min(1.0e5, self.anchor_value + delta.dot(self.drag_axis)))
        try:
            self._reset_work()
            if self.value >= 1.0e-7:
                targets = [self.work.edges[index] for index in self.edge_indices]
                # Work in world space so rotated/nonuniform object scale cannot
                # make the same physical drag bevel different edges unequally.
                self.work.transform(self.world)
                self.work.normal_update()
                result = bmesh.ops.bevel(self.work, geom=targets, offset=self.value,
                                        offset_type="OFFSET", segments=self.segments,
                                        profile=.5, affect="EDGES", clamp_overlap=True,
                                        loop_slide=True)
                layer = self.work.edges.layers.bool.get(SELECTION_ATTRIBUTE)
                if layer is None:
                    layer = self.work.edges.layers.bool.new(SELECTION_ATTRIBUTE)
                for edge in self.work.edges:
                    edge[layer] = False
                for edge in result["edges"]:
                    edge[layer] = True
                self.work.transform(self.inverse)
            self._write_preview()
        except (ValueError, RuntimeError) as exc:
            self.cancel(context)
            return EditResult(Decision.CANCEL_REQUIRED, f"Native edge bevel cancelled: {exc}")
        return EditResult(Decision.APPLY, f"Bevel width: {self.value:.4f} world units; {self.segments} segments")


def begin(context, pose_origin, pose_rotation, tool="select_edge", owner=None,
          target_generation=0, segments=None, world_units_per_metre=1.0):
    editing._require_main_thread()
    if tool not in {"select_edge", "bevel_edge"}:
        raise editing.EditRejected("Unknown edge tool")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    if not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    if not math.isfinite(world_units_per_metre) or world_units_per_metre <= 0:
        raise editing.EditRejected("Edge click scale must be positive and finite")
    if segments is None:
        settings = getattr(context.window_manager, "xrwb_settings", None)
        segments = getattr(settings, "bevel_segments", 3)
    if isinstance(segments, bool) or not isinstance(segments, int) or not 1 <= segments <= 32:
        raise editing.EditRejected("Bevel segments must be an integer from 1 to 32")
    try:
        pose = Pose(tuple(pose_origin), tuple(pose_rotation))
    except ValueError as exc:
        raise editing.EditRejected(str(exc)) from exc
    rotation = Quaternion(pose.rotation)
    origin = Vector(pose.position)
    if (not all(math.isfinite(value) for value in origin)
            or not math.isfinite(rotation.magnitude) or rotation.magnitude < 1.0e-8):
        raise editing.EditRejected("Controller pose is outside Blender's finite range")
    rotation.normalize()
    context.view_layer.update()
    hit, location, _normal, face_index, evaluated, matrix = context.scene.ray_cast(
        context.evaluated_depsgraph_get(), origin, rotation @ Vector((0, 0, -1)))
    if not hit or evaluated is None:
        raise editing.EditRejected("Point at a visible mesh edge")
    obj = evaluated.original
    try:
        face_editing._require_target(context, obj)
    except editing.EditRejected as exc:
        raise editing.EditRejected(f"Source-edge tool: {exc}. Use object bevel for a modifier stack.") from exc
    if any(abs(a - b) > 1.0e-7 for ra, rb in zip(matrix, obj.matrix_world) for a, b in zip(ra, rb)):
        raise editing.EditRejected("Instanced edges need an explicit source adapter")
    selected_indices(obj.data)  # Reject an incompatible existing attribute before copying anything.
    edge_index = _nearest_edge(obj, face_index, location)
    owner = owner or CaptureOwner(InputProvenance.CONTROLLER, "/user/hand/right", 0)
    transaction = EdgeTransaction(context, obj, face_index, edge_index, pose, tool, owner,
                                  target_generation, segments, world_units_per_metre)
    editing._ACTIVE_TRANSACTION = transaction
    return transaction


def selected_segments(context):
    """World-space selected line endpoints, with no retained RNA references."""
    positions = []
    # ponytail: scan marked mesh edges at 20Hz, never in the draw callback; use
    # depsgraph-driven cache invalidation if large selections need a lower cost.
    for obj in context.view_layer.objects:
        if obj.type != "MESH" or obj.mode != "OBJECT" or not obj.visible_get(view_layer=context.view_layer):
            continue
        if any(modifier.show_viewport for modifier in obj.modifiers):
            continue  # Source indices cannot describe an evaluated modifier surface.
        mesh = obj.data
        try:
            selected = selected_indices(mesh)
        except editing.EditRejected:
            continue  # An unrelated attribute collision must not hide other selections.
        for index in selected:
            edge = mesh.edges[index]
            if not edge.hide:
                positions.extend(tuple(obj.matrix_world @ mesh.vertices[vertex].co) for vertex in edge.vertices)
    return positions


def _clear_overlay_cache(*_unused):
    global _draw_positions
    _draw_positions = ()


def _refresh_overlay_cache(context):
    """Called only with a fresh main-thread desktop context, never from XR draw."""
    global _draw_positions
    _clear_overlay_cache()
    if (context.window is None or context.area is None or context.area.type != "VIEW_3D"
            or context.region is None or context.region.type != "WINDOW"
            or context.scene is None or context.view_layer is None):
        return False
    # The tuple is a complete immutable snapshot of Python numbers. No mesh,
    # object, context or mathutils/RNA object survives this function.
    _draw_positions = tuple(tuple(float(value) for value in point)
                            for point in selected_segments(context))
    context.area.tag_redraw()
    return True


def _overlay_timer():
    if not _draw_handles:
        _clear_overlay_cache()
        return None
    from . import ui
    try:
        # Like controller hover, app timers reacquire a desktop view every tick.
        # XR POST_VIEW invokes Python without a usable bContext; it must never
        # attempt bpy.context scene/view-layer reads, even inside try/except.
        with ui._view3d_override(bpy.context):
            _refresh_overlay_cache(bpy.context)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        _clear_overlay_cache()
    return .05


def _draw(region):
    global _last_draw_error
    positions = _draw_positions
    if not positions:
        return
    previous = None
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        previous = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 3.0)
        shader.uniform_float("color", (0.08, .65, 1.0, .9))
        gpu.state.blend_set("ALPHA")
        # Retained selection stays inspectable through the model, like an X-ray
        # selection overlay. Picking itself always uses the closest visible face.
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        batch_for_shader(shader, "LINES", {"pos": positions}).draw(shader)
        _draw_counts[region] = _draw_counts.get(region, 0) + 1
        _last_draw_error = ""
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if message != _last_draw_error:
            print(f"XR edge overlay ({region}) failed: {message}")
            _last_draw_error = message
    finally:
        if previous is not None:
            try:
                gpu.state.blend_set(previous[0])
                gpu.state.depth_test_set(previous[1])
                gpu.state.depth_mask_set(previous[2])
            except Exception as exc:
                message = f"GPU state restore: {type(exc).__name__}: {exc}"
                if message != _last_draw_error:
                    print(f"XR edge overlay ({region}) failed: {message}")
                    _last_draw_error = message


def start_overlay():
    if bpy.app.background:
        return
    if not _draw_handles:
        _clear_overlay_cache()
    for region in ("WINDOW", "XR"):
        if region not in _draw_handles:
            _draw_handles[region] = bpy.types.SpaceView3D.draw_handler_add(_draw, (region,), region, "POST_VIEW")
    for name in _CACHE_CLEAR_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if _clear_overlay_cache not in handlers:
            handlers.append(_clear_overlay_cache)
    if not bpy.app.timers.is_registered(_overlay_timer):
        bpy.app.timers.register(_overlay_timer, first_interval=.05, persistent=False)


def stop_overlay():
    _clear_overlay_cache()
    if bpy.app.timers.is_registered(_overlay_timer):
        bpy.app.timers.unregister(_overlay_timer)
    for name in _CACHE_CLEAR_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if _clear_overlay_cache in handlers:
            handlers.remove(_clear_overlay_cache)
    for region, handle in tuple(_draw_handles.items()):
        bpy.types.SpaceView3D.draw_handler_remove(handle, region)
        _draw_handles.pop(region)


CLASSES = ()
