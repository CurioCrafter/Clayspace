# Native Blender XR M0 integration

## Source and ownership

`native/blender-5.1.2` is an independent shallow checkout of the official
`https://projects.blender.org/blender/blender.git` tag `v5.1.2`, resolved to
`ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832`. The existing `D:/BlenderRewrite`
source, installed Blender binaries, preferences, runtime registrations and user
scenes are not modified. Its existing dependencies are read only build inputs.
The isolated changes are on branch `feature/xr-workbench-m0`.
The fork retains Blender's GPL-2.0-or-later licensing and bundled third-party notices;
the old add-on's license does not relicense Blender.

The XR diff is limited to `GHOST_XrContext.cc`, `GHOST_XrSession.cc` and
`GHOST_XrSession.hh`. `source/creator/CMakeLists.txt` also backports upstream's
FFmpeg 8 DLL install branch: the local libraries compile/link successfully but
the 5.1 installer otherwise requests nonexistent FFmpeg 5 DLL names.
It selects the available SDL3 DLL from the same local bundle, retaining SDL2
packaging for the pinned dependency tree. SDL audio itself remains disabled.
No Python frame timers, synthetic hardware results or
replacement renderer were added.

## Existing integration path

`source/blender/windowmanager/xr/intern/wm_xr.cc` creates the GHOST context and
registers drawing/passthrough callbacks. `wm_xr_session.cc` starts/stops the session.
`wm_xr_draw.cc` reads the existing `V3D_OFSDRAW_XR_SHOW_PASSTHROUGH` setting and
clears that setting with a user report when GHOST cannot activate passthrough.
GHOST owns the OpenXR instance, session, spaces, swapchains and compositor layer.

The patch queries `xrGetSystemProperties` with `XrSystemPassthroughPropertiesFB`
only when the extension is enabled. An advertised extension alone does not count
as device capability or working passthrough. It enumerates environment blend modes
for the actual view configuration and requires the existing opaque route. It never
forces environment alpha blending: the existing FB underlay only enables projection
texture alpha after successful layer creation.

Passthrough activates only through the existing user setting. Parent/layer creation
results are checked independently, creation structures are zero initialized, and
function pointers belong to the current instance/session. Layer creation failure
destroys the parent; turning the setting off destroys both; teardown destroys the
layer before its parent before the session. Missing functions and unsupported
devices retain opaque VR through Blender's existing fallback callback.

## Native diagnostic evidence

Successful instance creation writes `XR_WORKBENCH key=value` stdout lines:
`runtime_name`, `runtime_version`, repeated `enabled_extension`, repeated
`enabled_api_layer`, and integer `graphics_binding`. Enabled layer lines cover
explicit layers requested in `xrCreateInstance`; OpenXR core cannot enumerate
actually active implicit layers, so absence is not proof of no implicit layers.

System discovery adds `system_name` and `passthrough_status=available|unsupported`.
Preparation adds repeated integer `available_blend_mode` values. State transitions
emit integer `session_state` (`0` after destruction). Passthrough transitions emit
`active`, `inactive`, `function_unavailable`, `create_failed result=N`, or
`layer_failed result=N`; `composition_route` is `opaque` or `fb_passthrough`.
An active marker establishes successful resource creation, not visual calibration
or proof that the compositor displayed the camera image. Logs describe their run,
not current hardware after the process exits. These runtime/composition fields
still use stdout evidence; subsequent editor and hand RNA APIs are documented in
`docs/native-editor-implementation.md`.

## Build and checks

Run `native/build_native.ps1 -Target bf_intern_ghost` for the native XR library,
`-Target blender` for the executable, then `-Target install` for its runtime files.
The expected executable is `native/build-ninja/bin/blender.exe`. The script uses
MSVC 2022, CMake and Ninja, writes a target-specific build log, enables OpenXR and
uses OpenGL. Cycles, USD, OpenVDB and some optional heavy features are disabled for
the M0 build; this is not a feature-equivalent official distribution.

The default dependency input `D:/BlenderRewrite/lib/windows_x64` is from an existing
5.3 checkout, so a full build using it is provisional. The matching 5.1.2 dependency
gitlink is `17e87e0869383bcf1dd8339502e6008b7c0c8eb0` in
`https://projects.blender.org/blender/lib-windows_x64.git`; supply its checked-out
path as `-LibraryDir` for a reproducible release dependency set.

From a Visual Studio developer PowerShell:

```powershell
python native/test_passthrough.py --openxr-include D:/BlenderRewrite/lib/windows_x64/xr_openxr_sdk/include
git -C native/blender-5.1.2 diff --check
```

The C++ regression harness compiles the actual native passthrough method bodies
against a fake runtime and asserts parent/layer failure handling, missing function
handling, unsupported capability, repeated-frame reuse, reverse-order cleanup and
idempotent cleanup. This does not contact a runtime, camera or headset. Real
headset lifecycle, focus/recenter/restart, timing and passthrough visibility require
hardware validation before M0 can be marked complete.

## Verified native build (2026-09-21)

The complete executable build and runtime install passed with MSVC 19.44.35222
and the existing local dependency bundle. The install's initial FFmpeg/SDL filename
failures were fixed in the source installer as described above. Build logs retain
those attempts; the last install exited successfully.

`native/background-smoke.log` records `XR_NATIVE_BACKGROUND_SMOKE_PASS` and a clean
exit using factory startup and isolated user paths. The assertions verify version
5.1.2 and enabled OpenXR support. Binary SHA-256:
`20860BC3CE48B05013E064EB51F74BDD6EA633A539678C3918327A07E38AA14E`.
The compiled branch reports `feature/xr-workbench-m0 (modified)`.

Native GUI and headset evidence is a separate qualification step, tracked by the
application's `native/runtime-manifest.json` when available; the background smoke
does not activate XR or qualify hardware. Rebuilding changes the binary hash and
requires requalification before automatic launcher selection.

## Subsequent native work

The immutable M0 runtime is now preserved in `native/releases/m0-camera-off`.
The implemented M2 editor-window card, targeted input, native status and editor
texture capture have a separate preserved runtime in
`native/releases/m2-editor-card`; see `docs/native-editor-implementation.md` for
its exact hash, API, tests and hardware limitations. The M2 patch is preserved as
`native/xr-workbench-m2.patch` independently of newer working source.

Post-M2 source adds explicitly enabled `XR_EXT_hand_tracking` sampling with
UNOBSTRUCTED data-source checks. Unknown/controller-derived or stale joints are
not accepted as bare hands. Actual provider availability, camera-only tracking
accuracy and optical alignment remain separate qualification gates.

A repeated-start/stop test of the M2 runtime exposed a crash inside SteamVR's
`vrclient_x64.dll`. Source review found disabled GL/D3D interop cleanup and
incorrect graphics-binding/context destruction order. Coordinated native fixes
restore cleanup ordering and check failed transfers before copying a null
texture. A successful compile or cleanup mock does not prove that the physical
stress-test failure is fixed; controlled runtime soak evidence must establish it.
