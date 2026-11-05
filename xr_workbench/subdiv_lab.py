"""Subdivision Lab: alternate subdivision schemes as real Geometry Nodes groups.

Each scheme is a normal GN modifier on the object, so it stays editable in
plain Blender (Node Garden or the desktop node editor). Groups are tagged
`xrwb_lab = "<KIND>:<VERSION>"`; a tagged group is reused, a user's own group
with the same name is never touched (ours gets a numbered name).

Node and socket lookups go through `_sock`/`_menu`, by name with fallbacks and
never by index (the socket sets differ between Blender 4.5 and 5.x). The shared
group-building helpers here are also used by `growth.py` (Path Array).
"""
from __future__ import annotations

import math

import bpy

from . import editing

VERSION = "0.20.0"
MARKER = "xrwb_lab"
MODIFIER_TYPES = {"MESH"}
DETENT_M = 0.04  # Hand travel (physical metres) per level/step while dialing.
SLOW_FACES = 50_000
# kind: (menu label, one-line help, group name)
SCHEMES = {
    "TWIST": ("Twist Spiral", "Each level twists again: rings spiral as they subdivide", "XR Lab Twist"),
    "HARD": ("Hard Surface", "Sharp edges above a crease angle stay crisp when smoothed", "XR Lab Hard Surface"),
    "ORGANIC": ("Organic", "Smooth, then blur positions for soft, sculpted forms", "XR Lab Organic"),
    "GREEBLE": ("Greeble", "Random extruded panels for sci-fi surface detail", "XR Lab Greeble"),
    "LOOP": ("Triangle Smooth", "Loop-style: approximates Loop weights, not exact", "XR Lab Loop"),
}
# kind: (vertical input, step per detent, min, max, is_int) and (roll input, min, max, step, is_int).
# A max of None means "relative to the object size" (Greeble height).
DIALS = {
    "TWIST": (("Levels", 1, 0, 4, True), ("Twist", -180.0, 180.0, 5.0, False)),
    "HARD": (("Levels", 1, 0, 4, True), ("Crease Angle", 10.0, 90.0, 5.0, False)),
    "ORGANIC": (("Levels", 1, 0, 4, True), ("Softness", 0, 10, 1, True)),
    "GREEBLE": (("Depth", 1, 1, 3, True), ("Height", 0.0, None, None, False)),
    "LOOP": (("Levels", 1, 0, 4, True), None),
    "PATH_ARRAY": (("Spacing", 0.01, 0.01, 100.0, False), ("Twist", -360.0, 360.0, 15.0, False)),
}


# --- version-safe node helpers -------------------------------------------------

def _sock(node, side, *names, type=None):
    """First enabled socket of `node.inputs`/`node.outputs` matching a name or identifier."""
    for name in names:
        for sock in getattr(node, side):
            if sock.enabled and (sock.name == name or sock.identifier == name) \
                    and (type is None or sock.type == type):
                return sock
    raise RuntimeError(f"{node.bl_idname} has no {side[:-1]} {names} on Blender {bpy.app.version_string}")


def _menu(node, input_name, label, prop, prop_value):
    """Set an enum that is a menu input on 5.x and a node property on 4.5."""
    for sock in node.inputs:
        if sock.name == input_name and sock.type == "MENU":
            sock.default_value = label
            return
    setattr(node, prop, prop_value)


class _Graph:
    """Tiny builder: nodes by bl_idname, links that must be valid."""

    def __init__(self, tree):
        self.tree = tree
        self.x = -600

    def node(self, idname, column=None, row=0, **props):
        node = self.tree.nodes.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        if column is not None:
            node.location = (column * 260, row * -220)
        return node

    def link(self, out, inp):
        link = self.tree.links.new(out, inp)
        if not link.is_valid:
            raise RuntimeError(f"Invalid link {out.node.bl_idname}.{out.name} -> {inp.node.bl_idname}.{inp.name} "
                               f"on Blender {bpy.app.version_string}")

    def val(self, node, name, value):
        _sock(node, "inputs", name).default_value = value

    def math(self, op, a=None, b=None, column=None, row=0):
        """ShaderNodeMath; inputs 0/1 are positional (their names repeat)."""
        node = self.node("ShaderNodeMath", column, row, operation=op)
        for index, item in enumerate((a, b)):
            self._feed(node.inputs[index], item)
        return node.outputs[0]

    def vmath(self, op, a=None, b=None, column=None, row=0):
        node = self.node("ShaderNodeVectorMath", column, row, operation=op)
        for index, item in enumerate((a, b)):
            self._feed(node.inputs[index], item)
        return node

    def _feed(self, socket, item):
        if item is None:
            return
        if hasattr(item, "node"):
            self.link(item, socket)
        else:
            socket.default_value = item


