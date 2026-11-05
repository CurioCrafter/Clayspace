# Hand-menu 0.6 — installed and SteamVR-tested, physical input pending

Historical 0.6.0 evidence below. The newer exact-hand anchor, desktop toggle and
native drawing/editing pass is tracked in [0.6.1 validation](native-interaction-validation.md).

The required result is a native menu at the controller, visible and clickable in
the headset while absent from both the desktop window and its mirrored XR panels.
The desktop remains separately usable. This is not a desktop-popover relabeling.

## Implementation

- Controller-opened popup/search/tooltip regions have scoped XR ownership.
- Desktop composition excludes them; a transparent native UI texture is drawn
  above the tracked hand with head-facing text and shared crop/ray coordinates.
- Native nested menus and deferred replacement popups retain ownership.
- The controller's pointer capture is pinned to the hand menu; misses/releases
  cannot select scene objects or activate desktop modal operators/keymaps.
- Whole-menu close is deferred outside UI dispatch and calls native cancellation
  for nested keep-open popup trees. Session teardown revalidates callback state.
- Navigation drains held actions until release after menu dismissal.

## Checks already run

- 104 Python unit tests; compileall and Ruff passed.
- Controller routing smoke passed with synthetic native stubs; this is not GPU proof.
- MSVC actual-source fixtures passed for ownership inheritance, deferred popup
  replacement, context/event provenance, desktop composition exclusion, selective
  cancellation, ray/capture validation and navigation release latches.
- Native compile and separate link passed. The candidate is
  `native/build-features/bin/blender-hand-menu-test.exe`, SHA256
  `FA44C0C9C77F31DC2F84D8E7754D86CCDAA248A942CF77A7DA4DD5A5485FC445`.
  It was initially tested separately without replacing the user's running app.
  After the user saved and closed Clayspace, the exact candidate was installed
  as `native/build-features/bin/blender.exe`; source and installed hashes match.
  The previous executable is retained at
  `native/releases/pre-hand-menu-20260924/blender.exe`, SHA256
  `FBEF1148907F1A2EBB7D3A7CECE91DE206E5FEC5CBC10E94EDFBDBCB09A4C71C`.
  This executable-only backup needs the existing build's runtime resources.
  Headless API checks on both candidate and installed default
  passed with all five native operators and safe no-session behavior; the RNA
  empty-default startup warning was fixed and its clean retest passed.
- The camera-free in-process Monado simulator rendered the actual native UI,
  clicked a button by world-space ray to create a selected cube with 8 vertices
  and 6 faces while the desktop panel was hidden, and undid it. Width calibration,
  nested menus, continuous-hover help, all five command pages, dismissal,
  reopening and session stop completed. This is not physical tracking evidence.
- Latest complete simulator evidence:
  `.tmp_xr_probe_a05834b782cf48e4aa487750c04351bc/stdout.log` and
  `reports/hand-menu-simulator/a05834b782cf48e4aa487750c04351bc/`.
  Markers: `XRWB_HAND_MENU_FEATURES_PASS ... tooltip=1` and
  `XR_HAND_MENU_SIMULATOR_PASS camera=0 hardware=0`.
  Reviewed the separate hand layer, native tooltip, nested submenu, clean desktop
  mirror and composed XR eye. The eye shows the menu as an independent foreground
  surface, not part of the desktop texture.
- The first tooltip test used one motion event and failed; native popovers enable
  help after a follow-up motion. The revised test uses continuous hover matching
  the real router, and passes without changing native tooltip behavior.
- A subsequent real-rendering simulator run stopped XR twice with the hand menu
  still open and restarted it between stops. Marker:
  `XR_HAND_MENU_LIFECYCLE_PASS stop_open=2 restart=1 scene_unchanged=1 hardware=0`.
  Evidence: `.tmp_xr_probe_c379d349c67d440592bf3b4f7f74d8dc/stdout.log` and
  `reports/hand-menu-simulator/c379d349c67d440592bf3b4f7f74d8dc/desktop-after-open-menu-session-restart.png`.
  The reviewed restarted desktop contains no stranded hand popup. The complete
  feature test also passed again in that run.
