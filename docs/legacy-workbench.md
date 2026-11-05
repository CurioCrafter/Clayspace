# Legacy notes: camera research and the 4.5.11 VR Workbench

These notes moved out of the main README in 0.19.0. They describe earlier work
that still lives in this repository: the camera and hand-tracking research
launchers, and the older `vr_blender_workbench` add-on for stock Blender 4.5.11 LTS.
The current add-on is `xr_workbench/`; start with the [README](../README.md).

## 0.6.x SteamVR notes

Our drawing, Boolean, bevel and edge tools use Blender's native APIs. See
[setup, mappings and verification limits](steamvr-index-workspace.md).
A 0.6.1 hardware run exposed a native held-event context bug that the
earlier direct-handler tests missed. Version 0.6.2 preserves the off-screen XR
context and tests scene tools through Blender's actual XR event queue. The 0.6.0
candidate passed two real SteamVR automated runs (a clean profile and 53 private
add-ons) with synthetic input; see the [hand-menu validation tracker](hand-menu-validation.md).

## Earlier camera research and legacy 4.5.11 workbench

**New specification track:** [Blender XR Workbench for Blender 5.1.2](xr-workbench.md)
starts with `Launch XR Workbench.cmd`. Its [implementation ledger](implementation-status.md)
tracks the native source, passthrough, optical hands, and full-interface work.
The [SteamVR-free desktop simulator](steamvr-free-runtime.md) exercises the
source-built XR UI. A separate source-built 5.1.2 test has displayed its scene
and XR card in the physical Index, but bare-hand interaction, calibrated
passthrough, and base-station-free 6DoF are not yet qualified. The instructions below
describe the retained 4.5.11 SteamVR/controller workbench, not the new target
linked above.
For the experimental seated camera-plus-Blender test, double-click
`Open Index Blender Hands Add Cube.cmd` with SteamVR closed. A left-eye 2D
button is configured for open → pinch → open to add one undoable cube; physical
button activation is not yet verified. Stay seated: the camera is uncalibrated,
stops after five minutes, and saves no room images or hand traces. Each run
writes text status under `reports/index-physical-launch` and `.xr_profile/logs`.
The new [personal add-on and separated-panel workspace](personal-addons-vr-workspace.md)
copies the 5.1 profile privately and exposes native editor/add-on controls.
Use `Open VR Addons Workspace.cmd` for camera-off desktop inspection.
Native panel availability does not yet mean every control has working camera-hand input.
For a private desktop camera/hand-overlay experiment, use
`Open Index Hand Preview.cmd`; it is not an in-headset Blender session.
For the separate 15-second, stationary Index extended-display camera diagnostic,
double-click `Open Index Headset Hands.cmd` while SteamVR is closed. It is raw,
uncalibrated stereo video, not a safe walk-around VR mode or a Blender session.
Each run keeps text-only status and error output in
`reports/index-headset-preview` for later diagnosis; no camera pictures or
hand-joint traces are written by the launcher.

The Clayspace Legacy Workbench is an isolated SteamVR/OpenXR authoring layer for the
Blender 4.5.11 LTS build (set `CLAYSPACE_BLENDER_EXE` to your `blender.exe`). It adds controller-driven
scene work directly in the headset. Blender editors and tools without a native
adapter remain reachable as a 2D desktop through SteamVR Desktop View.

Version 0.1.1 removes the persistent context-polling timer that could crash
Blender during XR startup, file reload, or teardown.

## Start it

1. Power on the Valve Index headset, Knuckles controllers, and base stations.
2. Double-click **`Launch Clayspace.cmd`**. The launcher starts SteamVR when
   needed; wait until its headset and both controller indicators are green.

The launcher uses the byte-verified Blender 4.5.11 LTS build and stores its
preferences under `.vr_profile`. It does not enable, disable, or
overwrite add-ons in the normal Blender profile. If you want to configure the
desktop panel before opening OpenXR, use **`Launch Clayspace Safe.cmd`**, then
choose **3D View > Sidebar > VR Workbench > Start VR Workbench**.

SteamVR is already registered as Windows' active OpenXR runtime. If Blender
cannot start VR, first confirm SteamVR can see the headset and controllers. The
launcher stops with an explicit error if a different OpenXR runtime is active.

## Valve Index controls

