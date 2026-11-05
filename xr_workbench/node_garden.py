"""Node Garden: a real node tree as physical blocks you can handle.

The tree is a material's shader nodes, or the Geometry Nodes tree behind a
modifier: rewiring a procedural modifier updates the object live, because it
is Blender's own tree. Each node becomes a block standing in front of you, laid out exactly
like the node editor (1 mm per editor unit, scaled with your size). Output
sockets are spheres on a block's right edge, inputs on its left. Links are
drawn as lines between them by the ray overlay.

* Grab a block (grip) to move its node; the node editor follows, and a node
  moved on the desktop moves its block.
* Pull the trigger near a socket and drag to another to link them. Grabbing a
  linked input picks its link up: drop it on another input to move it, or in
  empty space to remove it. Each change is one undo step.
* Trigger a block with Select to make its node active; the hand menu then
  shows its values, and Add Node puts new nodes beside it.

The blocks are only a view of the tree, owned by one collection. Putting the
garden away deletes just those objects; the material or node group is never
touched except by the edits you make.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Vector

from . import editing, physical_tools, viewer_scale
from .interaction import CapturePhase, Decision, EditResult

COLLECTION = "XR Node Garden"
ROOT_KEY = "xrwb_garden"
GEOMETRY_KEY = "xrwb_garden_geometry"  # On the root of a Geometry Nodes garden.
BLOCK_KEY = "xrwb_garden_node"
SOCKET_KEY = "xrwb_garden_socket"
LABEL_KEY = "xrwb_garden_label"
SYNC_KEY = "xrwb_garden_synced"
UNIT_M = 0.001  # Metres per node-editor unit.
HEADER_UNITS = 36.0
ROW_UNITS = 24.0
DEPTH_M = 0.02
SOCKET_RADIUS_M = 0.007
PICK_RADIUS_M = 0.02  # How close the ray must pass a socket to grab it.
MAX_INPUTS = 14
SOCKET_COLOURS = {
    "SHADER": (0.39, 0.78, 0.39), "RGBA": (0.78, 0.78, 0.16), "VALUE": (0.63, 0.63, 0.63),
    "VECTOR": (0.39, 0.39, 0.78), "INT": (0.35, 0.55, 0.36), "BOOLEAN": (0.8, 0.65, 0.84),
    "GEOMETRY": (0.0, 0.84, 0.64), "STRING": (0.44, 0.7, 1.0), "ROTATION": (0.65, 0.39, 0.78),
    "OBJECT": (0.93, 0.62, 0.36), "COLLECTION": (0.96, 0.96, 0.96), "MATERIAL": (0.92, 0.46, 0.51),
}
ADD_NODES = (
    ("ShaderNodeBsdfPrincipled", "Principled BSDF"), ("ShaderNodeEmission", "Emission"),
    ("ShaderNodeTexNoise", "Noise Texture"), ("ShaderNodeTexVoronoi", "Voronoi Texture"),
    ("ShaderNodeTexChecker", "Checker Texture"), ("ShaderNodeTexWave", "Wave Texture"),
    ("ShaderNodeTexImage", "Image Texture"), ("ShaderNodeValToRGB", "Color Ramp"),
    ("ShaderNodeMix", "Mix"), ("ShaderNodeMath", "Math"), ("ShaderNodeBump", "Bump"),
    ("ShaderNodeMapping", "Mapping"), ("ShaderNodeTexCoord", "Texture Coordinate"),
    ("ShaderNodeMixShader", "Mix Shader"),
)
GEOMETRY_ADD_NODES = (
    ("GeometryNodeSetPosition", "Set Position"), ("GeometryNodeTransform", "Transform Geometry"),
    ("GeometryNodeSubdivisionSurface", "Subdivision Surface"), ("GeometryNodeExtrudeMesh", "Extrude Mesh"),
    ("GeometryNodeJoinGeometry", "Join Geometry"), ("GeometryNodeMeshCube", "Cube"),
    ("GeometryNodeMeshIcoSphere", "Ico Sphere"), ("GeometryNodeDistributePointsOnFaces", "Distribute Points"),
    ("GeometryNodeInstanceOnPoints", "Instance on Points"), ("GeometryNodeRealizeInstances", "Realize Instances"),
    ("GeometryNodeInputPosition", "Position"), ("GeometryNodeInputNormal", "Normal"),
    ("ShaderNodeTexNoise", "Noise Texture"), ("ShaderNodeMath", "Math"), ("ShaderNodeVectorMath", "Vector Math"),
)
PROTECTED_NODES = {"OUTPUT_MATERIAL", "GROUP_INPUT", "GROUP_OUTPUT"}


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("nodes", message, context, level=level)


def collection(context=None):
    found = bpy.data.collections.get(COLLECTION)
    scene = (context or bpy.context).scene
    if found is not None and scene.collection.children.get(found.name) is None:
        return None
    return found


def root(context=None):
    found = collection(context)
    if found is None:
        return None
    return next((obj for obj in found.objects if ROOT_KEY in obj.keys()), None)


def is_out(context=None) -> bool:
    return root(context) is not None


def material_of(garden_root):
    if garden_root is None or GEOMETRY_KEY in garden_root.keys():
        return None
    material = bpy.data.materials.get(garden_root[ROOT_KEY])
    return material if material is not None and material.node_tree is not None else None


def source_of(garden_root):
    """The material or Geometry Nodes group a garden shows, or None when it is gone."""
    if garden_root is not None and GEOMETRY_KEY in garden_root.keys():
        group = bpy.data.node_groups.get(garden_root[ROOT_KEY])
        return group if group is not None and group.bl_idname == "GeometryNodeTree" else None
    return material_of(garden_root)


def tree_of(garden_root):
    source = source_of(garden_root)
    return source if isinstance(source, bpy.types.NodeTree) else getattr(source, "node_tree", None)


def add_nodes(tree):
    """(type, label) of the nodes Add Node offers for this tree, those this Blender has."""
    table = GEOMETRY_ADD_NODES if getattr(tree, "bl_idname", "") == "GeometryNodeTree" else ADD_NODES
    return tuple(item for item in table if hasattr(bpy.types, item[0]))


def _visible_inputs(node):
    sockets = [(index, socket) for index, socket in enumerate(node.inputs)
               if socket.enabled and not socket.hide]
    if len(sockets) <= MAX_INPUTS:
        return sockets
    # Big nodes: every linked input, then the first unlinked ones.
    linked = [item for item in sockets if item[1].is_linked]
    rest = [item for item in sockets if not item[1].is_linked][:max(0, MAX_INPUTS - len(linked))]
    return sorted(linked + rest)


def _visible_outputs(node):
    return [(index, socket) for index, socket in enumerate(node.outputs) if socket.enabled and not socket.hide]


def signature(tree) -> str:
    """Changes whenever blocks or sockets must be rebuilt (not for moves or links)."""
    parts = []
    for node in tree.nodes:
        if node.type in {"FRAME", "REROUTE"}:
            continue
        parts.append(f"{node.name}:{[i for i, _s in _visible_inputs(node)]}:{[i for i, _s in _visible_outputs(node)]}"
                     f":{node.label}:{round(node.width)}")
    return "|".join(sorted(parts))


def _block_size(node):
    rows = len(_visible_outputs(node)) + len(_visible_inputs(node))
    return max(80.0, float(node.width)), HEADER_UNITS + ROW_UNITS * max(1, rows)


def _socket_local(node, output, slot):
    """Socket centre in block space (editor units): outputs first, then inputs."""
    width, _height = _block_size(node)
    row = slot if output else len(_visible_outputs(node)) + slot
    return (width if output else 0.0), -(HEADER_UNITS + ROW_UNITS * (row + 0.5))


def _box_mesh(name, width, height, depth):
    """A box whose origin is its top-left corner, like a node's location."""
    mesh = bpy.data.meshes.new(name)
    xs, ys, zs = (0.0, width), (-depth / 2.0, depth / 2.0), (-height, 0.0)
    verts = [(x, y, z) for x in xs for y in ys for z in zs]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    return mesh