_SOCKET_TYPES = {"INT": "NodeSocketInt", "FLOAT": "NodeSocketFloat", "VECTOR": "NodeSocketVector",
                 "BOOL": "NodeSocketBool", "OBJECT": "NodeSocketObject", "GEOMETRY": "NodeSocketGeometry"}


def new_tree(name, kind, inputs):
    """Fresh GN group with Geometry in/out plus `inputs` = [(name, type, default, min, max)]."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree[MARKER] = f"{kind}:{VERSION}"
    if hasattr(tree, "is_modifier"):
        tree.is_modifier = True
    iface = tree.interface
    iface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for label, kind_, default, low, high in inputs:
        item = iface.new_socket(label, in_out="INPUT", socket_type=_SOCKET_TYPES[kind_])
        if kind_ != "OBJECT":
            item.default_value = default
        if low is not None:
            item.min_value = low
        if high is not None:
            item.max_value = high
    graph = _Graph(tree)
    source = graph.node("NodeGroupInput", -1)
    sink = graph.node("NodeGroupOutput", 12)
    return graph, source, sink


def tree_for(kind, base_name, build):
    """Reuse the tagged group, else build one; never overwrite a user's group of the same name."""
    tag = f"{kind}:{VERSION}"
    for tree in bpy.data.node_groups:
        if tree.get(MARKER) == tag and tree.bl_idname == "GeometryNodeTree" and not tree.library:
            return tree
    name, number = base_name, 1
    while name in bpy.data.node_groups:
        number += 1
        name = f"{base_name} {number}"
    tree = None
    try:
        tree = build(name)
    except Exception:
        for stale in [t for t in bpy.data.node_groups if t.name == name and t.get(MARKER) == tag]:
            bpy.data.node_groups.remove(stale)
        raise
    return tree


def input_id(tree, name):
    for item in tree.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == name:
            return item.identifier
    raise RuntimeError(f"{tree.name} has no input {name}")


def lab_modifier(obj):
    """The Lab / Path Array modifier on obj (the active one if it is ours, else the last)."""
    mods = [m for m in obj.modifiers if m.type == "NODES" and m.node_group is not None and MARKER in m.node_group.keys()]
    if not mods:
        return None
    return obj.modifiers.active if obj.modifiers.active in mods else mods[-1]


def kind_of(mod):
    return mod.node_group[MARKER].split(":")[0]


# --- the five schemes ------------------------------------------------------------

