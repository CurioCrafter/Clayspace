# XR Workbench acceptance matrix

Source: `Blender_XR_Workbench_Specification.docx`, section 15.2. This ledger
uses only **passed**, **failed**, **blocked**, and **unverified**. A desktop
preview, registry entry, or inactive headset never upgrades an unverified
hardware gate.

These are the original v1 test IDs. The new hands/adaptive-UI v2 document
reuses AT numbers for different tests; see `adaptive-ui-status.md` for that
separate scope. Do not merge their IDs or evidence.

| Case | Required outcome | Current evidence state |
| --- | --- | --- |
| AT-01 | Independent alpha without fringes or black key | unverified |
| AT-02 | Stereo/camera order remains correct after restart | unverified |
| AT-03 | Qualified head-motion registration | unverified |
| AT-04 | Controller input is not qualified as optical touch | unverified hardware; native fixture rejects controller/unknown provenance |
| AT-05 | One poke, one action; hover none | unverified |
| AT-06 | Capture stays with its originating panel | unverified |
| AT-07 | Target-only object move, one undo | unverified physical hands; actual Blender simulator undo/redo passed |
| AT-08 | Stable two-hand scale | unverified |
| AT-09 | View scale preserves stored dimensions | unverified |
| AT-10 | Mesh-region edit preserves unselected geometry | unverified physical hands; actual Blender simulator test passed |
| AT-11 | Topology cancel restores data/selection | unverified |
| AT-12 | Modifier target is safe or visibly rejected | unverified physical workflow; unqualified modifier cage explicitly rejected in Blender test |
| AT-13 | Tracking loss does not commit | unverified |
| AT-14 | Stale result cannot resurrect/overwrite data | unverified |
| AT-15 | Save/reopen omits room frames/runtime helpers | unverified |
| AT-16 | Optical/controller/desktop input parity | unverified |
| AT-17 | Complete internal UI surface | unverified full parity; native window/popup surface, event dispatch and capture teardown tests passed |
| AT-18 | Runtime/camera fault leaves desktop and committed work usable | unverified |
| AT-19 | 100 cycles and 60-minute editing soak without leak/crash | original M2 failed at cycle 80; corrected candidate passed 100 guarded cycles; 60-minute editing soak remains unverified |
| AT-20 | Named fixture meets 90 Hz tier | unverified |
| AT-21 | One-hand/left-hand/resized UI/keyboard cancel | unverified |
| AT-22 | Default outputs contain no camera images | unverified |

## M0 evidence boundary

M0 is **incomplete**. Existing desktop and connected-headset lifecycle runs
show camera-off start/stop recovery and diagnostic overlay behavior, but do
not qualify stereo order, continuous visible frames, calibration, optics,
frame pacing, full UI, or editing. The generated JSON ledger deliberately
keeps every case unverified unless a harness supplies explicit evidence.

Private evaluator evidence is narrower: an explicit, consented five-frame
Index-camera run delivered five sequence-changing pairs to the offline tracker,
reported zero active hands and a 4.377 ms mean process time, restored the
temporary OpenVR settings, and saved no images or joint traces. This does not
qualify AT-02, AT-03, AT-04, AT-16, or AT-22 for the complete product. The
30-frame consent-only diagnostic records aggregate input/inference and current
HMD-pose checks separately, without outputting any camera or joint content.
That diagnostic has now completed with 30 fresh non-flat, distinct stereo
pairs and 29 exposure changes, but with zero active hands and zero valid pose
samples. It remains evidence of an input path only; no optical acceptance case
is advanced.

## Lifecycle harness

Use an isolated profile and install. The harness sends session requests only
through a modal operator with a real `VIEW_3D` window context; it has no
application timer and forces `show_passthrough = False` before every request.

```powershell
$env:XRWB_EXPECTED_INSTALL = '.\.xr_profile\scripts\addons\xr_workbench'
$env:XRWB_SOAK_CYCLES = '100'
$env:XRWB_SOAK_DWELL_SECONDS = '0.25'
$env:XRWB_SOAK_OUTPUT = '.\artifacts\xr-acceptance.json'
& $env:XRWB_BLENDER --factory-startup --python .\scripts\xr_acceptance.py
```

An inactive headset is recorded as `headset_inactive`, not a visible-frame
pass and not a fabricated failure. A completed JSON file records actual
start/stop/draw/error counters and optional current-process memory samples,
without images, absolute paths, or device identifiers.

Each cycle checkpoints metadata to retain evidence on a native crash. The
harness stops if process commit grows by more than 1 GiB after first-cycle
driver/shader warm-up. Finishing cycles does not pass the separate 60-minute
editing soak or prove that the headset rendered visible frames.

The user's later requirement replaces keyboard cancellation with bare-hand
buttons/gestures and explicitly excludes a virtual keyboard. Keyboard event
tests are internal dispatch checks, not the proposed interaction design.
