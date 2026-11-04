# SteamVR-free Valve Index runtime track

The source-built Blender uses OpenXR. Its runtime can be selected for one
Blender process using `XR_RUNTIME_JSON`; the selector at
`native/steamvr_free/runtime_selector.ps1` validates a candidate manifest and
sets that variable only for the launched process. It does not change the
machine-wide Windows OpenXR registration. The selector deliberately reports
`alternate_runtime_qualified=false` until the complete hardware stack passes
real-device tests. It disables the SteamVR-specific Rectus layer only in the
child process.

Current machine inspection found the Windows active OpenXR manifest at
`C:\Program Files (x86)\Steam\steamapps\common\SteamVR\steamxr_win64.json`.
The original in-process Monado library and manifest in this workspace have
only simulated/NS drivers. A separate Release build now compiles the opt-in
Windows Vive/Index driver and extended-display compositor. On September 23 it
created a physical Index OpenXR session and submitted 694 stereo test frames
over 4.97 seconds; the tester saw the lens colors. A later camera-off launch of
the source-built Blender 5.1.2 showed its scene and native XR card in the
Index, stayed visible during a slow seated head turn, and exited with code 0.
Neither build includes qualified libsurvive tracking or a live Blender
optical-hand provider.
A name or a successful OpenXR instance is insufficient to verify Index
presentation.
The user currently reports no Lighthouse base station, so 6DoF Lighthouse
head tracking cannot be claimed on this hardware setup.

## Opt-in camera layer in Blender (not yet physically verified)

The source-built Blender has a separate, process-scoped **uncalibrated seated
camera diagnostic** path. It is off by default. With explicit camera consent,
the `Open Index Blender Camera Overlay.cmd` launcher selects the local Index
OpenXR runtime and the existing `mercury_direct_camera_eval.exe
--consented-stream-rgba` producer. The reader begins only from an active XR
eye-render callback, consumes each stereo RGBA frame in memory, draws the
live camera image at 72% alpha over the Blender scene, and then draws the XR
editor/card above it. The diagnostic writes no camera images or joint traces;
the existing XR test-eye and editor capture paths are disabled while this
camera layer is requested. Its text log records aggregate frame/draw/stale,
dropped-packet, and per-eye contour counters, not camera pixels or landmark
coordinates.

Capture ends no later than 20 seconds after the producer starts, and a frame
older than 500 ms is hidden rather than held on screen. XR/session teardown
stops the reader; a Windows kill-on-close job prevents its camera/FFmpeg child
from surviving a Blender crash. Blender's scene and card stay available after
camera shutdown. The v1 packets do not carry camera-exposure timestamps, so
500 ms is measured from arrival at Blender, not from image capture. The
Blender reader keeps only the newest complete packet. The producer also keeps
only the newest complete FFmpeg frame and rejects frames more than 500 ms old
before writing a packet, but conversion, pipe transit, and actual camera
exposure age still need a separate hardware timing check. This is **not
optically calibrated passthrough**, room-scale
tracking, or a safety view for walking. The source-level protocol test, the
source-built Blender link, factory-startup feature smoke, and camera-off
simulator XR-card probe pass. A seated physical overlay check must pass
separately before calling this camera path verified in the Index.

## Candidate stack and missing Windows work

