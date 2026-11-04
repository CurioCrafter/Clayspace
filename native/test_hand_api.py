"""Background-only API smoke: safe unavailable values, never starts a provider."""
import bpy
assert not any(bpy.types.XrSessionState.hand_tracking_world_transform(bpy.context))

state = bpy.types.XrSessionState
assert state.hand_tracking_enable(bpy.context, enabled=True) is False
assert state.hand_tracking_enable(bpy.context, enabled=False) is True
for hand in ("LEFT", "RIGHT"):
    assert tuple(state.hand_tracking_info(bpy.context, hand=hand)) == (0, 0, 0, 0, 0, 0)
    for joint in range(26):
        assert tuple(state.hand_joint_sample(bpy.context, hand=hand, joint=joint)) == (0.0,) * 9
print("XR_HAND_API_PASS unavailable=1 samples_zero=52 provider_started=0", flush=True)
