"""Draw in the air and paint colour: the 3D Pen and Paint Colour tools.

* 3D Pen: hold the trigger and draw. The stroke follows the pen tip (the prop's nib
  with Tool Props on, else 20 cm in front of the controller) and is shown live;
  touching a surface draws on it; release makes a real Blender
  curve with a round tube profile (bevel), smoothed with automatic Bezier
  handles. A harder squeeze makes the tube thicker at that point. The curve
  stays a curve, so it can be edited, converted to a mesh or used for
  modifiers later.
* Paint Colour: hold the trigger and sweep over a mesh to paint the chosen
  colour into a colour attribute ("XR Paint"), softly, with a falloff. A
  harder squeeze paints wider and stronger. Cancel restores the colours
  exactly; release is one undo step.
"""
from __future__ import annotations

import bpy
import numpy as np
from mathutils import Vector

from . import editing, physical_tools
from .physical_tools import _pose_ray, _Stroke, require_mesh_target

PEN_TIP_FAR_M = 0.2  # Without a prop in the hand the pen tip floats 20 cm ahead of the controller.
PEN_SPACING_M = 0.008
PAINT_ATTRIBUTE = "XR Paint"
PAINT_RATE = 0.35


def pen_tip_m() -> float:
    """Metres from the aim origin to the ink: 0 with Tool Props on (the prop's nib is the aim origin)."""
    try:
        from . import onboarding
        return 0.0 if onboarding.prefs().get("tool_props", True) else PEN_TIP_FAR_M
    except Exception:
        return PEN_TIP_FAR_M


def __getattr__(name):  # creative_tools.PEN_TIP_M stays readable for the overlay and old smokes.
    if name == "PEN_TIP_M":
        return pen_tip_m()
    raise AttributeError(name)