def _socket_mesh():
    mesh = bpy.data.meshes.get("XR Garden Socket")
    if mesh is None:
        import bmesh
        mesh = bpy.data.meshes.new("XR Garden Socket")
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=SOCKET_RADIUS_M / UNIT_M)
        bm.to_mesh(mesh)
        bm.free()
        mesh.shade_smooth()
        mesh.materials.append(None)
    return mesh


def _socket_material(kind):
    name = f"XR Garden {kind.title()}"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True  # Blender 4.5 starts without nodes; 5.0+ ignores this
        colour = SOCKET_COLOURS.get(kind, (0.8, 0.8, 0.8))
        material.diffuse_color = (*colour, 1.0)
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*colour, 1.0)
    return material


def _block_material():
    material = bpy.data.materials.get("XR Garden Block")
    if material is None:
        material = bpy.data.materials.new("XR Garden Block")
        material.use_nodes = True  # Blender 4.5 starts without nodes; 5.0+ ignores this
        material.diffuse_color = (0.05, 0.055, 0.07, 1.0)
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.05, 0.055, 0.07, 1.0)
    return material


def _label_material():
    material = bpy.data.materials.get("XR Garden Label")
    if material is None:
        material = bpy.data.materials.new("XR Garden Label")
        material.use_nodes = True  # Blender 4.5 starts without nodes; 5.0+ ignores this
        material.diffuse_color = (0.95, 0.95, 1.0, 1.0)
        bsdf = material.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Emission Color"].default_value = (0.95, 0.95, 1.0, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 1.0
    return material


