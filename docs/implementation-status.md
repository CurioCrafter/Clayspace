# Specification implementation ledger

Design baseline: `Blender_XR_Workbench_Specification.docx`, version 1.0,
September 21, 2026. Implementation target: Blender **5.1.2**, OpenXR, Windows,
Valve Index. This supersedes the original 4.5.11 target for the new native
track. The reference document is preserved.

## Current work

XR-0001 (M0) is in progress. The native source, diagnostics, and presentation
tasks are integrated separately so a working desktop diagnostic cannot be
misreported as verified headset behavior.

## September 23 physical milestones

With SteamVR closed and the Index temporarily in Windows extended-display
mode, the tester saw a stereo X/box pattern through the lenses. They then
launched the guarded 15-second Index camera preview and confirmed blue hand
outlines and the in-lens countdown. The latest hands-in-view text log records
230 stereo frames, 723 contour observations, `stream_failed=0`, and a normal
timer exit; no matching Windows application crash event was found. This is a
raw, uncalibrated stationary diagnostic at about 15 fps, not safe walk-around
passthrough or Blender interaction.

An isolated, source-built Windows Monado runtime now passes a bounded physical
`xrCreateInstance`/`xrGetSystem` probe on the Index with its configuration
parsed. It reports orientation support and no positional tracking, and does
not alter the registered OpenXR runtime. A subsequent camera-free physical
color test created an OpenXR session and submitted 694 stereo frames over
4.97 seconds. A hands-on test confirmed seeing the lens colors; the corner markers
were too high to inspect comfortably. The frame-test log is
`native/steamvr_free/physical-probe-logs/frame-probe-20260923-160350-54240.log`.
An instrumented follow-up submitted 684 frames and found orientation-valid and
position-valid flags on all 684 `xrLocateViews` results, while system capability
still reports no positional tracking. The Vive 3DoF path supplies a fixed
position and marks its relation valid; these flags allow Blender's current
view guard but do **not** prove 6DoF motion. Its log is
`native/steamvr_free/physical-probe-logs/frame-probe-20260923-161105-40228.log`.
The user then launched source-built Blender 5.1.2 via the isolated runtime and
confirmed seeing the scene and native task card in the Index, stable while
slowly turning their head. The run exited normally with code 0; status log:
`reports/index-physical-launch/launch-20260923-161715-55fcf102.txt`.
Its first attempt had failed during add-on registration before XR; the
callback-signature fix passed an isolated real-Blender smoke before the
successful retry. Camera passthrough and bare-hand Blender interaction remain
unverified. No Lighthouse base station is available for room-scale 6DoF
tracking.

## September 22 hardware/runtime boundary

The user additionally requires a SteamVR-free implementation. That is a new
runtime/display-driver milestone, not a launcher flag or a completed Blender
feature. The current frozen executable still uses SteamVR's OpenXR runtime.
Its metadata-only headset probe reported zero connected Lighthouse references
and no valid HMD pose; a hands-on test confirmed that no base station is available.
Accordingly, no present software build can be marked as physically verified
6DoF Index tracking on this setup. A process-scoped Monado simulator has opened
an isolated Blender XR session without SteamVR, but the Windows build has no
Index display driver or independent physical tracking. It does not change the
system's registered OpenXR runtime.

The repaired hands-ready Mercury camera retest processed 30 fresh stereo pairs
without the former OpenCV crash, but produced zero active hands and zero valid
HMD poses. This is a failed optical qualification gate, not proof that visible
hands were detected or safely registered to the Blender scene.

The SteamVR-free DirectShow path read five advancing Index stereo frames, then
an isolated Mercury diagnostic processed 14 non-flat, distinct pairs in a
consented 15-second run. The detector produced zero scores above 0.3 and zero
hand regions/keypoints. A source-level row-orientation mismatch was corrected
and passed an offline mapping test; a second consented Release run still
processed 14 pairs with zero scores above 0.3. A third consented Release run
processed 13 distinct pairs, measured center-eye grayscale means of 61.56/67.93,
and again recorded zero detector scores above 0.3. Frame acquisition averaged
0.94 seconds after the first frame, while Mercury processing averaged 2.68 ms
per pair. No camera images or joint traces were saved. This is independent camera
access, not a qualified optical hand provider or headset display.

