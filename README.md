# VRBlender — model, sculpt, paint and animate in Blender 5.1.2, in PC VR

VRBlender puts the real Blender inside your headset. You point, pull a trigger and
hold. A preview shows what you are about to make, and releasing commits it as one
normal Blender undo step. Everything runs on Blender's own data, modifiers, menus
and undo, plus our XR patch and add-on. No Hard Ops, BoxCutter or other paid
add-on is needed, and your normal Blender preferences and scenes are never
touched: VR runs in its own isolated profile.

> **Status: 0.18.0 candidate.** Everything below passes automated checks in real
> Blender (geometry, undo, menus, rendered UI) with *simulated* controller input.
> Physical feel on a real Index or Quest 3 is being checked now; see
> [what is verified](#what-is-verified).

## Quick start

| Headset | Start | Notes |
| --- | --- | --- |
| **Valve Index** | Finish SteamVR Room Setup, enable Direct Display Mode, then double-click `Open SteamVR Blender Index.cmd`. | Cameras stay off. |
| **Meta Quest 3** (PC VR) | Connect with Quest Link / Air Link or Steam Link so SteamVR sees the headset, then double-click `Open SteamVR Blender Quest.cmd`. | Uses the Touch controller bindings. This is PC VR, not a standalone Quest app. |

The first time, a short **Guide** opens on your hand. After that, VR starts with the
hand menu open and the Blender desktop hidden. Labels next to each controller always
show what every button does right now.

## Controls at a glance (defaults, right-handed)

| | Index | Quest 3 |
| --- | --- | --- |
| Use a tool / press a button | Either trigger (harder squeeze = stronger clay, wider spray) | Either trigger |
| Pick up what you point at (moves and turns with your hand) | Right grip | Right grip |
| Resize / turn with both hands | **Squeeze the other grip on it too** (or its trigger) and pull apart | Same |
| Hand menu (sits on your **left** hand; point and click with the right) | Right A | Right A |
| Add shapes, **prefabs** and **SDF blobs** | Right B | **Right B** (now the same as Index) |
| **Tool wheel** (flick toward a tool, click again) | Right stick click | Right stick click |
| Undo | Left A | X |
| Back / cancel / hide desktop | Left B | Y |
| Show / hide the Blender desktop | Left stick click | Left stick click |
| Walk / turn | Left stick / right stick | Left stick / right stick |
| Grab the world | **Either grip on empty space**; **both grips: pull apart / together to change your size, twist to turn** (clicks at 1x) | Same |
| Teleport | Left pad, or Settings > Start & Travel > **Teleport** (left stick forward, aim, let go) | **Teleport mode** (left stick) |
| Vertex / Edge / Face | Wheel, Edit page, or Quick > Edit V·E·F; trigger picks, sweep adds, grip moves | Same |
| Move floating panels | **Squeeze a grip while pointing at one** (Index right pad also works) | Squeeze a grip on one |

**While you hold something:** right A stamps a copy, right stick click cycles
Follow / Precision / Snap / Surface, right stick up/down pushes or pulls it, left
stick click sets it down on the surface below. **While you extrude:** right A keeps
the segment and extrudes again from its new end; right stick click switches between
straight out and free (the end follows your hand). **In a menu:** right stick up/down
picks, stick click presses, B goes back.

**Tool wheel:** press the right stick. A ring of tools for your workflow appears in
front of that hand. Move the hand a few centimetres toward one (you feel a tick for
each), then click the stick again or pull a trigger. The middle, or B, closes it.
"More ›" opens the Quick menu. The full map is in
[docs/controller-map.md](docs/controller-map.md).

## Made to feel finished

- **Live readouts:** while you work, a small plate over your hand shows what is
  happening in real units, like "Extrude 12.4 cm", "Moved 23 cm · 15°", "Scale face ×1.45"
  or "Island 12 faces · crease 30°".
- **Detents:** you feel a crisp tick at every centimetre, every 10 % of scale, every 15°
  and every snap cell, like turning a dial.
- **Toasts:** the result of each action floats briefly by the hand that did it, with a
  green, amber or red edge for done, check this or refused, then fades.
- **Haptic language:** a commit is a crisp double tap and a refusal a low double buzz,
  so you can tell them apart without looking. Hover and wedge ticks are lighter.
- **Hover outline:** corner brackets in your tool's colour frame whatever your ray is on.
- **One look:** rounded plates, the same accent colours on rings, labels, the wheel and
  toasts, and a tool wheel that eases open, lifts the wedge you are on and names it in
  the centre.
- **Comfort presets:** Settings > Feel > Gentle, Balanced or Pro sets haptics, labels,
  wheel size, snapping and precision in one tap. The lit preset shows you are still on it.

## Make it yours (Settings > Feel / Buttons / Tools / Start & Travel)

- **Any action on any button.** Right A, right B, right stick click, left A/X and
  left stick click can each be set to Hand Menu, Add Shapes, Tool Wheel, Quick Menu,
  Blender Menus, Show / Hide Desktop, Undo, Redo, Guide, Next Workflow, Play / Pause or
  Nothing. The
  labels and the Guide follow your choices; Reset Buttons restores the defaults.
- **Left-handed:** the left grip picks up and the left stick turns and scrolls; the
  right grip grabs the world and the right stick walks.
- **Sensitivity:** trigger click point, grip point and stick step point. A lighter grip
  helps Quest 3.
- **Feel:** haptic strength, ray rings, button labels, menu width, scale speed, click
  tolerance, trigger pressure and Multi-Select.
- **Tools:** which tools the wheel shows (follow the workflow, or always Modeling,
  Sculpting, Shading or Animation) and its size; the Snap grid and angle; how much of
  your motion Precision passes through; label size; hover ticks; the UV crease limit.
- **Start and travel:** menu or desktop at start, replay the Guide, travel speed, snap
  turn, turn angle and motion vignette. Your size (Giant ×10 to Micro 1/20) is on the
  Travel and Sculpt pages.

Settings are saved in the VR profile's own `xr_workbench/vr_prefs.json`. The
controller profile, handedness and sensitivity apply the next time VR starts.

## What you can do

- **Model:** place shapes with a ghost preview, or draw boxes, spheres and cylinders
  by hand. Drawing stays upright, so every direction stretches as expected. Live
  Boolean cutters (Cut Away / Join / Keep Overlap), Combine selected objects,
  extrude faces straight out or freely in any direction, chain extrusions segment by
  segment, inset and scale faces, select and bevel edges, Bevel Object, Multi-Select,
  Tape Measure, and Arrange (Drop to Floor, Face Me, Reset Rotation, Apply Scale).
- **Hold and shape:** grip-carry objects; stretch with both hands, uniformly or along
  one of the object's axes; Precision, Snap or Surface modes; stamp copies.
- **Sculpt:** Clay Ball, then Grab, Clay (or Dig), Smooth and Flatten. Trigger
  pressure, Mirror X, and Remesh for Detail. Brushes stay a physical size, so
  shrinking yourself gives finer detail.
- **Shade:** a shelf of material blocks you can hold, Spray paint, Eyedropper, Show
  Logic (the real node tree), and your own presets saved for every scene.
- **Node Garden:** the material's real node tree as blocks standing in front of you.
  Grip a block to move its node, drag from socket to socket with the trigger to link,
  pick a link up by its input to move or remove it, add nodes, and change the active
  node's values on your hand. The desktop node editor stays in step.
- **UVs by parts:** UV Island floods from the face you point at up to a crease angle
  (drag sideways to grow or shrink it, the border is outlined live) and on release
  unwraps just that part with its border as seams. Also Smart UV, Pack, Clear Seams,
  a UV checker material and the UV editor.
- **Animate:** Key and Delete Key, jump between keys, Play, Record (Blender's
  auto-key), and a physical time scrub.
- **Stage (new workflow):** lights appear at your hand aimed along the ray; put the camera
  where your eyes or hand are; Live Render in the headset; render and save; EEVEE / Cycles /
  Workbench; world presets; physics (falls, solid, cloth, collision, ground, play).
- **Studio (Quick menu, Menus page, wheels):** Modifiers (16 kinds with their values, apply,
  reorder, including Geometry Nodes), Lights, Camera & Render, World, Physics, Rigging
  (armature at hand, pose, bind with automatic weights), Paint Colour and the 3D Pen, and a
  Scene Outline to select, hide and isolate objects.
- **Prefabs:** 34 ready-made starters (furniture, architecture, nature, props, characters) at
  real-world size with fitting materials, placed with a ghost like a shape. Starter scenes
  (Studio, Outdoor, Tabletop) set up a stage in one go. **Save Selection as Prefab** keeps your
  own builds for every scene (Add > Prefabs > My Prefabs).
- **SDF modeling:** blobs that melt together (Blender's metaballs): hold the trigger to draw a
  trail of clay-like blobs or place single ones (ball, capsule, ellipsoid, rounded cube), carve
  to cut away smoothly, trigger a blob to move it, then Bake to Mesh. The ring at the pen tip is
  exactly the size you get.
- **Draw and paint:** the 3D Pen draws smooth tube curves in the air (squeeze for thicker);
  Paint Colour sprays soft vertex colour with pressure.
- **All of Blender:** Blender Menus (File, Edit, Add, Object, …) walkable with the
  stick, the Menus page, and every Blender editor on the floating desktop. See the
  [Blender coverage map](docs/blender-coverage.md) for how every area is reached.

## What is verified

Each release adds automated checks that run in real Blender 5.1.2:

- geometry, exact cancel and one-step undo;
- rendered hand-menu pages at 150% UI scale;
- menu navigation driven by Blender's own event simulator;
- the controller profiles for Index, Quest and Auto, right- and left-handed, with no
  input bound twice.

Input in these checks is simulated. Nothing is called hardware-verified until someone
wearing the headset confirms it. Evidence and open questions:
[controller map](docs/controller-map.md) ·
[physical workflows](docs/physical-workflows.md) ·
[native interaction validation](docs/native-interaction-validation.md).

## Version history (short)

- **0.18:** movement: grab the world with either grip, two-hand world turn and resize with a 1x
  click, Smooth / Teleport / Fly locomotion (stick teleport for Quest), Go to Selection, Reset View.
- **0.17:** controls unified for Quest 3 and Index (menus on the off hand, Quest B = Add, grip
  a panel to move it); 34 prefabs, starter scenes and your own prefabs; SDF blob modeling.
- **0.16:** stick click always presses in open menus; hover follows the hand pointing at the
  menu, with a cursor dot and row ticks; both grips resize a held object (soft dead zones,
  Two Hands setting); the face you extrude is filled and outlined live; Vertex / Edge / Face
  editing with ray picking, sweep selection, Extrude Selection and mesh actions.
- **0.15:** Studio (modifiers, lights, camera, render, world, physics, rigging, outline),
  Stage workflow and wheel, 3D Pen, Paint Colour, Blender coverage map.
- **0.14:** live readouts, detents, toasts, haptic double taps, hover outlines, rounded
  unified overlay, animated wheel, Gentle / Balanced / Pro presets.
- **0.13:** tool wheel, Node Garden, UV islands, free and chained extrusion, inset,
  Tools settings (snap, precision, wheel, labels).
- **0.12:** assignable buttons, left-handed mode, sensitivity, Quest 3 guidance,
  Flatten brush, Eyedropper.
- **0.11:** button labels, Blender Menus with Back, menu at start, upright drawing,
  per-axis stretch, double-ray fix.
- **0.10:** Multi-Select, Surface hold mode, Tape Measure, Arrange.
- **0.9:** ghost previews, solid draw previews, Add menu, stick-driven menus.
- **0.8:** stamp copies, Precision/Snap, set-down, Quick menu, trigger pressure,
  Mirror X.
- **0.7:** sculpt, material blocks and spray, animation, workflows, change your size,
  grip grab, two-hand stretch, haptics, ray rings, Guide.
- **0.6:** native XR dispatch fixes, hand menus, draw/Boolean/bevel tools, logging.

Earlier engineering notes from the 0.6 series follow below.

<details><summary>0.6.x SteamVR notes</summary>

Our drawing, Boolean, bevel and edge tools use Blender's native APIs. See
[setup, mappings and verification limits](docs/steamvr-index-workspace.md).
A 0.6.1 hardware run exposed a native held-event context bug that the
earlier direct-handler tests missed. Version 0.6.2 preserves the off-screen XR
context and tests scene tools through Blender's actual XR event queue. The 0.6.0
candidate passed two real SteamVR automated runs (a clean profile and 53 private
add-ons) with synthetic input; see the [hand-menu validation tracker](docs/hand-menu-validation.md).

</details>

## Build from source (Windows)

Install Visual Studio 2022 C++ tools and Windows SDK, CMake, Ninja, Git, Git LFS,
and Python. Put the command-line tools on PATH. The GitHub repository publishes
our source and a consolidated patch, not personal add-ons or Blender binaries.
From a fresh clone:

```powershell
git clone https://github.com/CurioCrafter/VRBlender.git
Set-Location VRBlender
git lfs install
git clone https://projects.blender.org/blender/blender.git native/blender-5.1.2
git -C native/blender-5.1.2 checkout --detach ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832
git -C native/blender-5.1.2 apply --check ../patches/blender-5.1.2-xr-workbench.patch
git -C native/blender-5.1.2 apply ../patches/blender-5.1.2-xr-workbench.patch
git clone https://projects.blender.org/blender/lib-windows_x64.git native/dependencies/windows_x64_5_1
git -C native/dependencies/windows_x64_5_1 checkout --detach 17e87e0869383bcf1dd8339502e6008b7c0c8eb0
git -C native/dependencies/windows_x64_5_1 lfs pull
powershell -NoProfile -ExecutionPolicy Bypass -File native/build_feature_complete.ps1 -Target install -Jobs 4
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launch_steamvr_blender.ps1 -Launch -CleanProfile
```

Output: `native/build-features/bin/blender.exe`, with runtime resources beside it.
The install stays within the checkout. Dependencies and compilation require
substantial disk space; vendor-specific Cycles GPU kernel bundles are not built.
Use `-ControllerProfile QUEST` on the clean launcher for Quest-only bindings.
See [detailed build instructions and checks](native/README-source.md) for toolchain
overrides, patch verification and optional research dependencies. The recipe's
patch application/content parity is tested; a fresh-machine full build is not.

## Earlier camera research and legacy 4.5.11 workbench

**New specification track:** [Blender XR Workbench for Blender 5.1.2](docs/xr-workbench.md)
starts with `Launch XR Workbench.cmd`. Its [implementation ledger](docs/implementation-status.md)
tracks the native source, passthrough, optical hands, and full-interface work.
The [SteamVR-free desktop simulator](docs/steamvr-free-runtime.md) exercises the
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
The new [personal add-on and separated-panel workspace](docs/personal-addons-vr-workspace.md)
copies the 5.1 profile privately and exposes native editor/add-on controls.
Use `Open Blender VR Addons Workspace.cmd` for camera-off desktop inspection.
Native panel availability does not yet mean every control has working camera-hand input.
For a private desktop camera/hand-overlay experiment, use
`Open Index Hand Preview.cmd`; it is not an in-headset Blender session.
For the separate 15-second, stationary Index extended-display camera diagnostic,
double-click `Open Index Headset Hands.cmd` while SteamVR is closed. It is raw,
uncalibrated stereo video, not a safe walk-around VR mode or a Blender session.
Each run keeps text-only status and error output in
`reports/index-headset-preview` for later diagnosis; no camera pictures or
hand-joint traces are written by the launcher.

Blender VR Workbench is an isolated SteamVR/OpenXR authoring layer for the
Blender 4.5.11 LTS build (set `VRBLENDER_BLENDER_EXE` to your `blender.exe`). It adds controller-driven
scene work directly in the headset. Blender editors and tools without a native
adapter remain reachable as a 2D desktop through SteamVR Desktop View.

Version 0.1.1 removes the persistent context-polling timer that could crash
Blender during XR startup, file reload, or teardown.

## Start it

1. Power on the Valve Index headset, Knuckles controllers, and base stations.
2. Double-click **`Launch VR Blender.cmd`**. The launcher starts SteamVR when
   needed; wait until its headset and both controller indicators are green.

The launcher uses the byte-verified Blender 4.5.11 LTS build and stores its
preferences under `.vr_profile`. It does not enable, disable, or
overwrite add-ons in the normal Blender profile. If you want to configure the
desktop panel before opening OpenXR, use **`Launch VR Blender Safe.cmd`**, then
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
& $env:VRBLENDER_BLENDER_EXE --background --factory-startup --python scripts\blender_smoke.py
python scripts\build_package.py
```

Headset/controller rendering, binding, haptic, comfort, and frame-time tests
must be run with the physical hardware connected. Automated factory tests do
not establish those claims.

## Safety notes

- Normal launches keep the headset cameras **off**. The camera and hand-tracking
  launchers (`Open Index Cameras Live.cmd`, `Open Index Headset Hands.cmd`, `Open Index
  Hand Preview.cmd`) are experiments: they show live camera images on your own PC and
  record nothing, but they are uncalibrated, so stay seated.
- A few research probes change SteamVR settings (camera access, keep-awake) only when
  you pass their `--consented-*` / `-ConsentedStream` flags, and put the keep-awake
  values back when they exit.
- The Mercury hand-tracking research uses Monado's hand-tracking models, which have no
  stated redistribution licence. They are not included here and must be fetched by you.
- The `.cmd` launchers run this repository's own PowerShell scripts with
  `-ExecutionPolicy Bypass`. Read them first if you are unsure.

## License

Copyright (C) 2026 Andrew Rainsberger.

VRBlender is free software: you can redistribute it and/or modify it under the terms
of the [GNU General Public License version 3](LICENSE) or (at your option) any later
version. It comes with no warranty.

The Blender patch in `native/patches/` changes Blender's own GPL-2.0-or-later source
and keeps the Blender Authors' notices. A few native probes are under the Boost
Software License 1.0, which is compatible with the GPL. Third-party dependencies
(Blender, OpenXR, OpenVR, Monado, OpenCV, ONNX Runtime) are fetched at build time
under their own licences and are not included in this repository.

VRBlender is an independent community project. It is not made, endorsed or supported
by the Blender Foundation. Blender is a trademark of the Blender Foundation; Valve
Index, SteamVR and Meta Quest are trademarks of their owners.