def _label(owner, parent, text, location, size):
    curve = bpy.data.curves.new(f"{parent.name} label", "FONT")
    curve.body = text
    curve.size = size
    curve.materials.append(_label_material())
    label = bpy.data.objects.new(f"{parent.name} label", curve)
    label[LABEL_KEY] = True
    label.hide_render = True
    label.hide_select = True
    label.parent = parent
    label.location = location
    label.rotation_euler = (math.pi / 2.0, 0.0, 0.0)  # Stand up, facing the viewer (-Y).
    owner.objects.link(label)
    return label


def _centre(tree):
    nodes = [node for node in tree.nodes if node.type not in {"FRAME", "REROUTE"}]
    if not nodes:
        return 0.0, 0.0
    xs = [node.location.x for node in nodes] + [node.location.x + _block_size(node)[0] for node in nodes]
    ys = [node.location.y for node in nodes] + [node.location.y - _block_size(node)[1] for node in nodes]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def build(context, material, placement=None):
    """Create the garden for a material or Geometry Nodes group; ``placement`` keeps an old root's transform."""
    geometry = isinstance(material, bpy.types.NodeTree)
    if material is None or (not geometry and material.node_tree is None):
        raise editing.EditRejected("Select an object with a node material first")
    if collection(context) is not None:
        raise editing.EditRejected("The node garden is already out")
    owner = bpy.data.collections.new(COLLECTION)
    owner.hide_render = True
    context.scene.collection.children.link(owner)
    try:
        tree = material if geometry else material.node_tree
        garden = bpy.data.objects.new("XR Node Garden", None)
        garden.empty_display_type = "PLAIN_AXES"
        garden.empty_display_size = 0.01
        garden[ROOT_KEY] = material.name
        if geometry:
            garden[GEOMETRY_KEY] = True
        centre = _centre(tree)
        garden["xrwb_centre"] = centre
        garden["xrwb_signature"] = signature(tree)
        if placement is not None:
            garden.matrix_world = placement
        else:
            from . import materials_lab
            location, rotation, units = materials_lab.viewer_frame_flat(context)
            forward = rotation @ Vector((0.0, 1.0, 0.0))
            garden.location = location + forward * 0.75 * units - Vector((0.0, 0.0, 0.15 * units))
            garden.rotation_euler = rotation.to_euler()
            garden.scale = (units * UNIT_M,) * 3  # Children are in node-editor units.
        garden.hide_select = True
        owner.objects.link(garden)
        socket_mesh = _socket_mesh()
        for node in tree.nodes:
            if node.type in {"FRAME", "REROUTE"}:
                continue
            _build_block(owner, garden, node, centre, socket_mesh)
    except Exception:
        remove(context)
        raise
    return garden