def _build_twist(name):
    graph, gi, go = new_tree(name, "TWIST", [("Levels", "INT", 2, 0, 4), ("Twist", "FLOAT", 30.0, -180.0, 180.0),
                                             ("Axis", "VECTOR", (0.0, 0.0, 1.0), None, None)])
    rin = graph.node("GeometryNodeRepeatInput", 1)
    rout = graph.node("GeometryNodeRepeatOutput", 10)
    rin.pair_with_output(rout)
    graph.link(_sock(gi, "outputs", "Geometry"), _sock(rin, "inputs", "Geometry"))
    graph.link(_sock(gi, "outputs", "Levels"), _sock(rin, "inputs", "Iterations"))
    sub = graph.node("GeometryNodeSubdivisionSurface", 3)
    graph.link(_sock(rin, "outputs", "Geometry"), _sock(sub, "inputs", "Mesh"))
    graph.val(sub, "Level", 1)
    box = graph.node("GeometryNodeBoundBox", 4, 2)
    graph.link(_sock(sub, "outputs", "Mesh"), _sock(box, "inputs", "Geometry"))
    axis = graph.vmath("NORMALIZE", _sock(gi, "outputs", "Axis"), column=2, row=3).outputs[0]
    pos = graph.node("GeometryNodeInputPosition", 4, 3).outputs[0]
    here = graph.vmath("DOT_PRODUCT", pos, axis, column=5, row=3).outputs["Value"]
    low = graph.vmath("DOT_PRODUCT", _sock(box, "outputs", "Min"), axis, column=5, row=4).outputs["Value"]
    high = graph.vmath("DOT_PRODUCT", _sock(box, "outputs", "Max"), axis, column=5, row=5).outputs["Value"]
    span = graph.math("MAXIMUM", graph.math("SUBTRACT", high, low, 6, 5), 1.0e-6, 7, 5)  # Flat objects: no divide by 0.
    fraction = graph.math("DIVIDE", graph.math("SUBTRACT", here, low, 6, 3), span, 8, 3)
    angle = graph.math("MULTIPLY", graph.math("RADIANS", _sock(gi, "outputs", "Twist"), column=6, row=2),
                       fraction, 9, 2)
    centre = graph.vmath("SCALE", graph.vmath("ADD", _sock(box, "outputs", "Min"), _sock(box, "outputs", "Max"),
                                              column=6, row=6).outputs[0], column=7, row=6)
    _sock(centre, "inputs", "Scale").default_value = 0.5
    rot = graph.node("ShaderNodeVectorRotate", 9, 4, rotation_type="AXIS_ANGLE")
    graph.link(pos, _sock(rot, "inputs", "Vector"))
    graph.link(centre.outputs[0], _sock(rot, "inputs", "Center"))
    graph.link(axis, _sock(rot, "inputs", "Axis"))
    graph.link(angle, _sock(rot, "inputs", "Angle"))
    setp = graph.node("GeometryNodeSetPosition", 9, 0)
    graph.link(_sock(sub, "outputs", "Mesh"), _sock(setp, "inputs", "Geometry"))
    graph.link(_sock(rot, "outputs", "Vector"), _sock(setp, "inputs", "Position"))
    graph.link(_sock(setp, "outputs", "Geometry"), _sock(rout, "inputs", "Geometry"))
    graph.link(_sock(rout, "outputs", "Geometry"), go.inputs[0])
    return graph.tree


def _build_hard(name):
    graph, gi, go = new_tree(name, "HARD", [("Levels", "INT", 2, 0, 4), ("Crease Angle", "FLOAT", 30.0, 10.0, 90.0)])
    edge = graph.node("GeometryNodeInputMeshEdgeAngle", 1, 2)
    over = graph.node("FunctionNodeCompare", 3, 2, data_type="FLOAT", operation="GREATER_THAN")
    graph.link(_sock(edge, "outputs", "Unsigned Angle"), _sock(over, "inputs", "A", type="VALUE"))
    graph.link(graph.math("RADIANS", _sock(gi, "outputs", "Crease Angle"), column=1, row=3),
               _sock(over, "inputs", "B", type="VALUE"))
    marked = graph.node("GeometryNodeInputNamedAttribute", 1, 4, data_type="BOOLEAN")
    graph.val(marked, "Name", "sharp_edge")
    either = graph.node("FunctionNodeBooleanMath", 5, 3, operation="OR")
    graph.link(_sock(over, "outputs", "Result"), either.inputs[0])
    graph.link(_sock(marked, "outputs", "Attribute"), either.inputs[1])
    sub = graph.node("GeometryNodeSubdivisionSurface", 7)
    graph.link(_sock(gi, "outputs", "Geometry"), _sock(sub, "inputs", "Mesh"))
    graph.link(_sock(gi, "outputs", "Levels"), _sock(sub, "inputs", "Level"))
    graph.link(either.outputs[0], _sock(sub, "inputs", "Edge Crease"))  # True converts to crease 1.
    _menu(sub, "Boundary Smooth", "Keep Corners", "boundary_smooth", "PRESERVE_CORNERS")
    graph.link(_sock(sub, "outputs", "Mesh"), go.inputs[0])
    return graph.tree


def _blur_position(graph, iterations, weight, column, row):
    blur = graph.node("GeometryNodeBlurAttribute", column, row, data_type="FLOAT_VECTOR")
    graph.link(graph.node("GeometryNodeInputPosition", column - 1, row).outputs[0],
               _sock(blur, "inputs", "Value", type="VECTOR"))
    if hasattr(iterations, "node"):
        graph.link(iterations, _sock(blur, "inputs", "Iterations"))
    else:
        graph.val(blur, "Iterations", iterations)
    graph.val(blur, "Weight", weight)
    return _sock(blur, "outputs", "Value", type="VECTOR")


