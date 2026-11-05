"""Guided Growth: branches, vines and spirals you plant and pull, plus Path Array.

The algorithm builds the structure; you choose where it starts, which way it
goes and how far. Results are real POLY Curve objects ("Growth") with an
editable Geometry Nodes skin ("XR Growth Skin"), or a GN Path Array modifier.

The generators at the top are pure Python (no bpy): `random.Random(seed)`,
tuples in, `[(points, radii), ...]` out. Everything that touches Blender sits
under `if bpy is not None:` so unit tests can load this file by path.
"""
from __future__ import annotations

import math
import random

try:
    import bpy
except ImportError:  # Unit tests load this file without Blender.
    bpy = None

MAX_POINTS = {"BRANCH": 2000, "SPIRAL": 2000, "VINE": 600}
PREVIEW_POINTS = 400
STEPS = 60  # Steps along a full-length polyline; same for preview and result so the preview is honest.
SPLIT_ANGLE = 0.6  # Radians between a child and the parent direction.


# --- tiny vector helpers (tuples) ------------------------------------------------

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a, fallback=(0.0, 0.0, 1.0)):
    length = math.sqrt(_dot(a, a))
    return _mul(a, 1.0 / length) if length > 1.0e-12 else fallback


def _rotate(v, axis, angle):
    """Rodrigues rotation of v about a unit axis."""
    c, s = math.cos(angle), math.sin(angle)
    return _add(_add(_mul(v, c), _mul(_cross(axis, v), s)), _mul(axis, _dot(axis, v) * (1.0 - c)))