def _build_block(owner, garden, node, centre, socket_mesh):
    width, height = _block_size(node)
    mesh = _box_mesh(f"XR Garden {node.name}", width, height, DEPTH_M / UNIT_M)
    mesh.materials.append(_block_material())
    block = bpy.data.objects.new(f"XR Garden {node.name}", mesh)
    block[BLOCK_KEY] = node.name
    block.hide_render = True
    block.parent = garden
    block.location = (node.location.x - centre[0], 0.0, node.location.y - centre[1])
    block[SYNC_KEY] = (float(node.location.x), float(node.location.y), *block.location)
    owner.objects.link(block)
    title = node.label or node.bl_label or node.name
    _label(owner, block, title[:28], (6.0, -DEPTH_M / UNIT_M, -HEADER_UNITS * 0.75), 16.0)
    for output, sockets in ((True, _visible_outputs(node)), (False, _visible_inputs(node))):
        for slot, (index, socket) in enumerate(sockets):
            sphere = bpy.data.objects.new(f"{block.name} {'out' if output else 'in'} {socket.name}", socket_mesh)
            # Shared sphere mesh, per-object colour: the slot links to the object.
            sphere.material_slots[0].link = "OBJECT"
            sphere.material_slots[0].material = _socket_material(socket.type)
            sphere[SOCKET_KEY] = node.name
            sphere["xrwb_garden_output"] = 1 if output else 0
            sphere["xrwb_garden_index"] = index
            sphere.hide_render = True
            sphere.parent = block
            x, z = _socket_local(node, output, slot)
            sphere.location = (x, -DEPTH_M / UNIT_M / 2.0, z)
            owner.objects.link(sphere)
            sphere.color = (*SOCKET_COLOURS.get(socket.type, (0.8, 0.8, 0.8)), 1.0)
    return block


def remove(context) -> int:
    found = bpy.data.collections.get(COLLECTION)
    if found is None:
        return 0
    removed = 0
    keys = (LABEL_KEY, SOCKET_KEY, BLOCK_KEY, ROOT_KEY)
    owned = sorted((obj for obj in found.objects if any(key in obj.keys() for key in keys)),
                   key=lambda obj: next(i for i, key in enumerate(keys) if key in obj.keys()))
    for obj in owned:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
        if data is not None and data.users == 0:
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.curves).remove(data)
    socket_mesh = bpy.data.meshes.get("XR Garden Socket")
    if socket_mesh is not None and socket_mesh.users == 0:
        bpy.data.meshes.remove(socket_mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith("XR Garden ") and m.users == 0]:
        bpy.data.materials.remove(material)
    if not found.objects and not found.children:
        bpy.data.collections.remove(found)
    return removed


def blocks(garden):
    return [child for child in garden.children if BLOCK_KEY in child.keys()]


def sync(context) -> str:
    """Keep blocks and nodes together; "" when nothing changed.

    A moved block moves its node. A node moved elsewhere (desktop editor,
    undo) moves its block. Added, removed or reshaped nodes rebuild the garden
    in place. The active block becomes the active node.
    """
    garden = root(context)
    if garden is None:
        return ""
    source = source_of(garden)
    if source is None:
        remove(context)
        return "removed"
    tree = tree_of(garden)
    if garden.get("xrwb_signature") != signature(tree):
        placement = garden.matrix_world.copy()
        remove(context)
        build(context, source, placement)
        return "rebuilt"
    centre = tuple(garden["xrwb_centre"])
    changed = ""
    for block in blocks(garden):
        node = tree.nodes.get(block[BLOCK_KEY])
        if node is None:
            continue
        synced = tuple(block.get(SYNC_KEY, ()))
        here = tuple(block.location)
        if len(synced) == 5 and any(abs(a - b) > 1.0e-4 for a, b in zip(here, synced[2:])):
            node.location = (here[0] + centre[0], here[2] + centre[1])  # Block moved: node follows.
            changed = "moved node"
        elif len(synced) == 5 and any(abs(a - b) > 1.0e-4 for a, b in zip(node.location, synced[:2])):
            block.location = (node.location.x - centre[0], here[1], node.location.y - centre[1])
            changed = "moved block"
        block[SYNC_KEY] = (float(node.location.x), float(node.location.y), *block.location)
    active = context.view_layer.objects.active
    if active is not None and active.parent == garden and BLOCK_KEY in active.keys():
        node = tree.nodes.get(active[BLOCK_KEY])
        if node is not None and tree.nodes.active != node:
            tree.nodes.active = node
            changed = changed or "active node"
    return changed


def _sockets(garden):
    for block in blocks(garden):
        for child in block.children:
            if SOCKET_KEY in child.keys():
                yield child


def socket_key(sphere):
    """(node name, 1 for output / 0 for input, socket index) of a socket sphere."""
    return sphere[SOCKET_KEY], int(sphere["xrwb_garden_output"]), int(sphere["xrwb_garden_index"])