def _build_organic(name):
    graph, gi, go = new_tree(name, "ORGANIC", [("Levels", "INT", 2, 0, 4), ("Softness", "INT", 3, 0, 10),
                                               ("Inflate", "FLOAT", 0.0, None, None)])
    sub = graph.node("GeometryNodeSubdivisionSurface", 1)
    graph.link(_sock(gi, "outputs", "Geometry"), _sock(sub, "inputs", "Mesh"))
    graph.link(_sock(gi, "outputs", "Levels"), _sock(sub, "inputs", "Level"))
    soft = graph.node("GeometryNodeSetPosition", 4)
    graph.link(_sock(sub, "outputs", "Mesh"), _sock(soft, "inputs", "Geometry"))
    graph.link(_blur_position(graph, _sock(gi, "outputs", "Softness"), 0.5, 3, 2), _sock(soft, "inputs", "Position"))
    grow = graph.node("GeometryNodeSetPosition", 7)
    graph.link(_sock(soft, "outputs", "Geometry"), _sock(grow, "inputs", "Geometry"))
    normal = graph.node("GeometryNodeInputNormal", 5, 2).outputs["Normal"]
    push = graph.vmath("SCALE", normal, column=6, row=2)
    graph.link(_sock(gi, "outputs", "Inflate"), _sock(push, "inputs", "Scale"))
    graph.link(push.outputs[0], _sock(grow, "inputs", "Offset"))  # Offsets the shrinkage that blurring causes.
    graph.link(_sock(grow, "outputs", "Geometry"), go.inputs[0])
    return graph.tree


def _build_greeble(name):
    graph, gi, go = new_tree(name, "GREEBLE", [
        ("Depth", "INT", 1, 1, 3), ("Height", "FLOAT", 0.05, 0.0, None), ("Seed", "INT", 0, None, None),
        ("Probability", "FLOAT", 0.5, 0.0, 1.0), ("Min Area", "FLOAT", 0.01, 0.0, None)])
    rin = graph.node("GeometryNodeRepeatInput", 1)
    rout = graph.node("GeometryNodeRepeatOutput", 12)
    rin.pair_with_output(rout)
    graph.link(_sock(gi, "outputs", "Geometry"), _sock(rin, "inputs", "Geometry"))
    graph.link(_sock(gi, "outputs", "Depth"), _sock(rin, "inputs", "Iterations"))
    seed = graph.math("ADD", _sock(gi, "outputs", "Seed"), _sock(rin, "outputs", "Iteration"), 3, 4)  # New dice per pass.
    big = graph.node("FunctionNodeCompare", 4, 2, data_type="FLOAT", operation="GREATER_THAN")
    graph.link(graph.node("GeometryNodeInputMeshFaceArea", 3, 2).outputs["Area"], _sock(big, "inputs", "A", type="VALUE"))
    graph.link(_sock(gi, "outputs", "Min Area"), _sock(big, "inputs", "B", type="VALUE"))
    chance = graph.node("FunctionNodeRandomValue", 4, 3, data_type="BOOLEAN")
    graph.link(_sock(gi, "outputs", "Probability"), _sock(chance, "inputs", "Probability"))
    graph.link(seed, _sock(chance, "inputs", "Seed"))
    pick = graph.node("FunctionNodeBooleanMath", 6, 2, operation="AND")
    graph.link(_sock(big, "outputs", "Result"), pick.inputs[0])
    graph.link(_sock(chance, "outputs", "Value", type="BOOLEAN"), pick.inputs[1])
    tall = graph.node("FunctionNodeRandomValue", 4, 5, data_type="FLOAT")
    graph.val(tall, "Min", 0.3)
    graph.val(tall, "Max", 1.0)
    graph.link(seed, _sock(tall, "inputs", "Seed"))
    lift = graph.math("MULTIPLY", _sock(tall, "outputs", "Value", type="VALUE"), _sock(gi, "outputs", "Height"), 6, 5)
    ext = graph.node("GeometryNodeExtrudeMesh", 8, mode="FACES")
    graph.link(_sock(rin, "outputs", "Geometry"), _sock(ext, "inputs", "Mesh"))
    graph.link(pick.outputs[0], _sock(ext, "inputs", "Selection"))
    graph.link(lift, _sock(ext, "inputs", "Offset Scale"))
    graph.val(ext, "Individual", True)
    shrink = graph.node("FunctionNodeRandomValue", 8, 4, data_type="FLOAT")
    graph.val(shrink, "Min", 0.6)
    graph.val(shrink, "Max", 0.9)
    graph.link(graph.math("ADD", seed, 1, 6, 6), _sock(shrink, "inputs", "Seed"))
    scale = graph.node("GeometryNodeScaleElements", 10, domain="FACE")
    graph.link(_sock(ext, "outputs", "Mesh"), _sock(scale, "inputs", "Geometry"))
    graph.link(_sock(ext, "outputs", "Top"), _sock(scale, "inputs", "Selection"))
    graph.link(_sock(shrink, "outputs", "Value", type="VALUE"), _sock(scale, "inputs", "Scale"))
    graph.link(_sock(scale, "outputs", "Geometry"), _sock(rout, "inputs", "Geometry"))
    graph.link(_sock(rout, "outputs", "Geometry"), go.inputs[0])
    return graph.tree


