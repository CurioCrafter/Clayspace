"""Print Blender XR RNA surfaces used by the VR Workbench.

Run with Blender 4.5 in factory-startup background mode.  This script is
deliberately read-only and exits after describing the compiled API.
"""

from __future__ import annotations

import bpy


def describe_rna(rna_type: type) -> None:
    print(f"RNA_TYPE {rna_type.__name__}")
    for prop in rna_type.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        enum_items = ""
        if prop.type == "ENUM":
            enum_items = ",".join(item.identifier for item in prop.enum_items)
        print(
            "RNA_PROP "
            f"{rna_type.__name__}.{prop.identifier} "
            f"type={prop.type} array={getattr(prop, 'array_length', 0)} "
            f"readonly={prop.is_readonly} "
            f"enum={enum_items}"
        )
    for func in rna_type.bl_rna.functions:
        params = ",".join(
            f"{param.identifier}:{param.type}" for param in func.parameters
        )
        print(f"RNA_FUNC {rna_type.__name__}.{func.identifier} params={params}")


print(f"BLENDER_VERSION {bpy.app.version_string}")
print(f"XR_OPENXR_BUILD {bpy.app.build_options.xr_openxr}")

for name in (
    "XrSessionState",
    "XrSessionSettings",
    "XrActionMap",
    "XrActionMaps",
    "XrActionMapItem",
    "XrActionMapItems",
    "XrActionMapBinding",
    "XrActionMapBindings",
    "XrComponentPath",
    "XrComponentPaths",
    "XrUserPath",
    "XrUserPaths",
    "XrEventData",
    "Event",
):
    rna_type = getattr(bpy.types, name, None)
    if rna_type is None:
        print(f"RNA_MISSING {name}")
        continue
    describe_rna(rna_type)

print(
    "XR_TYPE_NAMES "
    + ",".join(name for name in dir(bpy.types) if name.startswith("Xr"))
)
session_state = bpy.context.window_manager.xr_session_state
for owner_name, owner in (
    ("actionmaps", session_state.actionmaps if session_state else None),
):
    if owner is None:
        print(f"XR_COLLECTION_MISSING {owner_name}")
        continue
    methods = ",".join(name for name in dir(owner) if not name.startswith("__"))
    print(f"XR_COLLECTION_METHODS {owner_name} {methods}")

for operator_id in (
    "wm.xr_session_toggle",
    "wm.xr_navigation_grab",
    "wm.xr_navigation_fly",
    "wm.xr_navigation_teleport",
    "wm.xr_navigation_reset",
):
    namespace, name = operator_id.split(".")
    op = getattr(getattr(bpy.ops, namespace), name)
    try:
        rna = op.get_rna_type()
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"OPERATOR_ERROR {operator_id} {exc!r}")
        continue
    props = ",".join(
        prop.identifier for prop in rna.properties if prop.identifier != "rna_type"
    )
    print(f"OPERATOR {operator_id} props={props}")

print("VR_WORKBENCH_XR_INTROSPECTION_PASS")