def socket_ref(tree, sphere):
    node_name, output, index = socket_key(sphere)
    node = tree.nodes.get(node_name)
    if node is None:
        return None
    sockets = node.outputs if output else node.inputs
    return sockets[index] if 0 <= index < len(sockets) else None


def nearest_socket(context, origin, direction, want_output=None, exclude_node=""):
    """Socket sphere passing closest to the ray, within the pick radius."""
    garden = root(context)
    if garden is None:
        return None
    units = viewer_scale.world_units_per_metre(context)
    origin, direction = Vector(origin), Vector(direction).normalized()
    best, best_distance = None, PICK_RADIUS_M * units
    for sphere in _sockets(garden):
        node_name, output, _index = socket_key(sphere)
        if want_output is not None and bool(output) != want_output or node_name == exclude_node:
            continue
        centre = sphere.matrix_world.translation
        along = (centre - origin).dot(direction)
        if along <= 0.0:
            continue
        distance = (origin + direction * along - centre).length
        if distance < best_distance:
            best, best_distance = sphere, distance
    return best


def link_lines(context):
    """(start, end, colour) of every link, in world space; plain tuples."""
    garden = root(context)
    tree = tree_of(garden)
    if tree is None:
        return []
    spheres = {}
    for sphere in _sockets(garden):
        node_name, output, index = socket_key(sphere)
        spheres[(node_name, bool(output), index)] = sphere.matrix_world.translation
    lines = []
    for link in tree.links:
        start = spheres.get((link.from_node.name, True, _index_of(link.from_node.outputs, link.from_socket)))
        end = spheres.get((link.to_node.name, False, _index_of(link.to_node.inputs, link.to_socket)))
        if start is not None and end is not None:
            colour = SOCKET_COLOURS.get(link.from_socket.type, (0.8, 0.8, 0.8))
            lines.append((tuple(start), tuple(end), colour))
    return lines


def _index_of(sockets, socket):
    for index, candidate in enumerate(sockets):
        if candidate == socket:
            return index
    return -1


class NodeLinkStroke(physical_tools._Stroke):
    """Drag from a socket to another; release links, moves or removes one link."""

    label = "Node Link"

    def __init__(self, context, pose, sphere, owner, generation, units):
        super().__init__(context, pose, owner, generation, units)
        garden = root(context)
        self.tree = tree_of(garden)
        tree = self.tree
        socket = socket_ref(tree, sphere)
        if socket is None:
            raise editing.EditRejected("That socket no longer exists")
        self.picked_link = None
        self.start_sphere = sphere
        if not socket.is_output and socket.is_linked:
            # Grabbing a linked input picks its link up by the output end.
            self.picked_link = socket.links[0]
            output = self.picked_link.from_socket
            node = self.picked_link.from_node
            self.start_sphere = next((s for s in _sockets(garden) if socket_key(s)
                                      == (node.name, 1, _index_of(node.outputs, output))), sphere)
            socket = output
        self.from_output = socket.is_output
        self.start_node = socket_key(self.start_sphere)[0]
        self.target = None
        self.distance = (self.start_sphere.matrix_world.translation - Vector(pose.position)).length
        self.lines = []
        self.colour = SOCKET_COLOURS.get(socket.type, (0.8, 0.8, 0.8))
        self.changed = True
        self._aim(context, pose)

    def _aim(self, context, pose):
        origin, direction, _rotation = physical_tools._pose_ray(pose)
        self.target = nearest_socket(context, origin, direction, want_output=not self.from_output,
                                     exclude_node=self.start_node)
        start = self.start_sphere.matrix_world.translation
        end = self.target.matrix_world.translation if self.target is not None else origin + direction * self.distance
        self.lines = [tuple(start), tuple(end)]

    def _valid(self, context):
        try:
            return super()._valid(context) and self.tree.nodes is not None and is_out(context)
        except ReferenceError:
            return False

    def _restore(self, context):
        pass  # Nothing changes until release.

    def _apply(self, context, pose):
        self._aim(context, pose)
        if self.target is None:
            return "Drop on a socket to link" + (", or here to remove the link" if self.picked_link else "")
        return f"Release to link to {socket_key(self.target)[0]}"

    def commit(self, context, owner=None):
        editing._require_main_thread()
        if editing.active_transaction() is self and not self.finished and self._valid(context):
            tree = self.tree
            try:
                target = socket_ref(tree, self.target) if self.target is not None else None
                start = socket_ref(tree, self.start_sphere)
                if target is None and self.picked_link is None:
                    self.changed = False
                    self.message = "No socket there; nothing linked"
                else:
                    if self.picked_link is not None:
                        tree.links.remove(self.picked_link)
                        self.picked_link = None
                    if target is not None:
                        output, input_ = (start, target) if self.from_output else (target, start)
                        tree.links.new(output, input_)
                        self.message = f"Linked {output.node.name} {output.name} → {input_.node.name} {input_.name}"
                    else:
                        self.message = "Link removed"
            except (ReferenceError, RuntimeError) as exc:
                self._finish(CapturePhase.CANCELLED)
                return EditResult(Decision.REJECTED, f"Link failed: {exc}")
        return super().commit(context, owner)

    def commit_message(self):
        return getattr(self, "message", "Link changed")