def _build_loop(name):
    graph, gi, go = new_tree(name, "LOOP", [("Levels", "INT", 2, 0, 4)])
    tri = graph.node("GeometryNodeTriangulate", 1)
    graph.link(_sock(gi, "outputs", "Geometry"), _sock(tri, "inputs", "Mesh"))
    rin = graph.node("GeometryNodeRepeatInput", 2)
    rout = graph.node("GeometryNodeRepeatOutput", 8)
    rin.pair_with_output(rout)
    graph.link(_sock(tri, "outputs", "Mesh"), _sock(rin, "inputs", "Geometry"))
    graph.link(_sock(gi, "outputs", "Levels"), _sock(rin, "inputs", "Iterations"))
    split = graph.node("GeometryNodeSubdivideMesh", 4)
    graph.link(_sock(rin, "outputs", "Geometry"), _sock(split, "inputs", "Mesh"))
    graph.val(split, "Level", 1)
    setp = graph.node("GeometryNodeSetPosition", 6)
    graph.link(_sock(split, "outputs", "Mesh"), _sock(setp, "inputs", "Geometry"))
    graph.link(_blur_position(graph, 1, 0.5, 5, 2), _sock(setp, "inputs", "Position"))
    graph.link(_sock(setp, "outputs", "Geometry"), _sock(rout, "inputs", "Geometry"))
    graph.link(_sock(rout, "outputs", "Geometry"), go.inputs[0])
    return graph.tree


_BUILDERS = {"TWIST": _build_twist, "HARD": _build_hard, "ORGANIC": _build_organic,
             "GREEBLE": _build_greeble, "LOOP": _build_loop}


def build_group(kind):
    """The tagged Lab group for `kind`: reused when present, else built."""
    if kind not in _BUILDERS:
        raise editing.EditRejected("Unknown subdivision scheme")
    return tree_for(kind, SCHEMES[kind][2], _BUILDERS[kind])


# --- adding, dialing, baking -------------------------------------------------------

def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("lab", message, context, level=level)