A one-click stereo desktop preview now offers privacy-local, current-frame
blue estimated hand contours from palm-plus-landmark agreement, with a labeled
Mercury region fallback. The Release offline gate/mapping tests and a read-only
smoke on the user's supplied screenshot passed with one estimated contour per
camera half. The user deferred the new live preview test, so actual camera
cadence, physical contour placement, and headset presentation remain unverified.

Separately, a simulator-only OpenXR client submitted three synthetic stereo
projection frames through Monado's desktop compositor and verified that the
system OpenXR registration did not change. A screenshot-only, in-memory bridge
then submitted both annotated camera views through the same client: one blue
estimated contour per eye, matching GPU texture readback, and three accepted
stereo layers. These prove software texture upload and layer submission, not
live passthrough, compositor output pixels, or the physical Index display.

A separate full-feature Blender 5.1.2 source build passes factory-startup CPU
Cycles rendering, USD export, release configuration and native XR API smoke.
Its SteamVR-free Monado simulator GUI test passed Select/Move/Rotate/Scale
button routing, Add Cube, Undo and Redo, Exit, and scene restoration. The fifth
primary modeling slot (More) remains disabled, and physical optical input has
not been qualified. It has not been physically presented in the Index; see
`docs/full-feature-build-status.md` for its exact hash and remaining features.

The separate 0.3.0 manual-install extension ZIP was rebuilt from current
source, passed byte-for-byte source-parity checks, and passed Blender 5.1.2's
factory-startup extension manifest validation. It contains no native runtime,
camera provider, or optical model and is not a SteamVR-free headset release.

## Recorded evidence — September 21, 2026

- Compiled and installed the actual Blender 5.1.2 fork; exact executable,
  source and patch identity are recorded in `native/runtime-manifest.json`.
- Native resource-lifetime C++ regression passed against a mock OpenXR runtime.
  This covers creation failures and reverse-order teardown, not a real camera.
- 20 Python regression tests passed, including the original 4.5.11 tests.
- Ruff checks/format passed. The source-parity-audited 0.2.0 ZIP passed
  Blender's extension manifest validation and a freshly extracted install's
  registration, opacity, recall-matrix, geometry-cache and teardown smoke.
- The default native launcher passed its PowerShell 5.1 smoke; the actual
  GUI bootstrap passed with XR and camera auto-start both disabled.
- Native OpenGL desktop drawing, save/reload, no scene/image mutation, and
  handler cleanup passed. Stock OpenGL/Vulkan desktop drawing also passed.
- Connected Index: native OpenGL and stock Vulkan entered running sessions,
  invoked the XR draw callback twice, and stopped cleanly with camera off.
  SteamVR native states progressed 1/2/3, then 6/1/8/0 on stop; no visible or
  focused state was observed. Continuous frames and post-Recall visibility
  are explicitly **unverified**, not passed.
- The extended live harness first failed its continuous-draw assertion
  because the connected headset remained inactive. It now records Recall
  as UNOBSERVED in that case; `XRWB_REQUIRE_CONTINUOUS_DRAW=1` retains the
  strict assertion for a worn/awake headset. Startup/cleanup gates are separate.
- Following explicit user consent, Rectus v0.4.2-beta was installed. Its
  current-instance loader evidence and native `fb_passthrough` active marker
  passed after enabling the Index cameras and restarting SteamVR. Provider
  initialization and shutdown passed; sustained visible frames, calibration,
  freshness, and timing remain unverified. The layer is globally disabled
  between controlled tests. No camera pixels were saved.
- M2's actual source-built internal full-window editor passed a factory-scene
  test: native event dispatch changed a transform, undo restored it, a search
  popup composed into the editor texture, and capture/session teardown passed.
  Evidence: `.tmp_xr_probe_096834ca235c4e99874eb9d5bac1bdfd`. The captured
  editor texture was inspected visually. This is not headset visibility proof.
