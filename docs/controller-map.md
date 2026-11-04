# Controller map 0.18.0 — what every button does, by situation

**Design rule:** pick with the stick, see a preview, commit on release. Menus are
short lists you can walk with the right stick, and a click presses the
highlighted entry, so you never have to aim at small buttons. Every shape you are
about to create shows as a ghost first. The same buttons do different, related
things depending on whether you are holding something. **Small labels float beside each
controller and always show what every button does right now:** idle, holding or in a
menu, with Index or Quest names. Settings > Button Labels turns them off.

When VR starts, the hand menu is already open (the Guide on first launch) and the
desktop is hidden. You feel a haptic click whenever a press takes effect, and a
distinct buzz when it is refused. Quest names are in brackets.

| Button | Nothing held | While holding an object |
| --- | --- | --- |
| Either trigger | Press menu buttons; use the chosen tool (hold, drag, release). Harder squeeze = stronger clay, wider spray. With the Select tool, on empty space: clear the selection; with Multi-Select on, on objects: add or remove them. | The *other* trigger joins: spread to scale, twist to rotate, move both to carry. |
| Right grip | Pick up what the ray points at (or the selection); it follows and turns with your hand. | — |
| Left grip | Grab and move the world. | (Cancels the hold: it changes navigation.) |
| Right A (A) | Open / close the hand menu. While extruding: **keep this segment and extrude again** from its new end (one undo each). | **Stamp a copy** where the object is and keep carrying. |
| Right stick click | **Tool wheel** for the current workflow (see below). With a list menu open: **press the highlighted entry**. While extruding: **straight out ↔ free**. | **Cycle hold mode:** Follow → Precision (¼ speed) → Snap (5 cm grid, 15°) → **Surface** (slides upright along floors and tables under your ray). |
| Right stick up/down | With a list menu open: **move the highlight** (no aiming needed). A panel: scroll it. No menu: sculpt/spray brush size ±20%. | **Push away / pull closer** along your ray. |
| Right stick left/right | Snap turn. | Snap turn. |
| Left stick | Walk. | Walk (cancels the hold). |
| Left stick click | Show / hide the Blender desktop. | **Set it down** on the surface below. |
| Left A (X) | Undo. | Cancels the hold first. |
| Right B (Index) | **Add menu:** just the shapes. Pick one; a ghost follows your ray; trigger places it. Quest: "Add Shape…" at the top of the Quick menu. | Refused (you feel a buzz). |
| Left B (Y) | **Back:** one level up in a menu, closes it at the top. With nothing open: **hides the desktop**. | Cancel: everything goes back exactly. |
| Index right pad (Quest B hold) | Move the floating panels. | — |
| Index left pad | Teleport. | — |

## Make it yours (Settings > Buttons)

The table above is the default. Right A, right B (Index), right stick click, left A/X
and left stick click can each be set to Hand Menu, Add Shapes, Tool Wheel, Quick Menu, Blender Menus,
Show / Hide Desktop, Undo, Redo, Guide, Next Workflow, Play / Pause or Nothing. The labels
and the Guide show your choices. The "while holding" and "in a menu" jobs stay fixed so
they are always predictable; left B / Y always means back or cancel.

- **Left-Handed:** swaps the grips (left picks up, right grabs the world) and the sticks
  (left turns and scrolls, right walks). Buttons stay on their physical controllers, and
  Quest's panel move stays on right B.
- **Trigger / Grip / Stick sensitivity:** where each input clicks. Quest 3 triggers and
  grips are lighter than Index force sensors; lower Grip if picking up feels stiff.
- **Next VR start:** profile, handedness and sensitivity take effect then. Button
  actions change immediately. Reset Buttons restores everything.

## Moving yourself (0.18)

- **Grip on empty space** (either hand) holds the world: move your hand and the world moves
  with it. **Both grips on empty space:** pull your hands apart to shrink yourself (the world
  gets bigger), push them together to grow, twist around each other to turn the world. It
  only ever turns about the vertical, so the horizon never tilts. Size clicks firmly at
  exactly 1x and ticks every 10 %; the readout shows "You ×0.50 · turned 30°". Let go with
  one hand and the other keeps holding.
- A grip on an object still picks it up. A grip on a panel moves the panels. With something
  held, the second grip resizes the object. Settings > Start & Travel > Empty-space grip:
  Moves the World (default) or Grabs Selection (the old remote grab).