def _undo(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def _guard(context):
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode for the Subdivision Lab")
    if not editing.undo_available(context):
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Release or cancel the current edit first")


def require_target(obj, allowed=MODIFIER_TYPES, purpose="Subdivision Lab"):
    if obj is None or obj.type not in allowed:
        raise editing.EditRejected(f"{purpose} needs a {'mesh' if allowed == MODIFIER_TYPES else 'curve'} selected")
    if obj.library or obj.override_library or not obj.is_editable:
        raise editing.EditRejected("Linked or read-only objects cannot change modifiers")


def _read(mod, identifier):
    # Blender 5.2 moved modifier inputs from mod[identifier] to mod.properties.inputs[identifier]["value"].
    props = getattr(mod, "properties", None)
    return props.inputs[identifier]["value"] if props is not None else mod[identifier]


def _write(mod, identifier, value):
    props = getattr(mod, "properties", None)
    if props is not None:
        props.inputs[identifier]["value"] = value
    else:
        mod[identifier] = value


def set_inputs(obj, mod, values):
    """Write modifier inputs by interface name; one depsgraph tag."""
    for name, value in values.items():
        _write(mod, input_id(mod.node_group, name), value)
    obj.update_tag()


def get_input(mod, name):
    return _read(mod, input_id(mod.node_group, name))


def edge_warning(obj, kind):
    """A toast line for the cases where the scheme cannot show anything, else ''."""
    if kind == "TWIST" and obj.dimensions.z < 1.0e-6:
        return "Twist needs height along the axis"
    if kind == "GREEBLE" and obj.type == "MESH" and obj.data.polygons:
        if max(p.area for p in obj.data.polygons) <= 0.01:
            return "No face is bigger than Min Area: Greeble changes nothing"
    return ""


def add_scheme(context, kind, obj=None):
    _guard(context)
    obj = obj or context.active_object
    require_target(obj)
    label = SCHEMES[kind][0]
    tree = build_group(kind)
    stacked = any(m.type == "SUBSURF" for m in obj.modifiers)
    mod = obj.modifiers.new(f"Lab {label}", "NODES")
    if mod is None:
        raise editing.EditRejected(f"{label} does not work on a {obj.type.lower()}")
    mod.node_group = tree
    obj.modifiers.active = mod
    _undo(f"XR Lab {label}")
    notes = [edge_warning(obj, kind)]
    if stacked:
        notes.append("It stacks on top of your Subdivision")
    return " ".join([f"{label} added to {obj.name}. Dial it with the Lab Dial tool."] + [n for n in notes if n])


def bake(context):
    _guard(context)
    obj = context.active_object
    require_target(obj)
    mod = lab_modifier(obj)
    if mod is None:
        raise editing.EditRejected("No Lab modifier on this object")
    if obj.data.users > 1:
        raise editing.EditRejected("Shared data: make it single-user before baking")
    name = mod.name
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj],
                               selected_editable_objects=[obj]):
        if "FINISHED" not in bpy.ops.object.modifier_apply(modifier=name):
            raise editing.EditRejected(f"Blender could not apply {name}")
    _undo(f"XR Bake {name}")
    return f"{name} baked: it is now real geometry"


def _detent_value(start, steps, step, low, high, is_int):
    value = min(max(start + steps * step, low), high)
    return int(round(value)) if is_int else value


def _roll(start_rotation, rotation):
    """Signed wrist roll (radians) about the pointing axis (local Z), relative to the start."""
    rel = start_rotation.inverted() @ rotation
    angle = 2.0 * math.atan2(rel.z, rel.w)
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _make_dial_class():  # Lazy: physical_tools imports a lot and must not be needed at import time.
    from . import physical_tools
    from mathutils import Quaternion

    class LabDialStroke(physical_tools._Stroke):
        """Hand up/down = the vertical input (Levels, Spacing); wrist roll = the main value.

        Inputs are written only when a detent is crossed, so re-evaluation stays bounded.
        `pulse` is True after an update that crossed a detent (the integrator fires a detent haptic).
        """

        label = "Lab Dial"

        def __init__(self, context, obj, mod, pose, owner, generation, units):
            super().__init__(context, pose, owner, generation, units)
            self.obj_name, self.mod_name = obj.name, mod.name
            self.kind = kind_of(mod)
            self.vertical, self.roll_spec = DIALS[self.kind]
            self.start_rotation = Quaternion(pose.rotation).normalized()
            self.original = {item.name: _read(mod, item.identifier) for item in mod.node_group.interface.items_tree
                             if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name != "Geometry"}
            self.vstart = get_input(mod, self.vertical[0])
            self.rstart = get_input(mod, self.roll_spec[0]) if self.roll_spec else None
            self.big = obj.type == "MESH" and len(obj.data.polygons) > SLOW_FACES
            self.pulse = False
            self.steps = (0, 0.0)
            self.label = f"Lab {SCHEMES[self.kind][0] if self.kind in SCHEMES else 'Path Array'} Dial"
            diag = max(obj.dimensions) if max(obj.dimensions) > 0 else 1.0
            self.roll_high = self.roll_spec[2] if self.roll_spec and self.roll_spec[2] is not None else max(0.05, 0.25 * diag)
            self.roll_step = self.roll_spec[3] if self.roll_spec and self.roll_spec[3] is not None else self.roll_high / 20.0

        def _target(self):
            obj = bpy.data.objects[self.obj_name]
            return obj, obj.modifiers[self.mod_name]

        def _valid(self, context):
            try:
                self._target()
            except (KeyError, ReferenceError):
                return False
            return super()._valid(context)

        def _restore(self, context):
            obj, mod = self._target()
            set_inputs(obj, mod, self.original)

        def readout(self):
            parts = [f"{self.vertical[0]} {get_input(self._target()[1], self.vertical[0]):g}"]
            if self.roll_spec:
                parts.append(f"{self.roll_spec[0]} {get_input(self._target()[1], self.roll_spec[0]):g}")
            return "  ".join(parts)

        def _apply(self, context, pose):
            obj, mod = self._target()
            name, step, low, high, is_int = self.vertical
            detents = round((pose.position[2] - self.anchor.z) / (DETENT_M * self.units))
            values = {name: _detent_value(self.vstart, detents, step, low, high, is_int)}
            note = ""
            if name == "Levels" and values[name] > 3 and self.big:
                values[name] = 3
                note = " (capped at 3: slow on a big mesh, Bake when done)"
            if self.roll_spec:
                rname, low, high, step, rint = self.roll_spec
                roll = math.degrees(_roll(self.start_rotation, Quaternion(pose.rotation).normalized()))
                span = (self.roll_high - low)
                steps = round(roll / 90.0 * (span / 2.0) / self.roll_step)
                values[rname] = _detent_value(self.rstart, steps, self.roll_step, low, self.roll_high, rint)
            current = {k: get_input(mod, k) for k in values}
            self.pulse = any(abs(current[k] - values[k]) > 1.0e-9 for k in values)
            if self.pulse:
                set_inputs(obj, mod, values)
                self.changed = True
            return self.readout() + note

        def commit_message(self):
            return f"{self.label}: {self.readout()}"

    return LabDialStroke


