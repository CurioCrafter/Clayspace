"""SDF modeling: shape with smooth, melting blobs, then bake to a mesh.

This is implicit-surface (signed-field) modeling on Blender's own metaballs,
with no add-on needed. Every blob adds to a field and the surface is where
the field crosses a threshold, so blobs melt into each other smoothly.
Carve blobs subtract, which acts like a smooth boolean.

* SDF Blobs tool: hold the trigger. A blob appears at the pen tip (the prop's
  tip with Tool Props on, else 20 cm in front of the controller; touching a mesh
  puts it on the contact point) and follows it. In Trail mode, moving leaves a
  string of blobs, like drawing with clay. A harder squeeze makes bigger
  blobs. Capsules and ellipsoids turn with your hand.
* Trigger on an existing blob grabs it: move it and everything re-melts live.
* Carve (toggle): new blobs cut away instead of adding.
* Blend: how softly blobs merge (stiffness).
* Detail: surface resolution.
* Bake to Mesh turns the field into ordinary geometry for sculpting,
  modifiers or export. Every stroke is one undo step; cancel removes what the
  stroke added or puts a grabbed blob back.
"""
from __future__ import annotations

import bpy
from mathutils import Quaternion, Vector

from . import editing, physical_tools, viewer_scale

SDF_KEY = "xrwb_sdf"
SHAPES = (("BALL", "Ball", "MESH_UVSPHERE"), ("CAPSULE", "Capsule", "MESH_CAPSULE"),
          ("ELLIPSOID", "Ellipsoid", "MESH_UVSPHERE"), ("CUBE", "Rounded Cube", "MESH_CUBE"))
MODES = (("TRAIL", "Trail", "Moving leaves a string of blobs, like drawing with clay"),
         ("PLACE", "Place", "One blob follows the pen tip; release places it"))
PEN_TIP_FAR_M = 0.2
TRAIL_SPACING = 0.6  # Fraction of the radius between trail blobs.


def pen_tip_m() -> float:
    from . import creative_tools
    return creative_tools.pen_tip_m()


def __getattr__(name):
    if name == "PEN_TIP_M":
        return pen_tip_m()
    raise AttributeError(name)


def _log(context, message, level="INFO"):
    from . import interaction_log
    interaction_log.emit("sdf", message, context, level=level)


def is_sdf(obj) -> bool:
    return obj is not None and obj.type == "META" and SDF_KEY in obj.keys()


def new_sdf_object(context, location, radius):
    data = bpy.data.metaballs.new("XR SDF")
    data.resolution = max(0.004, radius * 0.25)
    data.render_resolution = data.resolution * 0.5
    data.threshold = 0.6
    obj = bpy.data.objects.new("XR SDF", data)
    obj[SDF_KEY] = True
    obj.location = location
    context.collection.objects.link(obj)
    from . import prefabs
    data.materials.append(prefabs.material("CLAY"))
    return obj


def field_radius(visible, stiffness, threshold):
    """Element radius whose surface lands at ``visible``.

    Blender's metaball field is stiffness * (1 - (d/R)^2)^3 and the surface is
    where it equals the threshold, so a ball shows at R * sqrt(1 - (t/s)^(1/3)).
    Solving that makes the ring at the pen tip exactly the blob you get.
    """
    ratio = min(0.95, max(1.0e-3, threshold / max(stiffness, 1.0e-3)))
    return visible / max(0.05, (1.0 - ratio ** (1.0 / 3.0)) ** 0.5)


def world_centre(obj, element):
    return obj.matrix_world @ Vector(element.co)


def element_under_ray(obj, origin, direction):
    """The blob the ray passes through (nearest along the ray), or None."""
    direction = Vector(direction).normalized()
    scale = max(obj.matrix_world.to_scale())
    best, best_along = None, float("inf")
    for element in obj.data.elements:
        centre = world_centre(obj, element)
        along = (centre - Vector(origin)).dot(direction)
        if along <= 0.0:
            continue
        miss = (Vector(origin) + direction * along - centre).length
        if miss <= element.radius * scale * 0.8 and along < best_along:
            best, best_along = element, along
    return best