class CurvePenStroke(_Stroke):
    label = "3D Pen"
    needs_undo = True

    def __init__(self, context, pose, owner, generation, units, settings, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        self.radius = max(1.0e-5, float(settings.pen_radius_m)) * units
        self.pressure = 1.0
        self.points = [self._tip(pose)]
        self.pressures = [1.0]
        self.lines = []
        self.obj = None
        self.material_name = str(settings.spray_material)

    def _tip(self, pose):
        """The nib. Touching a surface, the ink lies on it (the contact point lifted by the tube radius)."""
        found = physical_tools.current_contact(self.hand) if self.contact_mode else None
        if found is not None:
            return Vector(found.point) + Vector(found.normal).normalized() * self.radius
        origin, direction, _rotation = _pose_ray(pose)
        return origin + direction * pen_tip_m() * self.units

    def _restore(self, context):
        pass  # Nothing exists until release.

    def _apply(self, context, pose):
        tip = self._tip(pose)
        if (tip - self.points[-1]).length >= PEN_SPACING_M * self.units:
            self.points.append(tip)
            self.pressures.append(max(0.15, float(self.pressure)))
            a, b = self.points[-2], self.points[-1]
            self.lines += [tuple(a), tuple(b)]
            self.changed = len(self.points) >= 2
        return f"Pen: {len(self.points)} points"

    def length(self):
        return sum((b - a).length for a, b in zip(self.points, self.points[1:]))

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if (editing.active_transaction() is self and not self.finished and self.changed
                and self._valid(context)):
            try:
                self.obj = build_curve(context, self.points, self.pressures, self.radius,
                                       bpy.data.materials.get(self.material_name))
            except (RuntimeError, ValueError) as exc:
                self._finish(physical_tools.CapturePhase.CANCELLED)
                return physical_tools.EditResult(physical_tools.Decision.REJECTED, f"3D Pen failed: {exc}")
        return super().commit(context, owner)

    def commit_message(self):
        from . import polish
        return f"Drew a {polish.length(self.length())} curve ({len(self.points)} points); one undo"


def build_curve(context, points, pressures, radius, material=None):
    """A smooth bevelled Bezier curve through ``points``; selected and active."""
    if len(points) < 2:
        raise ValueError("A stroke needs at least two points")
    centre = sum(points, Vector()) / len(points)
    curve = bpy.data.curves.new("XR Pen Stroke", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 3
    curve.use_fill_caps = True
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for point, co, pressure in zip(spline.bezier_points, points, pressures):
        point.co = co - centre
        point.handle_left_type = point.handle_right_type = "AUTO"
        point.radius = pressure
    if material is not None:
        curve.materials.append(material)
    obj = bpy.data.objects.new("XR Pen Stroke", curve)
    obj.location = centre
    context.collection.objects.link(obj)
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


class VertexPaintStroke(_Stroke):
    label = "Paint Colour"
    needs_undo = True

    def __init__(self, context, pose, owner, generation, units, settings, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        self.colour = np.array(tuple(settings.paint_colour), dtype=np.float32)
        self.radius = max(1.0e-6, float(settings.spray_radius_m) * units)
        self.pressure = 1.0
        self.targets = {}
        self.last_obj = None

    def _record(self, context, obj):
        key = editing._safe_pointer(obj)
        if key in self.targets:
            return self.targets[key]
        try:
            require_mesh_target(context, obj, "Paint")
        except editing.EditRejected:
            self.targets[key] = None
            return None
        if any(m.show_viewport for m in obj.modifiers):
            self.targets[key] = None
            return None
        mesh = obj.data
        attribute = mesh.color_attributes.get(PAINT_ATTRIBUTE)
        created = attribute is None
        if created:
            attribute = mesh.color_attributes.new(PAINT_ATTRIBUTE, "FLOAT_COLOR", "POINT")
            attribute.data.foreach_set("color", np.ones(len(mesh.vertices) * 4, dtype=np.float32))
        if attribute.domain != "POINT" or attribute.data_type != "FLOAT_COLOR":
            self.targets[key] = None
            return None
        colours = np.empty(len(mesh.vertices) * 4, dtype=np.float32)
        attribute.data.foreach_get("color", colours)
        if not len(mesh.vertices):
            self.targets[key] = None
            return None
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", positions)
        world = physical_tools._matrix(obj)
        positions = positions.reshape(-1, 3).astype(np.float64) @ world[:3, :3].T + world[:3, 3]
        previous_active = mesh.color_attributes.active_color_name
        mesh.color_attributes.active_color = attribute
        record = {"obj": obj, "mesh": mesh, "created": created, "original": colours.copy(),
                  "current": colours.reshape(-1, 4), "positions": positions, "active": previous_active,
                  "chunks": physical_tools.sculpt_math.Chunks(positions)}
        self.targets[key] = record
        return record

    def _valid(self, context):
        if not super()._valid(context):
            return False
        try:
            return all(record is None or len(record["mesh"].vertices) == len(record["positions"])
                       for record in self.targets.values())
        except ReferenceError:
            return False

    def _restore(self, context):
        for record in self.targets.values():
            if not record:
                continue
            mesh = record["mesh"]
            attribute = mesh.color_attributes.get(PAINT_ATTRIBUTE)
            if record["created"]:
                if attribute is not None:
                    mesh.color_attributes.remove(attribute)
            elif attribute is not None:
                attribute.data.foreach_set("color", record["original"])
            if record["active"] and record["active"] in mesh.color_attributes:
                mesh.color_attributes.active_color_name = record["active"]
            mesh.update()

    def dab(self, record, point):
        """Blend the colour into the vertices under the brush. Chunked: only the chunks near `point` are read,
        as contiguous slices. Returns the vertex count painted (0 = between vertices)."""
        radius = self.radius * (0.5 + 0.5 * self.pressure)
        point = np.asarray(point, dtype=np.float64)
        current, total = record["current"], 0
        for start, stop in record["chunks"].spans(point, radius):
            offset = record["positions"][start:stop] - point
            squared = np.einsum("ij,ij->i", offset, offset)
            near = np.flatnonzero(squared < radius * radius)
            if not near.size:
                continue
            falloff = (1.0 - squared[near] / (radius * radius)) * PAINT_RATE * (0.4 + 0.6 * self.pressure)
            ids = near + start
            current[ids] += (self.colour - current[ids]) * falloff[:, None]
            total += int(ids.size)
        return total

    def _apply(self, context, pose):
        aim = physical_tools.aim_hit(context, pose, self.hand, self.contact_mode, last=self.last_obj)
        if aim is None:
            return "Aim at a mesh to paint"
        obj, location, normal, _index = aim
        record = self._record(context, obj)
        if record is None:
            return f"{obj.name} cannot take paint (linked, shared or with modifiers)"
        self.last_obj = obj
        count = self.dab(record, location)
        if not count:
            return "Move closer: the brush is between vertices"
        mesh = record["mesh"]
        mesh.color_attributes[PAINT_ATTRIBUTE].data.foreach_set("color", record["current"].ravel())
        mesh.update()
        self.changed = True
        return f"Painting {count} vertices"

    def commit_message(self):
        return "Paint committed as one undo step (colour attribute \"XR Paint\")"


def begin(context, tool, pose, owner, generation, units, settings, contact=None):
    if tool == "draw_curve":
        return CurvePenStroke(context, pose, owner, generation, units, settings, contact=contact)
    transaction = VertexPaintStroke(context, pose, owner, generation, units, settings, contact=contact)
    editing._ACTIVE_TRANSACTION = transaction
    transaction._apply(context, pose)  # Paint right away under the brush.
    _show_paint(context)
    return transaction


def _show_paint(context):
    """Headset shading that shows colour attributes (Solid, Attribute colour)."""
    try:
        shading = context.window_manager.xr_session_settings.shading
        shading.type = "SOLID"
        shading.color_type = "VERTEX"
    except (AttributeError, TypeError):
        pass


CLASSES = ()
