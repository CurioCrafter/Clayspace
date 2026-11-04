# Offline-only: no -Launch invocation, camera query, headset access, or marker write.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$launcher = Join-Path $root 'scripts\launch_physical_index_blender.ps1'
$source = Get-Content -LiteralPath $launcher -Raw
$overlayWrapper = Get-Content -LiteralPath (Join-Path $root 'Open Index Blender Camera Overlay.cmd') -Raw
$gestureWrapper = Get-Content -LiteralPath (Join-Path $root 'Open Index Blender Hands Add Cube.cmd') -Raw
$cameraOffWrapper = Get-Content -LiteralPath (Join-Path $root 'Open Index Blender XR.cmd') -Raw
$workbench = Get-Content -LiteralPath (Join-Path $root 'launch_xr_workbench.ps1') -Raw
$bootstrap = Get-Content -LiteralPath (Join-Path $root 'scripts\xr_launch_bootstrap.py') -Raw
if ($overlayWrapper -notmatch 'launch_physical_index_blender\.ps1" -Launch -CameraOverlay' -or
    $overlayWrapper -notmatch 'five-minute seated, uncalibrated diagnostic' -or
    $overlayWrapper -notmatch 'outlines only, not optical tool control' -or
    $overlayWrapper -notmatch 'may remain open after the camera feed blanks' -or
    $overlayWrapper -match '(?i)\b(taskkill|pause)\b' -or
    $gestureWrapper -notmatch 'launch_physical_index_blender\.ps1" -Launch -CameraOverlay -CameraGestureHud' -or
    $gestureWrapper -match '(?i)\b(taskkill|pause)\b' -or
    $cameraOffWrapper -match '-CameraOverlay' -or
    $source -notmatch '\& \$cameraProbe 2>\&1' -or
    $source -match '\& \$cameraProbe\s+-ConsentedStream' -or
    $source -notmatch 'XRWB_INDEX_CAMERA_CONSENT' -or
    $source -notmatch 'XRWB_INDEX_CAMERA_GESTURE' -or
    $source -notmatch 'XRWB_PHYSICAL_GESTURE_OPT_IN' -or
    $source -notmatch 'XRWB_TASK_TEST_ALLOW = \$null' -or
    $source -notmatch 'XRWB_TASK_CAPTURE_PATH = \$null' -or
    $workbench -notmatch 'camera_overlay_requested=' -or
    $bootstrap -notmatch 'camera_overlay_requested=') {
    throw 'Opt-in wrapper, read-only probe, process environment, or truthful status wiring changed.'
}

try {
    & $launcher -CameraOverlay | Out-Null
    throw 'Overlay without -Launch unexpectedly proceeded.'
}
catch {
    if ($_.Exception.Message -notmatch 'CameraOverlay requires explicit -Launch') { throw }
}
try {
    & $launcher -CameraGestureHud | Out-Null
    throw 'Gesture HUD without -Launch unexpectedly proceeded.'
}
catch {
    if ($_.Exception.Message -notmatch 'CameraGestureHud requires explicit -Launch -CameraOverlay') { throw }
}
try {
    & $launcher -Launch -CameraGestureHud | Out-Null
    throw 'Gesture HUD without -CameraOverlay unexpectedly proceeded.'
}
catch {
    if ($_.Exception.Message -notmatch 'CameraGestureHud requires explicit -Launch -CameraOverlay') { throw }
}

$preflight = (. $launcher | ConvertFrom-Json)
if (!$preflight.preflight_only -or $preflight.camera_requested -or
    $preflight.physical_display_queried -or $preflight.blender_started) {
    throw 'Default static preflight unexpectedly used physical hardware or camera.'
}
$ffmpegFirst = @(Get-Command ffmpeg -CommandType Application -ErrorAction Stop)[0].Source
if ((Get-FileHash -LiteralPath $ffmpegFirst -Algorithm SHA256).Hash -ne $expectedFfmpeg -or
    @(Get-Command ffmpeg -ErrorAction Stop)[0].Source -ine $ffmpegFirst) {
    throw 'Read-only overlay preflight would not resolve the pinned FFmpeg first.'
}
$good = [pscustomobject]@{
    steamvr_processes = 0
    backend = 'Windows DirectShow'
    index_usb_id = 'VID_28DE&PID_2400'
    camera_detected = $true
    camera_name = 'Index Camera'
    capture_attempted = $false
    capture_succeeded = $false
    frames = 0
    image_output = 'none; FFmpeg null muxer'
}
if ((Assert-IndexCameraProbeReport $good) -cne 'Index Camera') {
    throw 'Exact safe DirectShow probe report was rejected.'
}
foreach ($bad in @(
    @{ index_usb_id = 'VID_28DE&PID_2000' },
    @{ backend = 'Other' },
    @{ capture_attempted = $true },
    @{ camera_detected = $false },
    @{ camera_name = 'Camera;cmd' }
)) {
    $sample = $good.PSObject.Copy()
    foreach ($key in $bad.Keys) { $sample.$key = $bad[$key] }
    try {
        Assert-IndexCameraProbeReport $sample | Out-Null
        throw 'An unsafe or non-Index camera probe report was accepted.'
    }
    catch {
        if ($_.Exception.Message -notmatch 'Exact read-only Index USB/DirectShow camera probe failed') { throw }
    }
}
'XR_INDEX_CAMERA_OVERLAY_LAUNCHER_TEST_PASS static_preflight=1 explicit_opt_in=1 trusted_ffmpeg=1 exact_usb_probe=1 unsafe_report_rejected=1 capture=0 physical_launch=0'
