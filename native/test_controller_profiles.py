"""Headless Index/Quest PCVR binding contract; no runtime or hardware starts.

Uses synthetic collections with actual Blender operator/action-map RNA validation.
Physical profile negotiation requires a separate SteamVR headset test.
"""

from pathlib import Path
import sys
from types import SimpleNamespace as NS

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xr_workbench  # noqa: E402
from xr_workbench import controllers  # noqa: E402


class Collection(list):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory

    def new(self, *args):
        item = self.factory(*args)
        self.append(item)
        return item


def paths():
    return Collection(lambda path: NS(path=path))


def bindings():
    return Collection(lambda name, _replace: NS(name=name, component_paths=paths()))


def maps():
    return Collection(
        lambda _state, name, _replace: NS(
            name=name,
            actionmap_items=Collection(
                lambda name, _replace: NS(
                    name=name,
                    user_paths=paths(),
                    bindings=bindings(),
                    op_properties=NS(),
                )
            ),
        )
    )


COMMON = {
    "/input/grip/pose",
    "/input/aim/pose",
    "/input/trigger/value",
    "/input/thumbstick/x",
    "/input/thumbstick/y",
    "/input/thumbstick/click",
    "/output/haptic",
}
LEGAL = {
    (controllers.INDEX_PROFILE, hand): COMMON
    | {
        "/input/squeeze/force",
        "/input/a/click",
        "/input/b/click",
        "/input/trackpad/force",
    }
    for hand in (controllers.LEFT_HAND, controllers.RIGHT_HAND)
}
LEGAL[(controllers.QUEST_PROFILE, controllers.LEFT_HAND)] = COMMON | {
    "/input/squeeze/value",
    "/input/x/click",
    "/input/y/click",
}
LEGAL[(controllers.QUEST_PROFILE, controllers.RIGHT_HAND)] = COMMON | {
    "/input/squeeze/value",
    "/input/a/click",
    "/input/b/click",
}

xr_workbench.register()
contracts = {}
for family, action_count, binding_count in (
    ("INDEX", 22, 22),
    ("QUEST", 20, 20),
    ("AUTO", 22, 42),
):
    state = NS(actionmaps=maps())
    actionmap = controllers.build_actionmap(state, family)
    items = actionmap.actionmap_items
    assert len(items) == action_count
    assert sum(len(item.bindings) for item in items) == binding_count
    summary = controllers.action_spec_summary(family)
    assert (
        summary["total_actions"] == action_count
        and summary["total_bindings"] == binding_count
    )
    seen = set()
    for item in items:
        for identifier in ("type", "op_mode"):
            if hasattr(item, identifier):
                assert (
                    getattr(item, identifier)
                    in bpy.types.XrActionMapItem.bl_rna.properties[
                        identifier
                    ].enum_items
                )
        if hasattr(item, "op"):
            group, operator = item.op.split(".")
            props = getattr(getattr(bpy.ops, group), operator).get_rna_type().properties
            for name, value in vars(item.op_properties).items():
                assert name in props, (item.name, name)
                if props[name].type == "ENUM":
                    assert value in props[name].enum_items, (item.name, name, value)
        for binding in item.bindings:
            assert (
                binding.axis0_region
                in bpy.types.XrActionMapBinding.bl_rna.properties[
                    "axis0_region"
                ].enum_items
            )
            assert len(item.user_paths) == len(binding.component_paths)
            for hand, component in zip(item.user_paths, binding.component_paths):
                assert component.path in LEGAL[(binding.profile, hand.path)], (
                    family,
                    item.name,
                    component.path,
                )
                key = (binding.profile, hand.path, component.path, binding.axis0_region)
                assert key not in seen, (family, item.name, key)
                seen.add(key)
    by_name = {item.name: item for item in items}
    contracts[family] = by_name
    if family == "QUEST":
        # Quest B is Add, exactly like Index; panels move by gripping them.
        assert "panel_grab" not in by_name and "teleport" not in by_name
        assert by_name["add_menu"].bindings[0].component_paths[0].path == "/input/b/click"
        assert by_name["world_grab"].op == "xrwb.controller_world_grip"
        assert by_name["undo"].bindings[0].component_paths[0].path == "/input/x/click"
        assert by_name["cancel"].bindings[0].component_paths[0].path == "/input/y/click"
        assert (
            by_name["grab_move"].bindings[0].component_paths[0].path
            == "/input/squeeze/value"
        )
    # Hover is primary-hand based and sees the same actions for every family.
    state.controller_aim_active_get = lambda _ctx, index: index == 1
    state.controller_aim_location_get = lambda _ctx, _index: (0.0, 0.0, 1.0)
    state.controller_aim_rotation_get = lambda _ctx, _index: (1.0, 0.0, 0.0, 0.0)
    state.action_state_get = lambda *_args: (0.0, 0.0)
    context = NS(window_manager=NS(xr_session_state=state))
    assert controllers._hover_ray(context) == ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))

for family in ("QUEST", "AUTO"):
    for name, item in contracts[family].items():
        original = contracts["INDEX"][name]
        assert getattr(item, "op", None) == getattr(original, "op", None)
        assert vars(item.op_properties) == vars(original.op_properties)
        assert [path.path for path in item.user_paths] == [
            path.path for path in original.user_paths
        ]

# Left-handed schemes stay legal and conflict-free for every family: grips and
# sticks swap hands, buttons stay on their physical controllers.
for family, action_count, binding_count in (("INDEX", 22, 22), ("QUEST", 20, 20), ("AUTO", 22, 42)):
    actionmap = controllers.build_actionmap(NS(actionmaps=maps()), family, {"left_handed": True})
    items = actionmap.actionmap_items
    assert len(items) == action_count and sum(len(item.bindings) for item in items) == binding_count
    seen = set()
    for item in items:
        for binding in item.bindings:
            for hand, component in zip(item.user_paths, binding.component_paths):
                assert component.path in LEGAL[(binding.profile, hand.path)], (family, item.name, component.path)
                key = (binding.profile, hand.path, component.path, binding.axis0_region)
                assert key not in seen, (family, item.name, key)
                seen.add(key)
    by_name = {item.name: item for item in items}
    assert [p.path for p in by_name["grab_move"].user_paths] == [controllers.LEFT_HAND]
    assert [p.path for p in by_name["world_grab"].user_paths] == [controllers.RIGHT_HAND]

# The normal session hook honors the setting, not the backward-compatible builder default.
for family, count in (("AUTO", 42), ("QUEST", 20), ("INDEX", 22)):
    state = NS(
        actionmaps=maps(),
        action_set_create=lambda *_: True,
        action_create=lambda *_: True,
        action_binding_create=lambda *_: True,
        controller_pose_actions_set=lambda *_: True,
        active_action_set_set=lambda *_: True,
    )
    context = NS(
        window_manager=NS(
            xr_session_state=state, xrwb_settings=NS(controller_profile=family)
        )
    )
    ok, message = controllers.configure_action_map(context)
    assert ok and f"bindings={count} profile={family}" in message, message

state = NS(actionmaps=maps())
try:
    controllers.build_actionmap(state, "UNSUPPORTED")
except ValueError:
    assert not state.actionmaps
else:
    raise AssertionError("Invalid profile mutated the action map")

xr_workbench.unregister()
print(
    "XR_CONTROLLER_PROFILES_PASS index=22/22 quest=20/20 auto=22/42 conflicts=0 left_handed=3 hover=3 settings=3 hardware=0",
    flush=True,
)
