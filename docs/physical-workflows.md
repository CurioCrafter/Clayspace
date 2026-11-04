# Physical workflows 0.7.1 — sculpt, materials, animate, change your size, controller feel

This candidate adds physical versions of Blender's main workflows to the hand
menu. Everything is our own code on Blender's native mesh, material, node,
keyframe and XR navigation data: no Hard Ops, BoxCutter or other add-on. Every
controller stroke is hold, drag, release. Release commits one undo step; Left B / Y,
tracking loss or a changed target cancels and restores the exact original.

## Hand menu

Tabs are a 5 x 2 grid: Create, Sculpt, Animate, Desktop, Settings on the top row;
Edit, Materials, Menus, Travel below. All pages were measured at 150% UI scale in a
1080p window and fit without clipping.

### Workflows (Desktop page)

One button sets up each workflow. It chooses the page and tool, opens the matching
native editor on the desktop panel, sets the headset shading, and restores the size
you last used in that workflow:

| Workflow | Page / tool | Desktop editor | Headset shading |
| --- | --- | --- | --- |
| Model | Edit / Move | Properties | Solid |
| Sculpt | Sculpt / Clay | Properties | Solid, MatCap clay look |
| Shade | Materials / Spray | Shader Nodes | Material Preview |
| Animate | Animate / Scrub | Dope Sheet | Solid |

The Desktop editor grid now also offers Texture Nodes, Timeline, Graph Editor, NLA,
Drivers, Video Editing, Python Console, Info Log and File Browser, alongside
Properties, Outliner, Assets, Geometry/Shader/Compositor nodes, UV, Images, Dope
Sheet, Spreadsheet and Text Editor. Native menus (including Sculpt Mode and its
brushes) stay on the Menus page and desktop panel.

### Change your size (Sculpt and Travel pages)

- Giant x10, Large x3, Human x1, Detail 1/4, Micro 1/20, plus Smaller /2 and Bigger x2.
- Uses Blender's native XR navigation scale, pivoting on your head so you do not jump.
- Brush, spray, bevel, drawing and remesh sizes are physical. Shrinking yourself
  therefore makes every tool finer in the world. At Detail 1/4, a 4 cm brush
  touches 1 cm of the model.
- Each workflow remembers its size for the session. Scene files and preferences
  are not changed.
- A size change is refused while a tool is held.

### Sculpt page: clay tools

- **Grab:** hold the trigger on the surface and pull a soft region with your hand.
- **Clay:** hold the trigger and sweep over the surface to build up. Turn on
  **Dig (carve)** to cut in instead.
- **Smooth:** sweep to relax bumps.
- **Brush** and **Strength** sliders; the brush size is physical.
- **Clay Ball:** a smooth 10,242-vertex sphere within reach, with the Clay material.
- **Remesh for Detail:** Blender's voxel remesh with a physical voxel size, so it
  gets finer as you shrink. It refuses to go above about 1.5 million estimated
  vertices.
- The tools work in Object Mode on local, single-user meshes without shape keys.
  They refuse meshes under 50 vertices and tell you to use Clay Ball or Remesh.
  Native Sculpt Mode brushes remain available on the desktop panel.

### Materials page: blocks, spray and logic

- **Material Shelf:** brings out a physical grid of rounded blocks in front of you,
  one per preset plus your own presets, each with a floating name. They live in an
  "XR Material Shelf" collection hidden from renders. You can grip a block to look
  at it up close. Pressing the button again removes only the shelf's own objects.
- **Presets:** Clay, Red Plastic, Rubber, Steel, Gold, Copper, Glass, Car Paint,
  Neon, Wood, Marble and Checker. They are native node materials whose nodes are
  labelled by role ("1. Where", "2. Pattern", "3. Colour", "4. Surface",
  "5. Output") to teach how the graph works.
- **Spray Material:** trigger a block to load it into the spray can, or press a
  preset button. Then hold the trigger on any mesh and sweep to paint faces under
  the physical spray radius. A mesh with no materials first gets an empty base
  slot, so the unpainted faces keep their look. Dense meshes, or Remesh for
  Detail, give finer paint.
