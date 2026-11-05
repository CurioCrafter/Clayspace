# Blender 5.1.2: personal add-ons and spatial panels

The private snapshot is `.xr_addons_profile`, separate from both the normal
Blender profile and the camera diagnostic `.xr_profile`. It contains 60
discoverable user add-ons/extensions and the additional nested `blenderquad`
container. The initial count of 61 filesystem entries included that container,
which is not itself an importable add-on.

The snapshot preserves `userpref.blend`, scripts, extensions, datafiles and
add-on configuration sidecar folders. No startup scene, recent-file history,
symlink or junction is copied. Creation refuses an existing destination.
The loader checks all four Blender resource directories point into the private
profile and verifies the original preferences hash before/after registration.
This is profile separation, not an OS sandbox for arbitrary third-party code.
External asset-library references remain references; their contents are not copied.

## Launchers

- `Open VR Addons Workspace.cmd`: desktop inspection of the source-built
  5.1.2 workspace with personal add-ons; no automatic camera or XR session.
- `Open Index Blender Addons Workspace.cmd`: opt-in, seated physical Index test;
  camera plus image-local Add Cube, personal add-ons and separated editor panels.
  SteamVR must be closed. Existing runtime/display/confirmation guards still apply.
- `Open Index Blender Hands Add Cube.cmd`: the smaller diagnostic profile for
  isolating camera/pinch behavior from third-party add-ons.

All launchers retain text logs. Personal-profile logs are in
`.xr_addons_profile/logs`; the load summary is `addon-load-report.json` there.
The two known remote-control add-ons, `blender_bridge_addon` and
`bl_ext.user_default.mcp`, are available in Preferences but deferred from automatic
registration. Other saved enabled add-ons are loaded. Disabled installed add-ons
are discoverable, not forcibly enabled. Preferences auto-save is disabled in the
private session; explicit saves target that private profile.

## Native UI

The VR Workspace sidebar has larger grouped controls for panels, editors,
object/output settings and add-ons/preferences. It reuses Blender's actual
editors rather than duplicating third-party interfaces. The native
`wm.xr_editor_surface(separated=True)` lays out up to three area crops from one
window texture: modeling viewport in the middle, Properties to the right and
Outliner to the left (largest available editors are fallbacks). Pointer
hit-testing uses the same rectangles. The private launcher starts native UI
scaling at least 1.5; panel size, distance and UI scaling remain adjustable.
Captured drags stay on their original panel; changed/closed layouts cancel them.

Full Window exposes all workspace menus/editor selectors. Native popup and child
windows automatically use the complete window for their coordinate routing.
The shader/node/asset/UV/editor buttons reuse a side editor without replacing the
modeling viewport. A one-editor layout is not automatically split.

## Evidence and limits

- All 53 non-service enabled entries registered in the GUI; original preferences
  SHA-256 stayed unchanged. This includes bundled modules and user add-ons.
- Eleven editor types and eight Properties contexts passed native Blender checks.
- GUI redraw/opening checks passed for the workspace sidebar and five editor types.
- Headless registration had one failure: HardOps uses GPU drawing at import time.
  Its GUI registration subsequently passed; background mode is not a valid test
  for every installed add-on.
- The camera-off simulator exercised separated editor composition, numeric
  transform, undo, popup input, capture release and session shutdown.
- Final simulator log: `.tmp_xr_probe_e76824524ab04caa88de630741be6c6a/stdout.log`.
  The inspected eye image is `reports/workspace-panels/separated-eye-final.png`.
  All three surfaces render; side panels extend beyond the forward view and
  require turning toward them. Comfort and reach are not physically verified.
- Visual inspection caught a shared native texture vertex-format reset bug in
  both the editor and camera paths. Software input/composition counters alone
  did not detect the bad image. Physical camera confirmation remains required.

No claim is made that every third-party operation works through bare hands.
The Index-camera pinch currently activates the image-local Add Cube target;
it does not supply calibrated world-space pointing at arbitrary native panels.
Text entry without a keyboard, arbitrary modal add-on workflows, full-stack
latency and physical panel comfort are still unqualified. The simulator also
reported late frames under test load; it is not a 90 Hz performance result.
The source build does not include the optional GPU Cycles kernel bundles.
The camera route was developed without base stations: stay seated, do not use
its raw camera view for walking, and do not treat it as qualified 6DoF passthrough.
The user now reports base stations and both controllers ready for the separate
[SteamVR controller route](steamvr-index-workspace.md); its physical room-scale
acceptance test is still pending.