- **Locomotion** (Settings > Start & Travel, next VR start): **Smooth** (left stick glides,
  motion vignette applies) or **Teleport** (push the left stick forward to aim the arc, let go
  to jump: works on Quest 3 too). **Fly:** smooth moves follow your gaze, up and down included.
- **Travel page:** Go to Selection (stand in front of it, at a distance that fits its size),
  Reset View (normal size, upright, at the origin), speeds, snap turn, turn angle, vignette,
  size presets. Both are also assignable to any button (Settings > Buttons).

## One layout for Quest 3 and Index (0.17)

Every default now does the same thing on both controllers; only the Index pads are extra.

| Button | Both controllers |
| --- | --- |
| Right A | Hand menu, **on your left hand** (Settings > Buttons > Menus: Off Hand / Pressing Hand) |
| Right B | Add: shapes, **Prefabs & Starter Scenes ›**, **SDF Blobs ›**, draw tools (Quest B used to move panels) |
| Right stick click | Tool wheel; presses the highlighted entry in a menu |
| Left A / X | Undo |
| Left B / Y | Back / cancel |
| Left stick click | Show / hide desktop |
| Grip on a desktop panel | Move the panels (either grip) |
| Index left pad press | Teleport (Index only) |

With menus on the off hand you point with your dominant hand: trigger clicks, the right
stick walks the list, and stick click presses. Left-handed mode mirrors all of this.

## Prefabs and SDF (0.17)

- **Add > Prefabs:** Furniture (table, chair, stool, bookshelf, sofa, bed, floor lamp),
  Architecture (wall, doorway, window, stairs, pillar, arch, floor tiles, room, little house,
  fence), Nature (tree, pine, rock, bush, mushroom, terrain), Props (crate, barrel, mug, vase,
  bottle, gear, sword, coin), Characters (1.8 m mannequin, snowman, robot). A ghost box shows
  where it lands; the trigger drops it; hold to slide. One undo each.
- **Starter scenes:** Studio (curved backdrop, key/fill/rim lights, camera), Outdoor
  (terrain, trees, rock, sun, sky), Tabletop (table, mug, vase, coin, lamp light).
- **My Prefabs:** select what you built and Save Selection as Prefab. It appears in every
  scene and keeps its parts' arrangement.
- **SDF Blobs (Add, Sculpt wheel, Studio):** Ball / Capsule / Ellipsoid / Rounded Cube;
  Trail or Place; Carve; Blend; Blob Size (squeeze harder or use the right stick);
  trigger an existing blob to move it; Finer / Coarser detail; Melt Softer; Remove Last Blob;
  Bake to Mesh.

## Menus, both hands, faces and components (0.16)

- **Menus:** while a hand menu is open, the stick click *always* presses the highlighted
  entry (0.13–0.15 opened the tool wheel instead: fixed). Point either controller at the
  menu: the hand that is actually on it hovers, a bright dot marks where the ray touches,
  and you feel a light tick for each row you cross. Trigger clicks under the dot.
- **Both grips:** squeeze the second grip on an object you are already holding and pull
  apart to resize it, or swing to turn it. Small wobbles below about 3 % and 6° do nothing,
  and larger motion is exact. Settings > Tools > Two Hands: Scale + Turn (default), Scale
  Only, or Stretch Axis (the old snap to an object axis). A left grip on an object picks it
  up; on nothing it moves the world.
- **Seeing the face:** with Extrude, Free Extrude, Inset, Scale Face or UV Island, the face
  under your ray is tinted and outlined before you press (with an arrow for extrude
  direction). While you pull, the moving face is filled and outlined in front of everything,
  with its side edges and a faint outline of where it started.
- **Vertex / Edge / Face:** the wheel, the Edit page or Quick > Edit V·E·F switch modes and
  enter Edit Mode. Trigger picks the element under the ray, highlighted before you press as
  a dot, a bar or a tinted face. Hold and sweep to add more; with Multi-Select a click
  toggles. Grip moves the selection. **Extrude Selection:** hold the trigger and pull; it
  extrudes like Blender's E, along the average normal (freely for vertices and edges), and is
  one undo step. In Edit Mode the wheel becomes Vertex, Edge, Face, Extrude, Select, Delete,
  Object Mode and Mesh ›. Mesh › lists Select All/None/Invert/Linked/Grow/Shrink, Fill,
  Merge, Subdivide, Duplicate, Separate, normals, Merge by Distance, Dissolve and Delete.

## Feedback you can feel and read (0.14)

