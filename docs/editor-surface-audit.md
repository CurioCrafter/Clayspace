# M2 internal editor surface audit (Blender 5.1.2)

## Decision

Build the first spatial editor surface from an existing Blender editor region,
not OS mouse injection, a second process, or a reimplemented widget toolkit.
The smallest credible slice is a **mono `SPACE_PROPERTIES` region rendered to
one session-owned texture**, displayed on a world-space quad, with one
explicitly targeted internal event route. It is not yet a promise that every
popup or file browser works on the quad.

## Existing anchors

| Need | Existing source anchor | Consequence |
| --- | --- | --- |
| XR frame ownership | `source/blender/windowmanager/xr/intern/wm_xr_session.cc:1430` `wm_xr_session_surface_draw()`; `:1580` `wm_xr_session_surface_create()` | XR draws on the main loop and owns a `wmSurface`, GPU contexts, and session teardown. |
| Per-eye framebuffer | `.../wm_xr_draw.cc:144` `wm_xr_draw_view()`; `:176` `ED_view3d_draw_offscreen_simple()`; `:206` `GPU_offscreen_bind()` | The scene is already drawn to an XR offscreen/viewport pair before OpenXR reads the bound framebuffer. The panel must use this context; it must not create a second graphics device/context. |
| Existing runtime-only editor | `.../wm_xr_session.cc:1346`–`:1357` | XR creates `ED_area_offscreen_create(win, SPACE_VIEW3D)` strictly for XR events. This proves a session-owned, unsaved area is an established pattern. |
| Generic offscreen area lifetime | `source/blender/editors/screen/area.cc:2328` `ED_area_offscreen_create()`; `:2369` `ED_area_offscreen_free()` | A `SPACE_PROPERTIES` area can use the same construction/free path; free also removes modal handlers, timers, and message-bus owners. |
| Region render path | `.../area.cc:482` `ED_region_do_draw()` | Given a context, region rect, and bound target, existing editor UI draws itself; no UI clone is required. |
| XR overlays | `.../wm_xr_session.cc:1390` and `.../wm_xr_operators.cc:1307` | `ED_region_draw_cb_activate(..., RGN_TYPE_XR, ..., REGION_DRAW_POST_VIEW)` is the native world-space overlay seam. |
| XR actions | `.../wm_xr_session.cc:1212` `wm_xr_session_events_dispatch()`; `:1286` `wm_event_add_xrevent()` | XR currently queues `EVT_XR_ACTION`, not pointer coordinates or text events. |
| Region dispatch | `source/blender/windowmanager/intern/wm_event_system.cc:4113` `wm_event_do_region_handlers()`; `:4150`–`:4172` | Dispatcher is private and chooses regions from `wmEvent::xy` in desktop window coordinates. A narrow native target-region entry point is required. |
| Standard button/text/numeric behavior | `source/blender/editors/interface/interface.cc:3318` `button_string_eval_number()`; `:3345` `button_string_set()` | Once a genuine region receives Blender UI events, existing RNA number parsing and text setters are reused. |
| Popup ownership | `source/blender/editors/interface/regions/interface_region_popup.cc:922` `popup_block_create()` | Popups allocate a temporary region on `CTX_wm_screen()` and take `window->runtime->eventstate->xy` (`:956`), so they are not automatically surface-local. |
| GPU alpha precedent | `.../wm_xr_operators.cc:1274` `wm_xr_navigation_teleport_draw()`; `.../wm_xr_draw.cc:211` `wm_xr_passthrough_enabled()` | Alpha blending is usable for the in-XR panel, but room composition remains runtime passthrough policy; panel alpha is not optical transparency. |

## Proposed first vertical slice: XR-0002

**Interactive Properties card.** During an active XR session, create one
unsaved `SPACE_PROPERTIES` offscreen area and its `RGN_TYPE_WINDOW` region.
Render that region into a session-owned `GPUOffScreen`, then draw its texture
on one head-locked/world-space quad through the existing `RGN_TYPE_XR`
post-view path. Convert a controller-ray hit to that region's pixel coordinate
and send press/release/move through one new internal, target-region dispatcher.
The card proves a built-in button plus one existing RNA numeric field; it uses
normal Blender undo/operator dispatch and never moves the desktop cursor.

### Pass/fail acceptance

- **Pass:** one Properties panel is legible in both eyes; controller hit selects
  a built-in button and edits a built-in numeric RNA field; undo restores the
  numeric change; desktop input remains usable; ending XR frees the area,
  texture, callback, and handlers with no saved screen/scene data.
- **Fail:** any OS cursor injection, external UI process, duplicated widget
  implementation, leaked `ScrArea`/GPU resource, or UI event that mutates a
  different desktop area.
- **Deferred with explicit desktop fallback:** arbitrary popup menus, search,
  text keyboard/IME, and save/file browser. Their current temporary-region and
  `eventstate->xy` assumptions require a subsequent surface-local popup/input
  contract, not a fake success in XR-0002.

## Concrete blockers before coding

1. `wm_event_do_region_handlers()` is `static`; expose a minimal internal
   function that receives a supplied `ScrArea`, `ARegion`, and synthetic event,
   sets context exactly once, and does not re-run visual desktop hit-testing.
2. Define synthetic pointer/text ownership and focus/capture rules. Existing
   XR events carry action/pose data only; they do not represent `MOUSEMOVE`,
   keyboard, IME, or window cursor state.
3. Decide popup routing before claiming full editor access. `popup_block_create()`
   creates desktop-screen temporary regions, so reusing it unmodified renders
   and receives events in the wrong ownership space.
4. Allocate/free the panel texture inside `wmSurface`'s active XR GPU context
   and lifetime. Do not retain it across `wm_xr_session_surface_free_data()` or
   expect alpha to alter the OpenXR environment blend mode.
