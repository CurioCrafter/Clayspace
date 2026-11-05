"""Prefabs: ready-made starters you place like a shape, plus your own saved builds.

Every built-in prefab is real, editable Blender geometry assembled from
native primitives (bmesh), at real-world size in scene metres, with fitting
materials and non-destructive modifiers (bevel, subdivision) you can tweak in
Studio > Modifiers. Pick one from Add > Prefabs: a ghost box follows your ray
and rests on floors and tables, the trigger drops it, hold to slide it,
release keeps it as one undo step.

Starter scenes (Studio, Outdoor, Tabletop) set up a whole stage in one go.
"Save Selection as Prefab" keeps your own builds in your user data folder
(one .blend per prefab); they appear under My Prefabs in every scene.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

from . import editing, physical_tools

PREFAB_KEY = "xrwb_prefab"
COLOURS = {
    "LEAF": ((0.12, 0.42, 0.12, 1.0), 0.8, 0.0), "PINE": ((0.05, 0.25, 0.1, 1.0), 0.85, 0.0),
    "STONE": ((0.42, 0.42, 0.44, 1.0), 0.9, 0.0), "PAINT": ((0.86, 0.85, 0.82, 1.0), 0.7, 0.0),
    "ROOF": ((0.52, 0.12, 0.08, 1.0), 0.75, 0.0), "FABRIC": ((0.12, 0.2, 0.45, 1.0), 0.95, 0.0),
    "SNOW": ((0.95, 0.96, 1.0, 1.0), 0.6, 0.0), "SKIN": ((0.78, 0.66, 0.55, 1.0), 0.6, 0.0),
    "GRASS": ((0.2, 0.45, 0.12, 1.0), 0.95, 0.0), "CAP": ((0.75, 0.08, 0.05, 1.0), 0.5, 0.0),
    "ORANGE": ((1.0, 0.45, 0.05, 1.0), 0.5, 0.0), "DARK": ((0.04, 0.04, 0.045, 1.0), 0.6, 0.0),
    "LINEN": ((0.92, 0.9, 0.86, 1.0), 0.9, 0.0),
}
GLOW = {"LAMP": ((1.0, 0.82, 0.55, 1.0), 4.0), "EYES": ((0.1, 0.9, 1.0, 1.0), 6.0)}


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("prefab", message, context, level=level)


def material(key):
    """A material preset (Wood, Steel…) or a simple named colour, created once."""
    from . import materials_lab
    if key in materials_lab.PRESETS:
        return materials_lab.build_preset(key)
    name = f"XR Prefab {key.title()}"
    found = bpy.data.materials.get(name)
    if found is not None:
        return found
    found = bpy.data.materials.new(name)
    found.use_nodes = True  # Blender 4.5 starts without nodes; 5.0+ ignores this
    bsdf = found.node_tree.nodes["Principled BSDF"]
    if key in GLOW:
        colour, strength = GLOW[key]
        bsdf.inputs["Base Color"].default_value = colour
        bsdf.inputs["Emission Color"].default_value = colour
        bsdf.inputs["Emission Strength"].default_value = strength
    else:
        colour, roughness, metallic = COLOURS[key]
        bsdf.inputs["Base Color"].default_value = colour
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    found.diffuse_color = colour
    return found


class Kit:
    """Native primitives assembled into one mesh, one material slot per material."""

    def __init__(self):
        self.bm = bmesh.new()
        self.slots: list[str] = []

    def _slot(self, key):
        if key not in self.slots:
            self.slots.append(key)
        return self.slots.index(key)

    def _tag(self, before, key):
        """Give the faces created since ``before`` (a set) this material.

        BMesh reuses freed slots (icospheres free faces while subdividing), so
        new faces are found by identity, not by position at the end.
        """
        index = self._slot(key)
        for face in self.bm.faces:
            if face not in before:
                face.material_index = index

    @staticmethod
    def _matrix(centre, size=(1.0, 1.0, 1.0), rotation=None):
        rotation = rotation if rotation is not None else Quaternion()
        return Matrix.LocRotScale(Vector(centre), rotation, Vector(size))

    def box(self, centre, size, key, rotation=None):
        before = set(self.bm.faces)
        bmesh.ops.create_cube(self.bm, size=1.0, matrix=self._matrix(centre, size, rotation))
        self._tag(before, key)

    def cylinder(self, centre, radius, depth, key, top=None, segments=24, rotation=None):
        before = set(self.bm.faces)
        bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=segments, radius1=radius,
                              radius2=radius if top is None else top, depth=depth,
                              matrix=self._matrix(centre, rotation=rotation))
        self._tag(before, key)

    def sphere(self, centre, radius, key, scale=(1.0, 1.0, 1.0), subdivisions=2, uv=False):
        before = set(self.bm.faces)
        matrix = self._matrix(centre, scale)
        if uv:
            bmesh.ops.create_uvsphere(self.bm, u_segments=24, v_segments=12, radius=radius, matrix=matrix)
        else:
            bmesh.ops.create_icosphere(self.bm, subdivisions=subdivisions, radius=radius, matrix=matrix)
        self._tag(before, key)

    def lathe(self, profile, key, segments=32):
        """Spin a (radius, height) profile around Z, like a pot on a wheel."""
        before = set(self.bm.faces)
        verts = [self.bm.verts.new((r, 0.0, z)) for r, z in profile]
        edges = [self.bm.edges.new((a, b)) for a, b in zip(verts, verts[1:])]
        bmesh.ops.spin(self.bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1), angle=math.tau,
                       steps=segments, use_merge=True)
        bmesh.ops.remove_doubles(self.bm, verts=self.bm.verts[:], dist=1.0e-6)
        self._tag(before, key)

    def prism(self, centre, width, depth, height, key):
        """A gable roof: triangular prism along X."""
        before = set(self.bm.faces)
        cx, cy, cz = centre
        corners = [(-width / 2, -depth / 2, 0.0), (-width / 2, depth / 2, 0.0), (-width / 2, 0.0, height),
                   (width / 2, -depth / 2, 0.0), (width / 2, depth / 2, 0.0), (width / 2, 0.0, height)]
        v = [self.bm.verts.new((cx + x, cy + y, cz + z)) for x, y, z in corners]
        for face in ((v[0], v[1], v[2]), (v[5], v[4], v[3]), (v[0], v[3], v[4], v[1]),
                     (v[1], v[4], v[5], v[2]), (v[2], v[5], v[3], v[0])):
            self.bm.faces.new(face)
        self._tag(before, key)

    def grid(self, size, cuts, key, height):
        before = set(self.bm.faces)
        bmesh.ops.create_grid(self.bm, x_segments=cuts, y_segments=cuts, size=size / 2.0)
        for vert in self.bm.verts:
            vert.co.z = height(vert.co.x, vert.co.y)
        self._tag(before, key)

    def finish(self, name, smooth=False):
        """(mesh centred on its bounding box, dimensions)."""
        bm = self.bm
        bm.normal_update()
        low = Vector(tuple(min(v.co[i] for v in bm.verts) for i in range(3)))
        high = Vector(tuple(max(v.co[i] for v in bm.verts) for i in range(3)))
        centre = (low + high) / 2.0
        for vert in bm.verts:
            vert.co -= centre
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        for key in self.slots:
            mesh.materials.append(material(key))
        if smooth:
            mesh.shade_smooth()
        return mesh, tuple(high - low)


def _rz(degrees):
    return Quaternion((0.0, 0.0, 1.0), math.radians(degrees))


def _ry(degrees):
    return Quaternion((0.0, 1.0, 0.0), math.radians(degrees))


def _rx(degrees):
    return Quaternion((1.0, 0.0, 0.0), math.radians(degrees))


# ---- Built-in prefabs: (label, category, icon, builder, modifiers, smooth) ----

def _table(k):
    k.box((0, 0, 0.73), (1.2, 0.7, 0.04), "WOOD")
    for x in (-0.54, 0.54):
        for y in (-0.29, 0.29):
            k.box((x, y, 0.355), (0.05, 0.05, 0.71), "WOOD")


def _chair(k):
    k.box((0, 0, 0.45), (0.45, 0.45, 0.04), "WOOD")
    for x in (-0.19, 0.19):
        for y in (-0.19, 0.19):
            k.box((x, y, 0.215), (0.04, 0.04, 0.43), "WOOD")
        k.box((x, 0.19, 0.72), (0.04, 0.04, 0.5), "WOOD")
    k.box((0, 0.19, 0.86), (0.42, 0.03, 0.18), "WOOD")


def _stool(k):
    k.cylinder((0, 0, 0.62), 0.18, 0.04, "WOOD")
    for x in (-0.11, 0.11):
        for y in (-0.11, 0.11):
            k.cylinder((x, y, 0.3), 0.018, 0.6, "STEEL", segments=12)


def _shelf(k):
    for x in (-0.39, 0.39):
        k.box((x, 0, 0.9), (0.02, 0.3, 1.8), "WOOD")
    for z in (0.02, 0.46, 0.9, 1.34, 1.78):
        k.box((0, 0, z), (0.76, 0.3, 0.02), "WOOD")
    k.box((0, 0.145, 0.9), (0.8, 0.01, 1.8), "WOOD")


def _sofa(k):
    k.box((0, 0, 0.22), (2.0, 0.9, 0.3), "FABRIC")
    k.box((0, 0.38, 0.55), (2.0, 0.15, 0.5), "FABRIC")
    for x in (-0.93, 0.93):
        k.box((x, 0, 0.42), (0.14, 0.9, 0.36), "FABRIC")
    for x in (-0.45, 0.45):
        k.box((x, -0.06, 0.43), (0.84, 0.66, 0.12), "FABRIC")


def _bed(k):
    k.box((0, 0, 0.2), (1.6, 2.1, 0.3), "WOOD")
    k.box((0, 0, 0.43), (1.5, 2.0, 0.16), "LINEN")
    k.box((0, 0.78, 0.56), (0.62, 0.32, 0.1), "LINEN")
    k.box((0, 1.03, 0.6), (1.6, 0.05, 0.9), "WOOD")


def _lamp(k):
    k.cylinder((0, 0, 0.015), 0.13, 0.03, "STEEL")
    k.cylinder((0, 0, 0.73), 0.012, 1.4, "STEEL", segments=12)
    k.cylinder((0, 0, 1.5), 0.23, 0.26, "LAMP", top=0.11)


def _wall(k):
    k.box((0, 0, 1.25), (4.0, 0.2, 2.5), "PAINT")


def _door_wall(k):
    for x in (-1.225, 1.225):
        k.box((x, 0, 1.25), (1.55, 0.2, 2.5), "PAINT")
    k.box((0, 0, 2.3), (0.9, 0.2, 0.4), "PAINT")
    k.box((0.0, -0.02, 1.05), (0.86, 0.04, 2.08), "WOOD")


def _window_wall(k):
    for x in (-1.3, 1.3):
        k.box((x, 0, 1.25), (1.4, 0.2, 2.5), "PAINT")
    k.box((0, 0, 0.45), (1.2, 0.2, 0.9), "PAINT")
    k.box((0, 0, 2.2), (1.2, 0.2, 0.6), "PAINT")
    k.box((0, 0, 1.4), (1.2, 0.02, 1.0), "GLASS")


def _stairs(k):
    for step in range(10):
        height = (step + 1) * 0.18
        k.box((0, step * 0.28, height / 2), (1.0, 0.28, height), "STONE")


def _pillar(k):
    k.box((0, 0, 0.1), (0.6, 0.6, 0.2), "MARBLE")
    k.cylinder((0, 0, 1.5), 0.22, 2.6, "MARBLE", segments=32)
    k.box((0, 0, 2.9), (0.6, 0.6, 0.2), "MARBLE")


def _arch(k):
    for x in (-1.0, 1.0):
        k.box((x, 0, 1.1), (0.3, 0.3, 2.2), "STONE")
    for i in range(9):
        angle = math.pi * i / 8
        k.box((math.cos(angle) * 1.0, 0, 2.2 + math.sin(angle) * 1.0), (0.32, 0.3, 0.3), "STONE",
              rotation=_ry(-math.degrees(angle)))


def _floor(k):
    k.box((0, 0, -0.025), (4.0, 4.0, 0.05), "CHECKER")


def _room(k):
    k.box((0, 0, 0.05), (5.0, 5.0, 0.1), "WOOD")
    k.box((0, 2.45, 1.5), (5.0, 0.1, 3.0), "PAINT")
    for x in (-2.45, 2.45):
        k.box((x, 0, 1.5), (0.1, 5.0, 3.0), "PAINT")


def _house(k):
    k.box((0, 0, 1.5), (6.0, 5.0, 3.0), "PAINT")
    k.prism((0, 0, 3.0), 6.4, 5.4, 2.4, "ROOF")
    k.box((0, -2.52, 1.0), (1.0, 0.06, 2.0), "WOOD")
    for x in (-1.8, 1.8):
        k.box((x, -2.52, 1.6), (1.0, 0.06, 1.0), "GLASS")


def _fence(k):
    for x in (-2.0, -1.0, 0.0, 1.0, 2.0):
        k.box((x, 0, 0.5), (0.08, 0.08, 1.0), "WOOD")
    for z in (0.4, 0.8):
        k.box((0, 0, z), (4.1, 0.04, 0.08), "WOOD")


def _tree(k):
    k.cylinder((0, 0, 1.0), 0.13, 2.0, "WOOD", top=0.08, segments=10)
    k.sphere((0, 0, 2.3), 0.9, "LEAF", subdivisions=1)
    k.sphere((0.45, 0.2, 2.75), 0.6, "LEAF", subdivisions=1)
    k.sphere((-0.4, -0.25, 2.65), 0.55, "LEAF", subdivisions=1)


def _pine(k):
    k.cylinder((0, 0, 0.4), 0.1, 0.8, "WOOD", segments=8)
    for z, radius in ((1.2, 1.0), (1.8, 0.78), (2.35, 0.55)):
        k.cylinder((0, 0, z), radius, 1.1, "PINE", top=0.0, segments=10)


def _rock(k):
    k.sphere((0, 0, 0), 0.5, "STONE", scale=(1.25, 1.0, 0.7), subdivisions=2)
    for vert in k.bm.verts:
        wobble = 0.12 * math.sin(vert.index * 12.9898) * math.cos(vert.index * 4.1414)
        vert.co += vert.co.normalized() * wobble


def _bush(k):
    for x, y, r in ((0, 0, 0.45), (0.35, 0.1, 0.35), (-0.3, -0.12, 0.33)):
        k.sphere((x, y, r * 0.8), r, "LEAF", subdivisions=1)


def _mushroom(k):
    k.cylinder((0, 0, 0.15), 0.06, 0.3, "LINEN", top=0.05, segments=16)
    k.sphere((0, 0, 0.32), 0.2, "CAP", scale=(1.0, 1.0, 0.55), uv=True)


def _terrain(k):
    k.grid(10.0, 40, "GRASS", lambda x, y: 0.4 * math.sin(x * 0.7) * math.cos(y * 0.6)
           + 0.15 * math.sin(x * 1.9 + y * 1.3))


def _crate(k):
    k.box((0, 0, 0.3), (0.6, 0.6, 0.6), "WOOD")


def _barrel(k):
    k.cylinder((0, 0, 0.45), 0.3, 0.9, "WOOD", segments=24)
    for z in (0.1, 0.45, 0.8):
        k.cylinder((0, 0, z), 0.307, 0.04, "STEEL", segments=24)


def _mug(k):
    k.lathe([(0.0, 0.0), (0.045, 0.0), (0.045, 0.1), (0.04, 0.1), (0.04, 0.008), (0.0, 0.008)], "PLASTIC")
    for z, x, h in ((0.075, 0.062, 0.012), (0.025, 0.062, 0.012)):
        k.box((x - 0.006, 0, z), (0.024, 0.014, h), "PLASTIC")
    k.box((0.072, 0, 0.05), (0.012, 0.014, 0.062), "PLASTIC")


def _vase(k):
    k.lathe([(0.0, 0.0), (0.08, 0.0), (0.12, 0.1), (0.1, 0.25), (0.05, 0.33), (0.06, 0.38),
             (0.052, 0.38), (0.042, 0.33), (0.09, 0.25), (0.11, 0.1), (0.07, 0.012), (0.0, 0.012)], "MARBLE")


def _bottle(k):
    k.lathe([(0.0, 0.0), (0.04, 0.0), (0.04, 0.2), (0.015, 0.26), (0.015, 0.3), (0.0, 0.3)], "GLASS")


def _gear(k):
    k.cylinder((0, 0, 0.03), 0.2, 0.06, "STEEL", segments=48)
    for i in range(16):
        angle = math.tau * i / 16
        k.box((math.cos(angle) * 0.22, math.sin(angle) * 0.22, 0.03), (0.06, 0.05, 0.06), "STEEL",
              rotation=_rz(math.degrees(angle)))
    k.cylinder((0, 0, 0.03), 0.04, 0.064, "DARK", segments=16)


def _sword(k):
    k.box((0, 0, 0.55), (0.05, 0.008, 0.8), "STEEL")
    k.cylinder((0, 0, 0.99), 0.035, 0.08, "STEEL", top=0.0, segments=4, rotation=_rz(45))
    k.box((0, 0, 0.14), (0.22, 0.03, 0.03), "GOLD")
    k.cylinder((0, 0, 0.065), 0.016, 0.13, "RUBBER", segments=12)
    k.sphere((0, 0, -0.01), 0.026, "GOLD", subdivisions=1)


def _coin(k):
    k.cylinder((0, 0, 0.002), 0.03, 0.004, "GOLD", segments=32)


def _mannequin(k):
    k.sphere((0, 0, 1.68), 0.11, "SKIN", scale=(0.9, 1.0, 1.15), subdivisions=2)
    k.cylinder((0, 0, 1.55), 0.05, 0.1, "SKIN", segments=12)
    k.box((0, 0, 1.25), (0.4, 0.22, 0.55), "SKIN")
    k.box((0, 0, 0.92), (0.34, 0.2, 0.14), "SKIN")
    for x in (-0.1, 0.1):
        k.cylinder((x, 0, 0.44), 0.07, 0.85, "SKIN", top=0.05, segments=12)
    for x in (-0.28, 0.28):
        k.cylinder((x, 0, 1.15), 0.05, 0.68, "SKIN", top=0.04, segments=12)


def _snowman(k):
    for z, r in ((0.4, 0.4), (1.0, 0.28), (1.42, 0.18)):
        k.sphere((0, 0, z), r, "SNOW", subdivisions=2)
    k.cylinder((0, -0.24, 1.42), 0.03, 0.16, "ORANGE", top=0.0, segments=12, rotation=_rx(90))
    for x in (-0.06, 0.06):
        k.sphere((x, -0.16, 1.48), 0.018, "DARK", subdivisions=1)


def _robot(k):
    k.box((0, 0, 0.9), (0.6, 0.4, 0.7), "STEEL")
    k.box((0, 0, 1.45), (0.36, 0.32, 0.3), "STEEL")
    for x in (-0.09, 0.09):
        k.box((x, -0.165, 1.48), (0.07, 0.02, 0.05), "EYES")
    k.cylinder((0, 0, 1.7), 0.01, 0.2, "STEEL", segments=8)
    k.sphere((0, 0, 1.81), 0.03, "EYES", subdivisions=1)
    for x in (-0.15, 0.15):
        k.box((x, 0, 0.28), (0.14, 0.18, 0.55), "DARK")
    for x in (-0.38, 0.38):
        k.box((x, 0, 0.92), (0.12, 0.14, 0.6), "DARK")


BEVEL = (("BEVEL", {"width": 0.006, "segments": 2, "limit_method": "ANGLE"}),)
SOFT = (("BEVEL", {"width": 0.03, "segments": 3, "limit_method": "ANGLE"}),)
SMOOTHED = (("SUBSURF", {"levels": 1, "render_levels": 2}),)

PREFABS = {
    "TABLE": ("Table", "FURNITURE", "MESH_CUBE", _table, BEVEL, False),
    "CHAIR": ("Chair", "FURNITURE", "MESH_CUBE", _chair, BEVEL, False),
    "STOOL": ("Stool", "FURNITURE", "MESH_CYLINDER", _stool, (), True),
    "SHELF": ("Bookshelf", "FURNITURE", "MESH_CUBE", _shelf, BEVEL, False),
    "SOFA": ("Sofa", "FURNITURE", "MESH_CUBE", _sofa, SOFT, False),
    "BED": ("Bed", "FURNITURE", "MESH_CUBE", _bed, SOFT, False),
    "LAMP": ("Floor Lamp", "FURNITURE", "LIGHT", _lamp, (), True),
    "WALL": ("Wall", "ARCHITECTURE", "MESH_PLANE", _wall, (), False),
    "DOOR_WALL": ("Wall with Door", "ARCHITECTURE", "MESH_PLANE", _door_wall, (), False),
    "WINDOW_WALL": ("Wall with Window", "ARCHITECTURE", "MESH_PLANE", _window_wall, (), False),
    "STAIRS": ("Stairs", "ARCHITECTURE", "MESH_CUBE", _stairs, (), False),
    "PILLAR": ("Pillar", "ARCHITECTURE", "MESH_CYLINDER", _pillar, BEVEL, True),
    "ARCH": ("Arch", "ARCHITECTURE", "MESH_TORUS", _arch, (), False),
    "FLOOR": ("Floor Tiles", "ARCHITECTURE", "MESH_GRID", _floor, (), False),
    "ROOM": ("Room", "ARCHITECTURE", "HOME", _room, (), False),
    "HOUSE": ("Little House", "ARCHITECTURE", "HOME", _house, (), False),
    "FENCE": ("Fence", "ARCHITECTURE", "MESH_GRID", _fence, BEVEL, False),
    "TREE": ("Tree", "NATURE", "OUTLINER_OB_POINTCLOUD", _tree, (), False),
    "PINE": ("Pine", "NATURE", "MESH_CONE", _pine, (), False),
    "ROCK": ("Rock", "NATURE", "MESH_ICOSPHERE", _rock, (), False),
    "BUSH": ("Bush", "NATURE", "MESH_ICOSPHERE", _bush, (), False),
    "MUSHROOM": ("Mushroom", "NATURE", "MESH_UVSPHERE", _mushroom, (), True),
    "TERRAIN": ("Terrain Patch", "NATURE", "MESH_GRID", _terrain, (), True),
    "CRATE": ("Crate", "PROPS", "MESH_CUBE", _crate, BEVEL, False),
    "BARREL": ("Barrel", "PROPS", "MESH_CYLINDER", _barrel, (), True),
    "MUG": ("Mug", "PROPS", "MESH_CYLINDER", _mug, (), True),
    "VASE": ("Vase", "PROPS", "MESH_CYLINDER", _vase, (), True),
    "BOTTLE": ("Bottle", "PROPS", "MESH_CYLINDER", _bottle, (), True),
    "GEAR": ("Gear", "PROPS", "PREFERENCES", _gear, BEVEL, False),
    "SWORD": ("Sword", "PROPS", "MESH_CUBE", _sword, (), False),
    "COIN": ("Coin", "PROPS", "MESH_CIRCLE", _coin, (), True),
    "MANNEQUIN": ("Mannequin (1.8 m)", "CHARACTERS", "OUTLINER_OB_ARMATURE", _mannequin, (), True),
    "SNOWMAN": ("Snowman", "CHARACTERS", "MESH_UVSPHERE", _snowman, SMOOTHED, True),
    "ROBOT": ("Robot", "CHARACTERS", "MESH_CUBE", _robot, BEVEL, False),
}
CATEGORIES = (("FURNITURE", "Furniture", "MESH_CUBE"), ("ARCHITECTURE", "Architecture", "HOME"),
              ("NATURE", "Nature", "OUTLINER_OB_POINTCLOUD"), ("PROPS", "Props", "PACKAGE"),
              ("CHARACTERS", "Characters", "OUTLINER_OB_ARMATURE"))
_dimensions: dict[str, tuple] = {}


def build(key, context):
    """A new object for a built-in prefab, not yet linked; returns (object, dimensions)."""
    label, _category, _icon, builder, modifiers, smooth = PREFABS[key]
    kit = Kit()
    builder(kit)
    mesh, dims = kit.finish(label, smooth)
    obj = bpy.data.objects.new(label, mesh)
    obj[PREFAB_KEY] = key
    for kind, values in modifiers:
        modifier = obj.modifiers.new(kind.title(), kind)
        for name, value in values.items():
            setattr(modifier, name, value)
    _dimensions[f"BUILTIN:{key}"] = dims
    return obj, dims


# ---- Your own prefabs ------------------------------------------------------------


def user_dir(create=False) -> Path:
    path = Path(bpy.utils.user_resource("DATAFILES", path="xr_workbench/prefabs", create=create))
    return path


def user_prefabs() -> list[str]:
    folder = user_dir()
    return sorted(path.stem for path in folder.glob("*.blend")) if folder.is_dir() else []


def _safe(name):
    return re.sub(r"[^A-Za-z0-9 _-]", "", name).strip()[:40] or "Prefab"


def save_selection(context) -> str:
    objects = [obj for obj in context.selected_objects if not obj.library]
    if not objects:
        raise editing.EditRejected("Select what you built first")
    base = _safe((context.active_object or objects[0]).name)
    folder = user_dir(create=True)
    name, number = base, 2
    while (folder / f"{name}.blend").exists():
        name, number = f"{base} {number}", number + 1
    corners = [obj.matrix_world @ Vector(c) for obj in objects for c in obj.bound_box]
    low = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    high = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    bpy.data.libraries.write(str(folder / f"{name}.blend"), set(objects), fake_user=True)
    (folder / f"{name}.json").write_text(json.dumps({"dimensions": list(high - low),
                                                     "centre": list((low + high) / 2),
                                                     "objects": [obj.name for obj in objects]}), encoding="utf-8")
    return name


def load_user(name, context):
    """Append a saved prefab's objects (not linked yet); returns (objects, dims, centre)."""
    folder = user_dir()
    path = folder / f"{name}.blend"
    info = json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.objects = [n for n in source.objects if n in info["objects"]] or list(source.objects)
    objects = [obj for obj in target.objects if obj is not None]
    for obj in objects:
        obj.use_fake_user = False
    return objects, tuple(info["dimensions"]), Vector(info["centre"])


