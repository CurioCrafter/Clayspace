# Native interaction candidate 0.6.2

## Outcome

The user's 0.6.1 physical feedback exposed a missing native modal context:
XR invokes in an off-screen VIEW_3D, but later held/release/timer events rejected
that area because it was absent from the desktop screen. The validator now
accepts only the current XR session area and still refuses stale areas/regions.
The controller adapter also refreshes its edit context at each event. Different
hands cannot drain or cancel one another's modal handlers.

Object and face scaling use a frozen sideways hand axis and a physical drag
distance (20 cm doubles size by default), independent of object distance.
Settings exposes that sensitivity, menu size, click tolerance and travel speed.
Buttons show valid-context guards and the last action outcome. Only our two
redundant desktop sidebar panels hide while XR runs. Native and personal add-on
panels remain untouched. The Index launcher now explicitly selects Index mappings.

Persistent console logs include `XR_INTERACTION` action records and native
`XR_OPERATOR_REPORT` warnings/errors, including rejected/cancelled actions.
No controller pose stream or camera imagery is logged.

## Assumptions

This pass targets camera-off SteamVR Index interaction, with reversible edits,
one-step undo and no purchased add-on requirement. Settings remain session-local.
The separate optical-hand/passthrough experiments and later sculpt/material
workflow expansion are not part of this repair.

## Verification

- 106 Python unit tests, Ruff, compileall, isolated Blender controller/UI/native
  tools checks passed. Scale checks cover two ray depths and the 3 cm RNA endpoint.
- MSVC compiled the real dispatcher/context/report/injector function bodies.
  The regression reproduces the old null-context failure, then checks invoke,
  held/release/timer, stale pointers, report forwarding and test opt-in guards.
- Recompiled `bf_windowmanager`, `bf_rna`, and linked the executable. Installed
  SHA256: `3EF439B76B6A66361BD13607763FF6A11CC00929F2F016C0A30F78CE11066E22`.
  Version 0.6.1 is backed up at `native/releases/pre-polish-20260924/blender.exe`.
- Camera-off simulator passed both-hand scene tools through Blender's actual
  XR event queue, cross-hand refusal/release, all six cube sides, draw/cutter/edge
  operations, undo, all 14 rendered tool choices, desktop open/close, object bevel,
  both origin buttons, nested menus, tooltip, and session restart/teardown.
  Local evidence: `.tmp_xr_probe_eb94ef9b7fd5422f813bf607e75c21d4/stdout.log`.
  UI images were inspected; no room imagery was read or captured.
- Two development harness failures were corrected: queued events need a delivery
  acknowledgement, not an assumed next timer tick; the origin fixture must be
  selected before opening its native popover, and its rendered target moved.
  Neither failed run is counted as a pass.
- The subsequent real SteamVR run was **not a full pass**. At production 150%
  UI scale, the Edit card clipped its lower rows; the test's bottom-relative
  origin target hit Undo instead. The final source compacts the card and aims
  from its top. This layout revision still needs the full SteamVR rerun.
  Failed-run evidence: `.xr_profile/logs/4b3f225d9fba4238a73781d77439da57.log`.
  Both temporary keep-awake settings were restored successfully.
- Latest 150%-scale simulator run (`.tmp_xr_probe_95cd032decf44fb28cc0aa4e98ffc308`)
  passed the scene and rendered-menu feature markers, including both origins.
  It subsequently failed a misplaced sidebar assertion during intentional XR
  shutdown. That test assertion was corrected, but not rerun before handoff.
  The Edit card's bottom status text still clips and needs additional sizing
  polish. The final overall test result is therefore **not a pass**.

- Follow-up source fix (cloud, no XR): the command card's status area went from
  three fixed rows to two, the Settings help uses normal line pitch, and guard
  text never adds rows at the bottom of the Edit card. A stock-Blender desktop
  render at 150% measured the Edit card at about 970 px instead of about
  1050 px, fully visible at 1080p; Settings no longer scrolls. Rows above the
  origin buttons are unchanged, so the tested click offsets still apply. This
  is a layout measurement, **not** a simulator or SteamVR pass; both reruns and
  the lifecycle assertion are still outstanding.

Unlike the 0.6.1 checks below, scene gesture checks now enqueue actual native
`XR_ACTION` payloads and run invoke/modal/release handling. Poses are still
synthetic, not evidence of physical controller aiming or comfortable interaction.
The internal injector requires `--enable-event-simulate` and explicit owned-test
environment flags with cameras off; ordinary interactive launches cannot use it.

## Risk register

- **Unverified / high:** physical aiming and hand feel after this repair. Real
  dispatch and geometry checks passed, but a wearer must judge the revised drag.
- **Verified limitation / medium:** source-edge/face tools operate on base meshes,
  not evaluated Boolean topology. Object Bevel supports the evaluated modifier stack.
- **Unverified / medium:** dense-scene preview performance and every third-party
  modal operator. Empty-scene checks and optional add-on loading are not that proof.

## Review notes

Kept native Blender menus, modifiers and undo; added no dependencies or commercial
code. Temporary profiles protect normal preferences/scenes. Headset qualification
is kept separate from executable compilation, rendered buttons and synthetic input.

---

# Historical 0.6.1 evidence (superseded by physical feedback)

