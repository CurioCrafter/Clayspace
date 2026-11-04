# Mercury offline private evaluation

This target evaluates the Monado Mercury tracker with synthetic grayscale
frames only. It intentionally does not compile or link the Mercury SteamVR
driver wrapper, OpenVR, camera enumeration, `VR_Init`, USB access, or driver
registration.

The checked-out models originate from Monado's public model repository, but
that repository has no model licence or redistribution notice. This directory
is private evaluation material only and must not be packaged, redistributed,
or enabled for users until model licensing is resolved.

Build from a Visual Studio developer shell with at most two jobs:

```powershell
cmake -S native/providers/mercury_offline -B native/providers/mercury_offline/build -G Ninja -DCMAKE_BUILD_TYPE=Debug -DCMAKE_TOOLCHAIN_FILE=native/providers/mercury_offline/third_party/vcpkg/scripts/buildsystems/vcpkg.cmake
cmake --build native/providers/mercury_offline/build --target mercury_offline_selftest --parallel 2
native/providers/mercury_offline/build/mercury_offline_selftest.exe
```

The target copies its required runtime DLLs beside the executable. Do not
connect it to a camera source, a Blender build, or a SteamVR/OpenVR process in
this private-evaluation state.

## OpenVR metadata bridge

`openvr_mercury_bridge` is deliberately camera-free source code that accepts
only metadata already obtained by another, consented public OpenVR client. It
requires `Undistorted` frames, a packed stereo-horizontal layout, two pixel
intrinsics, and the two public `CameraToHead` transforms. It crops each eye,
converts an in-memory RGBA frame to grayscale, and resizes to 960 square
pixels; the calibration applies the same crop/scale to focal lengths and
principal points.

Valve's public OpenVR SDK defines +X right, +Y up, -Z forward; Monado's camera
model uses OpenCV optical rays (+X right, +Y down, +Z forward). The bridge's
evidence-backed basis is therefore `diag(1,-1,-1)`, the same 180-degree
X-axis conversion used by Rectus's OpenVR/OpenCV transform path. An arbitrary
or absent basis still fails closed, and the identity-basis helper is test-only.
This establishes camera-relative input math, not an Index-camera, hand-track,
world-pose, or optical-quality result.

## Bounded local camera evaluator

`mercury_openvr_camera_eval.exe` has no camera action by default. It accepts
`--consented-camera-test` for five fresh frames and
`--consented-camera-diagnostic` for thirty fresh frames; both have a 15-second
deadline. The explicit paths are private, local Index evaluations with no
SteamVR power override (`power_override=0`); they clear the raw pixel buffer
when closing the stream and write neither images nor joint traces. When a
temporary keep-awake period is specifically approved for a physical run, use
the separately-owned bounded helper first and allow it to restore naturally.
Do not let this evaluator own a persistent setting because a native abort can
bypass its destructor.

The aggregate-only summary records whether the tracker initialized its model
sessions, completed `t_ht_sync_process` calls, returned matching timestamps,
received non-flat and distinct stereo pairs, and observed changing exposure
metadata. It also reports monotonic host intervals between sequence-changing
frames. These are input/inference-path diagnostics, not visual proof of eye
order or calibration. `camera_header_pose_valid_frames` is intentionally
separate from the current public `GetDeviceToAbsoluteTrackingPose` aggregates:
connection, pose validity, tracking-result class, and activity class. Neither
value reveals a pose or device identifier.

The same summary now gives cumulative detector view runs, finite-score failures,
raw hand-score counts above 0.3 and 0.92, passes through Mercury's combined
gate, accepted regions of interest, keypoint view runs, valid normalized
keypoint inputs, keypoint model score passes (0.97), active keypoint views,
and optimizer reprojection/distance rejects. It never reports a score, region,
pixel, or joint. The source currently stores detector confidence in a `bool`
before the combined 0.92 check, so `detector_combined_gate_pass` reflects the
actual binary gate; compare it with `detector_scores_over_092` rather than
assuming the latter threshold governs acceptance. These counters are diagnostic
only and do not change model thresholds or tracking behavior.