class SdfStroke(physical_tools._Stroke):
    """Add (or carve) blobs at the pen tip, or grab one and move it."""

    label = "SDF"

    def __init__(self, context, pose, owner, generation, units, settings, contact=None):
        super().__init__(context, pose, owner, generation, units)
        self.contact_mode = contact is not None
        if context.collection is None or not context.collection.is_editable:
            raise editing.EditRejected("Choose a local editable collection first")
        self.selection = editing._selection_snapshot(context)
        self.base_radius = max(0.002, float(settings.sdf_radius_m)) * units
        self.shape = str(settings.sdf_shape)
        self.carve = bool(settings.sdf_carve)
        self.blend = float(settings.sdf_blend)
        self.trail = str(settings.sdf_mode) == "TRAIL"
        self.pressure = 1.0
        self.created_object = False
        self.added = []  # (element index at creation time is unstable) keep references
        self.moved = None
        origin, direction, rotation = physical_tools._pose_ray(pose)
        active = context.view_layer.objects.active
        self.obj = active if is_sdf(active) and not active.library else None
        if self.obj is not None:
            grabbed = element_under_ray(self.obj, origin, direction)
            if grabbed is not None:
                self.moved = (grabbed, Vector(grabbed.co), Vector(pose.position))
                self.label = "SDF Move"
                self.changed = False
                return
        tip = self._tip(pose)
        if self.obj is None:
            self.obj = new_sdf_object(context, tip, self.base_radius)
            self.created_object = True
            for other in tuple(context.selected_objects):
                other.select_set(False)
            self.obj.select_set(True)
            context.view_layer.objects.active = self.obj
        self.label = "SDF Carve" if self.carve else "SDF Add"
        self._add(tip, rotation)
        self.last_tip = tip
        self.changed = True

    def _tip(self, pose):
        """The tip; touching a mesh, the blob's centre sits on the contact point."""
        found = physical_tools.current_contact(self.hand) if self.contact_mode else None
        if found is not None:
            return Vector(found.point)
        origin, direction, _rotation = physical_tools._pose_ray(pose)
        return origin + direction * pen_tip_m() * self.units

    def radius(self):
        return self.base_radius * (0.5 + 0.5 * self.pressure)

    def _add(self, tip, rotation):
        element = self.obj.data.elements.new(type=self.shape)
        self._place(element, tip, rotation)
        element.use_negative = self.carve
        element.stiffness = self.blend
        self._place(element, tip, rotation)  # Again: the radius depends on the stiffness just set.
        self.added.append(element)
        self._fx("touch")  # The squish of clay going on.
        return element

    def _place(self, element, tip, rotation):
        inverse = self.obj.matrix_world.inverted_safe()
        scale = max(1.0e-9, max(self.obj.matrix_world.to_scale()))
        element.co = inverse @ tip
        visible = self.radius() / scale
        element.radius = field_radius(visible, element.stiffness, self.obj.data.threshold)
        if self.shape == "CAPSULE":
            element.size_x = visible  # Half-length of the core: a capsule about 4 blobs long.
        elif self.shape == "CUBE":
            element.size_x = element.size_y = element.size_z = visible * 0.6
        elif self.shape == "ELLIPSOID":
            element.size_x, element.size_y, element.size_z = 1.0, 0.6, 0.6
        local_rotation = inverse.to_quaternion() @ Quaternion(rotation)
        element.rotation = local_rotation

    def _valid(self, context):
        try:
            return super()._valid(context) and self.obj.name in context.view_layer.objects
        except ReferenceError:
            return False

    def _restore(self, context):
        try:
            if self.created_object:
                data = self.obj.data
                bpy.data.objects.remove(self.obj, do_unlink=True)
                if data.users == 0:
                    bpy.data.metaballs.remove(data)
                editing._restore_object_selection(context, self.selection)
                return
            if self.moved is not None:
                element, start, _hand = self.moved
                element.co = start
            for element in reversed(self.added):
                self.obj.data.elements.remove(element)
            self.added.clear()
        except ReferenceError:
            pass

    def _apply(self, context, pose):
        if self.moved is not None:
            element, start, hand = self.moved
            delta = Vector(pose.position) - hand
            element.co = start + self.obj.matrix_world.inverted_safe().to_3x3() @ delta
            self.changed = delta.length > 1.0e-6
            return f"Moving a blob {delta.length * 100:.1f} cm"
        _origin, _direction, rotation = physical_tools._pose_ray(pose)
        tip = self._tip(pose)
        if self.trail and (tip - self.last_tip).length >= self.radius() * TRAIL_SPACING:
            self._add(tip, rotation)
            self.last_tip = tip
        else:
            self._place(self.added[-1], tip, rotation)
            if not self.trail:
                self.last_tip = tip
        return f"{'Carving' if self.carve else 'Adding'}: {len(self.added)} blob(s)"

    def commit_message(self):
        if self.moved is not None:
            return "Blob moved; the surface re-melted (one undo)"
        return f"{len(self.added)} {'carving' if self.carve else ''} blob(s) added (one undo)".replace("  ", " ")


def begin(context, pose, owner, generation, contact=None):
    if context.mode != "OBJECT":
        raise editing.EditRejected("Switch to Object Mode for SDF blobs")
    settings = context.window_manager.xrwb_settings
    stroke = SdfStroke(context, pose, owner, generation, viewer_scale.world_units_per_metre(context), settings,
                       contact=contact)
    editing._ACTIVE_TRANSACTION = stroke
    return stroke


def _undo(message):
    if not bpy.ops.ed.undo_push.poll() or "FINISHED" not in editing.push_undo(message=message):
        raise RuntimeError("Blender rejected the undo entry")