This pass prioritizes core viewport interaction over the deferred material,
sculpting, animation and physical-workflow expansion. It does not import,
redistribute or require Hard Ops/BoxCutter code. The source checkout uses our
Python interaction code, Blender's native geometry/modifier APIs, and our native
XR patch. Optional personal add-ons are separate compatibility, not dependencies.

## Installed build and rollback

After the user saved and closed Clayspace, the compiled exact-hand-anchor and
Python draw-context fixes were installed as `native/build-features/bin/blender.exe`.
Final native-tools SHA256: `803511D9F855D97EC7DF16C500B9C104DF050C6CCA46697E69AD9F3CBAC80EC0`.
The previous executable is retained as
`native/releases/pre-interaction-20260924/blender.exe`, SHA256
`FA44C0C9C77F31DC2F84D8E7754D86CCDAA248A942CF77A7DA4DD5A5485FC445`.
The backup is executable-only and needs the existing runtime resources.
Python controls are copied with source-hash checks by the isolated launcher.

## Verified software checks

- 104 Python unit tests; Ruff and compileall on authored Python.
- MSVC fixtures compiling actual native function bodies: exact opening hand,
  tracking-loss cancellation without transfer, grip-follow placement, 100 ray
  press/releases across root/submenu areas, ownership and lifecycle cleanup.
- Blender 5.1.2 background controller smoke and profile checks: Index21/21,
  Quest19/19, Auto21/40; no duplicate action/component conflicts.
- Factory-profile geometry tests with real undo/redo: face extrusion on a cube
  produces 12 vertices/20 edges/10 manifold faces; face scaling, source-edge
  selection and segmented bevel preserve source attributes and cancel exactly.
- Native draw/cutter smoke: three primitive shapes and three cutter shapes;
  exact Boolean evaluated volumes 4/12/4 for difference/union/intersection on
  overlapping volume-8 cubes; editable retained cutters; unit-scale commits;
  an immediately beveled drawn box; one-step undo/redo and no-motion history.
- External geometry, object, material, UV and attribute changes to an owned
  preview are preserved on cancellation rather than deleted as disposable data.
- Native whole-object bevel changes evaluated geometry, keeps existing stack
  and source mesh, and undoes/redoes in one step. Nonuniform and non-finite
  transforms are rejected before adding a modifier. All 14 tool choices register
  in factory-startup Blender without Hard Ops or BoxCutter.
- Launcher offline checks cover missing optional personal profiles, clean
  dispatch, unchanged registration, process safety and logged display-mode errors.
- An actual XR run found that reading scene context in an XR Python draw
  callback dereferenced a null native context. The overlay now draws plain cached
  coordinates; a second run exposed that the native callback also left the global
  Python context null for subsequent timers. The native callback now restores its
  prior context under the GIL. An actual-source C++ regression covers success,
  exceptions, nesting, GIL ordering, ordinary callbacks and worker-thread policy.
  The rebuilt candidate then passed the real rendered edge-overlay/gesture tests.
- The installed executable passed the complete camera-off SteamVR test with a
  clean profile and no personal add-ons: 6,506 prepared panel frames, FOCUSED,
  valid stage bounds, all 14 tool choices clicked on the rendered menu, both-hand
  scene edits, all six cube sides, three drawn primitives, three Boolean modes,
  edge selection/bevel with an actual XR GPU overlay, object bevel, desktop
  show/close, two origin buttons, undo and clean exit (code 0). Input was synthetic;
  the runtime, native UI rendering and scene operations were real.
  Local evidence: `reports/steamvr-launch/launch-20260924-153141-0dae692b.txt`
  and `.xr_profile/logs/e30bdeb5ca034f979c4d8a8ec53e7f62.log.stdout`.
  The temporary camera-off keep-awake helper restored both original power settings;
  global OpenXR registration was unchanged. Runtime logs are not published.
- A second complete real SteamVR run passed with the optional private snapshot:
  53 add-ons loaded, zero load failures, 4,223 prepared panel frames, the same
  menu/geometry checks and clean exit (code 0). Local evidence:
  `reports/steamvr-launch/launch-20260924-153456-9b827216.txt` and
  `.xr_addons_profile/logs/69d933a8f50741f481a86570ba6be89d.log.stdout`.
  Original preferences and global OpenXR registration remained unchanged, and
  the bounded keep-awake helper restored its original settings. Compatibility
  loading does not establish correctness of every third-party modal tool.

Reproduce the geometry tests in separate factory-startup background processes:
`scripts/xr_face_editing_smoke.py`, `scripts/xr_draw_editing_smoke.py`,
`scripts/xr_edge_editing_smoke.py`, `scripts/xr_native_tools_smoke.py`.
Use `--python-exit-code 1`; do not run these destructive fixtures in a user scene.

## Limits, not success claims

- Physical controller aiming, hand-follow placement and subjective comfort still
  need a wearer. Synthetic events and GPU draws do not establish physical input.
- Face/edge editing supports local single-user source meshes, not evaluated
  modifier geometry. Use native Object Bevel after a Boolean stack.
- Geometry fingerprints and live previews are not profiled on dense sculpt
  meshes. Native EXACT Boolean results depend on input topology; general open
  or self-intersecting operands are not qualified.
- Quest hardware, optical hands, cameras/passthrough and arbitrary third-party
  modal tools are outside this camera-off Index interaction verification.