def kind_dimensions(kind):
    if kind in _dimensions:
        return _dimensions[kind]
    source, _sep, name = kind.partition(":")
    try:
        if source == "BUILTIN":
            obj, dims = build(name, bpy.context)
            mesh = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)
            return dims
        info = json.loads((user_dir() / f"{name}.json").read_text(encoding="utf-8"))
        _dimensions[kind] = tuple(info["dimensions"])
        return _dimensions[kind]
    except (KeyError, OSError, ValueError):
        return (0.3, 0.3, 0.3)


# ---- Placing ---------------------------------------------------------------------


class PrefabStroke(physical_tools._Stroke):
    """A ghost-first drop: the prefab rests where the ray lands; hold to slide."""

    label = "Place Prefab"

    def __init__(self, context, pose, kind, owner, generation, units, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        if contact is not None:
            self._rest = (Vector(contact.point), Vector(contact.normal))
        collection = context.collection
        if collection is None or not collection.is_editable or collection.library:
            raise editing.EditRejected("Choose a local editable collection for the prefab")
        self.selection = editing._selection_snapshot(context)
        source, _sep, name = kind.partition(":")
        if source == "BUILTIN" and name in PREFABS:
            obj, dims = build(name, context)
            self.objects = [obj]
            centre = Vector()
            self.name = PREFABS[name][0]
        elif source == "USER" and name in user_prefabs():
            self.objects, dims, centre = load_user(name, context)
            self.name = name
        else:
            raise editing.EditRejected("Pick a prefab first (Add > Prefabs)")
        self.dimensions = tuple(dims)
        self.label = f"Place {self.name}"
        # Offsets from the group's bounding-box centre, kept as the group moves.
        # (matrix_basis: appended objects have no evaluated world matrix yet.)
        self.offsets = [Matrix.Translation(-centre) @ obj.matrix_basis for obj in self.objects]
        for obj in self.objects:
            collection.objects.link(obj)
        self.object_names = [obj.name for obj in self.objects]
        self._move(context, pose)
        for other in tuple(context.selected_objects):
            other.select_set(False)
        for obj in self.objects:
            obj.select_set(True)
        context.view_layer.objects.active = self.objects[0]
        self.changed = True

    def _move(self, context, pose):
        rest = physical_tools.rest_contact(self, {obj.name for obj in self.objects})
        if rest is not None:
            # Touching: rest on the contact point. Only an upward-facing surface tilts the prefab with it,
            # so furniture stays upright against a wall.
            location, rotation = physical_tools.contact_frame(
                rest[0], rest[1], self.dimensions, physical_tools.hand_yaw(pose), self.units,
                align=rest[1].z > 0.5)
            frame = Matrix.Translation(location) @ rotation.to_matrix().to_4x4()
        else:
            origin, direction, _rotation = physical_tools._pose_ray(pose)
            location, yaw, _hit = physical_tools.placement(context, origin, direction, self.dimensions,
                                                           exclude=set(self.objects))
            frame = Matrix.Translation(location) @ yaw.to_matrix().to_4x4()
        for obj, offset in zip(self.objects, self.offsets):
            if obj.parent is None or obj.parent not in self.objects:
                obj.matrix_world = frame @ offset

    def matrix(self):
        return self.objects[0].matrix_world if len(self.objects) == 1 else None

    def _valid(self, context):
        try:
            return super()._valid(context) and all(obj.name in context.view_layer.objects for obj in self.objects)
        except ReferenceError:
            return False

    def _restore(self, context):
        for obj in self.objects:
            try:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
            except ReferenceError:
                pass
        editing._restore_object_selection(context, self.selection)

    def _apply(self, context, pose):
        self._move(context, pose)
        return f"{self.label}: release to keep it here"

    def commit_message(self):
        return f"{self.name} placed (one undo). Grab, recolour or edit it like anything else."

    def commit(self, context, owner=None):
        result = super().commit(context, owner)
        if self.finished and result.decision is physical_tools.Decision.APPLY and self.changed:
            self.flash(*self.objects)  # Brackets on the drop; the thunk is the controller's commit event.
        return result


# ---- Starter scenes ---------------------------------------------------------------

SCENES = (("STUDIO", "Studio (backdrop, 3 lights, camera)", "LIGHT_AREA"),
          ("OUTDOOR", "Outdoor (terrain, sun, sky, trees)", "LIGHT_SUN"),
          ("TABLETOP", "Tabletop (table, props, lamp light)", "MESH_CUBE"))


def _link(context, obj, location, rotation=None):
    context.collection.objects.link(obj)
    obj.location = location
    if rotation is not None:
        obj.rotation_euler = rotation.to_euler()
    return obj


def _light(context, name, kind, energy, location, target, size=1.0):
    from . import studio
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    if kind == "AREA":
        data.size = size
    obj = bpy.data.objects.new(name, data)
    return _link(context, obj, location, studio.aim_rotation(Vector(target) - Vector(location)))


def build_scene(context, key) -> list:
    """Create a starter scene in front of you, on the floor. Returns the new objects."""
    from . import studio
    location, rotation, _units = physical_tools_frame(context)
    forward = rotation @ Vector((0.0, 1.0, 0.0))
    base = Vector((location.x, location.y, 0.0)) + forward * 2.5
    made = []
    if key == "STUDIO":
        floor = bpy.data.objects.new("XR Studio Backdrop", _backdrop())
        made.append(_link(context, floor, base, rotation))
        made.append(_light(context, "Key Light", "AREA", 400.0, base + rotation @ Vector((-2.0, -2.0, 2.5)),
                           base + Vector((0, 0, 0.8)), 1.5))
        made.append(_light(context, "Fill Light", "AREA", 120.0, base + rotation @ Vector((2.5, -1.5, 1.5)),
                           base + Vector((0, 0, 0.8)), 2.0))
        made.append(_light(context, "Rim Light", "AREA", 250.0, base + rotation @ Vector((0.5, 2.0, 2.8)),
                           base + Vector((0, 0, 1.0)), 1.0))
    elif key == "OUTDOOR":
        terrain, _dims = build("TERRAIN", context)
        made.append(_link(context, terrain, base, rotation))
        context.view_layer.update()
        for name, offset in (("TREE", (-2.5, 1.5)), ("PINE", (2.8, 2.2)), ("ROCK", (1.0, -1.5)),
                             ("BUSH", (-1.2, -2.0))):
            obj, dims = build(name, context)
            x, y = offset
            spot = base + rotation @ Vector((x, y, 0.0))
            # Stand it on the terrain: cast straight down onto the new ground.
            hit, ground, _n, _i, _o, _m = context.scene.ray_cast(
                context.evaluated_depsgraph_get(), spot + Vector((0, 0, 20.0)), Vector((0, 0, -1)))
            spot.z = (ground.z if hit else spot.z) + dims[2] / 2.0
            made.append(_link(context, obj, spot, rotation))
        made.append(_light(context, "Sun", "SUN", 3.0, base + Vector((0, 0, 8)), base + Vector((2, 3, 0))))
        studio.world_preset(context, "DAY", push=False)  # Part of this scene's one undo step.
    else:
        table, dims = build("TABLE", context)
        made.append(_link(context, table, base + Vector((0, 0, dims[2] / 2)), rotation))
        top = base.z + dims[2]
        for name, offset in (("MUG", (-0.25, 0.05)), ("VASE", (0.3, 0.1)), ("COIN", (0.05, -0.15))):
            obj, obj_dims = build(name, context)
            x, y = offset
            made.append(_link(context, obj, base + rotation @ Vector((x, y, 0)) + Vector((0, 0, top - base.z
                                                                                               + obj_dims[2] / 2)),
                              rotation))
        made.append(_light(context, "Lamp Light", "POINT", 60.0, base + Vector((0.0, 0.3, top + 0.8)),
                           base + Vector((0, 0, top))))
    camera = bpy.data.objects.new("XR Camera", bpy.data.cameras.new("XR Camera"))
    eye = base + rotation @ Vector((0.0, -5.0, 1.6))
    made.append(_link(context, camera, eye, studio.aim_rotation(base + Vector((0, 0, 0.8)) - eye)))
    if context.scene.camera is None:
        context.scene.camera = camera
    return made


def _backdrop():
    """A floor that curves up into a wall: the classic seamless studio sweep."""
    kit = Kit()
    profile = [(y, 0.0) for y in (-4.0, -2.0, 0.0, 1.5)]
    radius = 1.5
    for step in range(1, 9):
        angle = math.pi / 2 * step / 8
        profile.append((1.5 + math.sin(angle) * radius, radius - math.cos(angle) * radius))
    profile += [(3.0, 3.0), (3.0, 5.0)]
    rows = []
    for y, z in profile:
        rows.append([kit.bm.verts.new((x, y, z)) for x in (-5.0, 5.0)])
    for (a0, a1), (b0, b1) in zip(rows, rows[1:]):
        kit.bm.faces.new((a0, a1, b1, b0))
    kit._tag(set(), "PAINT")
    mesh, _dims = kit.finish("XR Studio Backdrop", smooth=True)
    return mesh


def physical_tools_frame(context):
    from . import materials_lab
    return materials_lab.viewer_frame_flat(context)


# ---- Operators and menus -------------------------------------------------------------


def _undo(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
        raise RuntimeError("Blender rejected the undo entry")


class XRWB_OT_pick_prefab(bpy.types.Operator):
    bl_idname = "xrwb.pick_prefab"
    bl_label = "Pick Prefab"
    bl_description = "Choose a prefab; a ghost box follows your ray until the trigger places it"
    kind: bpy.props.StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        from . import ui
        reason = ui._scene_tool_reason(context, "place_prefab")
        if reason:
            _log(context, reason, "WARNING")
            return {"CANCELLED"}
        source, _sep, name = self.kind.partition(":")
        if not ((source == "BUILTIN" and name in PREFABS) or (source == "USER" and name in user_prefabs())):
            _log(context, "That prefab is not available", "WARNING")
            return {"CANCELLED"}
        settings = context.window_manager.xrwb_settings
        settings.prefab_kind = self.kind
        settings.edit_tool = "place_prefab"
        label = PREFABS[name][0] if source == "BUILTIN" else name
        _log(context, f"{label}: the ghost shows where it lands. Trigger places it; hold to slide.")
        return {"FINISHED"}


class XRWB_OT_prefab_action(bpy.types.Operator):
    bl_idname = "xrwb.prefab_action"
    bl_label = "Prefab"
    bl_description = "Save your selection as a prefab, or set up a starter scene; one undo"
    action: bpy.props.EnumProperty(items=[("SAVE", "Save Selection as Prefab", "")]
                                   + [(key, label, "") for key, label, _icon in SCENES], options={"SKIP_SAVE"})

    def execute(self, context):
        if context.mode != "OBJECT" or editing.active_transaction() is not None:
            _log(context, "Switch to Object Mode and release the current edit", "WARNING")
            return {"CANCELLED"}
        try:
            if self.action == "SAVE":
                name = save_selection(context)
                _log(context, f"Saved as prefab \"{name}\": Add > Prefabs > My Prefabs, in every scene.")
                return {"FINISHED"}
            made = build_scene(context, self.action)
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in made:
                obj.select_set(True)
            _undo(f"XR Starter Scene {self.action.title()}")
            _log(context, f"{self.action.title()} scene ready: {len(made)} objects in front of you.")
        except (editing.EditRejected, OSError, RuntimeError, ValueError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        return {"FINISHED"}


def _category_menu(category, label):
    def draw(self, context):
        layout = self.layout
        layout.label(text=f"{label.upper()} — PICK, SEE THE GHOST, TRIGGER PLACES", icon="PACKAGE")
        for key, (item_label, item_category, icon, _b, _m, _s) in PREFABS.items():
            if item_category == category:
                layout.operator("xrwb.pick_prefab", text=item_label, icon=icon).kind = f"BUILTIN:{key}"
    return type(f"XRWB_MT_prefabs_{category.lower()}", (bpy.types.Menu,),
                {"bl_idname": f"XRWB_MT_prefabs_{category.lower()}", "bl_label": label, "draw": draw})


CATEGORY_MENUS = tuple(_category_menu(key, label) for key, label, _icon in CATEGORIES)


class XRWB_MT_prefabs_mine(bpy.types.Menu):
    bl_idname = "XRWB_MT_prefabs_mine"
    bl_label = "My Prefabs"

    def draw(self, context):
        layout = self.layout
        layout.label(text="MY PREFABS", icon="USER")
        layout.operator("xrwb.prefab_action", text="Save Selection as Prefab", icon="FILE_TICK").action = "SAVE"
        names = user_prefabs()
        if not names:
            layout.label(text="Build something, select it, then Save.")
        for name in names:
            layout.operator("xrwb.pick_prefab", text=name, icon="PACKAGE").kind = f"USER:{name}"


class XRWB_MT_prefabs(bpy.types.Menu):
    bl_idname = "XRWB_MT_prefabs"
    bl_label = "Prefabs"

    def draw(self, context):
        layout = self.layout
        layout.label(text="PREFABS — READY-MADE STARTERS", icon="PACKAGE")
        for (key, label, icon), menu in zip(CATEGORIES, CATEGORY_MENUS):
            layout.operator("xrwb.open_hand_menu", text=f"{label}  ›", icon=icon).menu = menu.bl_idname
        layout.operator("xrwb.open_hand_menu", text="My Prefabs  ›", icon="USER").menu = "XRWB_MT_prefabs_mine"
        layout.separator()
        layout.label(text="Starter scenes (in front of you)")
        for key, label, icon in SCENES:
            layout.operator("xrwb.prefab_action", text=label, icon=icon).action = key


CLASSES = (XRWB_OT_pick_prefab, XRWB_OT_prefab_action, *CATEGORY_MENUS, XRWB_MT_prefabs_mine, XRWB_MT_prefabs)