Monado provides a full OpenXR runtime, libsurvive provides independent
Lighthouse 6DoF tracking, and Monado Mercury can process Index stereo camera
frames for hands. Monado's published [Index setup](https://monado.freedesktop.org/valve-index-setup.html) is Linux specific.
Its [supported hardware table](https://monado.freedesktop.org/) lists native
Index and libsurvive paths on Linux; its [getting started guide](https://monado.freedesktop.org/getting-started.html) says Windows is in
progress. The [direct-mode description](https://monado.freedesktop.org/direct-mode.html) documents Linux display
acquisition. [libsurvive](https://github.com/collabora/libsurvive/blob/master/README.md)
supports Windows tracking, but says it does not independently run the HMD.
The upstream Monado source has a Windows compositor window but gates its native
Vive/Index driver on Linux. This workspace fork now has an opt-in Windows
driver and extended-display compositor; the default remains off.
There is no verified Windows Monado direct-display path for this Index, and
building libsurvive alone would not make an OpenXR headset runtime.

The local Monado source revision is
`78a4069fbbde7b084886abea2ddf7bc61df7a99f`. Its Mercury hand-tracking
files had separate uncommitted edits at build time; Mercury was disabled in
this runtime configuration. The Windows CMake build of `openxr_monado.dll`
passed with `XRT_BUILD_DRIVER_VIVE=OFF` and `XRT_BUILD_DRIVER_SURVIVE=OFF`.
The initial attempt to force `VIVE=ON` failed the `XRT_HAVE_LINUX` dependency check. Forcing
`SURVIVE=ON` failed because `SURVIVE_FOUND` was false. The library at
`native/steamvr_free/monado-build/src/xrt/targets/openxr/openxr_monado.dll`
has SHA-256
`1F9995A022EA6A9B9CAADFE903326C6D056D2FF49DFF1FBB6B157627182DC1C5`.
Its generated `openxr_monado-dev.json` manifest has SHA-256
`9F86011C99B4750BD255AC687F02F61756C1062062AB054CB91C7BD914094867`.

A bounded factory-startup Blender test selected that manifest with a
process-only `XR_RUNTIME_JSON`. The OpenXR loader loaded Monado and enumerated
`XR_EXT_hand_tracking`. `xrCreateInstance` then returned
`XR_ERROR_RUNTIME_FAILURE` because `monado-service` was not running. The
probe exited without an XR session; its strict recovery assertion failed and
it drew zero XR frames. Its output is
in `.tmp_xr_probe_0b6857d7f9834086a778f0ee9bd426c7`. Even if a service
later starts successfully, this build has no Index driver and cannot be used
as Index display, camera, or physical hand-tracking evidence.

The service build also compiled on Windows, but a bounded, owned-process
attempt left Blender unable to connect to `\\.\pipe\/tmp/monado_comp_ipc`.
The exact service process was stopped and global OpenXR registration was
unchanged. A separate in-process Monado build (`XRT_FEATURE_SERVICE=OFF`,
simulated driver only) avoids that IPC route. Its OpenXR DLL has SHA-256
`A29EB053071D07487EAF5F1F45CC90714CE22D9B9A5BC21AD71EB94B27A40B9C`.
Its `openxr_monado-dev.json` manifest has SHA-256
`86AD4BC518B445B5752018ACA4AE963416EC28DCAF831E5BAA1A572C9BF28D4C`.
Its first probe failed creating Monado's default user config directory;
process-only `LOCALAPPDATA` redirected that config to
`native/steamvr_free/simulated-config` within the workspace. No system
OpenXR registration or global environment was changed.

With the in-process manifest and `SIMULATED_ENABLE=1`, isolated Blender
factory startup entered a simulated OpenXR session without any SteamVR
process. The session probe observed `running=1`, 352 initial XR draws, 740
draws after recalling the overlay, and clean desktop recovery. An earlier
full-feature Blender executable at
`native/build-features/bin/blender.exe` (SHA-256
`DD2BA446A179F3EECDA44953A83160C3153862A1177A486CB3DF1981E3120E99`)
completed `native/test_task_card.py` in that simulated session. The bounded
run is in `.tmp_xr_probe_938ac95317494afca1e8b9963ea3820c`, with
`XR_TASK_CARD_PASS simulator=1 composed=1 opacity=1 generation=1 capture=1 cancel=1 arrange=1 tools=4 add_cube=1 history_undo=2 history_redo=1 history_guard=1 disabled_more=1 native_exit=1 scene_unchanged=1 optical_verified=0`
and `Blender quit`. The run captured
`output/native-task-eye.png`, which shows the Session Undo/Redo buttons above
the selected cube and grid. Four Arrange tools, Add Cube and disabled More
were separately exercised during the same run. These are
simulated stereo-eye images, not physical Index views. The software
runner reports no process exit code because the existing Blender probe
launcher owns the child process; it requires the exact pass marker, no fail
marker, a normal-quit marker, and no timeout instead. Blender emitted
nonfatal asset-cache write warnings to its default Windows cache path,
which is outside this sandbox. No live user Blender profile was modified.

Repeat the process-isolated simulator check with:

```powershell
& .\native\steamvr_free\run_monado_inproc_probe.ps1 -Mode task `
    -BlenderExe .\native\build-features\bin\blender.exe `
    -ExpectedSha256 F1DF6224688146078D185E5DA0AC323B1232E4801B97B028CF0A887EDB215750
```

The script refuses to run while SteamVR or another Monado service is active,
hash-checks Blender, runs within the isolated factory profile, caps the GUI
test at 75 seconds, disables camera passthrough, and verifies that the
machine-wide OpenXR runtime did not change. It deliberately does not qualify
the Index display, real hand input, or physical pose tracking.

A separate simulator-only OpenXR client at
`native/steamvr_free/projection_smoke` compiled and submitted three synthetic
stereo projection layers: red left-eye pixels and blue right-eye pixels, each
with a green corner marker. Its isolated launcher reported
`MONADO_SIM_PROJECTION_PASS frames=3 views=2 ... layers_submitted=3` and
`MONADO_SIM_PROJECTION_LAUNCH_PASS registration_unchanged=1 environment_restored=1`:

```powershell
& .\native\steamvr_free\projection_smoke\run_simulator.ps1
```

This checks OpenXR swapchain creation, per-eye texture upload and projection
submission to Monado's desktop simulator. The marker does not prove compositor
pixel readback, sustained frame pacing, physical Index display, camera
passthrough, or eye/camera calibration. The test opens no camera or headset.

An optional offline bridge reuses the local palm/landmark preview to draw
thin blue estimated contours on the two camera views in the user's existing
screenshot. A bounded in-memory pipe passes those annotated pixels to the
same OpenXR client. The Release check reported one contour per eye, GPU texture
readback equal to the uploaded eye pixels, and three accepted stereo layers:

```powershell
& .\native\steamvr_free\projection_smoke\run_simulator.ps1 `
    -OfflineScreenshot 'path\to\screenshot.png'
```

This is a still-image software check, not live passthrough. GPU texture
readback does not capture the compositor's final pixels, and the Windows
runtime still cannot present to the physical Index. It opens no camera,
changes no system runtime registration, and saves no annotated images.

For an interactive **desktop simulator preview** (not an Index headset
session), `native/steamvr_free/launch_monado_simulator_preview.ps1` starts the
hash-checked source build through the isolated XR Workbench launcher with
process-only Monado, workspace-local Monado config, and SteamVR-specific
passthrough disabled. Open Blender's XR Workbench panel and start XR there;
the simulated compositor is designed to present in a desktop window. The frozen build can
be preflighted or opened with:

```powershell
$preview = '.\native\steamvr_free\launch_monado_simulator_preview.ps1'
$blender = '.\native\build-features\bin\blender.exe'
$hash = 'F1DF6224688146078D185E5DA0AC323B1232E4801B97B028CF0A887EDB215750'
& $preview -PreflightOnly -BlenderExe $blender -ExpectedSha256 $hash
& $preview -BlenderExe $blender -ExpectedSha256 $hash
```

The current hash-checked preview preflight passed with `XRWB_PREFLIGHT_PASS`,
zero SteamVR processes, and unchanged global OpenXR registration. An earlier
bounded interactive launch of the prior source binary opened a visible
`(Unsaved) - Blender 5.1.2` GUI; only that test's newly started Blender and
helper PIDs were stopped. That earlier binary passed the stricter simulated
XR GUI test above, but its interactive preview launch was only preflighted.
The current full-feature source build, hash-pinned in the commands above,
passed its own simulated Cancel Edit GUI probe; see
`docs/full-feature-build-status.md`.
This verifies software wiring, **not** Index presentation or hands. The pinned
hash must be updated after every source rebuild; the launcher fails closed on
mismatch.

Read-only Windows desktop enumeration showed one active screen, the LG
`27GN7`, and no Index extended-desktop screen. This alone cannot diagnose the
DisplayPort connection because NVIDIA direct mode hides HMDs from ordinary
monitor listings. A September 23 GPU-level NVAPI probe established that the
Index **is physically connected** on the RTX 3080 Ti: display ID
`0x80061080`, hardware-read EDID `VLV 0x91a8 / Index HMD`, connected and
physical flags set, but active flag clear. Its 256-byte EDID has a DisplayID
1.x extension, not a Microsoft HMD/specialized-display extension. The probe
is `native/steamvr_free/index_nvapi_probe.c`; it only enumerates and reads
EDID, and saves no raw EDID or serial.

The same GPU exposed zero displays through `VK_KHR_display`. Windows
`DisplayManager.GetCurrentTargets` returned only the LG as connected, and
`vkGetWinrtDisplayNV` returned null handles for all enumerated Windows
targets. Consequently, the current public Vulkan/WinRT path cannot acquire
the hidden Index for the Monado compositor. SteamVR's September 23 log shows
its proprietary NVAPI direct-mode path selected 2880x1600 at 120 Hz and
submitted 606 frames before shutdown; this demonstrates an available driver
route, **not** that the tester saw those frames or that this fork can use it.
Application presentation in the Index and live passthrough remain unverified.

The user approved a temporary extended-desktop test. SteamVR 2.17.10's
Developer Settings `Disable Direct Mode` action was applied; its saved
`direct_mode.enable` is false. The original settings file was backed up before
the toggle. With SteamVR fully closed, NVAPI and Windows enumerate the Index
`VLV91A8` as a second attached display while the LG remains attached. The
user sees Wallpaper Engine imagery through the Index lenses. The display
probe and launcher preflight passed without opening the camera. Test windows
launched from the automation session returned PASS but left wallpaper visible
in the headset. When the user double-clicked `Open Index Headset Pattern.cmd`
from File Explorer, they reported seeing the blue X and box in the lenses.
This physically verifies **interactive Windows window presentation** on the
Index extended display, not OpenXR presentation or calibrated passthrough.

The guarded camera evaluator accepts exactly one active, non-cloned
`VLV91A8` target and checks fullscreen placement before camera startup and
around each live frame. After the visible pattern, the user launched
`Open Index Headset Hands.cmd` from File Explorer. They reported that the hand
preview seemed to work briefly, but could not tell whether the view moved live
or was a frozen image. The process reported zero complete frames, 102 empty
pipe polls, and `stream_failed=1`, then Windows recorded an access violation
in `mercury_direct_camera_eval.exe`. This is a **failed** physical camera
preview, not verified passthrough or hand tracking. The shared Mercury
predictor had a missing timestamp at the first tracked-hand transition;
that access-violation path now has a history/cadence guard and an offline
regression test. The evaluator also drains bounded FFmpeg stderr and reports
early exit rather than crashing silently. The current preview build avoids
clearing the full canvas per frame, prefetches one frame during inference,
shows a larger final five-second countdown in both eyes, records whether the
timer or user closed the window, and wipes the scaled frame at teardown.
The newest build also infers each eye on a separate model session in parallel;
its executable SHA-256 is
`725915AB96DF0B92A12980EC704D74CB2ABF4A6E61B1E470BBDBA19CF1727C0E`.
`scripts/open_index_hand_preview.ps1` saves text-only status/error output from
each run under `reports/index-headset-preview`; the launcher refuses SteamVR
and writes no camera pictures or hand-joint traces. The original zero-frame
FFmpeg exit has not recurred, but its cause remains unknown.

The first logged user-launched hands-in-view test processed 186 stereo pairs,
reported 680 estimated contour observations, and exited normally at the
15-second timer. After the prefetch/countdown build, an automation-launched
no-hands run processed 296 frames in 15 seconds without stream errors. The
latest user-launched hands-in-view run
(`reports/index-headset-preview/headset-20260923-155043-cab14ecb.txt`)
processed 230 frames in 15 seconds (~15.3 fps), with 723 estimated contour
observations and 172 Mercury regions. It recorded `stream_failed=0`,
`XR_INDEX_PREVIEW_NORMAL_END reason=15s_safety_limit`, and exit code 0; a
20-minute Windows Application event check found no matching crash. The user
confirmed both blue outlines and the countdown were visible in the Index.
That physical run used the preceding C2E7 build, not the newest parallel-eye
build. An offline 30-frame screenshot comparison of the newer build found
identical contour geometry (60/60) and reduced the parallelizable inference
stage from 527 to 294 ms total. Live frame rate and added model-session memory
remain unmeasured. The physical run verifies a timed stereo-camera/hand-outline
diagnostic, not smooth hand tracking: neither latency nor alignment was
measured, and the earlier hands-in-view run was ~12.4 fps under different
conditions. No world-space hands or Blender interaction was validated by this
preview.
The raw camera layout is **not** calibrated to the lenses; this preview is a
stationary diagnostic, not a safe moving-around passthrough mode. Extended
mode is not a qualified low-latency VR release.

The generic Windows `vk_display` target compiles but does not auto-detect an
Index display; the default `mswin` target presents to a desktop window. Source
review found unguarded invalid display indices, empty mode/plane lists, failed
capability queries, and allocation failures in the generic direct target.
Those paths now fail closed, and `comp_main` rebuilt successfully. This is
source hardening only: no Vulkan direct target was forced or physically
presented, and the Windows driver/display acquisition gap remains.

An opt-in `XRT_COMPOSITOR_INDEX_EXTENDED=1` path now selects the
Windows `mswin` compositor target only when an active, non-primary, non-cloned
`VLV91A8` Index display is unambiguous. It verifies the target window before
Vulkan presentation and hides it on display-topology change. `comp_main`
rebuilt and the Index-selection CTest passed in an isolated offline build.
The separate Release runtime at
`native/steamvr_free/monado-index-experimental-build` includes this source
path and the opt-in Windows Vive/Index driver. The current physical frame-test
DLL SHA-256 is
`3712425D5DD087E3CEEA230E633A6EFE08A2AF866B4E483E52FDDE0E10AAD592`.
`native/steamvr_free/launch_monado_index_experimental.ps1` defaults to
preflight, and its launch gate requires explicit confirmation of the visible
X/box pattern. Earlier physical probes failed first on the MI_01 watchman
collection and then on configuration feature reads. A guarded report probe
showed intermittent Win32 error 31 on the first read of a feature report;
the shared Windows HID adapter now retries once only for that exact zero-byte
error within the original 100 ms budget, verifies the returned report ID and
length, and leaves the Index optics/identity checks fail-closed. The bounded
process-only system probe then passed on the physical Index: `xrCreateInstance`
and `xrGetSystem` returned success and identified `Valve Index (vive)` with
orientation support but no positional tracking. Its text log is
`native/steamvr_free/physical-probe-logs/system-probe-20260923-155236-32524.log`.
That system-probe child exited, left the registered OpenXR runtime unchanged,
created no session, submitted no frame, and requested no camera. A later,
separate physical OpenXR color test did create a session and submit 694 stereo
frames over 4.97 seconds. A hands-on test confirmed seeing the colors in the Index;
the corner markers were too high to inspect. The text log is
`native/steamvr_free/physical-probe-logs/frame-probe-20260923-160350-54240.log`.
This first proved a bounded stereo headset frame path. A subsequent
source-built Blender run showed the scene and native task card in the Index;
its status log is
`reports/index-physical-launch/launch-20260923-161715-55fcf102.txt`.
That is a camera-off, fixed-position 3DoF proof, not qualified optical hands.

A separate read-only `pnputil` probe did work despite the PnP/WMI permission
limit. `native/steamvr_free/index_hid_probe.ps1` found six connected, Started
HIDClass/USB nodes with `VID_28DE&PID_2300`, paired across `MI_00`, `MI_01`,
and `MI_02`, while all SteamVR processes were absent. The pinned Monado source
defines that vendor/product pair as its Valve Index HMD candidate
(`vive_prober.h`, `VALVE_VID` and `VIVE_PRO_LHR_PID`). The probe opened no HID
handle and read no IMU packets:

```powershell
& .\native\steamvr_free\index_hid_probe.ps1
```

The pinned Monado implementation shows why merely enabling its driver CMake
flag would not work on Windows. `CMakeLists.txt` gates `XRT_BUILD_DRIVER_VIVE`
on `XRT_HAVE_LINUX`; `src/xrt/state_trackers/prober/p_prober.c` explicitly
returned `HID devices not yet supported on Windows` from
`p_open_hid_interface` before the source milestone below. Its Linux path
enumerates `hidraw` through `libudev` and opens `os_hid_hidraw.c`; the new
Windows adapter below addresses only HID collection opening, not full device
discovery. The current Windows build also has no libusb and no
V4L2. The Index prober requires separate sensors and watchman HID interfaces
before `vive_device_create`; the sensor path contains IMU-based 3DoF fusion,
but none of those physical reads were performed here. The camera source's
timestamp code assumes a V4L2 frame clock, so independently acquired Windows
DirectShow frames still need an explicit clock/calibration adapter.

## Windows HID source milestone (not physical qualification)

The workspace's pinned Monado source now has a narrow Windows HID adapter in
`src/xrt/auxiliary/os/os_hid_win32.c`, linked under `WIN32` by `aux_os`. The
existing prober's Windows `open_hid_interface` branch calls it with the
prober device vendor ID, product ID, and interface number. It searches
Windows HID collection paths with [SetupAPI](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdigetclassdevsw) and can open a matching path via
`CreateFileW`; its `os_hid_device` methods use overlapped `ReadFile`/`WriteFile`
and synchronous `HidD_GetFeature`/`HidD_SetFeature` on **separate handles**.
The dual-handle correction follows [Microsoft's HID client sample](https://github.com/microsoft/Windows-driver-samples/blob/main/hid/hclient/pnp.c),
which states that `HidD_` calls must use a non-overlapped handle while
overlapped reads use another. A mock-only contract test
at `native/steamvr_free/tests/test_win32_hid_contract.c` checks interface-path
selection and report callback dispatch without opening any physical device.
The Windows `aux_os` and `st_prober` targets compiled, and the CTest contract
test passed. That step did not rebuild the in-process Monado OpenXR DLL;
its SHA-256 then was `A29EB053071D07487EAF5F1F45CC90714CE22D9B9A5BC21AD71EB94B27A40B9C`.
The current in-process DLL was rebuilt subsequently and hashes to
`989848AFDD486342FDF9A17AA698C804D2C4DAF2C1E756E60A72EC029495B99C`.

The Windows collection matcher was subsequently tightened to require exact
VID/PID/interface token boundaries in a canonical HID path. After opening, it
checks `HidD_GetAttributes` before returning the handle. The offline contract
CTest passed and `aux_os` rebuilt; no Index handle was opened. This prevents a
similar-looking path or stale enumeration record from being treated as the
headset, but a noncanonical physical collection path would now be rejected.

This is a physical **system-and-bounded-frame** implementation, not a qualified
Index application runtime. The Windows prober requires matching `MI_00` sensors
and `MI_01` watchman paths in one unambiguous device container. The opt-in
Windows port adds portable report layouts and an overlapped, bounded
`IOCTL_HID_GET_FEATURE` callback. The physical Index's MI_00 feature
collection advertises IDs `0x01`, `0x10`, and `0x11`; guarded probes showed
intermittent Win32 error 31 on an initial read. A single zero-byte error-31
retry in the shared adapter let the full system probe read and parse the
device configuration, identify the Index, and pass `xrGetSystem`. Its optics
are checked for required fields before acceptance. Bounded color-frame
presentation was physically confirmed, followed by a user-confirmed Blender
scene and task card. Lens accuracy, continuous IMU data, and long-run
compositor stability remain unverified.
Offline mocks cover first-fail/second-success, double-fail,
wrong-ID, short, timeout, and device-error behavior; `get_physical_address`
still fails closed. Microsoft's [feature-report contract](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidclass/ni-hidclass-ioctl_hid_get_feature)
defines the unchanged report-ID byte and 64-byte payload transfer for a
65-byte buffer. [CancelIoEx only requests cancellation](https://learn.microsoft.com/en-us/windows/win32/fileio/cancelioex-func),
so cancellation drains before buffers are released; a faulty driver can
still exceed the nominal deadline. The overlapped input-report path needs a
separate live streaming and teardown test.

The smallest credible physical Windows path is much larger than this preview:

1. Port Monado's Index USB/HID driver to Windows and validate headset identity,
   optics calibration, and live IMU packets. The pinned source's `vive_device.c`
   has Index IMU fusion with a 3DoF fallback. The partial `os_hid_device`
   adapter, container-ID discovery, portable endian/packed-report definitions,
   and opt-in `drv_vive` compilation pass offline checks. Bounded feature
   reads and Index identity/configuration passed one physical system probe;
   live IMU pose quality remains a separate milestone.
   A successful IMU port could provide orientation, not physical
   translation or room-scale 6DoF. Changing only the CMake gate does not
   produce a working Windows driver.

2. Implement and validate Windows Index display acquisition and compositor
   presentation. The pinned Windows compositor target is `comp_window_mswin.c`
   and creates a Win32 `HWND`/Vulkan window; it has not been shown to acquire
   the Index's direct-mode DisplayPort endpoint. Vulkan and WinRT expose no
   usable direct-mode Index display handle. In the approved extended-desktop
   test, the Index is an attached second monitor and the tester saw our stereo
   X/box pattern when launched interactively. The opt-in OpenXR compositor
   subsequently presented a five-second stereo color pattern and, in a later
   run, the source-built Blender scene and task card.
3. Bring the independently captured stereo cameras through calibrated
   undistortion and a working optical hand tracker. The raw direct camera test
   and the timed headset preview visibly outlined hands. That raw diagnostic
   is not lens-calibrated and does not feed Monado or Blender. No camera-only
   positional headset tracking implementation is present.
4. For room-scale 6DoF, add powered Lighthouse base stations and an independent
   tracked-pose implementation such as libsurvive, or develop and qualify a
   new camera visual-inertial tracking subsystem. The user has no base station,
   so Lighthouse 6DoF is impossible in the present physical setup regardless
   of runtime choice. Camera-only 6DoF is a separate research/engineering
   effort, not a launch flag.

The Windows HID adapter reads `HIDP_CAPS.FeatureReportByteLength`, pads shorter
feature writes to that length, and rejects oversize reports. The Vive driver
remains opt-in. Its bounded session/frame path is physically proven, while
Blender integration, sustained tracking, hands, and safe display calibration
remain unqualified.

For a SteamVR-free Windows release, implement and qualify all of these:

1. Index USB/HID and display control, headset distortion, and Windows direct
   or extended-mode presentation in an OpenXR compositor.
2. Independent head pose from libsurvive or another qualified 6DoF source;
   Lighthouse tracking requires a working base station. Validate tracking
   origin, loss, recentering, and eye-pose timing.
3. Independent stereo camera acquisition, calibrated passthrough composition,
   and Mercury optical hands, with no SteamVR tracked-camera dependency.
4. `XR_RUNTIME_JSON` manifest, loader negotiation, Blender stereo submission,
   runtime exit/recovery, latency, and restart soak on the physical Index.

Only after those checks should `alternate_runtime_qualified` become true.
The current Windows fork has user-confirmed SteamVR-free OpenXR color frames
and a source-built Blender scene/card on the Index. A separate Windows
DirectShow path can read the Index camera without SteamVR and
feed an isolated Mercury diagnostic, but it is not connected to calibrated
passthrough or the live Blender hand provider. Neither a selector nor the Linux
recipe proves the full runtime is ready.

## Windows camera access without SteamVR

With `vrserver`, `vrcompositor`, `vrmonitor`, `vrwebhelper`, `steamvr`, and
`vrstartup` all absent, Windows DirectShow enumerated `eTronVideo` at physical
USB `VID_28DE&PID_2400`. The bounded probe at
`native/steamvr_free/index_camera_probe/index_camera_probe.ps1` then used the
already-installed FFmpeg DirectShow input and null output:

```powershell
ffmpeg -nostdin -hide_banner -loglevel info -debug_ts -f dshow `
    -i 'video=eTronVideo' -frames:v 5 -f null NUL
```

The probe completed with five 1920x960 YUY2 frames and five distinct,
increasing DirectShow packet timestamps. This proves frame advancement in the
short sample. Sensor exposure age and sustained rate remain unknown. No
camera pixels were saved; the output was discarded by the null muxer and the
probe removes temporary text logs. The script imposes an eight-second timeout
and repeats the SteamVR-process check before opening the camera. Windows
[Media Foundation enumeration](https://learn.microsoft.com/en-us/windows/win32/medfound/enumerating-video-capture-devices)
and the [USB Video Class driver](https://learn.microsoft.com/en-us/windows-hardware/drivers/stream/usb-video-class-driver-overview)
are possible native follow-on APIs. DirectShow device visibility alone did
not prove capture; the five-frame test supplies that separate evidence.

This is a camera acquisition milestone only. Display presentation, correct
eye/camera calibration, optical hand inference, and physical 6DoF tracking
are still unqualified. The absent Lighthouse base station remains a physical
tracking blocker regardless of runtime choice.

For a local visual check, `.\Open Index Cameras Live.cmd` starts
the already-installed FFplay against `eTronVideo` and shows the two raw camera
halves side by side. It has no file output and starts neither SteamVR nor a
network server. A hands-on test confirmed that double-clicking the launcher displayed
the live preview on the desktop; starting FFplay programmatically from an
automated task did not surface a visible window. Close the preview before running
another camera diagnostic, because it owns the capture device while open.

`.\Open Index Hand Preview.cmd` is a separate one-click stereo
preview that feeds the raw Index camera to local palm/landmark models and
Mercury. When current-frame palm and landmark checks agree, a thin blue
convex-hull contour is estimated from the 21 landmarks. A Mercury region
border is a fallback only when its own keypoint checks pass. The neutral
label says when no hand is validated. Neither visual is a pixel-accurate
hand silhouette, calibrated headset passthrough, or a Blender hand provider.
The launcher verifies the
Index USB camera and SteamVR-off state before capture; Esc, Q, or closing its
window stops the stream. Its PowerShell 5.1 preflight passed without camera
access. The Release preview's read-only known-screenshot smoke passed with one
conservatively gated estimated contour per camera half, and its synthetic
positive/negative overlay and model tests passed. The user deferred a new
live preview check, so physical overlay visibility and frame rate remain
unverified. The checked Release preview executable has SHA-256
`A69AC2E742D61102D03EB0C3C7FFC4C5D1D54A32BEE575590C2D482DB5B04CC6`.
No recording is built into this path.

A second consented test ran the direct camera through the isolated Mercury
diagnostic at `scripts/run_mercury_direct_camera_diagnostic.ps1`, with SteamVR
stopped. In its 15-second limit it processed 14 non-flat, distinct stereo
pairs, but **zero detector scores exceeded 0.3**, and it produced zero hand
regions, keypoints, or active hands. FFmpeg reported a full real-time buffer
and one dropped frame. The diagnostic uses a synthetic, unverified pinhole
fixture because raw DirectShow YUY2 has not been calibrated or matched to
OpenVR's undistorted camera layout. It deliberately produces no world-space
output and saves no camera frames or joint traces. Its exit code was nonzero
because it processed fewer than its 30-frame target. This is a failed hand
inference test, not a validated hands-only interaction path.

The diagnostic launcher selects a separate Release build of the same
privacy-guarded evaluator. The first Release live test processed 14 distinct
stereo pairs in 15 seconds, with 14.96 seconds waiting for frames and 42.03 ms
total Mercury processing (3.00 ms per pair). It found no detector scores above
0.3. Source review found that this evaluator vertically flipped each eye even
though Mercury's active Index path crops the grayscale eyes without a flip
(`CAMERA_ORIENTATION_0`). The corrected evaluator, SHA-256
`3E472794EF9B45F5B82746A604A70D266AEA9F0FBCF8E61443BA1E15E833DFB4`,
passed its offline eye/row mapping self-test and a 30-frame synthetic
benchmark. A second consented live run with that corrected Release binary
again processed 14 distinct pairs in 15 seconds, spent 14.96 seconds waiting
for frames and 36.01 ms in Mercury (2.57 ms per pair), and found zero detector
scores above 0.3. Both live runs exited nonzero because they obtained fewer
than the 30-frame diagnostic target; neither detected hands. FFmpeg warned of
a full input buffer and a dropped frame in both. No frames or joint traces
were saved. Correcting the orientation mismatch did not by itself qualify
the hand provider; image visibility/lighting, DirectShow conversion, and
Mercury's detector input remain unresolved.

A third consented 15-second Release diagnostic added privacy-preserving image
statistics and timing counters (binary SHA-256
`1A426AE4E30AAB3749E449F5A9BE6251BEE9E0C4B30E92DB6269030A1A947D0D`).
It processed 13 changing, non-flat stereo pairs, with whole-eye grayscale means
34.50/36.35 and center 480x480 means 61.56/67.93. The first read took 2.91 s,
later reads averaged 0.94 s, and Mercury processing averaged 2.68 ms per pair.
Again there were zero detector scores above 0.3 and zero active hands. It exited
nonzero against the diagnostic's 30-frame target, not because of a crash. No
camera images or joint traces were saved. The user-supplied desktop screenshot
showed visible hands in both raw camera halves, but their apparent size/cropping
and room lighting are only possible contributors, not established causes of the
failed detector. The approximately 1 FPS acquisition and zero scores remain
unresolved.

## Probe and eventual launch

```powershell
& .\native\steamvr_free\runtime_selector.ps1
& .\native\steamvr_free\test_runtime_selector.ps1
& .\native\steamvr_free\runtime_selector.ps1 `
    -Manifest .\native\steamvr_free\monado-build\openxr_monado-dev.json

# Once a real Windows Index-capable OpenXR runtime exists:
& .\native\steamvr_free\runtime_selector.ps1 `
    -Manifest 'D:\path\to\alternate_runtime.json' -Launch
```

The selector returns JSON containing manifest identity, library existence,
and separate unverified hardware fields. A successful launch only proves the
isolated, hash-checked Blender launcher started with a selected manifest. It
does not change any of the hardware qualification fields. Khronos documents the process override in the
[OpenXR loader runtime specification](https://github.com/KhronosGroup/OpenXR-SDK-Source/blob/main/specification/loader/runtime.adoc).