def begin_link(context, pose, owner, generation=0):
    """A link stroke when the ray passes a socket; None otherwise."""
    if not is_out(context):
        return None
    origin, direction, _rotation = physical_tools._pose_ray(pose)
    sphere = nearest_socket(context, origin, direction)
    if sphere is None:
        return None
    if not editing.undo_available(context):
        raise editing.EditRejected("Enable Global Undo and at least one undo step")
    stroke = NodeLinkStroke(context, pose, sphere, owner, generation, viewer_scale.world_units_per_metre(context))
    editing._ACTIVE_TRANSACTION = stroke
    return stroke


def _target_geometry(context, name=""):
    """The Geometry Nodes group of the named (else active, else first) Geometry Nodes modifier."""
    from . import modifier_tower
    obj = modifier_tower.target_object(context)
    modifiers = [m for m in getattr(obj, "modifiers", ()) if m.type == "NODES" and m.node_group is not None]
    chosen = next((m for m in modifiers if m.name == name), None) if name else None
    chosen = chosen or next((m for m in modifiers if getattr(m, "is_active", False)), None) or next(
        iter(modifiers), None)
    if chosen is None:
        raise editing.EditRejected("Select an object with a Geometry Nodes modifier first")
    return chosen.node_group


def _target_material(context):
    garden = root(context)
    if garden is not None:
        return material_of(garden)
    obj = context.active_object
    if obj is not None and "xrwb_swatch" in obj.keys():
        return bpy.data.materials.get(obj["xrwb_swatch"])
    material = obj.active_material if obj is not None else None
    if material is None:
        material = bpy.data.materials.get(context.window_manager.xrwb_settings.spray_material)
    return material


