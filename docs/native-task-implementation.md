# Native semantic session card (v2 first ticket)

Candidate: `native/releases/m3-native-task-card-stereo/blender.exe`, SHA256
`2D2F30E8DB5EFB6000D257EA2B4C3D9172E82CDE4606E322E00D22392A15BF01`.
Pinned Blender 5.1.2, same limited-feature/mismatched-library build constraints as
the preceding candidates. This is not the complete modeling release.

The primary native card is generated from two semantic action descriptors, not
a desktop screenshot. Stable identities are `session.panel_opacity` (1) and
`session.exit_xr` (2). Native GPU primitives and Blender font drawing use the
same immutable 1000x500 layout bounds as ray hit testing. The runtime publishes
a generation only after composition. Composition is not headset visibility.
The existing desktop editor card is the separate expert compatibility route.

## Public contract

- `bpy.ops.wm.xr_task_card(visible=True, recall=True, width=.65, distance=.9)`.
- `bpy.types.XrSessionState.task_card_info(context)` returns six integers:
  visible, layout generation, composed generation, captured pointer (-1 none),
  captured action (0 none), background opacity percent.
- `bpy.types.XrSessionState.task_card_pose(context)` returns eight floats:
  shared world position XYZ, quaternion WXYZ, and card width. This is the exact
  drawing/hit pose and is useful for explicit simulator tests.
- `bpy.ops.wm.xr_task_card_event(phase='MOVE'|'PRESS'|'RELEASE'|'CANCEL',
  generation=..., pointer_id=0|1, ray_origin=XYZ, ray_direction=XYZ)`.
  Rays use world coordinates and must enter the front of the card.

MOVE returns FINISHED for a button, visible panel backing, or owned capture;
only space outside the panel may pass to scene input. Backing/gutter hits never
arm or execute a command. PRESS requires neutral MOVE from that pointer, a matching
composed generation, and no competing task or legacy capture. RELEASE invokes
only the same captured target; release outside cancels. CANCEL ignores the
generation and never invokes. Competing pointers cannot cancel the owner.
Recall/resize is refused while captured. Exit uses the existing native session
stop path and never touches runtime references afterward.

Production input requires OpenXR FOCUSED. Main-thread frame preparation cancels
task capture on focus loss. Qualified-hand recognition must send CANCEL on
required tracking loss, never synthesize RELEASE. The API accepts intents, not
a claim of qualified optical sensing; the provider/recognizer remain separate.
Generation identity is process-global across session restarts (31-bit wrap is
not a practical normal-use boundary). Runtime-only state is not saved to .blend.
Production placement/input also requires runtime-valid rendered view poses.
These flags reset each frame, so a non-rendered frame cannot retain validity.

Background cycles 85 -> 25 -> 55 -> 85 percent. Text remains opaque. No object
materials, scene alpha, room camera opacity, model dimensions, or history change.
The task has no popup, text-entry surface, or virtual keyboard. Native modeling
actions/transactions, adaptive pages, near poke, and optical qualification remain
subsequent work, not implied by these two safe session actions.

## Verification and explicit limits

Full compile/link/install passed, as did no-session background API tests for
editor, hands, and task card. `native/test_task_logic.py` compiles the production
header and tests shared bounds, neutral entry, capture arbitration, generation
invalidation, off-target release, repeated release, and cancellation.
Existing input, hand lifecycle/provenance, interop lifetime, and fault mocks pass.

The root GUI harness passed on the original native-card candidate and on the
visual-check candidate, but visual inspection exposed mirrored labels in the
actual projection-eye image. Those builds are **not visually qualified**.
The basis diagnostic candidate adds once-per-generation eye-space diagnostics
to resolve the real transform, not flip or retouch the screenshot.
Root's raw-pose probe showed `xrLocateViews` identity eye rotation but
`xrLocateSpace(VIEW)` 180-degree head roll. Saved and current Blender head poses
were identical: the mismatch was not a stale recall. The corrective source
anchors to the actual rendered stereo center and averaged eye orientation,
then uses the identical pose for both drawing and hit testing. This avoids a
fixed compensating reflection and supports ordinary canted stereo eyes.
The inherited VIEW-space disagreement remains a runtime qualification concern.
Raw OpenGL eye captures intentionally retain the D3D transfer Y convention.
They are not post-transfer headset screenshots.

`native/test_task_view.py` compiles the actual anchor-update body and verifies
stereo centering, use of the rendered basis, finite validation, and rejection
of a stale first eye. It does not establish physical headset calibration.

