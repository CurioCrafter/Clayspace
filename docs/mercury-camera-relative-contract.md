# Mercury camera-relative hand contract (proposal, not an integration)

## Decision

The current offline Mercury evaluation must remain **camera-relative diagnostic
output only**. It must not feed `GHOST_XrHandSample`, Blender world-space
gestures, or an OpenXR layer until the gates below have been met. The successful
30-frame camera run established fresh frames and inference calls, but it did
not establish an active hand, a valid HMD pose, a world registration, or an
OpenXR optical-hand source.

This proposal deliberately fits the existing OpenXR consumer rather than
inventing a second coordinate convention. `xr_workbench/hands.py` accepts only
world-space joints from the native XR session and fails closed unless source 1
(`unobstructed optical`), a valid navigation transform, and position-valid
thumb/index joints are present.

## Provider packet

The future native provider should publish a transient in-memory packet per
processed stereo pair. It is not a persisted telemetry format and must not
contain frames, images, device identifiers, or a joint trace log.

| Field | Required contract |
| --- | --- |
| `source` | Literal `index_camera_optical`; no controller or inferred-source fallback. |
| `space` | `mercury_calibrated_stereo`, an explicitly named camera-relative space. It is **not** OpenXR local/stage/view or Blender world. |
| `joints[2][26]` | Mercury `xrt_hand_joint_set` order, each relation flags, position, optional orientation, and radius. Omit/invalidate a joint without its position-valid and position-tracked flags. |
| `active[2]` | Exact `xrt_hand_joint_set.is_active`; false means no usable hand. Mercury itself waits for a short tracked-hand history before enabling it. |
| `timestamp_ns` | The exact timestamp passed into the input `xrt_frame` and returned by `t_ht_sync_process`. In the current evaluator this is a host monotonic timestamp sampled after frame retrieval and grayscale preparation, **not** measured camera capture/exposure time, OpenXR `XrTime`, or wall clock. It cannot establish sensor age. |
| `generation` | A nonzero provider epoch, not a frame counter. Increment before publishing again after start/stop, stream restart, calibration/basis/model change, source-provenance change, or any stale/invalidating fault. |
| `confidence` | No numeric confidence is currently safe to expose. Report only `active` plus relation flags until a calibrated confidence definition and threshold are implemented and tested. |
| `world_output` | Always false for the diagnostic path. |

Mercury writes the left frame's timestamp directly to its output and stamps the
joint set's hand pose with `hand_pose_camera_offset`, currently identity
(`hg_sync.cpp:686,723,961`). Its joint set API describes that pose as driver
global space without tracking-origin offset (`xrt_defines.h:1017-1025`). In the
offline bridge that is the calibrated stereo camera frame, because no
camera-to-HMD transform is applied. Treating it as a world pose would be a
coordinate error.

## Axes and units

The camera ingestion boundary is verified only for this conversion:

```
OpenVR camera axes (+X right, +Y up, -Z forward)
    <-> diag(1, -1, -1)
OpenCV / Mercury image axes (+X right, +Y down, +Z forward)
```

`openvr_mercury_bridge.cpp` derives the stereo relative transform from the two
public `CameraToHead` matrices and applies that named basis only while building
Mercury calibration. It is not a camera-to-Blender transform.

Mercury's calibration and OpenXR/XRT hand structures use metric calibration
inputs and xrt pose values; therefore the intended joint position unit is
metres. The first physical active-hand test must still validate scale with a
known-distance fixture before any interaction gate opens. Do not compensate
with an arbitrary Blender unit multiplier.

There is currently no validated transform from `mercury_calibrated_stereo` to
the OpenXR reference space. A future bridge needs a same-epoch,
finite camera-to-HMD transform and an explicit space identifier. It must then
apply Blender's existing `WM_xr_hand_world_transform` path, which combines the
native reference-space sample with `prev_base_pose`, `prev_base_scale`,
navigation pose, viewer scale, and tracking settings. With the observed invalid
HMD poses, this gate is closed.

## Timestamp, provenance, and lifecycle gates

1. Track frame sequence/exposure changes separately from host arrival time.
   Only accept strictly forward provider sample timestamps; invalidate after
   250 ms of no advance, matching the workbench's stream watchdog. Advancing
   host timestamps alone do not prove fresh sensor observations.
2. Do not convert the current steady-clock timestamp to `XrTime` by arithmetic.
   A native OpenXR-time correlation must be implemented and tested at the
   provider boundary, or the data remains camera-relative.
3. Map output to the existing source marker only after the runtime reports
   `XR_HAND_TRACKING_DATA_SOURCE_UNOBSTRUCTED_EXT`; `XR_EXT_hand_tracking`
   alone can carry controller-inferred data and is intentionally rejected by
   `GHOST_XrSession::updateHandSamples`.
4. On loss of active, relation flags, timestamp freshness, generation match,
   calibration, camera stream, session focus, or reference-space stability:
   immediately publish inactive and cancel/pause any capture. Never reuse the
   last hand pose.
5. An edit capture owns `(optical, "index_camera_optical", generation)` so the
   existing `CaptureOwner` rules cancel it on a provider epoch change.

## Camera coexistence constraints

The present public OpenVR client calls `VR_InitInternal` as a background
application and is safe only as the isolated evaluator/helper pair. It must
not be called from Blender's OpenXR process. The Rectus source audit records
that an OpenXR application needs a compatible in-process `IVRClientCore` route
from `vrclient_x64`, not a second `VR_Init` call. That acquisition design,
camera consent/revocation, and stream cleanup are separate implementation work.

The probe supervisor now uses a separate 30-second keep-awake helper. The
camera evaluator has no power-setting writes; even an evaluator abort cannot
skip the helper's natural restore. The supervisor waits for the post-apply
`XR_POWER_HOLD` marker, bounds camera-child termination, and requires the
helper's restore marker. It still cannot make an operating-system crash or a
force-killed helper restore settings early, so live testing remains explicit
and bounded.

## Acceptance before a bridge ticket

- Active left/right hands with valid, changing required joints on a consented
  Index-camera run; no frame/joint persistence.
- Known-distance scale and OpenVR/OpenCV basis fixtures pass.
- A valid, changing same-epoch HMD/reference-space transform permits a
  camera-relative-to-OpenXR mapping fixture; invalid HMD pose keeps
  `world_output=0`.
- Native OpenXR probe proves `XR_EXT_hand_tracking` and the unobstructed optical
  data-source result on this SteamVR runtime.
- Start/stop/restart and tracking-loss tests prove generation increments,
  stale-data rejection, camera release, and no controller fallback.

Until all five pass, the honest status is: **camera frames and Mercury inference
are exercised; optical world-space hand interaction is unverified and disabled.**

## Narrow image-local live gesture

The separate `XRWG` v1 stream carries 21 pixel landmarks per eye and explicitly
sets `world_output=0`. It may drive only `wm.xr_task_card_cycle_opacity` through
`xr_workbench.image_gestures`: a neutral, sustained thumb/index pinch followed
by an open hand changes the fixed native card opacity action. This path does not
claim a 3D hand ray, select a card button by pointing, or enter edit transactions.
Missing, ambiguous, stale, or low-confidence image observations cancel the
pinch. The camera process is launched with explicit consent after Index USB and
SteamVR-free preflight; its preview and Blender packet stream share one camera
capture. Physical gesture reliability and headset appearance remain unverified.
