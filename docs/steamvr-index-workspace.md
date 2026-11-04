# Blender 5.1.2 — SteamVR / Index and Quest PC VR workspace

Double-click **Open SteamVR Blender Index.cmd** in the repository folder from File Explorer.
This is the source-built 5.1.2 application, not the retained 4.5.11 launcher.
It opens the separated native editor panels. A private add-on snapshot loads if
available; a fresh checkout uses the isolated clean profile automatically.
All project drawing, Boolean and bevel tools work without commercial add-ons.
The normal Blender profile and any normal Blender windows are left alone.

## Before the first physical run

1. Close the separate camera-only/Monado Blender test. Do not close an unsaved
   normal Blender scene just to run this launcher.
2. In SteamVR Settings → Developer, enable **Direct Display Mode** and restart
   SteamVR. The SteamVR-free Index test used extended-display mode; the launcher
   detects that setting and stops with an explanation instead of changing it.
3. Complete SteamVR Room Setup with the headset, both Index controllers and
   powered Lighthouse base stations tracking. Keep SteamVR's boundary enabled.
4. Launch the CMD. Check the panels and controls while standing still before
   slowly checking a small step within the cleared play area. Stop if the view
   jumps, loses tracking or feels uncomfortable.

SteamVR is selected only for this process through `XR_RUNTIME_JSON`; the launcher
does not alter system-wide OpenXR registration, display settings or Chaperone.
This version does not start the Index camera, optical hand detector, Monado or
the experimental passthrough layer. The separate camera route remains available
but requires SteamVR stopped and the Index back in extended-display mode.

## Controls

| Index control | Action |
|---|---|
| Either trigger | Click UI first; otherwise run the selected scene tool, hold to preview and release to confirm |
| Right squeeze | Transform selection using the chosen Move / Rotate / Scale tool; Select defaults to Move |
| Right A | Toggle one six-page native menu on the right hand; use the other trigger to click it |
| Right thumb-pad pressure, held | Aim at a panel and move/rotate the entire panel group; release to place |
| Right B | Redo |
| Right stick click | Open Settings: interaction size/sensitivity, travel, profile and controller help |
| Right stick up/down | One scroll notch per deflection; return to center for another |
| Right stick left/right | Snap turn (initially 30 degrees) |
| Left thumb-pad pressure | Native teleport onto scene geometry |
| Left squeeze | Move the virtual world; rotation and world scaling locked |
| Left stick | Head-relative movement; light deflection for precision, firm hold for acceleration |
| Left stick click | Show/recall or close the desktop panels |
| Left A | Undo |
| Left B | Close the hand menu or cancel the active edit/click |
| System button | SteamVR dashboard |

### Meta Quest 3 / Touch-compatible PC VR

Use `Open SteamVR Blender Quest.cmd` after establishing a SteamVR PC VR connection.
Valve's [Steam Link setup](https://help.steampowered.com/en/faqs/view/0E2C-406B-9135-38A4)
describes supported headsets and the connection requirements. This project does
not build a standalone Quest app or enable Meta passthrough.

| Quest control | Action |
|---|---|
| Either trigger / right grip | Same pointer / transform tools as Index |
| Right A | Toggle the hand-mounted command menu |
| Right B, held | Grab the panel group; release to place |
| Left X / Y | Undo / cancel |
| Left stick click | Show/close separated panels |
| Both sticks, left grip | Same travel, scroll, snap-turn and world-grab roles as Index; no pressure-pad teleport |
| Edit page → Redo | Redo; no conflicting right-B shortcut |