| What | Where | Meaning |
| --- | --- | --- |
| Readout plate (tool-colour edge) | Over the working hand while an edit is live | Extrude / inset distance, face scale, moved distance · turn · scale, island faces and crease, link target, measure length, brush pressure. |
| Toast (green / amber / red edge) | Over the hand that acted, about 2.6 s, then fades | Done / check this / refused. |
| Detent (very short tick) | The working hand | Each centimetre, 10 % of scale, 15° of turn, snap cell or island size change. Settings > Feel > Detents. |
| Double tap | The working hand | Committed as one undo step. |
| Low double buzz | The pressing hand | Refused; nothing changed. |
| Corner brackets | Around what your ray is on | This is what the trigger will act on. |

Settings > Feel has **Gentle / Balanced / Pro** presets and switches for Toasts,
Readouts, Outline and Detents. Pro means Snap 1 cm / 5°, a compact wheel and crisper
haptics. Gentle means bigger labels and wheel, softer haptics and slower Precision.

## Tool wheel (right stick click)

A ring of up to eight tools opens in front of the hand that clicked, upright and
facing you. Move that hand a few centimetres toward a wedge: it lights up and you feel a
tick as you cross into each one. Click the stick again, or pull either trigger, to pick
it. Pressing while your hand is back in the middle, or pressing B, closes the wheel.
The tool you already have is shaded.

- **Model:** Select, Extrude, Free Extrude, Inset, Bevel, Scale Face, Cut Away, More ›.
- **Sculpt:** Clay, Grab, Smooth, Flatten, Dig (toggle), Mirror X (toggle), Clay Ball, More ›.
- **Shade:** Spray, Eyedropper, UV Island, Shelf, Node Garden, Smart UV, Checker, More ›.
- **Animate:** Scrub, Key, Play, Select, Place, Measure, More ›.
- **Stage:** Light Here, Spot Here, Camera Here, Live Render, Render, 3D Pen, Studio ›, More ›.

(0.15: the Model wheel swaps Scale Face for **Modifiers ›**; Shade swaps Checker for **Paint
Colour**. Scale Face and the checker stay in the Edit page and UV menu.)

## Studio (Quick menu top, Menus page, Stage wheel)

One list for the rest of Blender, each a native menu on your hand: Modifiers, Lights,
Camera & Render, World, Physics, Rigging, Paint & 3D Pen, Scene Outline, UV, Node Garden
and Blender Menus. Lights, cameras and armatures appear where you point, aimed along the
ray. Each change is one undo step. The full map is in
[blender-coverage.md](blender-coverage.md).

"More ›" opens the Quick menu. Settings > Tools picks which wheel you see (follow the
workflow, or always one of the four) and its size (6–25 cm).

## Modeling by hand

- **Extrude:** trigger on a face and pull; the new end moves straight out along the face.
- **Free Extrude:** the new end follows your hand in any direction, so you can extrude
  sideways, diagonally or at an angle from any side of the model.
- **Chain:** while pulling, right A keeps the segment and starts a new one from its end;
  arms, pipes and stairs grow segment by segment in one motion. Each segment is its own
  undo step.
- **Switch direction mid-pull:** right stick click toggles straight out and free. The
  end never jumps; the new rule applies from where your hand is.
- **Inset:** trigger on a face and drag sideways; a border grows inward (up to almost
  the whole face). Great before extruding a smaller panel.

## Node Garden (Shade wheel, Materials page, Quick menu > Node Garden ›)

The material's real node tree stands in front of you as blocks, laid out like the
node editor. Outputs are spheres on a block's right edge, inputs on its left, coloured
by type; links are lines between them. Point near a socket to see its name.

- **Move a node:** grip its block. The desktop node editor follows, and a node moved on
  the desktop moves its block.
- **Link:** trigger near a socket and drag to a socket on another node; release links
  them. The line snaps to the socket you are nearest.
- **Move or remove a link:** trigger on a linked input: you pick the link up. Drop it on
  another input to move it, or in empty space to remove it.
- **Values:** trigger a block with Select to make its node active; the Node Garden menu
  then shows its values to change, and Add Node puts a new node beside it. Delete This
  Node removes it (never the Material Output).
- Every change is one undo step. Put Away deletes only the garden's own objects.

## UV by parts (Shade wheel, Quick menu > UV ›)

- **UV Island:** trigger on a face. The island floods over neighbouring faces while
  they bend less than the crease limit and stops at existing seams; its border is
  outlined live. Drag sideways to widen (right) or narrow (left) the limit. Release:
  the border becomes seams and only that island is unwrapped. One undo.