_DIAL_CLASS = None


def begin(context, pose, owner, generation):
    """Start the Lab Dial stroke on the active object's Lab modifier (tool `lab_dial`)."""
    global _DIAL_CLASS
    from . import viewer_scale
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode for the Lab Dial")
    obj = context.active_object
    if obj is None or obj.type not in {"MESH", "CURVE"}:
        raise editing.EditRejected("Select an object with a Lab modifier")
    mod = lab_modifier(obj)
    if mod is None:
        raise editing.EditRejected("Add a Lab scheme first (Sculpt > Lab)")
    if editing.active_transaction() is not None:
        raise editing.EditRejected("Another edit owns capture")
    if _DIAL_CLASS is None:
        _DIAL_CLASS = _make_dial_class()
    stroke = _DIAL_CLASS(context, obj, mod, pose, owner, generation, viewer_scale.world_units_per_metre(context))
    editing._ACTIVE_TRANSACTION = stroke
    return stroke


def active_dial_modifiers(context=None):
    """(object name, modifier name) of the running dial, so Live Cage can leave it visible."""
    stroke = editing.active_transaction()
    if stroke is not None and hasattr(stroke, "mod_name") and hasattr(stroke, "vertical"):
        return {(stroke.obj_name, stroke.mod_name)}
    return set()


def _run(operator, context, action):
    try:
        message = action()
    except (editing.EditRejected, RuntimeError, ValueError, TypeError, KeyError, ReferenceError) as exc:
        _log(context, str(exc), "WARNING")
        operator.report({"WARNING"}, str(exc))
        return {"CANCELLED"}
    _log(context, message)
    return {"FINISHED"}


class XRWB_OT_lab_add(bpy.types.Operator):
    bl_idname = "xrwb.lab_add"
    bl_label = "Add Lab Scheme"
    bl_description = "Add a subdivision scheme as an editable Geometry Nodes modifier; one undo step"
    kind: bpy.props.EnumProperty(items=[(k, v[0], v[1]) for k, v in SCHEMES.items()], options={"SKIP_SAVE"})

    def execute(self, context):
        return _run(self, context, lambda: add_scheme(context, self.kind))


class XRWB_OT_lab_bake(bpy.types.Operator):
    bl_idname = "xrwb.lab_bake"
    bl_label = "Bake Lab"
    bl_description = "Apply the Lab modifier into real geometry; one undo step"

    def execute(self, context):
        return _run(self, context, lambda: bake(context))


class XRWB_MT_lab(bpy.types.Menu):
    bl_idname = "XRWB_MT_lab"
    bl_label = "Subdivision Lab"

    def draw(self, context):
        layout = self.layout
        layout.label(text="SUBDIVISION LAB (EDITABLE NODES)", icon="MOD_SUBSURF")
        for kind, (label, _help, _name) in SCHEMES.items():
            layout.operator("xrwb.lab_add", text=label).kind = kind
        layout.operator("xrwb.lab_bake", text="Bake", icon="CHECKMARK")


CLASSES = (XRWB_OT_lab_add, XRWB_OT_lab_bake, XRWB_MT_lab)
