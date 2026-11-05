# Blender XR Workbench

Version 0.5.0 adds a five-page command menu, object creation/manipulation,
movable panel groups and adjustable hold acceleration to the separate full-feature
source-built 5.1.2 SteamVR/Index route:
`Open SteamVR Clayspace Index.cmd`. See `STEAMVR.md` in the extension archive or
[SteamVR setup and controls](steamvr-index-workspace.md) in this workspace.
Native XR editor/tracking patches are required; the extension ZIP alone does
not turn stock Blender into this build. The older capability launcher below
remains a reduced-feature diagnostic, not the new SteamVR launch target.

This is the Blender 5.1.2 implementation track for the September 21, 2026
Blender XR Workbench specification. The first ticket, XR-0001, builds an
observable capability workbench before relying on camera composition or
optical hand tracking. The older Blender 4.5.11 controller workbench remains
available through its existing launcher.

## Run the capability workbench

Open **Launch XR Workbench.cmd** in the repository folder. It opens an isolated
Blender 5.1.2 profile with a diagnostic cube and translucent panel. Open the
3D View sidebar's **XR Workbench** tab for capability reporting and explicit
session controls. Camera access is never enabled automatically.

The default launcher uses a frozen native Blender 5.1.2 development build, guarded
by its verified executable hash in `native/runtime-manifest.json`. It supports
OpenGL; optional heavy features including Cycles, USD, OpenVDB and motion
tracking are disabled. Its available local dependency bundle differs from
the official release, so this is a provisional development build, not your
replacement production Blender. Your regular installation is untouched.

A separate [SteamVR-free runtime track](steamvr-free-runtime.md) now builds a
Windows Monado OpenXR simulator and tests process-scoped runtime selection. Its
desktop preview opens real Blender XR sessions and the native task card without
SteamVR, but it has no Windows Index driver and is **not** a usable physical
headset replacement. The normal launcher still uses the registered SteamVR
runtime; no system runtime setting is changed by the alternate-runtime selector.

`-Backend vulkan` deliberately selects the recorded stock 5.1.2 comparison
build, not the native fork. If no native readiness manifest exists, the
launcher also uses that stock diagnostic prototype and says so explicitly.

The default frozen build now includes the source-native Opacity and Exit buttons.
Its simulator input tests, stereo-basis checks, ten XR lifecycle cycles and
continuous draw/Recall recovery checks passed. Physical hand control and final
headset-image appearance remain unverified. This task surface, not the full desktop editor, is the
primary direction from the second specification. The separate full-feature
source build adds Select, Move, Rotate, Scale and Add Cube to the Arrange page;
their SteamVR-free simulator test passed Add Cube/Undo and Exit. The complete
native modeling pages are not implemented yet. See `docs/adaptive-ui-status.md`
for that ledger.

Start XR from the sidebar, then use **Show / Recall** under Expert Blender Editor to
place Blender's actual internal window in front of the headset. This includes
its regions and popups, not a copy of the operating-system desktop. The separate
diagnostic overlay has its own placement and opacity controls. Starting or
stopping a session clears camera consent.

The requested interaction is **bare hands using only the Index cameras, with
buttons and gestures and no virtual keyboard**. The optical provider is not
yet working end to end; controller bindings are an off-by-default developer
fallback, not a substitute. The source-built consumer rejects unknown or
controller-inferred hand samples. No hand-tracking driver is installed by launch.

Reversible spatial move/rotate/scale transactions support world constraints
and snapping. They preserve original transforms or selected mesh coordinates
on cancel and create one undo entry on commit. Unsafe linked/constrained,
multi-user mesh, shape-key, multi-edit and unqualified modifier-cage cases are
rejected. These contracts have deterministic and real Blender undo/redo tests;
they have not yet been qualified with physical optical hands.

Object Mode pinch selection now freezes the nearest visible ray target, previews
it, and commits only when release stays within the 10 mm default movement
limit. Isolated Blender tests passed hit/miss/occlusion, exact cancellation,
undo/redo and no-op behavior. Mesh component selection and physical optical
selection remain unverified.

The overlay does not add geometry, rewrite materials, change model scale, or
save the scene. Report exports contain metadata, not room images. Registered
OpenXR layers are not evidence of active layers, and controller tracking is
not evidence of optical fingertip tracking. Unknown measurements remain
unknown.

## Development and qualification

```powershell
.\launch_xr_workbench.ps1 -PreflightOnly
.\launch_xr_workbench.ps1 -SmokeTest
.\launch_xr_workbench.ps1 -Backend vulkan
python -m unittest discover -s tests -v
python scripts\build_xr_package.py
```

Use the native source build instructions in `docs/native-integration.md` for
the pinned fork. Do not change the system runtime or install API layers as
part of ordinary launch. Camera/layer setup is a separate explicit action.

The acceptance ledger in `docs/implementation-status.md` distinguishes
automated evidence from hardware gates. This prototype does not yet supply
working camera-only optical hands, calibrated Index passthrough, full editor
interaction parity, or a qualified modeling workflow. Its native editor surface
has passed actual event dispatch, popup composition and capture cleanup tests;
that is not a claim of complete tool coverage or in-headset visibility.

The connected Index passed native session start/stop and desktop recovery.
A later candidate with temporary supported sleep prevention reached
VISIBLE/FOCUSED, produced 1520 XR draw callbacks, and passed Recall/recovery.
That is runtime evidence, not a human check of stereo quality or comfort.
Sleep settings are restored after tests; tracking-validity safeguards are never
bypassed. Camera inference remains experimental: a repaired hands-ready retest
processed 30 fresh stereo pairs without crashing, but produced no tracked hands
or valid HMD pose. A hands-on test confirmed that no Lighthouse base station is
available, so 6DoF tracking cannot be qualified on this Index setup;
camera output is not connected to scene editing. Ordinary launches explicitly
disable the experimental passthrough layer; camera testing uses a separate,
consented runner.

After the user requested bypassing SteamVR, a separate Windows DirectShow
probe found the physical Index camera and read five advancing stereo frames
with SteamVR stopped. It discarded pixels and saved no room images. This is
camera acquisition only. A later consented direct-to-Mercury diagnostic read
14 stereo pairs but detected no hands; a frame-orientation retest is pending.
Raw-to-calibrated mapping, hand joints, headset display, and tracking are still
missing from the SteamVR-free route.

The matching official dependency bundle has been downloaded and passed Git LFS
integrity checking. `native/build_feature_complete.ps1` builds separately with
the upstream release feature preset, restoring CPU Cycles/OSL, USD, OpenVDB,
motion tracking, denoising and Vulkan. That full-feature source build passed
CPU Cycles rendering, USD export, native API checks, and a SteamVR-free Monado
simulator GUI task-card workflow; physical Index output is still unverified.
Vendor GPU-render kernel bundles and OptiX/HIP-RT remain excluded pending their
SDKs and validation; this is not a claim of complete production-build parity.

The separate ZIP under `dist` is a manual-install extension only. Check its
version and source-parity manifest; it does not include the native executable,
optical models or a passthrough provider. See `docs/passthrough-provider-setup.md`
before any consented camera/layer installation.