def _undo_push(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in bpy.ops.ed.undo_push(message=message):
        raise RuntimeError("Blender rejected the undo entry")


class XRWB_OT_node_garden(bpy.types.Operator):
    bl_idname = "xrwb.node_garden"
    bl_label = "Node Garden"
    bl_description = "The material's node tree as physical blocks: grab to arrange, drag sockets to link"
    action: bpy.props.EnumProperty(
        items=(("TOGGLE", "Bring Out / Put Away", ""), ("ADD", "Add Node", ""), ("DELETE", "Delete Node", ""),
               ("BRING", "Bring Here", "Put the garden in front of you again"),
               ("GEOMETRY", "Geometry Nodes", "Bring out the Geometry Nodes tree of a modifier")),
        default="TOGGLE", options={"SKIP_SAVE"})
    node_type: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})
    modifier: bpy.props.StringProperty(default="", options={"SKIP_SAVE"})

    def execute(self, context):
        if context.mode != "OBJECT" or editing.active_transaction() is not None:
            _log(context, "Switch to Object Mode and release the current edit", "WARNING")
            return {"CANCELLED"}
        try:
            if self.action == "GEOMETRY":
                group = _target_geometry(context, self.modifier)
                if is_out(context):
                    remove(context)
                garden = build(context, group)
                _undo_push("XR Node Garden")
                _log(context, f"{group.name}: {len(blocks(garden))} geometry node blocks. Relink a socket and the "
                              "object updates live.")
                return {"FINISHED"}
            if self.action == "TOGGLE":
                if is_out(context):
                    count = remove(context)
                    _undo_push("XR Put Away Node Garden")
                    _log(context, f"Node garden put away ({count} objects).")
                    return {"FINISHED"}
                material = _target_material(context)
                garden = build(context, material)
                _undo_push("XR Node Garden")
                _log(context, f"{material.name}: {len(blocks(garden))} node blocks. Grip a block to move it; "
                              "trigger a socket and drag to link.")
                return {"FINISHED"}
            garden = root(context)
            source = source_of(garden)
            if source is None:
                raise editing.EditRejected("Bring out the node garden first")
            tree = tree_of(garden)
            if self.action == "BRING":
                remove(context)
                build(context, source)
                _undo_push("XR Node Garden Here")
                _log(context, "Node garden moved in front of you.")
                return {"FINISHED"}
            active = tree.nodes.active
            if self.action == "ADD":
                if self.node_type not in {key for key, _label in add_nodes(tree)}:
                    raise editing.EditRejected("Unknown node type")
                node = tree.nodes.new(self.node_type)
                if active is not None:
                    node.location = (active.location.x - node.width - 80.0, active.location.y)
                else:
                    centre = garden["xrwb_centre"]
                    node.location = (centre[0], centre[1] + 200.0)
                for other in tree.nodes:
                    other.select = other == node
                tree.nodes.active = node
                sync(context)
                _undo_push(f"XR Add {node.bl_label}")
                _log(context, f"{node.bl_label} added beside the active node; grip it to place it.")
                return {"FINISHED"}
            if active is None or active.type in PROTECTED_NODES:
                raise editing.EditRejected("Trigger a node block (not an input or output) to choose it first")
            name = active.name
            tree.nodes.remove(active)
            sync(context)
            _undo_push("XR Delete Node")
            _log(context, f"{name} deleted.")
            return {"FINISHED"}
        except (editing.EditRejected, RuntimeError, ReferenceError) as exc:
            _log(context, str(exc), "WARNING")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


class XRWB_MT_node_add(bpy.types.Menu):
    bl_idname = "XRWB_MT_node_add"
    bl_label = "Add Node"

    def draw(self, context):
        layout = self.layout
        layout.label(text="ADD A NODE BESIDE THE ACTIVE ONE", icon="NODE")
        for key, label in add_nodes(tree_of(root(context))):
            operator = layout.operator("xrwb.node_garden", text=label)
            operator.action = "ADD"
            operator.node_type = key


class XRWB_MT_node_garden(bpy.types.Menu):
    bl_idname = "XRWB_MT_node_garden"
    bl_label = "Node Garden"

    def draw(self, context):
        layout = self.layout
        garden = root(context)
        source = source_of(garden)
        layout.label(text=f"NODE GARDEN: {source.name}" if source else "NODE GARDEN", icon="NODETREE")
        layout.operator("xrwb.node_garden", text="Put Away" if source else "Bring Out",
                        icon="X" if source else "NODETREE").action = "TOGGLE"
        layout.operator("xrwb.node_garden", text="Geometry Nodes of Selected",
                        icon="GEOMETRY_NODES").action = "GEOMETRY"
        if source is None:
            layout.label(text="Select an object with a material or Geometry Nodes, then Bring Out.")
            return
        layout.operator("xrwb.open_hand_menu", text="Add Node  ›", icon="ADD").menu = "XRWB_MT_node_add"
        layout.operator("xrwb.node_garden", text="Bring Here", icon="VIEW_PAN").action = "BRING"
        if GEOMETRY_KEY in garden.keys():
            layout.operator("xrwb.workspace_editor", text="Open in Node Editor",
                            icon="WINDOW").editor = "GeometryNodeTree"
        else:
            layout.operator("xrwb.show_material_logic", text="Open in Node Editor", icon="WINDOW")
        node = tree_of(garden).nodes.active
        if node is None:
            layout.label(text="Trigger a block to see and change its values.")
            return
        layout.label(text=f"{(node.label or node.name).upper()}", icon="NODE")
        shown = 0
        for socket in node.inputs:
            if shown >= 8:
                break
            if socket.enabled and not socket.hide and not socket.is_linked and hasattr(socket, "default_value"):
                layout.prop(socket, "default_value", text=socket.name)
                shown += 1
        if node.type not in PROTECTED_NODES:
            layout.operator("xrwb.node_garden", text="Delete This Node", icon="TRASH").action = "DELETE"


CLASSES = (XRWB_OT_node_garden, XRWB_MT_node_add, XRWB_MT_node_garden)