- 68 pure Python tests currently pass. Further transaction/hand integration
  is in progress and is not yet part of a qualified headset release.
- The isolated stock 5.1.2 transaction GUI smoke passed object and selected-mesh
  exact cancel and real undo/redo, parented/unapplied transforms, snapped
  move/rotate/scale, stale/tracking-generation rejection, and live topology
  invalidation. Input was explicitly simulator provenance, not camera input.
- M2's first 100-cycle lifecycle attempt crashed in `vrclient_x64.dll` after
  79 completed stop cycles. The crash alone does not identify responsibility.
  Source review found graphics bindings destroyed after their GL context and
  disabled interop unregister/close operations. The combined hand/interop
  candidate fixes that order and now checks failed GPU transfers. Its extracted
  native destructor regression and 10 guarded real camera-off restart cycles
  passed. Process commit was 1,415,655,424 bytes after cycle 1 and 1,392,922,624
  after cycle 10; no interop cleanup errors were logged. The 100-cycle retest
  then completed 100 starts and 100 stops without a crash or cleanup error
  (`.tmp_xr_probe_9b192180ad924839bc3471c60fb16ee6`). Commit grew from
  1,423,609,856 to 1,695,662,080 bytes, with a maximum of 1,702,625,280;
  cycles 20–100 remained approximately level. This is not a zero-leak proof,
  and the long editing soak/visible-frame gates remain unverified.

## Updated user interaction requirement

The user explicitly replaced keyboard/controller-first interaction with
**bare hands, no controllers, no virtual keyboard, using only the Index's
built-in cameras**. This overrides keyboard-specific document acceptance
details for the user-facing workflow. Virtual-keyboard work was removed;
controller bindings are an off-by-default developer fallback. Generic native
editor key-event tests remain diagnostic tests, not a proposed virtual keyboard.

No Ultraleap service or connected tracking device was detected. Camera-only
Windows hand tracking is the experimental provider track, not a completed
optical capability. Passthrough imagery must never be counted as joint tracking.

The earlier delayed live test exposed invalid global Blender context access
in the test harness. The live harness now uses a modal window-event context;
the shipped add-on has no polling app timer. Historical failed logs remain
available beside the final successful camera-off lifecycle runs.

| Milestone | Scope | Exit evidence required |
|---|---|---|
| M0 | Pinned native source, real capabilities, cube/panel, runtime recovery | Compiled fork; stereo calibration; provider coexistence; backend frame timing; camera failure |
| M1 | Independent opacity and workbench placement | AT-01–03, AT-09, AT-18 |
| M2 | Native task shell and internal editor surface | AT-05–06, AT-17, AT-21 |
| M3 | Transaction-safe object manipulation | AT-07–09, AT-13, AT-16 |
| M4 | Qualified optical hands | AT-04–06 plus measured registration and latency |
| M5 | Mesh components and topology adapters | AT-10–12, AT-14–15 |
| M6 | Retained-curve/SDF integration and heavy-scene proxies | Adapter tests and prior-gate regression |
| M7 | Packaging, lifecycle, accessibility, privacy | Supported configuration matrix; AT-19–22 |

## Architectural boundaries

- Blender remains the scene and undo authority. No separate imported model.
- One composition route may be active. Advertised passthrough is not proof of
  camera availability, calibration, or freshness.
- Optical input must carry optical provenance and live tracking evidence.
- Captured editing must preserve one owner and cancel on tracking failure.
- XR presentation helpers remain runtime data, outside saved model geometry.
- Main-thread context and native lifetimes must remain valid. The prior
  persistent-context-timer crash is an explicit regression constraint.
- Full UI access means an internal editor surface, including popup and text
  focus. The earlier SteamVR Desktop fallback does not satisfy AT-17.

## Hardware gates

No synthetic test or desktop preview qualifies stereo eye order, camera
calibration, 90 Hz pacing, optical accuracy, actual tracking loss, layer
coexistence, or headset comfort. These need the connected hardware and the
specification's measured fixtures. The first ticket remains open until those
results are recorded. The frozen launcher build and a separate full-feature
source build are available; neither qualifies the physical SteamVR-free Index stack.