| Control | Action |
|---|---|
| Right trigger | Use the active tool or press a palette button |
| Right grip | Directly move selected objects/components |
| Left grip | Grab and reposition/rotate the world |
| Left B | Open/close the spatial tool palette |
| Left A | Undo or cancel the active gesture |
| Right A | Redo |
| Right B | Next palette page |
| Left stick | Head-relative movement |
| Right stick X/Y | Smooth turn / move vertically |
| Right-stick click | Recenter navigation |
| SteamVR system button | Dashboard, Desktop View, and VR keyboard |

Oculus/Meta Touch, Vive, WMR, and Khronos simple profiles are also declared.
The reduced Vive/WMR/simple mappings deliberately avoid conflicting buttons;
use the laser-driven palette and SteamVR Desktop View for missing shortcuts.

The primary map is right-handed. Smooth turning and vertical movement are
enabled, so start seated or standing still and use small stick inputs until the
comfort of the current SteamVR setup is known.

## Native and bridged tool coverage

The headset palette contains eight pages:

- **Essential:** ray selection, move, rotate, scale, duplicate, guarded delete,
  hide, undo, and redo.
- **Selection:** replace/add/subtract/toggle ray modes, select all, deselect,
  invert, and reveal. These modes make multi-object and multi-face preparation
  possible without leaving VR.
- **Create:** cube, sphere, cylinder, cone, plane, torus, empty, light, and
  camera placement at the controller ray target.
- **Mesh:** Object/Edit mode switching, vertex/edge/face modes, extrude, inset,
  bevel, subdivide, and merge.
- **Sculpt:** experimental direct draw, smooth, inflate, and deflate, plus
  radius and strength controls.
- **Animate:** key insertion/deletion, play/pause, frame/key stepping, Auto Key,
  and Pose Mode.
- **Utility:** shading, apply transforms, join/separate, origin, guarded save,
  and instructions for opening Desktop View.
- **Favorites:** six user-configurable Blender operator IDs, JSON arguments, and
  an explicit **Undoable Edit** switch. Leave it off for selection, view,
  playback, and history operators; `ed.undo`/`ed.redo` are always protected
  from an outer undo entry.

Continuous transforms preview from an immutable snapshot inside one
UNDO-enabled modal operation, commit on the initiating trigger/grip release,
and restore on Escape, XR shutdown, a controller pose being unavailable to
Blender, or an XR input-stream stall. OpenXR may retain the last pose after
physical tracking is lost, so pose invalidation alone is not treated as a
guaranteed tracking-loss signal. Only one editing gesture can be active at a
time. Delete requires a second trigger release within 2.5 seconds against the
exact same selection; Save similarly confirms the same file path. VR Workbench
never saves automatically.

Palette colors are capability labels:

- Teal: native VR interaction.
- Blue: context bridge; verification coverage varies by operator.
- Orange: experimental adapter.
- Purple: SteamVR Desktop View required.

## Why Desktop View is part of the design

Blender 4.5's OpenXR output is a stereo offscreen 3D Viewport. It does not
render Blender's Properties, Outliner, UV Editor, node editors, file browser,
preferences, add-on panels, or other desktop regions into the headset. Ordinary
mouse-modal tools also do not receive synthesized controller mouse events.

Use the SteamVR system button and choose **Desktop** whenever you need those
surfaces. That is a 2D desktop workflow inside the headset, not a native spatial
version of those editors. Only the high-frequency spatial tools listed above
have controller-native behavior in this release.

Official references:

- [Blender 4.5 VR Scene Inspection](https://docs.blender.org/manual/en/4.5/addons/3d_view/vr_scene_inspection.html)
- [Blender 4.5 XR Python types](https://docs.blender.org/api/4.5/bpy.types.XrSessionState.html)
- [SteamVR OpenXR support](https://store.steampowered.com/news/app/250820/view/3044967019267211914)

## Manual installation

The release ZIP in `dist` is suitable for **Edit > Preferences >
Get Extensions > Install from Disk**. Manual installation is optional; the two
launchers load the workspace source directly into their isolated profile.

## Developer verification

```powershell
python -m unittest discover -s tests -v
python -m compileall vr_blender_workbench tests scripts
& $env:CLAYSPACE_BLENDER_EXE --background --factory-startup --python scripts\blender_smoke.py
python scripts\build_package.py
```

Headset/controller rendering, binding, haptic, comfort, and frame-time tests
must be run with the physical hardware connected. Automated factory tests do
not establish those claims.
