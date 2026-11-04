# Independent Index camera probe on Windows

Requires an already-installed FFmpeg with DirectShow support. No SteamVR or
OpenVR API is used. The script first rejects active SteamVR processes and
identifies the physical Valve USB camera by `VID_28DE&PID_2400`, excluding
virtual cameras. Enumeration does not open the camera.

```powershell
& .\native\steamvr_free\index_camera_probe\index_camera_probe.ps1
& .\native\steamvr_free\index_camera_probe\index_camera_probe.ps1 -ConsentedStream
```

The consented mode reads at most five frames and sends them to FFmpeg's null
muxer. The probe has an eight-second process timeout and removes its temporary
text logs. It reports device identity, format, frame count, and whether five
capture timestamps advanced monotonically, never pixels or raw timestamps.
Exposure age and sustained rate remain unknown.
Physical camera enumeration or five frames do not qualify headset display,
eye calibration, optical hands, or 6DoF head tracking.
