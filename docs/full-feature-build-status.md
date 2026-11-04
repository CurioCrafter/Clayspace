# Full-feature source build status

September 24 update: the [personal add-on/spatial-panel work](personal-addons-vr-workspace.md)
adds an isolated 5.1 profile snapshot, larger native access controls and separated
editor surfaces. The frozen camera diagnostic `blender-hands-test.exe` retains SHA-256
`6870107F3CE7E0760A0175C176793C66453E31D45D260499E37026F0F9956795`.
The later September 24 [SteamVR/Index controller build](steamvr-index-workspace.md)
is `native/build-features/bin/blender.exe`, SHA-256
`FBEF1148907F1A2EBB7D3A7CECE91DE206E5FEC5CBC10E94EDFBDBCB09A4C71C`.
Personal-workspace launchers use this latest binary; the camera diagnostic binary
was not overwritten. Native updates clear inactive input, reject invalid controller
poses before editing/navigation, and validate/log runtime stage bounds. The
combined 19-action map attached in a real Monado simulator session with clean
start/draw/stop; this is not physical SteamVR controller or room-scale proof.
The subsequent 0.5 upgrade adds native rig transforms/hit queries, opt-in bounded
hold acceleration and 21 actions with 41 Auto-profile bindings. Actual SteamVR clean and personal
profiles passed synthetic object/panel/menu feature tests. Current upgrade evidence
is in `reports/controller-features/verification.json`; the older baseline is in
`reports/steamvr-validation/verification.json`. New physical controls and the
Monado personal-workspace route have not been re-qualified in the lenses.
The user-confirmed previous executable is backed up at
`native/releases/steamvr-confirmed-20260924/blender.exe` (exe-only rollback copy,
not a standalone distribution; restore beside the existing DLLs with Blender closed).
The prior diagnostic executable is backed up as
`native/build-features/bin/blender-hands-test.pre-texture-fix.exe`.
A real visual defect was found in BOTH camera/editor
textured draws: a second `immVertexFormat()` erased the position attribute while
adding UVs. Both now share one format; the actual-code regression proves distinct
attributes. The historical software counts below do not by themselves prove a
correct rendered editor or camera image. The corrected build passed the
separated-panel simulator again; `reports/workspace-panels/separated-eye-final.png`
was visually inspected and shows native editor panels, unlike the corrupted
pre-fix eye image. This is camera-off simulator evidence, not physical camera proof.

The separate Windows release-feature build lives at `native/build-features/bin/blender.exe`. It uses Blender 5.1.2 source commit `ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832` with this workspace's XR changes, and the matching official Blender Windows library commit `17e87e0869383bcf1dd8339502e6008b7c0c8eb0`. `native/build_feature_complete.ps1` verifies both revisions before configuring or building. It does not replace the frozen reduced-feature runtime.

The CMake cache enables Cycles, OSL, CPU denoising, USD, OpenVDB, Alembic, tracking (`LIBMV`), fluid/ocean/remesh modifiers, FFmpeg, Vulkan, and OpenXR. CUDA/HIP/oneAPI kernel bundles, OptiX, and HIP RT are disabled in this configuration; `nvcc`, `hipcc`, `icx`, and `dpcpp` were not found on `PATH` during QA. GPU Cycles parity is therefore not established.

The earlier September 23 executable was rebuilt with a native
Cancel Edit action for the Session card and support-hand Cancel routing during
an active one-hand drag. A later build added a 2D Index-camera fingertip
pointer for task-card buttons in the Monado desktop simulator, with a visible
cursor and release-to-activate pinch. It cannot produce a scene/world ray.
Its SHA-256 is
`55D51645B15BB379DB1EB52448B9CCC2AF86983604AB2FE6675F2D54F3A70D53`.
The final binary passed `native/test_feature_build.py` from isolated factory
startup: a CPU Cycles render,
USD export, major release flags and native XR API registration
(`XR_FEATURE_BUILD_PASS cpu_cycles_render=1 usd_export=1 release_flags=1 native_xr_api=1 gpu_cycles_verified=0`).
It also passed the incremental native install build, the MSVC task logic
fixture, eight image-gesture tests, 30 hand tests, the no-session Blender API
check, and an in-process Monado simulator GUI probe with SteamVR stopped.
The GUI probe exercised four Arrange tools,
Add Cube, support-hand Session Cancel Edit during a held one-hand drag with
rollback and no history entry, Undo ->
Redo -> Undo, the active-edit history guard, Exit, and original scene
restoration. Its marker is in
`.tmp_xr_probe_9b67e15b187741e3a28a900f20162883/stdout.log`:
`XR_TASK_CARD_PASS ... image_card_pointer=1 image_cursor_composed=1 cancel_edit=1 support_cancel=1 ... optical_verified=0`.
The eye image `output/native-cursor-eye.png` in that probe shows the yellow
cursor and highlighted Move tile. The simulator uses synthetic hand
observations, not a live camera or physical optical provider. Physical
comfort and hand-button input remain untested.

The user launched this full-feature source build through the process-scoped,
SteamVR-free Windows Monado runtime and confirmed seeing its Blender scene and
native XR task card in the Index. They reported the view remained visible while
slowly turning their head, then closed Blender normally. The second launch log
`reports/index-physical-launch/launch-20260923-161715-55fcf102.txt` records
session running, card requested, Blender exit code 0, and no change to the
registered system OpenXR runtime. The first attempt failed before XR because
an add-on `cancel` callback had the wrong signature; that was fixed and a
factory-startup add-on smoke plus callback regression passed before the retry.
This is a seated, camera-off, fixed-position 3DoF milestone—not qualified
6DoF tracking, live hands, calibrated passthrough, or full tool coverage.

## Camera and image-local hand-button candidate (September 23)

The first opt-in camera overlay launch entered XR and processed 475 stereo
packets with draw calls, but the tester saw only the Blender scene/card. Its
status log `reports/index-physical-launch/launch-20260923-184624-696d1336.txt`
ended with Blender exit code 11. The native log shows a Vulkan surface-lost
error and a later access violation inside the experimental Monado runtime on
shutdown; that is a real crash, not a clean exit or proof of visible video.

The current source candidate places the camera after the editor and card draw,
sets camera composition to 90% over the Blender view, and adds a central cyan
draw marker. A separate `XRWM` v2 opt-in stream carries stereo camera pixels
and qualified image-local hand landmarks through one camera process. Its fixed
left-eye Add Cube HUD requires a fresh open → sustained pinch → open cycle,
then calls Blender's undoable Add Cube operator. It never projects camera
pixels into a claimed world-space hand ray. Both modes blank camera after five
minutes; the gesture launcher is `Open Index Blender Hands Add Cube.cmd`.
Native compile/link, stream protocol checks, launcher guards, Python gesture
tests, and headless RNA/add-on checks passed. Whether the new camera layer,
button, and created object are actually visible/usable in the Index remains
unverified pending a new physical run. A guarded Monado DLL now skips rendering
after a lost display surface and passed compile/link plus a focused offline
guard check; whether it prevents the physical shutdown crash is unverified.
The prior DLL is backed up at
`native/steamvr_free/monado-surface-guard-staged/openxr_monado.pre_guard.dll`.