Mercury only makes debug imagery when both of its debug sinks are active. This
evaluator never registers a sink or enables its debug setting, so model-input
and annotation frames stay disabled by default.

The offline self-test also forces Mercury's real keypoint projection branch
with a synthetic ROI while debug sinks are disabled. This covers the guard
against drawing to the default-constructed debug `cv::Mat`; it is a source
regression test, not a camera or hand-quality result.

For a further physical check, the designated tester can run one bounded
`--consented-camera-diagnostic` with a visible hand in each camera's field of
view. If `detector_scores_over_03` stays zero, inspect exposure, lighting,
camera presentation, and model input mapping; if ROIs pass but keypoint counts
or optimizer rejects dominate, investigate that branch and calibration. With
no Lighthouse base station available, this cannot validate head movement or
world registration even if camera-relative hands become active.

`world_output=0` is permanent in this diagnostic; even a successful local
camera inference does not produce room-aligned output. The camera evaluator is
not included in automated checks and must be run only by the designated local
hardware tester:

```powershell
native/providers/mercury_offline/build-isolated/mercury_openvr_camera_eval.exe --self-test
native/providers/mercury_offline/build-isolated/mercury_openvr_camera_eval.exe --consented-camera-test
# Longer aggregate-only diagnostic: the designated hardware tester runs this.
native/providers/mercury_offline/build-isolated/mercury_openvr_camera_eval.exe --consented-camera-diagnostic
```

## SteamVR-free direct camera diagnostic

### Single-capture stereo video plus image-local gestures (private v2)

`mercury_direct_camera_eval.exe --consented-stereo-packet-stream <Index camera name> <ffmpeg path>`
is a separate opt-in mode with a 15-second capture window and a 22-second hard
process deadline (including startup and a stuck output pipe). It uses **one** DirectShow/FFmpeg
capture and one palm/landmark inference per eye, then writes binary packets to
stdout. The existing `--consented-stream-rgba` (`XRWB` v1) and
`--consented-gesture-stream` (`XRWG` v1) formats are unchanged. Do not run those
streams concurrently with this mode: that would compete for the camera. This
mode has no OpenVR/SteamVR dependency and never writes images or joint traces
to disk. A consumer must still explicitly terminate the helper on revocation;
the helper's child job kills its FFmpeg child on exit.

Each `XRWM` v2 packet has an 80-byte little-endian header: 16 `uint32` values
`magic=0x4D575258`, `version=2`, `header_bytes=80`, `eye_width=960`,
`eye_height=960`, `sequence`, `flags=7` (image-local, top-down rows, blue
contours), `rgba_bytes_per_eye=3686400`, `gesture_count<=4`, left/right
contour counts, three *per-frame aggregate* landmark-presence bands
(`<0.80 or nonfinite`, `[0.80,0.95)`, `>=0.95`),
`capture_age_ms_at_publish`, and `world_output=0`. Two little-endian `uint64`
values follow: `capture_monotonic_ns`, `publish_monotonic_ns`. Payload is left
RGBA, right RGBA, then `gesture_count` image-local 184-byte records, each
`uint32 view`, `uint32 candidate`, `float32 presence`, `float32 palm_score`,
and 42 `float32` eye-pixel landmark coordinates. The packet contains no 3D or
world-space hand pose. The blue contour is an *estimated convex hull*, not a
validated silhouette; both video and records remain uncalibrated to the eye.