- Focused source review confirmed left/right user-path ordering, world-space
  grip anchoring and invalid-grip cancellation. This did not test physical
  controllers; fallback is chosen on opening, while losing that tracked hand
  closes the menu rather than transferring it unexpectedly.
- `dist/blender-xr-workbench-0.6.0.zip`: 14 entries, source-byte parity and CRC
  verified. SHA256 `cad9b24c90dd56ee48f9c3d8c035169366a9be95476e7afbf82cd9dfe37135a4`.
  Unpacked to a temporary profile; workbench and controller registration/routing
  smokes passed from that installed copy. This ZIP requires the matching native
  build for hand menus; it does not upgrade an older executable by itself.

## Installed build: actual SteamVR checks, 2026-09-24

Both commands completed with exit code 0 against SteamVR/OpenXR 2.17.10,
reached FOCUSED, and passed `XRWB_HAND_MENU_FEATURES_PASS`,
`XRWB_CONTROLLER_FEATURES_PASS` and `XRWB_STEAMVR_SMOKE_PASS`:

| Command | Profile | Panel frames | Status log |
|---|---|---:|---|
| `scripts/launch_steamvr_blender.ps1 -Launch -CleanProfile -SmokeTest` | Isolated clean profile | 2046 | `reports/steamvr-launch/launch-20260924-144411-0d4768c4.txt` |
| `scripts/launch_steamvr_blender.ps1 -Launch -SmokeTest` | Private add-on snapshot | 1525 | `reports/steamvr-launch/launch-20260924-144539-6a80af65.txt` |

The private snapshot registered 53 add-ons with zero registration failures;
two service add-ons remain manual. Registration does not verify every add-on's
tools. Original preferences were not modified. Both owned test processes closed
normally; no user scene was opened, discarded or saved. Blender's private
recovery file is not a save of the user's scene.

The tests exercised six primitives, three transforms, undo, cancellation, panel
placement, all five hand-menu pages, independent menu clicking with desktop UI
hidden, nested-menu expansion, width calibration and hover help. **Input and menu
placement were synthetic even though the compositor was real SteamVR.** Physical
controller events and hand anchoring therefore remain unverified.

Local console/capture evidence (not published):

- Clean: `.xr_profile/logs/a46aa45f90604fe3bad61fae5c5a7fd1.log` and
  `reports/controller-features/a46aa45f90604fe3bad61fae5c5a7fd1/`.
- Add-ons: `.xr_addons_profile/logs/4ede573862914acf9344f7bc3abb9107.log` and
  `reports/controller-features/4ede573862914acf9344f7bc3abb9107/`.

Reviewed native hand layers/tooltips and desktop captures confirm the menu is
separate and absent from the desktop mirror. Raw eye captures from the D3D11
interop route are vertically inverted OpenGL buffers before swapchain transfer;
they show both layers together, not final lens orientation or physical visibility.
The clean run selected stage with valid bounds; the add-on run selected local-floor
with invalid stage bounds. Room-scale boundary readiness is not established by
these tests and must be checked in SteamVR before moving around.

Cameras stayed off. Each bounded keep-awake helper reported successful restoration
of the prior pause/5-second screen timeout settings. System OpenXR registration
was unchanged. These are functional tests, not latency or comfort qualification.

## Required gates still open

- Physically verify hand following, button targeting, descriptions/tooltips,
  dismissal/reopening, tracking-loss cancellation and Index/Quest input mapping.
- Check SteamVR Room Setup and valid boundaries before room-scale movement.

Simulator frame-lateness warnings occurred with the user's live VR session still
running; this test is functional evidence, not a frame-rate/latency qualification.
The simulator has no Index display or camera driver and does not take over SteamVR.
No current hardware or complete user-facing success claim is made by these notes.
The older `controller-upgrade-validation.md` describes version 0.5, not this change.