def sdf_action(context, action):
    if context.mode != "OBJECT" or editing.active_transaction() is not None:
        raise editing.EditRejected("Switch to Object Mode and release the current edit")
    settings = context.window_manager.xrwb_settings
    if action == "NEW":
        for obj in context.selected_objects:
            obj.select_set(False)
        context.view_layer.objects.active = None
        settings.edit_tool = "sdf_blob"
        return "The next blob starts a new SDF object"
    obj = context.active_object
    if not is_sdf(obj):
        raise editing.EditRejected("Select an SDF object (trigger it with Select) first")
    if action == "BAKE":
        if not obj.data.elements:
            raise editing.EditRejected("Add blobs first")
        with context.temp_override(active_object=obj, object=obj, selected_objects=[obj],
                                   selected_editable_objects=[obj]):
            if "FINISHED" not in bpy.ops.object.convert(target="MESH"):
                raise editing.EditRejected("Blender could not bake the field")
        baked = context.view_layer.objects.active
        baked.name = "XR SDF Mesh"
        if SDF_KEY in baked.keys():
            del baked[SDF_KEY]
        _undo("XR Bake SDF")
        return f"Baked to a mesh ({len(baked.data.vertices)} vertices): sculpt, modify or export it"
    if action in {"FINER", "COARSER"}:
        factor = 0.7 if action == "FINER" else 1.0 / 0.7
        obj.data.resolution = min(1.0, max(0.002, obj.data.resolution * factor))
        obj.data.render_resolution = obj.data.resolution * 0.5
        _undo("XR SDF Detail")
        return f"Surface detail {obj.data.resolution * 100:.1f} cm"
    if action == "UNDO_BLOB":
        if not obj.data.elements:
            raise editing.EditRejected("No blobs left")
        obj.data.elements.remove(obj.data.elements[-1])
        _undo("XR Remove Blob")
        return "Last blob removed"
    if action == "SMOOTHER":
        for element in obj.data.elements:
            element.stiffness = max(0.3, element.stiffness * 0.75)
        _undo("XR SDF Smoother")
        return "Blobs melt together more softly"
    raise editing.EditRejected("Unknown SDF action")


class XRWB_OT_sdf_action(bpy.types.Operator):
    bl_idname = "xrwb.sdf_action"
    bl_label = "SDF"
    bl_description = "SDF modeling: new object, bake to mesh, detail, smoother, remove last blob"
    action: bpy.props.EnumProperty(items=(("NEW", "New SDF Object", ""), ("BAKE", "Bake to Mesh", ""),
                                          ("FINER", "Finer Detail", ""), ("COARSER", "Coarser Detail", ""),
                                          ("SMOOTHER", "Melt Softer", ""), ("UNDO_BLOB", "Remove Last Blob", "")),
                                   options={"SKIP_SAVE"})

    def execute(self, context):
        try:
            message = sdf_action(context, self.action)
        except (editing.EditRejected, RuntimeError) as exc:
            _log(context, str(exc), "WARNING")
            return {"CANCELLED"}
        _log(context, message)
        return {"FINISHED"}


class XRWB_OT_sdf_shape(bpy.types.Operator):
    bl_idname = "xrwb.sdf_shape"
    bl_label = "SDF Shape"
    bl_description = "Choose the blob shape and switch to SDF Blobs"
    shape: bpy.props.EnumProperty(items=[(k, label, "") for k, label, _i in SHAPES], options={"SKIP_SAVE"})

    def execute(self, context):
        settings = context.window_manager.xrwb_settings
        settings.sdf_shape = self.shape
        settings.edit_tool = "sdf_blob"
        _log(context, f"SDF {self.shape.title()}: hold the trigger to add; trigger a blob to move it.")
        return {"FINISHED"}


class XRWB_MT_sdf(bpy.types.Menu):
    bl_idname = "XRWB_MT_sdf"
    bl_label = "SDF Modeling"

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.xrwb_settings
        layout.label(text="SDF — BLOBS THAT MELT TOGETHER", icon="META_BALL")
        row = layout.row(align=True)
        for key, label, icon in SHAPES:
            row.operator("xrwb.sdf_shape", text=label, icon=icon,
                         depress=settings.sdf_shape == key and settings.edit_tool == "sdf_blob").shape = key
        layout.prop(settings, "sdf_carve", text="Carve (cut away)", toggle=True)
        layout.prop(settings, "sdf_mode", expand=True)
        layout.prop(settings, "sdf_radius_m", text="Blob Size")
        layout.prop(settings, "sdf_blend", text="Blend (lower = softer)")
        for action, label, icon in (("NEW", "New SDF Object", "ADD"), ("BAKE", "Bake to Mesh", "MESH_DATA"),
                                    ("FINER", "Finer Detail", "ZOOM_IN"), ("COARSER", "Coarser Detail", "ZOOM_OUT"),
                                    ("SMOOTHER", "Melt Softer", "MOD_SMOOTH"),
                                    ("UNDO_BLOB", "Remove Last Blob", "BACK")):
            layout.operator("xrwb.sdf_action", text=label, icon=icon).action = action
        obj = context.active_object
        if is_sdf(obj):
            layout.prop(obj.data, "threshold", text="Surface Threshold")
            layout.label(text=f"{len(obj.data.elements)} blobs in {obj.name}")


CLASSES = (XRWB_OT_sdf_action, XRWB_OT_sdf_shape, XRWB_MT_sdf)
