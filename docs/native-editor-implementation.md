# Native XR editor card: implementation and verification

This is an implemented Blender 5.1.2 native vertical slice, not a replacement UI
drawn by the add-on. Hardware acceptance remains separate from successful native
compilation. See the runtime manifest and current test logs for that boundary.

Frozen candidate: `native/releases/m2-editor-card/blender.exe`, SHA-256
`9972EA44ABF8C2125C8230C7591AD8D4465D5C96A8B694E65E583E51718A8ED2`.
The targeted native build, full link/install, actual-input-function fixture,
Python syntax checks, and isolated background API smoke passed. The native
worker did not start the headset/GUI smoke. The root hardware owner subsequently
passed a real Blender UI smoke (numeric transform, undo, popup text/capture,
capture release and session stop), with evidence in
`.tmp_xr_probe_096834ca235c4e99874eb9d5bac1bdfd`. Only session states 1/2/3 were
observed: this does not prove headset-visible presentation. A later editor-hidden
repeated-start/stop soak crashed inside `vrclient_x64.dll` after 79 stopped
sessions; resource-lifetime investigation is ongoing. Full M2 source delta from
pinned upstream: `native/xr-workbench-m2.patch`.

## Architecture

The session-owned card composes the **actual source Blender window** into a
`GPUOffScreen`. `wm_draw_window_to_offscreen()` reuses the normal window compositor:
cached editor regions, overlapping sidebars, floating menu regions, and window
callbacks. The texture is drawn as one world-space quad in each XR eye, after
viewport composition, with the binding's upside-down convention applied. It does
not create another Blender process, duplicate widgets, capture the operating
system desktop, or read room-camera pixels. Blender child windows (for example
its File Browser or Preferences) are followed when no button is captured.

This deliberately broadens the initial Properties-only audit: preserving the
source window's native pixel coordinates also preserves normal popup ownership
and editor hit-testing. It is a spatial view of the same workspace, not an
independent second screen layout. Changes and undo affect the original document.
Full-window text size therefore depends on the window's resolution, UI scaling,
and chosen physical card width; arbitrary DPI/monitor combinations need QA.

The XR surface owns its texture and frees it before its graphics context is
destroyed. Runtime window pointers are membership-checked before dereference;
closing the source window hides the card. A hidden texture can remain cached
until session teardown. Runtime-only card state is not written into `.blend`.

## Native Python contract

These operators require a ready XR session and a valid event context:

```python
bpy.ops.wm.xr_editor_surface(visible=True, recall=True, width=1.6, distance=1.2)
bpy.ops.wm.xr_editor_surface_event(
    type='MOUSEMOVE', value='NOTHING', use_ray=True,
    ray_origin=(0, 0, 0), ray_direction=(0, 0, -1), pointer_id=0)
bpy.ops.wm.xr_editor_surface_event(type='A', value='PRESS', unicode='a')
bpy.ops.wm.xr_editor_surface(visible=False)
state = bpy.types.XrSessionState.editor_surface_info(context)
```

`state` is `(visible, pixel_width, pixel_height, captured_pointer,
captured_button, composed_frames)`. The uncaptured pointer is `-1`.
`composed_frames` counts completed editor compositions, **not headset optical
presentation**, and stays zero until the texture is actually composed.

Pointer events accept `use_ray=False, u=..., v=...` for deterministic testing.
UV origin is bottom-left; values outside the card reject a new click. An owned
drag clamps off-card movement and releases even after a ray misses the plane.
Only one pointer and one button capture can own the card at a time. A different
pointer cannot steal capture. Recall/resize is rejected while captured.

Keyboard events preserve the last pointer coordinates and the existing Blender
field focus; their ray/UV parameters are ignored. `unicode` accepts one UTF-8
character on a keyboard press. Explicit `shift`, `ctrl`, and `alt` modifiers are
supported. Send ordinary release events as well. This is typed text delivery,
not OS input injection or an IME composition implementation.

Events go to that window's normal Blender queue, so existing editor/modal
handlers, popup routing, operators, and undo handle them. A scoped native flag
suppresses OS cursor warp and pointer grab for these events. A captured XR drag
excludes physical mouse events in the target window. Physical Escape reclaims
desktop control and hides the card; native typed Escape closes the focused
popup/modal without hiding the card. Hiding or destroying the session queues
Escape and a held-button release. Desktop behavior outside this scope is unchanged.

For diagnostics only:

```python
bpy.ops.wm.xr_editor_surface_capture(filepath='./native/evidence/editor.png')
```

The existing parent directory and an absolute `.png` path are required. This
queues a one-time readback of the **next editor texture only**, then writes
`XR_EDITOR_CAPTURE saved=1 frame=...` when the write succeeds. `FINISHED` means
queued, not saved. It does not capture either eye or the camera.

## Verification entry points

- `native/test_editor_input.py`: compiles the actual native input validation
  function against a small identity-pose RNA/event fixture. Checks rejected
  invalid events/text/rays/UVs, capture ownership, off-card release, modifiers,
  and keyboard focus coordinates. Does not prove Blender editor dispatch.
- `native/test_editor_api.py`: candidate Blender background registration and
  safe no-session status/poll check. Does not start OpenXR.