- **Smart UV, Pack Islands, Clear Seams, UV Checker, Open UV Editor:** for the selected
  meshes, one undo each. The checker material shows stretching right in the headset.

## Blender Menus (all of Blender, one level at a time)

Quick menu > **Blender Menus ›** (also on the Menus page) lists File, Edit, Add,
Object/Mesh, Select, Context Menu, View, Render, Window and Help, all resolved for the
current mode. Each entry opens *that* Blender menu on your hand, replacing the list, like
a phone settings screen: stick up/down picks, stick click opens or runs, B goes back one
level. Blender's own nested submenus (File > Import ›…) open with a stick click too. We
measured in a real window that Blender's Esc and Left arrow do not step back a single
level, which is why Back is our own menu stack.

## Stretching in every direction

- **Drawing boxes, spheres, cylinders and cutters is upright:** only the controller's
  heading turns the box. Up/down is always height, left/right width and forward/back depth,
  however you tilt or roll your wrist.
- **Two-hand stretch per axis:** hold a shape (or cutter) and pull the other trigger. With
  your hands lined up (within 25°) with one of the object's own axes, only that axis
  stretches: wider, taller or deeper. On a diagonal, it scales uniformly. Reposition your
  hands to stretch another axis next.

## Add menu and previews

- **Add (Index right B, or Quick > Add Shape…):** Cube, Sphere, Cylinder, Cone, Plane,
  Torus and Clay Ball, then the three draw-by-hand tools. Pick with the stick or trigger.
- **Place:** after picking, a green outline of the shape rests where your ray lands:
  on top of floors and tables, against walls, or 70 cm ahead in the air. Pull the
  trigger to drop the real shape there; hold to slide it around, release to keep it
  (one undo), or cancel with left B / Quest Y.
- **Draw and Cut:** before you press, a small ghost of the shape floats at the
  controller tip, exactly where drawing starts. While you drag, a drawn object is
  the real solid shape (no longer a faint wireframe), with a bright outline in the
  tool colour. Cutters stay wireframe in front, so the live Boolean shows through.

## Selecting several things

Turn on **Multi-Select** (Edit page, next to Object / Edit Mode, or the Model Quick
menu). Each trigger click on an object then adds it, or removes it if it is already
selected, and the last one added becomes active (the target for Combine). With the
Select tool, pointing at empty space and clicking clears the selection in either
mode. Every change is one undo step.

## Tape measure

Pick **Measure** from the Model Quick menu. Hold the trigger on one point and move to
another. The ray snaps to surfaces, or a point floats 20 cm ahead in the air. A yellow
line with end rings shows the span, and you feel a tick every 10 cm. The distance (in
scene units, and in real centimetres at your current size) goes to Last action.
Nothing in the scene changes.

## Quick menu (the wheel's More ›, or any button set to Quick Menu)

It opens as a native Blender menu on the hand you clicked with, and shows what
matters for the current workflow:

- **Model:** Select, Extrude Face, Bevel Edges, Draw Cube, Free Extrude, Inset Face, Cut Away,
  Measure, Subtract Selected, Bevel Object.
- **Sculpt:** Clay, Grab, Smooth, Flatten, Dig, Mirror X, Clay Ball.
- **Shade:** Spray Material, Eyedropper, UV Island, UV ›, Material Shelf, Show Logic,
  Node Garden ›, Apply to Selected.
- **Animate:** Scrub Time, Key Selected, Play / Pause, Record.
- **Top of every Quick menu:** Add Shape… (the Add menu as a submenu).
- **Model** also has Multi-Select and Measure.
- **Arrange… submenu:** Drop to Floor (rests each selected object on what is below it),
  Face Me (stands it upright with its front toward you), Reset Rotation, Apply Scale,
  Bring Here. One undo each.