The capture time is the host monotonic timestamp after a complete DirectShow
frame read, **not** the sensor exposure timestamp. Consumers must enforce
freshness from `capture_age_ms_at_publish` plus their own elapsed time since
packet receipt; they must not compare `steady_clock` epochs across processes
or reuse stale imagery. Binary stdout must be drained off Blender's XR draw
thread. The provider continuously drains *complete* raw frames from FFmpeg
into a latest-only slot for both `--consented-stream-rgba` and v2 packets;
slow inference drops superseded frames instead of letting the pipe backlog
grow. FFmpeg's DirectShow input buffer is capped at 16 MB. This limits, but
cannot prove the absence of, camera-driver or hardware buffering before
FFmpeg. Because v1 RGBA has no timestamp field, the producer suppresses a
packet if its completed raw-frame read is more than 500 ms old when inference
ends. This is fail-closed but may blank the v1 overlay on slow hardware;
500 ms source age plus a consumer's 500 ms receive-age guard still permits
nearly 1 second total age, so v2 timestamps are preferred for tighter
freshness. Full-resolution RGBA costs about 7.37 MB per frame before records;
the consumer needs its own latest-only slot as well.

The v1 RGBA path reuses the existing parallel left/right ONNX sessions from
the headset preview; this trades roughly one extra palm and landmark model
session, CPU contention, and memory for lower stereo inference latency. The
new v2 gesture-record path remains serial per eye until its richer result
aggregation has deterministic parallel coverage.

Offline serialization regression (does not open a camera):

```powershell
native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe --packet-self-test
native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe --latest-reader-self-test
```

`mercury_direct_camera_eval.exe` is a separate executable with no OpenVR
client or camera-probe link. It launches FFmpeg's Windows DirectShow input for
the Index camera and reads at most 30 grayscale 1920x960 frames through a
private pipe. Its internal clock stops after 15 seconds; it terminates its
FFmpeg child on exit. FFmpeg is also placed in a private Windows job with
kill-on-close before it is resumed, so abrupt evaluator termination closes
the job and stops only that child. It checks for SteamVR processes before opening the
camera and during each frame read. The launcher first identifies the camera
by `VID_28DE&PID_2400` using the existing read-only DirectShow probe. Neither
component changes SteamVR drivers, runtime settings, or device registration.

Run the synthetic model check before any hardware session:

```powershell
native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe --self-test
native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe --synthetic-benchmark
```

For a separately coordinated, consented physical test with SteamVR stopped:

```powershell
scripts/run_mercury_direct_camera_diagnostic.ps1 -ConsentedStream
```

The direct YUY2 stream has not been mapped to OpenVR's previously measured
*undistorted* image. Mercury therefore receives a labeled synthetic pinhole
fixture (960-pixel focal length 480, 60 mm baseline) only so detector and
keypoint branches can be diagnosed. Raw detector score counts can be useful;
keypoint and optimizer results depend on the unverified calibration. A 30-frame
count does not prove unique camera timestamps because rawvideo pipes omit PTS.
The executable prints aggregate counts only, holds pixels in RAM, clears its
frame buffer after each inference, and never publishes joints or world output.
It also prints cumulative `read_total_ms`, `process_total_ms`, and
`process_mean_ms`; a timed-out partial read is included in the read total.

## Local palm model candidate

`mercury_palm_model_selftest` runs the existing local MediaPipe-style palm and
landmark ONNX models on uniform synthetic tensors, checks decoder threshold,
NMS and independent eye-local geometry, then checks that a blank BGR eye yields
no palm box and no hand-presence pass. It opens no camera. `palm_detector.hpp`
accepts a `CV_8UC3` BGR eye and returns eye-local palm candidates using RGB
letterboxing, 896-anchor decoding, a conservative default 0.90 sigmoid score
gate, and IoU suppression. The local ONNX output splits into 512 16x16-head
and 384 8x8-head anchors, with 18 regression values and 1 logit each. Its
anchor geometry and decode follow the historical MediaPipe 128px/896-anchor,
7-keypoint graph, but the local ONNX file has no provenance metadata proving
its conversion. The preview should not use palm score alone as a hand outline.