def _perp(d, rng):
    """A random unit vector perpendicular to the unit vector d."""
    helper = (1.0, 0.0, 0.0) if abs(d[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _norm(_cross(d, helper))
    return _rotate(u, d, rng.uniform(0.0, 2.0 * math.pi))


def _basis(d):
    helper = (1.0, 0.0, 0.0) if abs(d[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _norm(_cross(d, helper))
    return u, _cross(d, u)


# --- generators ----------------------------------------------------------------

def grow(origin, direction, length, curl=0.0, seed=0, radius=0.01, project=None, max_points=2000,
         split=0.05, min_radius=None, steps=STEPS):
    """Branch (project=None) or Vine (project(point) -> (surface_point, unit_normal)).

    curl 0..1 bends each step by up to curl * 0.25 rad; splits have probability `split` per step, the
    parent ends there and two children continue at +-SPLIT_ANGLE with radius * 0.7. Returns
    [(points, radii)] with at most `max_points` points in total.
    """
    rng = random.Random(seed)
    d = _norm(direction)
    step = max(length, 1.0e-9) / steps
    min_radius = radius * 0.15 if min_radius is None else min_radius
    taper = 0.5 ** (1.0 / steps)  # A long run ends at half its starting radius.
    normal = (0.0, 0.0, 1.0)
    pos = tuple(origin)
    if project is not None:
        pos, normal = project(pos)
        d = _norm(_sub(d, _mul(normal, _dot(d, normal))), fallback=_perp(normal, rng))

    def emit(p, n, r):
        return _add(p, _mul(n, r)) if project is not None else p

    lines, total = [], 0
    tips = [(pos, d, radius, steps, normal)]
    while tips and total < max_points:
        pos, d, r, left, n = tips.pop()
        points, radii = [emit(pos, n, r)], [r]
        while left > 0 and r >= min_radius and total + len(points) < max_points:
            axis = n if project is not None else _perp(d, rng)
            d = _rotate(d, axis, rng.uniform(-1.0, 1.0) * curl * 0.25)
            moved = _add(pos, _mul(d, step))
            if project is not None:
                moved, n = project(moved)
                real = _sub(moved, pos)
                d = _norm(real, fallback=d)
            pos = moved
            r *= taper
            left -= 1
            points.append(emit(pos, n, r))
            radii.append(r)
            if left > 3 and rng.random() < split:
                axis = n if project is not None else _perp(d, rng)
                for sign in (1.0, -1.0):
                    tips.append((pos, _rotate(d, axis, sign * SPLIT_ANGLE), r * 0.7, int(left * 0.75), n))
                break
        lines.append((points, radii))
        total += len(points)
    return lines


def spiral(origin, direction, length, turns, size, thickness, taper=0.0, shell=False):
    """Helix around `direction`; the last point is exactly `length` along it.

    shell=True grows the helix radius geometrically per turn (a spiral shell).
    """
    d = _norm(direction)
    u, v = _basis(d)
    count = max(8, min(int(abs(turns) * 24), MAX_POINTS["SPIRAL"] - 1))
    points, radii = [], []
    for i in range(count + 1):
        t = i / count
        angle = 2.0 * math.pi * turns * t
        r = size * 1.35 ** (abs(turns) * t) if shell else size * (1.0 - taper * t)
        offset = _add(_mul(u, r * math.cos(angle)), _mul(v, r * math.sin(angle)))
        points.append(_add(_add(origin, _mul(d, length * t)), offset))
        radii.append(thickness * (1.0 - 0.8 * taper * t))
    return [(points, radii)]


def generate(kind, origin, direction, length, curl, turns, seed, radius, project=None, preview=False):
    """One entry point for the stroke: kind is BRANCH, VINE or SPIRAL."""
    if kind == "SPIRAL":
        return spiral(origin, direction, length, turns, size=max(radius * 3.0, length * 0.1),
                      thickness=radius, taper=min(curl, 1.0))
    cap = PREVIEW_POINTS if preview else MAX_POINTS[kind]
    return grow(origin, direction, length, curl=curl, seed=seed, radius=radius,
                project=project if kind == "VINE" else None, max_points=cap)


if bpy is not None:
    from mathutils import Quaternion, Vector

    from . import editing, physical_tools, subdiv_lab, viewer_scale
    from .interaction import Decision

    GROWTH_KEY = "xrwb_growth"
    SKIN_NAME = "XR Growth Skin"
    KINDS = {"BRANCH": "Branch", "VINE": "Vine", "SPIRAL": "Spiral"}
    MIN_PULL_M = 0.03
    DETENT_PULL_M = 0.10  # A `detent` haptic per 10 cm of length.
    REACH_M = 1.0  # A seed plants on a surface only if one is this close to the controller.
    # Per-session choices; the integrator may move these into XRWB_PG_settings.
    STATE = {"kind": "BRANCH", "seed": 1}

    def _log(context, message, level="INFO"):
        from . import interaction_log
        interaction_log.emit("growth", message, context, level=level)

    def _undo(message):
        if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
            raise RuntimeError("Blender rejected the undo entry")

    def skin_group():
        def build(name):
            graph, gi, go = subdiv_lab.new_tree(name, "SKIN", [("Thickness", "FLOAT", 1.0, 0.0, None),
                                                               ("Resolution", "INT", 8, 3, 32)])
            circle = graph.node("GeometryNodeCurvePrimitiveCircle", 1, 2)
            graph.link(subdiv_lab._sock(gi, "outputs", "Resolution"), subdiv_lab._sock(circle, "inputs", "Resolution"))
            graph.val(circle, "Radius", 1.0)
            mesh = graph.node("GeometryNodeCurveToMesh", 3)
            graph.link(subdiv_lab._sock(gi, "outputs", "Geometry"), subdiv_lab._sock(mesh, "inputs", "Curve"))
            graph.link(subdiv_lab._sock(circle, "outputs", "Curve"), subdiv_lab._sock(mesh, "inputs", "Profile Curve"))
            graph.link(subdiv_lab._sock(gi, "outputs", "Thickness"), subdiv_lab._sock(mesh, "inputs", "Scale"))
            graph.val(mesh, "Fill Caps", True)  # Point radii scale the profile on their own.
            smooth = graph.node("GeometryNodeSetShadeSmooth", 5)
            graph.link(subdiv_lab._sock(mesh, "outputs", "Mesh"), subdiv_lab._sock(smooth, "inputs", "Geometry", "Mesh"))
            graph.val(smooth, "Shade Smooth", True)
            graph.link(subdiv_lab._sock(smooth, "outputs", "Geometry", "Mesh"), go.inputs[0])
            return graph.tree
        return subdiv_lab.tree_for("SKIN", SKIN_NAME, build)

    def _surface(obj):
        """project(world_point) -> (world_point, world_normal) on obj's mesh."""
        world = obj.matrix_world
        inverse = world.inverted()
        rotation = world.to_3x3()  # ponytail: assumes uniform scale for normals.

        def project(point):
            ok, location, normal, _index = obj.closest_point_on_mesh(inverse @ Vector(point))
            if not ok:
                raise editing.EditRejected("Vines need a surface")
            return tuple(world @ location), tuple((rotation @ normal).normalized())
        return project

    class GrowStroke(physical_tools._Stroke):
        """Plant a seed, pull for direction and length, roll the wrist to curl. Nothing exists until release."""

        label = "Grow"

        def __init__(self, context, pose, owner, generation, units, kind, seed, contact=None):
            super().__init__(context, pose, owner, generation, units)
            self.contact_mode = contact is not None
            self.kind, self.seed, self.units = kind, seed, units
            self.label = f"Grow {KINDS[kind]}"
            self.start_rotation = Quaternion(pose.rotation).normalized()
            origin, direction, _rotation = physical_tools._pose_ray(pose)
            touched = bpy.data.objects.get(contact.obj_name) if contact is not None else None
            if touched is not None:  # Touching: plant the seed on the contact point, growing out along its normal.
                hit, location, normal, evaluated = True, Vector(contact.point), Vector(contact.normal), touched
            else:
                context.view_layer.update()
                hit, location, normal, _index, evaluated, _matrix = context.scene.ray_cast(
                    context.evaluated_depsgraph_get(), origin, direction, distance=REACH_M * units)
            self.target = evaluated.original if hit and evaluated is not None else None
            if kind == "VINE":
                if self.target is None or self.target.type != "MESH":
                    raise editing.EditRejected("Vines need a surface")
                if self.target.library or self.target.override_library:
                    raise editing.EditRejected("Vines cannot grow on linked objects")
                self.project = _surface(self.target)
            else:
                self.project = None
            self.seed_point = tuple(location) if hit else tuple(pose.position)
            self.normal = tuple(normal) if hit else None
            self.selection = editing._selection_snapshot(context)
            self.lines, self.pulse, self.detents, self.segments = [], False, 0, 0
            self.created = None  # ("NEW", object name) or ("EXTEND", object name, spline count before)
            self._pull, self._roll = Vector(), 0.0

        # -- preview ----------------------------------------------------------
        def _recompute(self, preview=True):
            length = self._pull.length
            if length < MIN_PULL_M * self.units:
                return []
            pull = Vector(self._pull)
            if self.normal is not None and self.kind != "VINE":
                # ponytail: the pull is reflected out of the surface so a branch always grows away from it; the
                # contact normal is not blended in as a true initial tangent.
                outward = pull.dot(Vector(self.normal))
                if outward < 0.0:
                    pull -= 2.0 * outward * Vector(self.normal)
            direction = tuple(pull / length)
            roll = min(abs(self._roll) / (math.pi / 2.0), 1.0)
            turns = 1.0 + round(roll * 10.0) / 2.0  # 1..6 turns in half-turn steps.
            return generate(self.kind, self.seed_point, direction, length, roll, turns, self.seed,
                            radius=0.012 * self.units, project=self.project, preview=preview)

        def _apply(self, context, pose):
            self._pull = Vector(pose.position) - self.anchor
            rel = self.start_rotation.inverted() @ Quaternion(pose.rotation).normalized()
            self._roll = (2.0 * math.atan2(rel.z, rel.w) + math.pi) % (2.0 * math.pi) - math.pi
            detents = int(self._pull.length / (DETENT_PULL_M * self.units))
            self.pulse, self.detents = detents != self.detents, detents
            self.lines = self._recompute()
            self.changed = bool(self.lines)
            count = sum(len(points) for points, _radii in self.lines)
            if count > self.segments:  # A tick and a soft click for every new segment.
                self._fx("detent", 0.5)
                self._fx("click", 0.5)
            self.segments = count
            return f"{KINDS[self.kind]}: {self._pull.length / self.units * 100:.0f} cm, release to grow"

        def reroll(self):
            """Right A while holding: new random shape, same pull. Not an undo step."""
            self.seed += 1
            STATE["seed"] = self.seed
            self.pulse = True
            self.lines = self._recompute()
            self.changed = bool(self.lines)

        def preview_points(self):
            """Plain cached data for the XR draw callback: a list of point lists."""
            return [points for points, _radii in self.lines]

        # -- result -------------------------------------------------------------
        def commit(self, context, owner=None):
            if (self.changed and editing.active_transaction() is self and not self.finished
                    and self.capture.can_finish(owner or self.request.owner).decision is Decision.APPLY
                    and self._valid(context)):
                try:
                    self.lines = self._recompute(preview=False)
                    if not self.lines:
                        raise editing.EditRejected("Nothing to grow")
                    self._create(context)
                except (editing.EditRejected, RuntimeError, ReferenceError, ValueError) as exc:
                    self._restore(context)
                    self.changed = False
                    _log(context, f"Grow failed: {exc}", "WARNING")
            return super().commit(context, owner)

        def _create(self, context):
            active = context.view_layer.objects.active
            if (active is not None and active.type == "CURVE" and GROWTH_KEY in active.keys()
                    and not active.library and not active.override_library):
                obj = active
                self.created = ("EXTEND", obj.name, len(obj.data.splines))
            else:
                collection = context.collection
                if collection is None or not collection.is_editable or collection.library:
                    raise editing.EditRejected("Choose a local editable collection for the growth")
                curve = bpy.data.curves.new("Growth", "CURVE")
                curve.dimensions = "3D"
                obj = bpy.data.objects.new("Growth", curve)
                obj[GROWTH_KEY] = 1
                collection.objects.link(obj)
                self.created = ("NEW", obj.name, 0)
                mod = obj.modifiers.new(SKIN_NAME, "NODES")
                mod.node_group = skin_group()
                for other in tuple(context.selected_objects):
                    other.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
            inverse = obj.matrix_world.inverted()
            for points, radii in self.lines:
                spline = obj.data.splines.new("POLY")
                spline.points.add(len(points) - 1)
                flat = []
                for point in points:
                    local = inverse @ Vector(point)
                    flat.extend((local.x, local.y, local.z, 1.0))
                spline.points.foreach_set("co", flat)
                spline.points.foreach_set("radius", radii)

        def _restore(self, context):
            if self.created is None:
                return
            mode, name, before = self.created
            self.created = None
            obj = bpy.data.objects.get(name)
            if obj is None:
                return
            if mode == "NEW":
                curve = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if curve.users == 0:
                    bpy.data.curves.remove(curve)
                editing._restore_object_selection(context, self.selection)
            else:
                while len(obj.data.splines) > before:
                    obj.data.splines.remove(obj.data.splines[-1])

        def commit_message(self):
            return f"{self.label} grown as one undo step"

    def begin(context, pose, owner, generation, contact=None):
        """Start a Grow stroke (tool `grow_seed`) with the kind/seed in `STATE`."""
        if context.mode != "OBJECT":
            raise editing.EditRejected("Switch to Object Mode to grow")
        if editing.active_transaction() is not None:
            raise editing.EditRejected("Another edit owns capture")
        if not editing.undo_available(context) or not bpy.ops.ed.undo_push.poll():
            raise editing.EditRejected("Enable Global Undo and at least one undo step")
        stroke = GrowStroke(context, pose, owner, generation, viewer_scale.world_units_per_metre(context),
                            STATE["kind"], STATE["seed"], contact=contact)
        editing._ACTIVE_TRANSACTION = stroke
        return stroke

    # --- Path Array ----------------------------------------------------------

    def _build_path_array(name):
        graph, gi, go = subdiv_lab.new_tree(name, "PATH_ARRAY", [
            ("Source", "OBJECT", None, None, None), ("Spacing", "FLOAT", 0.1, 0.001, None),
            ("Twist", "FLOAT", 0.0, -360.0, 360.0)])
        sock = subdiv_lab._sock
        resample = graph.node("GeometryNodeResampleCurve", 1)
        subdiv_lab._menu(resample, "Mode", "Length", "mode", "LENGTH")
        graph.link(sock(gi, "outputs", "Geometry"), sock(resample, "inputs", "Curve"))
        graph.link(sock(gi, "outputs", "Spacing"), sock(resample, "inputs", "Length"))
        info = graph.node("GeometryNodeObjectInfo", 1, 3)
        graph.link(sock(gi, "outputs", "Source"), sock(info, "inputs", "Object"))
        graph.val(info, "As Instance", True)
        tangent = graph.node("GeometryNodeInputTangent", 2, 5).outputs[0]
        align = graph.node("FunctionNodeAlignRotationToVector", 4, 4, axis="X")
        graph.link(tangent, sock(align, "inputs", "Vector"))
        along = graph.node("GeometryNodeSplineParameter", 2, 6).outputs["Factor"]
        angle = graph.math("MULTIPLY", graph.math("RADIANS", sock(gi, "outputs", "Twist"), column=3, row=6), along, 4, 6)
        spin = graph.node("FunctionNodeAxisAngleToRotation", 5, 6)
        graph.link(tangent, sock(spin, "inputs", "Axis"))
        graph.link(angle, sock(spin, "inputs", "Angle"))
        turn = graph.node("FunctionNodeRotateRotation", 7, 5)
        graph.link(sock(align, "outputs", "Rotation"), sock(turn, "inputs", "Rotation"))
        graph.link(sock(spin, "outputs", "Rotation"), sock(turn, "inputs", "Rotate By"))
        copies = graph.node("GeometryNodeInstanceOnPoints", 9)
        graph.link(sock(resample, "outputs", "Curve"), sock(copies, "inputs", "Points"))
        graph.link(sock(info, "outputs", "Geometry"), sock(copies, "inputs", "Instance"))
        graph.link(sock(turn, "outputs", "Rotation"), sock(copies, "inputs", "Rotation"))
        graph.link(sock(copies, "outputs", "Instances"), go.inputs[0])  # Instances stay unrealized: light.
        return graph.tree

    def path_array(context):
        """Selected object repeats along the active curve. One undo step."""
        subdiv_lab._guard(context)
        curve = context.active_object
        subdiv_lab.require_target(curve, {"CURVE"}, "Array Along Path")
        sources = [o for o in context.selected_objects if o is not curve and o.type in {"MESH", "CURVE", "FONT"}]
        if not sources:
            raise editing.EditRejected("Select the object to repeat, then the curve, then Array Along Path")
        tree = subdiv_lab.tree_for("PATH_ARRAY", "XR Path Array", _build_path_array)
        mod = curve.modifiers.new("Path Array", "NODES")
        mod.node_group = tree
        subdiv_lab.set_inputs(curve, mod, {"Source": sources[0]})
        curve.modifiers.active = mod
        _undo("XR Array Along Path")
        return f"{sources[0].name} repeats along {curve.name}. Dial spacing and twist with the Lab Dial tool."

    def _run(operator, context, action):
        try:
            message = action()
        except (editing.EditRejected, RuntimeError, ValueError, TypeError, KeyError, ReferenceError) as exc:
            _log(context, str(exc), "WARNING")
            operator.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        _log(context, message)
        return {"FINISHED"}

    class XRWB_OT_path_array(bpy.types.Operator):
        bl_idname = "xrwb.path_array"
        bl_label = "Array Along Path"
        bl_description = "Repeat the selected object along the active curve (editable Geometry Nodes); one undo step"

        def execute(self, context):
            return _run(self, context, lambda: path_array(context))

    class XRWB_OT_grow_kind(bpy.types.Operator):
        bl_idname = "xrwb.grow_kind"
        bl_label = "Growth Kind"
        bl_description = "Choose what the Grow tool plants"
        kind: bpy.props.EnumProperty(items=[(k, v, "") for k, v in KINDS.items()], options={"SKIP_SAVE"})

        def execute(self, context):
            STATE["kind"] = self.kind
            try:
                context.window_manager.xrwb_settings.edit_tool = "grow_seed"
            except (TypeError, AttributeError):  # ponytail: tool id not registered in ui.py yet.
                pass
            _log(context, f"Grow plants a {KINDS[self.kind]}: pull to set direction and length")
            return {"FINISHED"}

    class XRWB_MT_growth(bpy.types.Menu):
        bl_idname = "XRWB_MT_growth"
        bl_label = "Guided Growth"

        def draw(self, context):
            layout = self.layout
            layout.label(text="GUIDED GROWTH", icon="OUTLINER_OB_CURVE")
            for kind, label in KINDS.items():
                layout.operator("xrwb.grow_kind", text=label).kind = kind
            layout.operator("xrwb.path_array", text="Array Along Path", icon="MOD_ARRAY")

    CLASSES = (XRWB_OT_path_array, XRWB_OT_grow_kind, XRWB_MT_growth)