Follow-up source also isolates the card's BLF font for its session lifetime,
restores the prior GPU blend/depth state, and re-enables alpha before every
rectangle because Blender's font flush resets blending. The guarded image
capture reads the actual composed projection eye, only with test consent,
camera layer disabled, and session passthrough off; it is not a room recorder.

## Hand mapping and lifetime contract correction

`hand_joint_sample` is a **flat nine-float vector**, not a heterogeneous tuple:
flags, position XYZ, orientation WXYZ, radius. Position-valid joints do not
require orientation validity. Position-only flags10 samples preserve position
and radius and publish zero quaternion components, never invented valid
orientation. A malformed quaternion declared valid still rejects the joint.

`hand_tracking_world_transform(context)` returns 17 floats: world-units-per-meter
then the column-major 4x4 world-from-reference transform (translation in full
array entries13..15). It uses the exact helper as the joint conversion; it
includes base scale, viewer/navigation/view/scene-unit scale, base/navigation
poses and reference offsets. Invalid/unavailable mappings return zeros.
Actual-body C++ fixture verifies scale2*3=6, translated/rotated positions,
offsets, zero orientation for flags10, radius scaling and timestamp guards.

The six-int `hand_tracking_info` final generation is a stable lifecycle epoch,
not a per-frame counter. It changes on tracker lifetime changes, focus loss,
reported source-kind changes, and reference-space change events. Requested
pose time is the advancing freshness value; it is not a sensor exposure time.
The OpenXR interface cannot identify an unreported internal provider swap that
retains the same source-kind declaration. The fixture checks stable consecutive
frame epochs, advancing time, pending reference-space boundary and restart.

`native/test_task_card.py` is a modal window-context GUI harness for root QA.
It requires both camera-layer exclusion and `XRWB_TASK_TEST_ALLOW=1`; only then
may `allow_simulated_input=True` bypass focus for an explicitly logged SIMULATOR.
This switch is not exposed in shipped UI. The harness checks composed generation,
opacity, stale and cancelled release, native exit, and unchanged Cube transform.
Root ran the corrected stereo candidate successfully in
`.tmp_xr_probe_a8e85b6fe8a540dfb0229ef4ca262734`. The test passed generation,
capture, opacity, cancellation, native exit and unchanged scene checks under
explicit simulator mode. The raw eye PNG was inspected by root and native agent:
horizontal text/basis is corrected; vertical inversion remains the pre-D3D
transfer convention. Basis output is right=(1,0,0), up=(0,1,0), originZ=-.9.
No optical, camera, final-swapchain readback, human headset visibility, or
typography/reach comfort claim follows from this test.

### Transfer evidence boundaries

`GHOST_ContextD3D::isUpsideDown()` returns true and the D3D graphics binding
delegates its `needsUpsideDownDrawing()` query. The native card negates the
projection Y row, matching the established scene viewport's inverted Y rectangle.
The actual interop blit uses equal source/destination rectangle bounds; D3D
`CopyResource` does not perform a second transform. The compensation is applied
before copying between GL/D3D row-origin conventions. Nothing flips the PNG to
hide the defect.

`native/test_task_transfer_contract.py` checks that source contract and a
numerical row-origin example; the actual-body fault fixture checks unchanged
blit bounds. These are **not** a post-transfer GPU texture readback or physical
headset observation. The root raw-eye capture proves native content composition
and horizontal orientation, not complete display/calibration qualification.

## Preserved failure-path candidate

`native/releases/m2-hands-interop-2/blender.exe`, SHA256
`5C2D9FA12D3B128E261353CB1CCBD567746644167594C0567A2272C8CF2BD5B7`,
is separately immutable. It propagates failed NV interop unlock to the existing
no-CopyResource exception guard; retains the locked flag for cleanup; rejects
unequal/zero view sizes before initialization instead of re-registering an
incorrectly sized D3D texture; cancels captured editor input when its child
window disappears; and rejects RNA-unrepresentable hand timestamps consistently
in status and joint APIs. Actual-body fault tests cover those paths.

Root reported 100/100 clean lifecycle cycles on the preceding B7F candidate,
not a physical-visibility pass and not a soak of either newer candidate.
The NVIDIA-specific success does not qualify AMD interop. Unequal-eye/quad-view
sizes now fail closed; supporting them needs correctly sized per-view resources.