`hand_landmark_probe.hpp` implements the documented wrist-to-middle-MCP
orientation, local-Y shift of -0.5 palm height, square-long 2.6x ROI, 224px
RGB crop, 21-point projection, and hand-presence gating for **offline and
private desktop-preview evaluation only**. A known user-provided screenshot
can be evaluated entirely in memory with
`mercury_palm_model_selftest --known-user-screenshot <path>`;
this narrowly accepts its 1449x620 layout and crops only the two camera
viewports. It reports aggregate counts and scores, never pixels or landmarks.
At palm gate 0.5, one candidate per eye passed hand presence and projected
mostly into broad visible-hand regions (18/21 and 21/21 points); at the default
0.9 gate neither eye produced a candidate. Ceiling-only strips from each eye
produced zero candidates at the 0.5 gate. This is an offline screenshot smoke,
not live proof or a verified silhouette. Any preview integration remains
experimental until frame-by-frame false-positive and contour placement checks
can be made with fresh user consent. No world pose is inferred.

The preview executable's conservative gate additionally requires palm score
at least 0.5, landmark presence at least 0.95, and finite, bounded landmark
geometry before drawing a 1-pixel blue convex-hull contour. Its read-only
known-screenshot smoke drew one estimated contour per eye without saving an
image. This is **not** a pixel-accurate silhouette or a qualified live hand
provider.

The checked-out hand-tracking-models repository contains no model license or
redistribution notice. This candidate remains private evaluation material;
do not package the ONNX files with a preview or release.

For a simulator-only integration check, `--known-user-screenshot-rgba <path>`
passes the same two annotated eyes through a framed binary stdout pipe to
`native/steamvr_free/projection_smoke/run_simulator.ps1 -OfflineScreenshot`.
The mode has no text on stdout, and the receiver rejects missing or extra
bytes. It neither opens the camera nor saves an image; it does not establish
live passthrough, compositor output, or Index headset presentation.

## In-memory live stream contracts

`--consented-stream-rgba <camera-name> <ffmpeg-path>` uses the same DirectShow
camera guard and annotated blue contours as `--consented-preview`, but emits
repeated `XRWB` v1 frames on binary stdout for the Monado simulator projection
test. Each frame is six little-endian `uint32` values (magic `0x42575258`,
version `1`, eye width `960`, eye height `960`, left/right contour counts),
then left and right tightly packed RGBA eyes. Counts may be zero. The child is
terminated when the three-frame simulator test ends. The receiver performs a
GPU upload/readback check; this does not validate Index headset presentation.

`--consented-gesture-stream <camera-name> <ffmpeg-path>` emits only compact
`XRWG` v1 packets for a separate image-local gesture diagnostic. Each packet
starts with nine little-endian `uint32` values: magic `0x47575258`, version
`1`, eye width and height `960`, frame sequence, monotonic timestamp in
nanoseconds (low word, high word), record count `0..4`, and `world_output=0`.
Each 184-byte record is `view:uint32`, `candidate:uint32`, `presence:float32`,
`palm_score:float32`, followed by 21 `(x:float32, y:float32)` eye-local pixel
pairs. Only candidates that pass the preview's conservative contour gate are
emitted. Zero-record frames are normal. These 2D points are not calibrated 3D
joints or Blender-world positions. Both stream modes put binary data only on
stdout and aggregate status on stderr; neither writes images or joint traces.

For the next consented desktop test, `--consented-gesture-preview
<camera-name> <ffmpeg-path>` uses that same XRWG stdout protocol while showing
the blue-contour stereo window from the **same** DirectShow process. Esc, Q,
or closing the window stops capture. A Blender child-process reader can
consume the packets while the person sees the camera preview; the mode does
not require a second camera handle or produce world-space hand positions.

The camera-free checks are:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File native/providers/mercury_offline/check_gesture_stream.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File native/providers/mercury_offline/check_gesture_stream.ps1 -CombinedPreview
powershell -NoProfile -ExecutionPolicy Bypass -File native/providers/mercury_offline/check_child_job.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File native/steamvr_free/projection_smoke/run_simulator.ps1 -SyntheticStream
```

The gesture-preview mode shares one camera process between visual feedback
and XRWG packets. The Monado RGBA mode remains a separate process; opening
both at once has not been validated and may fail if DirectShow grants exclusive
camera access. The next physical test needs fresh consent and separate
verification of camera naming, FFmpeg input, contour placement, and gesture
stability.
