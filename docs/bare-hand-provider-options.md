# Bare-Hand Provider Options: Windows + Valve Index Cameras

## Decision

There is **no verified, ready-to-enable Windows provider** that turns Valve
Index camera frames into optical `XR_EXT_hand_tracking` joints through the
SteamVR OpenXR runtime. The only source-based candidate found that explicitly
targets this exact hardware is Mercury's SteamVR driver. It is a useful
*experimental source track*, not a completed provider: its own maintainer says
it is unmaintained and may damage the Index camera-driver setup.

Do not substitute Index controllers, SteamVR skeletal-controller emulation, or
passthrough imagery for optical hand tracking. The requested product state is
hands only, sourced from the built-in Index cameras.

`XR_EXT_hand_tracking` is the correct OpenXR consumer contract: it supplies
hand trackers and joint locations via `xrCreateHandTrackerEXT` and
`xrLocateHandJointsEXT`. [`XR_EXT_hand_tracking` specification](https://registry.khronos.org/OpenXR/specs/1.1/man/html/XR_EXT_hand_tracking.html)
does not require a particular optical provider or prove a runtime's support.
The optional hand-tracking-data-source extension can identify the source only
when the active runtime exposes it.

## Evidence-backed options

| Candidate | Windows + Index camera evidence | OpenXR optical-joint result | Dependencies / licence | Verdict |
| --- | --- | --- | --- | --- |
| [Mercury SteamVR driver](https://github.com/moshimeow/mercury_steamvr_driver) | The repository has a `win64` output path and documents a Windows build using Visual Studio 2019/2022, CMake, Ninja, vcpkg, and ONNX Runtime. Its stated purpose is Mercury tracking from Index cameras. | **Unknown.** It is an OpenVR/SteamVR driver, not an OpenXR API layer. Valve documents that a driver can emit skeletal input, but that alone does not prove SteamVR publishes optical joints through `XR_EXT_hand_tracking`. | Repository source is [Boost Software License 1.0](https://www.boost.org/LICENSE_1_0.txt); dependency/model licences must be audited separately. Requires its Monado submodule plus ONNX Runtime and vcpkg dependencies. | **Only credible experimental source candidate.** Block production integration until the proof gates below pass. The maintainer explicitly warns the driver is unmaintained and may break Index camera drivers. |
| [Monado Mercury hand tracking](https://monado.freedesktop.org/handtracking) as full runtime | Monado documents Mercury with Index cameras and no controllers, but its supported setup documentation is Linux-focused. [Monado's getting-started page](https://monado.freedesktop.org/getting-started.html) says its Windows port is in progress; its developer status calls Direct Mode with real HMDs a major blocker. | Monado implements OpenXR and Mercury is designed as an optical hand tracker, but this does **not** make it a viable replacement runtime for the present Windows SteamVR Index setup. | Mercury needs OpenCV and ONNX Runtime; Linux documentation supplies the tested/install path. | **Not viable for this Windows milestone.** Useful upstream algorithm/reference only. |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) plus a new stereo pipeline | MediaPipe is Apache-2.0 and its project documents on-device processing. Its Windows build instructions label Windows experimental and require a bespoke desktop build/OpenCV path. No primary source found that gives MediaPipe a supported Valve Index stereo-camera acquisition path or emits OpenXR hand joints. | MediaPipe hand landmarks are image-space inference, not an OpenXR provider. Stereo calibration, 3D reconstruction, temporal filtering, wrist/world registration, and an OpenXR runtime/driver bridge would all be new work. | Apache-2.0 framework; task-model terms and model provenance need a separate audit. | **Architecture ingredient, not an existing provider.** Too large and unverified to present as a near-term solution. |
| [Rectus SteamVR passthrough layer](https://github.com/Rectus/openxr-steamvr-passthrough) | It accesses SteamVR camera imagery for passthrough, consistent with the currently active passthrough route. | It supplies passthrough, not hand joints or `XR_EXT_hand_tracking`. | Open-source layer; its camera route is not a pose provider. | **Not a hand-tracking provider.** It may inform a future consented camera-acquisition design only. |
| [Ultraleap OpenXR layer](https://github.com/ultraleap/OpenXRHandTracking) | It is an OpenXR hand-tracking layer for its own optical tracker/service, neither of which is present. | The layer targets `XR_EXT_hand_tracking`, but does not consume Valve Index cameras. | External hardware/service is required. | **Rejected by scope:** no Ultraleap hardware or service, and no purchase/substitution claim. |

Valve's [OpenVR driver documentation](https://github.com/ValveSoftware/openvr/blob/master/docs/Driver_API_Documentation.md#skeletal-input)
confirms that a SteamVR driver can expose a skeletal stream. That is insufficient
for this requirement. In particular, current [Godot OpenXR documentation](https://github.com/godotengine/godot-docs/blob/master/tutorials/xr/openxr_hand_tracking.rst)
states that SteamVR hand tracking is controller-based and explains that source
provenance is runtime-dependent. Treat that as a strong compatibility warning:
even a functioning Mercury driver must prove **optical** OpenXR joints on this
specific SteamVR build rather than relying on inferred/controller hand data.

## Recommended next ticket: evidence-gated Mercury driver evaluation

**Title:** `optical-index-mercury-provider-spike`

This is the highest-leverage existing-source path, but only in an isolated
SteamVR profile with explicit camera consent and a documented recovery plan for
the Index camera driver. It must not modify the production profile and must
never fall back to controller-derived hand data.

Pass conditions:

1. Build provenance records the exact Mercury commit, Monado submodule commit,
   ONNX Runtime version, model files/hashes, and all applicable licences.
2. With Index controllers powered off, the provider produces valid, changing
   left and right optical hand poses from Index cameras under adequate light;
   loss of camera frames yields unavailable tracking, never synthetic poses.
3. The active SteamVR OpenXR runtime enumerates `XR_EXT_hand_tracking`; native
   probing creates both hand trackers and reports valid joints. If
   `XR_EXT_hand_tracking_data_source` is available, it must report an optical
   source rather than controller/inferred data.
4. Camera use is off until explicit consent, revocation stops it immediately,
   and the original SteamVR/Index camera configuration is restored on exit.
5. The built-in Blender gesture surface accepts only these proven optical
   samples and reports `unavailable`/`unverified` when any gate is absent.

Fail/blocked conditions include no `XR_EXT_hand_tracking` enumeration, only
controller-sourced skeletons, invalid/stale joints, uncertainty about the model
licence, or any unrecoverable camera-driver regression. A pass does not certify
accuracy, occlusion robustness, latency, or safety without a separate physical
headset test.

## Confidence summary

- **High confidence:** no discovered ready provider satisfies all constraints;
  Rectus is passthrough-only; full Monado is not a supported Windows Index
  replacement today.
- **Medium confidence:** Mercury is a real Windows-buildable source candidate
  for Index-camera optical tracking, based on its own repository and stated
  SteamVR purpose.
- **Low confidence:** Mercury data can reach this SteamVR OpenXR runtime as
  optical `XR_EXT_hand_tracking` joints. This is the critical untested bridge
  and must be established by the ticket's native enumeration/provenance gate.