- **Show Logic:** opens the real Shader node tree for that material on the desktop
  panel, pinned and framed, and switches the headset to Material Preview.
- **Save My Preset:** saves the active object's material as one file per preset
  under Blender's user data folder (`xr_workbench/material_presets`). Personal
  presets appear as buttons and on the shelf in every scene. Nothing is loaded
  until you pick one, and the open scene is never written.
- **Apply to Selected:** puts the spray material on whole selected meshes.
- **Node Garden (0.13):** the material's real node tree as blocks in front of you, laid
  out like the node editor at 1 mm per editor unit. Grip a block to move its node.
  Trigger near a socket and drag to another to link them. Trigger a linked input to
  pick its link up, then drop it on another input or in the air to remove it. Point
  near a socket to read its name. The Node Garden menu adds common nodes beside the
  active one and shows its values. Every edit is one undo step, and the desktop node
  editor stays in step both ways. Put Away deletes only the garden's objects and its
  own helper materials.
- **UV by parts (0.13):** UV Island floods from the face you point at, stops at seams
  and at bends sharper than the crease limit, and outlines the island live. Drag
  sideways to change the limit, about 20° per 10 cm. Release to mark the border as
  seams and unwrap only that island (Angle Based, 2% margin). The UV menu adds Smart
  UV, Pack, Clear Seams, a UV Checker material and the UV editor.

### Animate page

- **Key Selected** / **Delete Key:** location, rotation (matching the object's
  rotation mode) and scale on the current frame, one undo step.
- **Record:** Blender's own Auto Keying. When it is on, every controller
  move/rotate/scale commit keys the selected objects as a separate
  "XR Auto Key" undo step.
- Start, previous key, -1, +1, next key and End; Play / Pause; frame, start and end.
- **Scrub Time:** hold the trigger anywhere and move sideways. The default is
  50 frames per physical metre (2 cm per frame). Cancel returns to the starting
  frame. Scrubbing is not an undo step.
- **Dope Sheet**, **Graph** and **Timeline** buttons open those native editors.

## Controller feel (0.7.1)

- **Pick things up with the grip.** Right grip on an object selects it if needed
  and carries it rigidly: it moves and turns with your hand around your hand.
  With nothing under the ray, the grip carries the current selection. The Rotate
  and Scale tools, and a world constraint (X, Y, Z…), keep their precise
  single-purpose behaviour. The trigger with the Move tool still translates only.
- **Two hands.** While one hand holds an object (grip, or trigger with
  Move / Rotate / Scale), pull the other hand's trigger. Moving your hands apart
  or together scales about their midpoint, twisting the line between them rotates,
  and moving both carries. Releasing the second hand keeps the result and goes
  back to one-hand carrying. Cancel (left B / Quest Y) restores everything exactly.
- **Push and pull.** While holding, the right stick pushes the object away along
  your hand's ray, or pulls it closer (never nearer than 5 cm).
- **Brush size on the stick.** With a sculpt or spray tool and no panel under the
  ray, the right stick resizes the brush by 20% per push.
- **Haptics.** A native OpenXR vibration action gives a short click for menu
  presses and buttons (undo, redo, cancel, desktop, menu). You also feel pulses
  when a tool starts, a stronger one on commit, a long soft buzz on cancel, a
  distinct buzz when something is refused, and a light tick when a ray lands on
  a new object. Settings > Haptics sets the strength; 0 turns it off.
- **Ray rings.** Each tracked controller's ray ends in a ring the colour of the
  current tool:
  - white: select or transform
  - cyan: face tools
  - blue: edges
  - green: draw
  - red / yellow-green / purple: Cut Away / Join / Keep Overlap
  - orange: sculpt
  - paint colour: spray. Pointing at a material block previews its colour.

  Sculpt and spray rings are the real brush size. Blender's own controller rays
  stay on underneath. Settings > Ray Rings turns the rings off.
- **Combine selected (Create page).** Union / Subtract / Intersect put native EXACT
  Boolean modifiers on the active object, one for each other selected mesh. The
  cutters become wireframe and are hidden from renders; grab a cutter to change
  the live result. Apply bakes only the XR Booleans and hides (never deletes) the
  cutters. Each button is one undo step.
