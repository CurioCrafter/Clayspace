# Blender coverage in VR (0.17.0)

This page answers one question: **how do I reach each part of Blender from the headset?**
Every area has one of three routes:

- **By hand:** a physical tool. Point, hold the trigger or grip, move, and release for one undo step.
- **Hand menu:** a native Blender menu or page on your controller. The stick walks it; values
  are Blender's own fields.
- **Desktop panel:** Blender's real editor on the floating desktop (left stick click), used with
  the controller ray like a mouse. It works for everything, with no VR-specific shortcut.

"Checked" means an automated test in real Blender 5.1.2 with simulated input
(`scripts/xr_*_smoke.py`). Nothing is headset-verified until someone wearing it confirms it.

| Area | By hand | Hand menu | Desktop panel | Checked by |
| --- | --- | --- | --- | --- |
| Objects: select, move, rotate, scale, duplicate, delete, arrange | Grip carry, two-hand stretch/twist, Multi-Select, stamp, Surface / Snap / Precision | Quick, Arrange, Edit page | Outliner, Properties | controller, feel |
| Add primitives | Place Shape with ghost; Draw Cube / Sphere / Cylinder | Add (right B), Create page | Add menu | draw, feel |
| **Prefabs and starter scenes** | **Place Prefab with ghost, slide on surfaces** | **Add > Prefabs: 34 built-ins, 3 scenes, My Prefabs** | Append from the saved .blend files | **prefabs/sdf** |
| **SDF (implicit) modeling** | **SDF Blobs: trail / place / carve / move, pressure size** | **SDF menu: shapes, blend, detail, bake** | Metaball properties | **prefabs/sdf** |
| Mesh modeling | Extrude, Free Extrude, chained extrusion, Inset, Scale Face, Select / Bevel Edges | Edit page, Blender Menus > Mesh | Edit Mode with every tool | face, edge, modeling |
| **Vertex / edge / face editing** | **Ray pick and sweep select, grip moves the selection, Extrude Selection** | **Edit V·E·F menu:** modes and 16 mesh actions | Edit Mode | **interaction fixes** |
| Booleans and bevels | Cut Away / Join / Keep Overlap cutters | Combine selected, Bevel Object | Modifier panel | draw, native tools |
| **Modifiers** | — | **Studio > Modifiers:** add 16 kinds, see and change their key values, show/hide, reorder, apply, remove | Properties > Modifiers | **studio** |
| Geometry Nodes | — | Add a Geometry Nodes modifier (a working in→out group) | Geometry Node editor opens from the modifier | studio |
| Sculpting | Clay, Grab, Smooth, Flatten, Dig, Mirror X, trigger pressure, Clay Ball, your size | Sculpt page, wheel | Native Sculpt Mode brushes | physical |
| Materials | Material Shelf blocks, Spray Material, Eyedropper | Materials page, presets, save your own | Shader editor (Show Logic) | physical, feel |
| Shader node trees | **Node Garden:** move nodes, drag links, remove links | Add Node, node values | Shader editor stays in step | node garden |
| UVs | UV Island: unwrap by parts | Smart UV, Pack, Clear Seams, Checker | UV Editor | modeling |
| **Vertex colour painting** | **Paint Colour** spray with falloff and pressure | Studio > Paint & 3D Pen | Vertex Paint mode | **studio** |
| **Curves / drawing in the air** | **3D Pen:** real bevelled Bezier curves, pressure = thickness | Studio > Paint & 3D Pen | Curve Edit Mode | **studio** |
| **Lights** | Grip-aim any light | **Studio > Lights:** Point, Spot, Area, Sun at your hand along the ray; power, colour, size; aim at the selection | Light properties | **studio** |
| **Camera** | Grip the camera to fine-tune | **Camera from My Eyes / from My Hand**, make selected active, lens, depth of field | Camera properties | **studio** |
| **Rendering** | — | **Live Render in Headset**, Render Image, Save Render, EEVEE / Cycles / Workbench, samples, resolution | Render properties, render window | **studio** |
| **World** | — | **Studio > World:** Studio, Daylight, Sunset, Night, White; colour and strength | World properties | **studio** |
| **Physics** | Carry objects into place before playing | **Studio > Physics:** falls (active), solid (passive), cloth, collision, add ground, remove, play, back to start | Physics properties, cache | **studio** |
| **Rigging** | — | **Studio > Rigging:** armature at hand, edit bones, pose mode, bind mesh with automatic weights | Armature edit / pose tools | **studio** |
| Animation | Scrub Time, Record (auto-key every move) | Key, Delete Key, jump keys, Play | Dope Sheet, Graph, NLA, Timeline | physical |
| **Scene outline** | — | **Studio > Scene Outline:** select, hide/show, isolate, show all, collection visibility | Outliner | **studio** |
| Measurement | Tape Measure with 10 cm ticks | — | — | feel |
| Files: open, save, import, export | — | Blender Menus > File (every entry, incl. installed exporters) | File browser | feel (menus) |
| Preferences and add-ons | — | Desktop page: Add-ons, Preferences | Preferences window | ui settings |
| Compositing, Video Editing, Text Editor, Python Console, Spreadsheet, Drivers, Info | — | Desktop page buttons | The real editors | ui settings |
| Everything else | — | **Blender Menus:** File, Edit, Add, Object/Mesh, Select, Context, View, Render, Window, Help, mode-aware, stick-walkable with Back | Every editor | menu stick GUI |

## What "covered" does not mean yet

- **Pose Mode by hand:** posing bones by grip is not done yet; use the desktop tools. Edit Mode
  has hand selection, grip moves and Extrude Selection (0.16); loop cuts, knife and bevel
  of a selection are still desktop / Blender Menus tools.
- **Texture painting to image textures:** paint goes into a colour attribute, not an image.
  Blender's Texture Paint mode is reachable on the desktop.
- **Grease Pencil strokes:** the 3D Pen makes curves (meshes on demand) rather than Grease
  Pencil strokes. Grease Pencil drawing works on the desktop panel.
- **Rendering in the headset:** Live Render uses the XR viewport's Rendered shading. Final
  renders open Blender's render window on the desktop. GPU engines were not tested here: the
  cloud container has no GPU, so the automated still render uses CPU Cycles at 1 sample.
