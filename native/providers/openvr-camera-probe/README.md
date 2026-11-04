# Public OpenVR camera acquisition probe

This separate, bounded process tests Valve's supported camera service without
installing a camera/USB/SteamVR driver, changing a setting, or starting a scene
application. It never runs inside Blender, renders imagery, saves pixels,
publishes joints, or claims calibrated optical tracking. Run only after explicit
camera-test consent. It reads at most 60 advancing frames or runs for 8 seconds,
then clears its pixel buffer and releases the streaming handle before OpenVR.

The official Valve API header is pinned to commit
`0924064316de3effbcd1acf1e309182a2deb1c05`:
https://github.com/ValveSoftware/openvr/tree/0924064316de3effbcd1acf1e309182a2deb1c05

Header SHA-256: `3D32D8EB5BCBE2E250610123AEE8142C68F0E7D4DA823F87513B6A2A333078D7`.
The upstream license is retained under `vendor/LICENSE`. This is private local
diagnostic code, not a bundled replacement for SteamVR.

Build from a Visual Studio developer shell in this directory:

```powershell
cl /nologo /std:c++17 /EHsc /W4 /O2 camera_probe.cc /Fe:camera_probe.exe
.\camera_probe.exe --self-test
.\camera_probe.exe --consented-camera-test
# User-requested temporary keep-awake test; restores effective values on exit:
.\camera_probe.exe --consented-keep-awake-camera-test
# Same temporary settings, 30 seconds only, without opening the camera:
.\camera_probe.exe --consented-keep-awake-only
# Longer camera-off automated UI test; optional duration is bounded to 1..180 seconds:
.\camera_probe.exe --consented-keep-awake-only 120
```

Exposure timestamps are reported as changing/not changing only. Their epoch,
sensor-age relationship and camera-to-head calibration accuracy are not assumed.
Camera metadata availability is not the same as physical calibration validity.

The optional power test temporarily sets `power.pauseCompositorOnStandby=false`
and `power.turnOffScreensTimeout=600`, then restores the API's previous effective
values even if the probe throws. This does not bypass tracking or bad-tracking
fade policy. If the process itself crashes, check the printed prior values and
restore them before continuing; it is not a persistent background service.

September 21 local evidence: ordinary idle attempts returned one non-advancing
frame; the temporary keep-awake camera test returned 60 advancing frames and
60 distinct exposure values in 3.254 seconds. Both intrinsics and both extrinsic
matrices were available. Headset and frame poses remained invalid with tracking
result 101. Stream release and both power-setting restorations returned success.
Thus camera access is proven; room-aligned hand tracking is not. No pixels or
skeletal traces were saved.
