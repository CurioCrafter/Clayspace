# Mercury Source-Only Feasibility Audit

## Answer

**Yes, the Mercury hand-tracking core can be separated from its unsafe SteamVR
driver wrapper.** It accepts stereo frames and calibration, then produces two
full Monado joint sets. The isolated private-evaluation target now compiles and
runs on Windows using only synthetic blank frames. It is therefore a feasible
source dependency for a new, consented, in-process Blender native provider
that publishes its own JSON joint metadata. It is **not** a drop-in optical
provider: the current wrapper opens the Index camera through Media
Foundation/OpenCV, starts an OpenVR background process, and can invoke
`lighthouse_console`. None of that wrapper is built, registered, or reused by
the offline target.

No SteamVR driver was built, launched, registered, or configured; no camera,
OpenVR session, USB driver, or physical tracking test was opened or used.

## Locked source evidence

| Source | Commit / status | Licence evidence |
| --- | --- | --- |
| [`moshimeow/mercury_steamvr_driver`](https://github.com/moshimeow/mercury_steamvr_driver) | `e3948ace94a9f2cbd949adf50ffcc082002337cc` | `LICENSES/BSL-1.0.txt` is the Boost Software License 1.0. |
| Its Monado submodule | `78a4069fbbde7b084886abea2ddf7bc61df7a99f` | Mercury headers/files carry `SPDX-License-Identifier: BSL-1.0`. |
| Its `escapi` submodule | `058bba3bc9655a542652f59ca4f6c08bf71e77d1` | Separate third-party review required before delivery. |
| [`korejan/mercury_steamvr_driver`](https://github.com/korejan/mercury_steamvr_driver) | `cb96cef49d31ee68ba5add3adfb0c1d488a79391`; submodule gitlink `e91102d27822fc68195b61eed0dcbbb52cadfd26` | Same `LICENSES/BSL-1.0.txt`; fork README labels it “very in-development.” |
| [Monado hand-tracking models](https://gitlab.freedesktop.org/monado/utilities/hand-tracking-models) | `37a8a81bc8f433ac6cbdf2471909d2bac74beca1` | Official Monado script source, but this repository contains no `LICENSE`, `NOTICE`, or redistribution grant. Private evaluation only. |

The linked Korejan fork has a Windows `v1.1.0` binary-release page, but that
release instructs installation into SteamVR or registration with `vrpathreg`.
That is explicitly outside this task and requires separate approval because it
changes the SteamVR driver configuration. It was not downloaded or used.

The source-bundled ONNX models are present, but no separate model licence was
found alongside them. Their locked hashes are:

| Model | SHA-256 |
| --- | --- |
| `grayscale_detection_160x160.onnx` | `1F1A039A266E13DC186BB884430EBD9C8216BDDA680AB08A533D4C671F27ED36` |
| `grayscale_keypoint_jan18.onnx` | `40C0DAA598CEDB993B54FFF17685231B7465D6DB342656C401B01C2029EFD1D5` |

## The reusable boundary

1. `monado/src/xrt/tracking/hand/mercury/hg_interface.h:25` exposes
   `t_hand_tracking_sync_mercury_create(calib, extra_camera_info, models_folder)`.
2. `monado/src/xrt/include/tracking/t_hand_tracking.h:110-160` defines the
   synchronous tracker and `t_ht_sync_process(...)`; it consumes one left and
   one right `xrt_frame` and returns `xrt_hand_joint_set` for each hand.
3. `src/steamvr_driver/tracking_subprocess.cpp:114-193` shows the required
   calibration and tracker creation. `:615-631` converts the stereo image to
   grayscale, wraps each half as an `xrt_frame`, and calls `t_ht_sync_process`.
4. `:225-392` converts valid joints to a compact, timestamped
   `tracking_message`; `tracking_subprocess_protocol.hpp:32-54` gives that
   message shape. A new provider can instead serialize only valid joint pose,
   relation flags, timestamp, source=`optical_index_camera`, and generation.

This is a genuine offline C++ path: the tracker itself does not call an OpenXR
runtime. The top-level Mercury CMake explicitly sets `XRT_FEATURE_OPENXR OFF`.

## Camera and calibration route without a SteamVR driver

The installed Rectus source demonstrates the required public OpenVR data:

- `camera_manager_openvr.cpp::InitCamera` uses `IVRTrackedCamera::HasCamera`,
  `AcquireVideoStreamingService`, and the paired `ReleaseVideoStreamingService`.
- `UpdateStaticCameraParameters` reads frame layout/size, intrinsics,
  distortion properties, and `Prop_CameraToHeadTransforms_Matrix34_Array`.
- `GetTrackedCameraEyePoses` derives camera-to-HMD transforms; frame retrieval
  uses the tracked-camera interface rather than opening an Index USB device.

Those values can construct Mercury's `t_stereo_camera_calibration` in a small
adapter, but no existing public function performs that exact mapping. The
current Mercury wrapper instead parses the Index's downloaded HMD config with
`vive_get_stereo_camera_calibration`. Its camera path is not acceptable: it
uses `GetIndexIndex`, `cv::VideoCapture(...CAP_MSMF...)`, and can execute
`lighthouse_console.exe downloadconfig` in `path_utils.cpp`.

Rectus also documents why its OpenVR bootstrap is special: it obtains
`IVRClientCore` from the `vrclient_x64` module because calling `VR_Init` in an
OpenXR application is unsafe. A Blender-native provider must use a compatible
in-process OpenVR access strategy; it cannot directly “share” Rectus's private
frame queue or silently access its layer state.

## Minimal safe build assessment

**Offline core build passed, optical-provider build remains blocked.**
`native/providers/mercury_offline` is an isolated CMake executable that links
only Mercury's tracker, frame/calibration utilities, ONNX Runtime 1.13.1,
OpenCV 4.10.0, Eigen 3.4.0, cJSON, and PThreads4W. All dependencies were put
under that workspace directory. Its CMake boundary forces OpenXR, IPC,
service, compositor, SteamVR plugin, and every hardware-driver option off.
It also removes unused Monado PS Move / PSVR tracker sources from the shared
utility target; no source in the offline executable invokes camera capture.

Using MSVC 19.44 and Ninja with `--parallel 2`, the target built successfully.
The executable created synthetic 960x960 black stereo frames, constructed
calibration, created Mercury, called `t_ht_sync_process`, and cleanly released
all tracker/frame/calibration resources. It emitted:

```
MERCURY_OFFLINE_SELFTEST_PASS blank_frames=2 hands=0
```

The only runtime warnings were Mercury's expected zero-standard-deviation
warnings for deliberately blank images. This proves the offline tracker API
and Windows dependency set, not a camera path, useful hand joints, timing,
or headset integration.

### Public OpenVR metadata bridge (source/self-test only)

`native/providers/mercury_offline/openvr_mercury_bridge` now provides a pure,
camera-free boundary for metadata already collected by a separate consented
public OpenVR client. It accepts only `Undistorted` packed RGBA frames with
the stereo-horizontal layout bit, two matching pixel intrinsics, and the two
`CameraToHead` matrices. It crops each eye, converts pixels to grayscale in
RAM, resizes to 960 square pixels, and applies the identical crop/scale to the
intrinsics. It emits no images, device IDs, or joint data.

For transforms named `CameraToHead`, the bridge derives the OpenVR-frame
relative pose as `inverse(head_from_right) * head_from_left`. The basis is now
evidence-backed rather than guessed: Valve's public `public_vrtypes.h` defines
OpenVR as right-handed `+X right, +Y up, -Z forward`; Monado's
`t_camera_models_unproject_and_flip` explicitly converts OpenCV camera rays
`+X right, +Y down, +Z forward` to that same XRT/OpenVR basis; and Rectus
v0.4.2 `shared/mathutil.h::ChangeBasisToFromOpenCV` applies the corresponding
180-degree X rotation. The bridge supplies `diag(1,-1,-1)` only through its
named OpenVR/OpenCV factory. An arbitrary or absent basis still fails closed
with `unverified_optical_basis`; the identity mapping exists only for
synthetic math tests.

Those tests passed with a known 64 mm baseline, rejected swapped eyes, zero
baseline, and a crop crossing the stereo seam, and printed
`MERCURY_OPENVR_CALIBRATION_MATH_PASS camera_accessed=0 joints_emitted=0`.
This is input-math evidence only: camera calibration accuracy, live inference,
world pose, hand joints, timing, and optical qualification remain unverified.

### Bounded private local camera evaluation

`mercury_openvr_camera_eval` is built but has not been run against hardware by
this agent. A designated local tester first ran the five-frame explicit-consent
path and obtained `fresh_frames=5`, `active_hand_observations=0`,
`mean_process_ms=4.377`, `head_pose_valid_frames=0`, and `world_output=0`; the
OpenVR temporary settings restored to `pause=1` and `timeout=5`.

The designated tester then ran the 30-frame diagnostic successfully: 30 fresh
frame pairs, model inputs, sync-process calls, and matching timestamps; zero
active hands, camera-header pose validity, and current HMD-pose validity; 29
exposure changes; 30 non-flat and 30 distinct stereo pairs; mean processing
time 5.481 ms; host fresh-frame interval mean/min/max
27.033/21.385/41.506 ms; `world_output=0`. The temporary settings again
restored to `pause=1` and `timeout=5`. No image or skeletal trace was saved.
This proves only that fresh changing camera buffers reached Mercury's public
sync path; it is not a successful hand-tracking or room-alignment result.

The rebuilt evaluator now has an explicit `--consented-camera-diagnostic` mode:
at most thirty fresh frame pairs or fifteen seconds. Its aggregate-only result
reports model-session initialization, completed sync-process calls, timestamp
matches, input non-flatness, distinct stereo-pair count, exposure-change count,
and host-monotonic inter-frame intervals. It reports the camera-header pose
validity separately from the current public
`GetDeviceToAbsoluteTrackingPose` validity, so a stale frame header cannot be
mistaken for current SteamVR tracking. It never prints either pose, camera
pixels, or joints. `world_output=0` remains hard-coded because neither pose
field establishes safe room alignment.

The evaluator itself never writes SteamVR power settings and reports
`power_override=0`. A hardware tester that has separately approved a bounded
keep-awake interval must use the existing independent helper and wait for its
own natural restoration. This separation prevents an evaluator abort from
silently retaining a temporary power override.

Mercury's source initializes both detection models and four keypoint models in
`hg_sync.cpp:1093-1100`; when both hands were not tracked on the previous frame,
its processing callback dispatches detection at `:779-783`. The evaluator
creates that tracker and calls the public sync processor for every counted pair,
but does not reach into Mercury private state to invent a detector counter.
The upstream debug sinks are active only when both sinks are attached
(`:726-727`); none are attached here, so its image/debug path remains off.

The first hands-present run exposed a real Mercury source bug that blank-frame
tests did not reach: `hg_model.cpp` passed a pointer to an empty debug `cv::Mat`
to `stereographic_project_image` even when debug sinks were disabled. The
helper then unconditionally called `cv::polylines`, producing OpenCV's
`Mat::elemSize` assertion. The local research source now passes `nullptr`
unless `debug_scribble` is true. The synthetic offline self-test explicitly
forces this keypoint branch and passes with `keypoint_no_debug_guard=1`; this
does not qualify live hands.

The OpenVR C ABI audit found no evidence of a packing or signature mismatch:
the official pinned C header uses Win64 default packing and `__stdcall` table
members, and its `FnTable:IVRSystem_026` request matches the header version.
The evaluator self-test has compile-time checks for the 80-byte
`TrackedDevicePose_t`, 112-byte camera-frame header, their field offsets, and
the exact pose-function table signature. SteamVR's DLL file version `1.1.1`
is not the function-table ABI version; a successful `FnTable:IVRSystem_026`
lookup is the relevant compatibility signal. The newly logged current tracking
result, connection, and activity aggregates will distinguish runtime
out-of-range/calibration state from a future ABI concern without exposing poses.

## Recommended bounded implementation ticket

After model-license approval, evolve `native/providers/mercury_offline` behind
a strictly opt-in API:

`start(calibration, camera_source)` -> `process(left_gray, right_gray, timestamp)` ->
`{status, source, joints, generation}` -> `stop()`.

`start` must be called only after camera consent. `stop` must destroy the
Mercury sync object, release the OpenVR tracked-camera stream, clear all joint
state, and never synthesize hands on loss. Do not expose controller joints,
register an OpenVR driver, or claim `XR_EXT_hand_tracking` support. The first
test should use recorded synthetic grayscale frames and explicit calibration;
the later physical test needs separate consent and validates only the newly
implemented camera adapter.

## Remaining blockers

1. Model provenance is official Monado/Collabora, but its redistribution
   licence is unresolved. The model must not be packaged or redistributed.
2. The exact OpenVR-to-Monado distortion/extrinsics mapping needs unit tests;
   wrong handedness or units would make otherwise valid hand joints unsafe.
3. The OpenXR application's safe access to `IVRTrackedCamera` needs a native
   integration design based on Rectus, not a second `VR_Init` call.
4. Performance, tracking quality, and camera coexistence are unverified until
   a separately approved physical-camera test.