- **Startup.** VR now starts in the scene with the Blender desktop hidden (left
  stick click shows it). Settings > Desktop at Start brings the old behaviour back.
  Owned smoke tests still open the desktop. On first launch the hand menu opens on
  the **Guide**: seven steps covering the menu, your controller's button map (Index,
  Quest or both), ray rings, grabbing and stretching, making and cutting, sculpt,
  paint and animate, and size, travel and comfort. Each step has a "Try it".
  Finish or Skip marks it done; Settings > Replay Guide or the Guide tab brings it
  back. Haptics, ray rings, desktop at start and the Guide state are kept in
  `xr_workbench/vr_prefs.json` in the VR profile's user config. The user's scenes
  and Blender preferences are not written.

## Verification (cloud container, no headset)


- `scripts/xr_physical_tools_smoke.py` runs in a factory-startup background
  Blender 5.1.2 and **passed**. Set `BLENDER_USER_DATAFILES` to an empty
  temporary folder before running it. It uses real data, operators and undo;
  poses are synthetic. It checks:
  - Clay Ball, Grab, Clay build and Dig, and Smooth.
  - Exact cancel and one-step undo.
  - The brush shrinking with your size: 486 → 46 vertices at 1/4 size.
  - Native remesh.
  - All 12 presets.
  - Spray cancel, commit and undo, including the empty base slot.
  - Shelf with 12 blocks and 12 labels, dipping into a block by aiming from the
    viewer, and clean shelf removal.
  - Saving and loading a personal preset file.
  - Keys and key jumps, scrub and scrub cancel, delete key and native auto-key.
  - Head-pivot size math, and the size change requiring a running XR session.
- A GUI run under Xvfb exercised the Workflow button and Show Logic in real
  editor areas. The Shader editor opened pinned to the material with labelled
  nodes. It also showed the shelf, a sculpted clay ball and a sprayed cube in
  Material Preview.
- Hand-menu pages were rendered as desktop popovers at 150%. The rendered-click
  test (`xr_command_controls_test.py`) now clicks all 9 tabs and all 19 tools.
  Its new targets were measured from those renders, **but the test itself has not
  run**: it needs the patched Windows build with the Monado simulator or SteamVR.
- 0.7.1 checks, also on stock Blender with synthetic poses:
  - The controller smoke drives real fake-XR events through the dispatcher. Grip
    grab turned the cube exactly with a 90 degree hand twist. Spreading the left
    trigger hand from 1 m to 2 m doubled its size about the midpoint. Releasing the
    second hand kept the stretch; the right hand kept carrying; push and pull moved
    it along the ray. Cancel restored exactly and drained the held second trigger.
  - `scripts/xr_feel_smoke.py` passed (set `BLENDER_USER_CONFIG` to an empty
    temporary folder). Union / Subtract / Intersect gave exact volumes 12 / 4 / 4,
    each as one undo step. Moving a cutter changed the live result. Apply baked it
    and kept the cutter hidden. It also checked guards, prefs persistence, Guide
    navigation and done state, ray-ring geometry and colours for all 19 tools,
    stick brush resizing, and that haptics are safe without a session.
  - The profile contract with the new haptic action passed: Index 22/22,
    Quest 20/20, Auto 22/42 actions/bindings, with no component conflicts.
  - The offline bootstrap tests passed for both startup paths: hidden desktop
    with the Guide once, and the desktop when requested or during smoke tests.
  - The ray-ring draw function rendered through Blender's real POLYLINE shader in a
    desktop viewport. Its XR-region draw uses the same route as the edge overlay,
    which earlier runs saw in the headset, but it has not been seen in a headset.
- Not verified: anything in a headset. That includes whether the haptic pulse
  strengths and ray rings feel right, grab/stretch comfort, navigation-scale changes
  (native `navigation_scale` has not been exercised in a running session),
  physical brush feel and rates, spray feel, and whether the shelf's placement
  and label size are comfortable. Also unverified: sculpt and spray performance
  on meshes above about 100k vertices, and Quest hardware.