- `native/test_editor_surface.py`: explicitly opted-in factory-startup GUI/XR
  test, using modal window-event context (never a global-context app timer).
  Requires `XRWB_EDITOR_TEST_ALLOW=1`; optional `XRWB_EDITOR_CAPTURE_DIR` captures
  actual composed window and search popup. Exercises ordinary 3D numeric
  transform and undo, popup text events, capture cancellation, and session stop.
  This must be coordinated with the hardware owner; it is not implicitly run.

Build with `native/build_native.ps1 -Target bf_windowmanager -Jobs 8`, then
`-Target install -Jobs 8`. The preserved M0 release is
`native/releases/m0-camera-off/blender.exe`, SHA-256
`20860BC3CE48B05013E064EB51F74BDD6EA633A539678C3918327A07E38AA14E`.

## Acceptance still requiring evidence

Headset legibility, stereo geometry, physical controller precision, individual
button/slider/dropdown semantics, arbitrary file dialogs and child-window
transitions, third-party editors, mixed-DPI windows, and keyboard/IME workflows
are not proven by compilation or the input fixture. OS-native dialogs remain
a desktop handoff; only windows owned by this Blender instance are embedded.
Card alpha does not change room/object alpha or prove calibrated passthrough.
The frame readback is an explicit diagnostic operation and may stall that frame.

## Experimental native bare-hand API (post-M2 source)

The combined frozen candidate is `native/releases/m2-hands-interop/blender.exe`,
SHA-256 `B7F0E726B6E81B1377E4BF773CAE7E496AC736AC5A7BB49FC8E14BA872AAAE45`.
It includes the coordinated graphics interop cleanup fix. Source patch:
`native/xr-workbench-m2-hands-interop.patch`, SHA-256
`E547B6D2FF0D9F612428215B947E66A2ECC446741F0619FCAE7D61E084C1144E`.
Native compile/link/install, editor API smoke, hand API smoke, the native hand
method fixture, editor input fixture and passthrough lifetime fixture passed.
The root's extracted graphics-destructor fixture also passed. Physical runtime
soak qualification is tracked separately by the root; this candidate's build
success alone is not evidence that the previous SteamVR crash is fixed.

Native sampling uses `XR_EXT_hand_tracking`, and negotiates
`XR_EXT_hand_tracking_data_source` only when available. Merely advertising basic
hand tracking does **not** establish optical/bare-hand provenance. This path has
no controller-skeleton fallback and no built-in camera-to-hand inference model.
An external, compatible provider is still required for Index camera-only input.
UNOBSTRUCTED is a provider source declaration, not proof of camera calibration
or independently measured optical accuracy.

```python
state = bpy.types.XrSessionState
state.hand_tracking_enable(context, enabled=True)  # Explicit camera/provider opt-in.
info = state.hand_tracking_info(context, hand='LEFT')
thumb = state.hand_joint_sample(context, hand='LEFT', joint=5)
index = state.hand_joint_sample(context, hand='LEFT', joint=10)
state.hand_tracking_enable(context, enabled=False)
```

Tracker creation is off by default. Enabling returns whether tracker creation
succeeded, not whether a hand is visible. Two session-owned trackers are freed
on explicit disable, partial creation failure, or session destruction. Calls
without a session safely return unavailable/zero data; disabling is idempotent.

`info` has six integers:

1. System hand-tracking capability available.
2. Active, fresh, focused-session sample with **UNOBSTRUCTED** provenance and
   a valid Blender XR view transform.
3. Reported data source: 0 unknown, 1 unobstructed, 2 controller (rejected).
4. Predicted display timestamp, whole seconds.
5. Timestamp nanosecond remainder (combine with seconds exactly).
6. Lifecycle epoch, stable across consecutive frames and changing on tracker,
   source-kind, focus-loss, or reference-space discontinuities; survives session
   recreation until the 31-bit counter wraps. Zero means no sample yet.

The timestamp is OpenXR `XrTime`, not Unix/wall-clock time. Samples use the frame's
predicted display time and the session's own reference space. Native access
rejects samples older than 250 ms, unfocused sessions, unknown provenance,
controller-derived sources, failed locate calls, and invalid poses. Each joint
returns nine floats: `(flags, x, y, z, qw, qx, qy, qz, radius)`.
Position-only valid/tracked joints remain usable with zero quaternion components;
valid orientation is never fabricated. The new 17-float
`hand_tracking_world_transform(context)` returns world-units-per-meter and the
exact column-major world-from-reference matrix used by the joint conversion.
Positions, orientations and radii are transformed with the exact existing
Blender controller base/navigation path into Blender world space. Invalid or
unverified samples are all zero. World-transformed non-finite results are also
cleared. Joint indices follow OpenXR: palm 0, wrist 1, thumb tip 5, index tip 10.

Flags preserve OpenXR bits: orientation-valid 1, position-valid 2,
orientation-tracked 4, position-tracked 8. Position-only interactions require
bits2+8 (10); tools that use orientation additionally require bits1+4. Require
source1, active true, stable lifecycle epoch and advancing requested-pose time;
do not use a merely
valid but untracked estimate to confirm a gesture.

`native/test_hand_tracking.py` compiles the actual GHOST methods with mock OpenXR
calls. It checks partial creation cleanup, idempotence, function/locate failure,
unobstructed/controller/unknown provenance, freshness, focus loss, invalid pose,
and generation changes across restart. `native/test_hand_api.py` checks the
compiled Blender API in background without starting a runtime/provider. Neither
test proves physical hand tracking, gesture accuracy, or camera calibration.
