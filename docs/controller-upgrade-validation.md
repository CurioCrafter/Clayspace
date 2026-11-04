# Controller/menu upgrade validation — September 24, 2026

Workbench 0.5.0; source-built Blender 5.1.2 at upstream
`ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832` plus the published XR patch.
Final executable SHA-256:
`FBEF1148907F1A2EBB7D3A7CECE91DE206E5FEC5CBC10E94EDFBDBCB09A4C71C`.

## Executed checks

- Native CMake/Ninja build: passed, including the new active-aim RNA query.
- `python -m unittest discover -s tests`: 104 passed.
- `python -m ruff check xr_workbench scripts tests`: passed.
- Blender headless `scripts/xr_controller_smoke.py`: actual operator RNA,
  scene transforms/cancel, tracking/hover guards, and real undo/redo preserving
  the hover timer passed. No physical controller input was supplied.
- Blender headless `native/test_controller_profiles.py`: Index 21/21,
  Quest 20/20, Auto 21/41 actions/bindings; component legality, conflicts,
  shared operators, settings and hover contracts passed.
- Blender headless `native/test_editor_api.py`, `native/test_workspace_access.py`,
  `native/test_feature_build.py`: native API safety, 11 editors, 8 property
  contexts, release feature flags, CPU Cycles render and USD export passed.
- GUI `scripts/xr_editing_smoke.py -- --staged-smoke`: object/mesh selection,
  transform, undo/redo/cancel, two-hand scale and modifier rejection passed.
- Actual-code C++ fixtures `native/test_controller_tracking.py`,
  `native/test_editor_input.py`, `native/test_fly_hold_acceleration.py`: passed.
  Fixture compilation produced non-fatal unused-variable/signedness warnings.
- PowerShell SteamVR/physical/camera launcher and text-log tests: passed;
  tests include refusing disabled direct mode and timing out only an owned test.
- Source export self-test and all 34 changed native files applied against the
  pinned upstream revision with exact normalized content parity: passed.

## Actual SteamVR 2.17.10 integration

| Profile | Bindings | Panel preparations | Add-ons | Exit |
|---|---:|---:|---:|---:|
| Clean / Auto | 41 | 1,244 | Diagnostic profile | 0 |
| Personal / Auto | 41 | 1,016 | 53 loaded, 0 failures | 0 |
| Clean / Quest | 20 | 1,266 | Diagnostic profile | 0 |

All three used the available Index display/runtime, reached FOCUSED and reported
valid stage bounds. The test created six primitive types and undid them;
tested move/rotate/scale with undo and cancellation; tested native panel-group
placement/cancellation; drew all five command pages; and checked disabled-undo
guards and hover-timer survival. Inputs were explicitly **synthetic**. Five
native UI-only captures were written per run and representative pages were
visually inspected. Cameras were off. No images or logs are distributed here.
Temporary compositor keep-awake settings were restored after the checks.

Two development failures were caught and fixed before these passes: sidebar
visibility initialization without its owner window, and an RNA hover operator
invalidated by native undo. Hover now retains plain Python state, reacquires
context each tick, and queues only validated MOUSEMOVE events.

## Remaining limits

- **Unverified / high:** physical Quest 3 negotiation, new button mappings,
  controller aiming and comfort. Quest-map registration on an Index setup is
  not Quest hardware proof. A hands-on test confirmed the previous Index scene/window.
- **Verified ceiling / medium:** panels move together as a rig, not independently
  detachable windows. Nested menus remain on the full native Blender window.
- **Unverified / medium:** arbitrary third-party modal workflows and text entry
  without a keyboard. Loading an add-on is not testing all of its tools.
- **Verified ceiling / medium:** camera-off PC VR, not standalone Quest or
  calibrated passthrough. Cycles GPU kernel bundles are not included.
- **Unverified / medium:** full fresh-machine build. The local build and patch
  reconstruction passed; the published toolchain discovery/install recipe has
  been parsed and reviewed, not run from scratch on another computer.

Local evidence index: `reports/controller-features/verification.json` in the
development workspace. Raw logs, private add-on snapshots and captured UI are
deliberately excluded from the GitHub source export.
