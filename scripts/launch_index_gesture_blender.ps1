param([switch]$PreflightOnly)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$blender = Join-Path $workspace 'native/build-features/bin/blender.exe'
$gestureExe = Join-Path $workspace 'native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe'
$probePath = Join-Path $workspace 'native/steamvr_free/index_camera_probe/index_camera_probe.ps1'
foreach ($path in @($blender, $gestureExe, $probePath)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required source build or probe missing: $path" }
}
$probe = & $probePath | ConvertFrom-Json
if (!$probe.camera_detected -or $probe.index_usb_id -ne 'VID_28DE&PID_2400' -or $probe.steamvr_processes -ne 0) {
    throw 'Index camera preflight failed or SteamVR is running.'
}
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$hash = (Get-FileHash -LiteralPath $blender -Algorithm SHA256).Hash
$simulator = Join-Path $workspace 'native/steamvr_free/launch_monado_simulator_preview.ps1'
if ($PreflightOnly) {
    $simulatorCheck = & $simulator -BlenderExe $blender -ExpectedSha256 $hash -PreflightOnly | ConvertFrom-Json
    if (!$simulatorCheck.passed -or !$simulatorCheck.global_runtime_unchanged) { throw 'SteamVR-free simulator preflight failed.' }
    Write-Output 'XRWB_IMAGE_GESTURE_PREFLIGHT_PASS camera_accessed=0 steamvr=0 runtime=monado_simulator'
    exit 0
}
$env:XRWB_GESTURE_EXE = $gestureExe
$env:XRWB_GESTURE_CAMERA = $probe.camera_name
$env:XRWB_GESTURE_FFMPEG = $ffmpeg
$env:XRWB_TASK_TEST_ALLOW = '1'
Write-Host 'Opening the SteamVR-free Monado desktop simulator. Index display and head tracking are not qualified.'
Write-Host 'Open XR, show the native task card, then click Start Camera Card Pointer in the XR Workbench panel.'
Write-Host 'In the LEFT camera view, move one index fingertip over a card button, then open, pinch, release.'
Write-Host 'The 2D pointer can press task-card buttons only; it is not a world-space hand or object ray.'
& $simulator -BlenderExe $blender -ExpectedSha256 $hash