Settings has **Auto / Valve Index / Meta Quest 3 / Touch** profile selection.
Auto offers both binding families; explicit changes apply on the next XR start.
The Quest mapping uses the standard OpenXR
[`oculus/touch_controller` compatibility profile](https://registry.khronos.org/OpenXR/specs/1.0-khr/html/xrspec.html#_oculus_touch_controller_profile),
not the optional Touch Plus extension. Index has 21 actions/bindings, Quest has
19/19, and Auto has 21 actions with 40 bindings. Both use the same menu/operator
implementation. Software/profile tests do not establish physical Quest support.

Version 0.6.2 uses a separate controller-anchored surface with **Create, Edit,
Menus, Desktop, Travel, Settings** pages. Create offers immediate primitives,
hold-to-draw volumes and retained native Boolean cutters. Edit selects object
transforms, face extrusion/scaling, edge selection and beveling. Menus exposes
native Blender menus, including optional installed add-on entries. Desktop
switches editors and panel layout. Edit includes real object-origin controls.
Settings collects hand-menu width, **Scale Drag (2x)**, selection click tolerance,
maximum travel speed and controller profile. Scale Object and Scale Face use a
sideways drag relative to the controller orientation at the start of the gesture;
the default 20 cm drag doubles size. A smaller Scale Drag setting resizes faster.
Either trigger operates these controls; Left B (Index) / Y (Quest) cancels/dismisses
the active UI. Tracked idle controller movement supplies passive menu hover,
without synthesizing clicks. Authoring requires Global Undo and nonzero undo steps;
the menu exposes these settings if the copied profile disables them.

The single hand menu follows the controller that opened it and faces the headset
for readability. An untracked requested hand is refused, never silently replaced
by the other hand. Point with the opposite controller and pull the trigger. Native submenus, search
popups and tooltips belong to this hand layer, not the desktop texture. Hovering
buttons retains Blender's descriptions. The menu also works with the desktop
surface hidden. Right A or Left B/Y closes it; losing anchor tracking cancels it.
Settings' **Menu Width** calibrates the next opening from 0.22 to 0.9 m.
Navigation and object/panel grabs are suppressed while it is open; release held
travel controls before moving again after dismissal.

The full desktop screen remains available separately. Workspace/editor-switch
actions and separate OS child windows (such as Preferences/file browsers) are
still native editors, not independently hand-mounted windows. Arbitrary add-on
modal tools are not converted into spatial gestures. Simultaneous independent
desktop and VR modal interaction is not qualified.

While XR is running, only this add-on's **XR Workbench** desktop sidebar panels
are hidden: use the controller menu instead. Blender's native sidebars and all
other add-on tabs are left alone, including their visibility/layout preferences.
Our desktop troubleshooting panels return when XR stops. Each hand-menu page
keeps Close and Open/Close Desktop in its header, shows the active tool, and has a
fixed-height **Last action** area for recent outcomes or actionable rejections.
Full details remain in the launch console log. Controller scene tools explain
when Object Mode, Global Undo, or a selected editable Boolean target is required.

Left stick click shows/recalls or closes the separated panels. The right thumb pad grabs the
**whole panel group**, not independently detachable editor windows. Lost tracking
or Left B cancels the placement. The pressure binding uses the Index profile's
[`/input/trackpad/force`](https://registry.khronos.org/OpenXR/specs/1.1/html/xrspec.html#_valve_index_controller_profile).

Travel presets are Precision (0.35), Walk (1.4), and Travel (3.0); the speed slider
ranges from 0.1 to 4.0. At world scale 1 these are maximum per-axis meters/second.
A firm hold ramps from 20% to full speed over 1.5 seconds; light input, direction
reversal and release reset the ramp. A stall over 250 ms causes no movement on
that update, and integration steps are capped at 50 ms. Diagonal speed can reach
sqrt(2) times the per-axis limit. Start slowly; this is not a comfort guarantee.

The 21 actions live in one active OpenXR action set, so editor input and native
navigation coexist. Navigation uses Blender's positional + absolute tracking,
custom origin `(0,0,0)` and scale 1. The runtime supplies the stage/floor; no
eye height or room boundary is invented by the add-on. Native stage selection
falls back to local-floor/local when a runtime lacks valid stage bounds—this
must not be mistaken for a validated room-scale setup.

Tracking loss cancels edits and sends a no-ray cancellation to native UI instead
of clicking with an old ray. Release the trigger/squeeze fully after tracking
returns before beginning another interaction.
If it was already released while tracking was lost, the first press/release
after recovery may be consumed to re-arm safely rather than activating a tool.

## Native drawing, cutters and bevels

These are project-owned tools built on Blender's BMesh and modifier APIs. Hard Ops
and BoxCutter are neither bundled nor required. Optional third-party menus remain
available only when users separately install their own add-ons.

1. **Create → Draw Cube / Sphere / Cylinder:** close the menu, hold either trigger,
   then drag the opposite corner of a volume. Its first corner is 20 cm ahead of
   the controller aim pose. The initial controller orientation sets the volume axes.
   Release to keep the new selected object; Left B/Y cancels. A drag under 1 cm
   creates nothing. Thin axes have a 5 mm minimum. Distances follow XR world scale.
2. **Create → Cut Away / Join Volume / Keep Overlap:** first select a target mesh.
   Choose Box/Sphere/Cylinder cutter shape, then use the same drawing gesture.
   A native EXACT Boolean previews the result; release retains a wireframe cutter
   and editable modifier. Source mesh data is not baked. Select/move the cutter
   later to adjust the Boolean. One undo removes the new cutter and modifier.
3. **Edit → Select Edges:** point near an edge on a visible mesh face and click a
   trigger. Repeated clicks toggle accumulated selection. Cyan lines show the
   selected source edges, including an X-ray view through the object.
4. **Edit → Bevel Edges:** hold a trigger on the source mesh and drag to set width;
   moving back toward the starting point reduces it. Uses selected edges, or the
   nearest hit-face edge if none are selected. Release commits; Left B/Y restores
   the original. The Segments control sets bevel subdivisions.
5. **Edit → Bevel Object:** adds an editable angle-limited native Bevel modifier
   after the existing stack, using Object Bevel width and Segments. This works
   after drawn Boolean modifiers. It never applies existing modifiers or transforms.

Face and edge tools currently require Object mode and a local, single-user source
mesh without active modifiers, shape keys, custom normals or constraints. For a
Boolean stack, use **Bevel Object**; selected evaluated edges are not silently
mapped to unrelated source edges. Object bevel refuses nonuniform object scale;
our drawn objects commit at unit scale so this is not required for new drawings.
General non-manifold Boolean inputs and dense sculpt meshes are not qualified.
Every committed gesture is one undo step. Opening a menu does not commit a held
edit; release/cancel it first.

## Logs and checks

Each physical launch writes status and full console output under
`reports/steamvr-launch`, plus Blender output under `.xr_addons_profile/logs`
when using the optional personal snapshot, or `.xr_profile/logs` when clean.
Logs remain if the shell closes or Blender crashes. They contain no camera
images or joint traces. Native session startup alone is not headset proof.
`XR_INTERACTION` JSON lines record action start, refusal, cancellation and commit,
with tool, hand, editor context and caught exception details. `XR_OPERATOR_REPORT`
also records native warning/error reports from controller actions and ray-driven
menu buttons, including cancelled operators. Last Action in the hand menu shows
the latest Workbench result; it is not a replacement for the full saved log.
Failure reasons are printed before the console transcript closes. A sleeping
headset no longer makes panel startup give up after ten seconds: it logs a
waiting message and allows up to two minutes for the first composed view.
`XRWB_REFERENCE_SPACE` records the actual stage/local-floor/local choice and
whether stage bounds were valid, without logging room dimensions or poses.

`powershell -NoProfile -File scripts/launch_steamvr_blender.ps1` performs a
static preflight without starting SteamVR, Blender or a camera. `-CleanProfile`
selects the small isolated diagnostic profile instead of personal add-ons.

For a repeatable test against **actual SteamVR**, not the simulator:

```powershell
powershell -NoProfile -File scripts/launch_steamvr_blender.ps1 -Launch -SmokeTest
```

This loads the optional private snapshot if present, runs the production startup, requires the
SteamVR runtime and FOCUSED session state, checks at least 60 native panel
preparations and all 21 action bindings. On its empty scene it then tests six
primitive additions/undo, move/rotate/scale/undo/cancel, panel-group placement,
and six menu pages using explicitly synthetic controller events. It also checks
both-hand face editing through the native XR event queue, all six cube sides,
simultaneous-hand ownership, 14 actual menu tool clicks, drawn
primitives/cutters, edge selection/bevel, the XR edge overlay, object bevel,
origin controls and desktop closing. UI-only PNGs
are saved under `reports/controller-features`; cameras stay off. It then stops
XR and closes only its own empty test Blender. The event-loop deadline is 150 seconds after bootstrap;
an independent 180-second process deadline also covers native hangs. Ordinary
interactive launches have no process timeout. Bounded tests retain separate
`.stdout`/`.stderr` streams; their combined log is grouped by stream, not time.
Use `-CleanProfile` to check without personal add-ons. The headset must be
awake; this command does not change power, boundary, or display settings.

On September 24, the failed user launch was traced to Direct Mode still being
disabled from the earlier extended-display experiment. Direct Mode was restored
through SteamVR's settings API and verified enabled. Actual SteamVR tests then
passed with both profiles, including delayed headset wake and clean shutdown.
The final personal-profile run loaded 53 add-ons with zero load failures,
prepared 61 native panel frames, attached 19 actions/bindings, and reached
FOCUSED with valid stage bounds. Chaperone separately reported calibration OK,
the headset pose was valid, and two base stations were valid. Tests used the
existing consented temporary keep-awake helper; its prior settings were restored.
See `reports/steamvr-validation/verification.json` for exact evidence paths.

The final 0.5 upgrade passed three actual SteamVR runs: Auto with the clean
profile (1,244 prepared panel frames), Auto with 53 private add-ons and zero
load failures (1,016), and Quest-only bindings on the available Index display
(1,266). All reached FOCUSED, had valid stage bounds, passed the synthetic
feature checks, and exited with code 0. The last run verifies registration of
the Quest-compatible map, **not** a physical Quest headset or controller.
The two startup/hover failures found during development were repaired before
these final passes. See [upgrade validation](controller-upgrade-validation.md).

Software checks cover the action map, actual operator RNA, real Blender scene
edit/undo/cancel operations, room-scale settings, tracking-loss native code and
bootstrap/launcher guards. These runtime checks do not prove lens visibility,
submitted eye-frame correctness or physical button input. Controller aiming, real
room movement, tracking-loss behavior and comfort still require a headset run.
All third-party modal tools, arbitrary keyboard-free text entry and GPU Cycles
rendering are not qualified; the build lacks GPU Cycles kernel bundles.

Hardware requirements: [Valve Index setup](https://help.steampowered.com/en/faqs/view/7f7d-77fb-8caa-4329)
and [Lighthouse tracking](https://help.steampowered.com/en/faqs/view/1AF1-670B-FF5C-3323).