- **Always there:** Duplicate (a copy beside the selection, selected so you can grab it), Bring Here
  (moves a far-away selection to arm's reach), Delete. Each is one undo step. You can
  also switch to the other workflows, Redo, or open Settings.

## Sculpt extras

- **Trigger pressure** (on by default): a light squeeze builds a little clay, and a
  full squeeze builds the full Strength. Spray width grows from 50% to 100% of
  Spray Size.
- **Mirror X:** every clay, smooth and grab stroke also shapes the mirror side
  across the object's local X axis.

## Verification (cloud container, no headset)

These checks ran on stock Blender 5.1.2 with synthetic poses.

- **Controller smoke:**
  - Precision moved the object 0.1 for a 0.4 hand move.
  - Snap landed it on the 5 cm grid.
  - Changing mode never moved it.
  - Two stamps landed at the stamped positions. Cancel removed stamps, and one
    undo removed both stamps together with the move.
  - Set-down put the object's bottom exactly on a floor below.
- **Physical smoke:** a Mirror X grab moved the mirrored vertex by exactly the
  opposite X amount. Quarter pressure built less than half the clay.
- **Feel smoke:** all four Quick menus draw with valid operators and properties.
- **Modeling smoke (0.13):** Free Extrude moved the cap by exactly the hand's 3D
  offset; a chained extrusion left two segments as two undo steps; switching straight ↔
  free mid-pull never moved the cap and sideways motion was ignored once straight again;
  a 30 cm Inset made four border faces 0.3 m wide and clamped short of the centre; a UV
  Island on a cube re-unwrapped only the pointed face and bounded it with four seams,
  one undo; Smart UV, Pack, Checker and Clear Seams were one undo each.
- **Node Garden smoke (0.13):** one block per node and one line per link; a moved block
  moved its node and a moved node moved its block; a socket drag made a real link (one
  undo, redo restores it); picking up a linked input and dropping it in space removed
  the link; Add Node / Delete Node grew and shrank the garden; the output node is
  protected; Put Away left the scene exactly as before. The tool wheel's wedge maths,
  every wedge's tool / operator / setting, and picking a toggle were checked too.
  Duplicate, Bring Here and Delete are one undo each, and Delete is refused with
  nothing selected.
- **Render:** the Quick menu and the Sculpt page were rendered at 150% UI scale.

- **0.9.0 checks:**
  - Stick navigation sends ray-less Down/Up arrow and Enter key events to list menus. The
    native event operator accepts keyboard events without a ray, and Blender menus handle
    arrows natively. Panels keep ray-routed wheel scrolling. Idle hover pauses after stick
    use until the ray moves.
  - The Place ghost's bottom sits exactly on the floor, and Place drops, slides, cancels
    and undoes.
  - All six shapes build at their stated size.
  - The draw preview is solid, and its live outline matches the preview's corners exactly.
  - The Add menu draws with valid operators.
  - `scripts/xr_menu_stick_gui_smoke.py` ran in a real Blender window (virtual display,
    `--enable-event-simulate`). Down, Down, Enter in the Add menu picked Cylinder and
    armed Place with no mouse click. That is Blender's own menu handling of the same keys
    the stick sends. The XR-side injection has not run in a headset.

- **0.10.0 checks:**
  - Multi-Select add, remove, clear, and clear as one undo.
  - Surface mode rests the cube on the floor under the ray and slides it. A regression
    check caught and fixed coplanar flicker: a shape resting exactly on a thin floor let
    the next ray pass through, so shapes now rest 0.5 mm clear. The same fix applies to
    Place.
  - Measure gives an exact 50 cm with 5 ticks, draws its line, and leaves the scene
    unchanged.
  - Drop, Face Me, Reset and Apply Scale each work as one undo.

- **0.11.0 checks:**
  - The upright draw frame keeps height exactly vertical for a tilted, rolled controller.
  - Axis stretch: X-only and Z-only stretches change just that axis; a diagonal scales
    uniformly; cancel is exact. The test caught a crash in the first version, now fixed.
  - Button labels are short for all 18 profile/hand/state cases, and the plate faces the
    viewer at a size that follows your scale. They render in a real window through
    Blender's text drawing.
  - The drill-down stack pushes levels; B pops two levels and then closes; a new button
    press starts fresh.
  - Every Blender Menus entry resolves to a real menu.
  - Bootstrap opens the Guide the first time and the hand menu afterwards.
- **Double rays fixed:** we no longer draw a second, timer-lagged ray line on top of
  Blender's own controller rays. Only the ring at the hit point and the previews remain.

- **0.12.0 checks:**
  - Left-handed action maps for Index, Quest and Auto are legal and conflict-free, with the
    same action and binding counts.
  - Thresholds apply, and an out-of-range threshold is refused.
  - Slot actions open menus on the right hand and Nothing is refused.
  - Labels follow reassigned buttons and handedness.
  - Flatten levels a bump, and the Eyedropper picks the painted face's material.

None of this has run in a headset or simulator. Unverified there: world-space label text
in the XR view, and the native hand menu opening Python-defined menus and nested Blender
submenus. That includes whether the native
hand menu opens a Python-defined menu the same way it opens Blender's context menus.
