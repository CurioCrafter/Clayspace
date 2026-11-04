param([switch]$CameraConsent)

$ErrorActionPreference = 'Stop'
if (!$CameraConsent) { throw 'Pass -CameraConsent only when the user is ready for a 20-second live Index-camera test.' }
$workspace = Split-Path -Parent $PSScriptRoot
$blender = Join-Path $workspace 'native/build-features/bin/blender.exe'
$evaluator = Join-Path $workspace 'native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe'
$probePath = Join-Path $workspace 'native/steamvr_free/index_camera_probe/index_camera_probe.ps1'
$runtimeManifest = Join-Path $workspace 'native/steamvr_free/monado-inproc-build/openxr_monado-dev.json'
$testScript = Join-Path $workspace 'native/test_live_image_gesture.py'
foreach ($path in @($blender, $evaluator, $probePath, $runtimeManifest, $testScript)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required probe component missing: $path" }
}
$expectedHash = '8E21BE8B70640437EB21AC45A8DBD85F2D05C292F3A2DA5B67D7FCF93E7C84A2'
if ((Get-FileHash -LiteralPath $blender -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'Source-built Blender has changed since the checked gesture build.'
}
$runtime = Get-Content -LiteralPath $runtimeManifest -Raw | ConvertFrom-Json
if (!(Test-Path -LiteralPath $runtime.runtime.library_path -PathType Leaf)) {
    throw 'The workspace Monado simulator runtime is unavailable.'
}
$probe = & $probePath | ConvertFrom-Json
if (!$probe.camera_detected -or $probe.index_usb_id -ne 'VID_28DE&PID_2400' -or $probe.steamvr_processes -ne 0) {
    throw 'Index camera preflight failed or SteamVR is running.'
}
if (@(Get-Process -Name monado-service -ErrorAction SilentlyContinue).Count) {
    throw 'An external Monado service is running.'
}
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$runRoot = Join-Path $workspace ('.tmp_live_image_gesture_' + [Guid]::NewGuid().ToString('N'))
foreach ($subdir in @('config', 'scripts', 'extensions', 'datafiles', 'temp', 'logs')) {
    New-Item -ItemType Directory -Path (Join-Path $runRoot $subdir) -Force | Out-Null
}
$globalRuntimeBefore = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
$env:LOCALAPPDATA = Join-Path $workspace 'native/steamvr_free/simulated-config'
$env:SIMULATED_ENABLE = '1'
$env:XR_RUNTIME_JSON = $runtimeManifest
$env:DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough = '1'
$env:XRWB_TASK_TEST_ALLOW = '1'
$env:XRWB_LIVE_CAMERA_CONSENT = '1'
$env:XRWB_GESTURE_EXE = $evaluator
$env:XRWB_GESTURE_CAMERA = $probe.camera_name
$env:XRWB_GESTURE_FFMPEG = $ffmpeg
$env:BLENDER_USER_RESOURCES = $runRoot
$env:BLENDER_USER_CONFIG = Join-Path $runRoot 'config'
$env:BLENDER_USER_SCRIPTS = Join-Path $runRoot 'scripts'
$env:BLENDER_USER_EXTENSIONS = Join-Path $runRoot 'extensions'
$env:BLENDER_USER_DATAFILES = Join-Path $runRoot 'datafiles'
$env:TEMP = Join-Path $runRoot 'temp'
$env:TMP = Join-Path $runRoot 'temp'
$stdout = Join-Path $runRoot 'logs/blender.stdout.log'
$stderr = Join-Path $runRoot 'logs/blender.stderr.log'
$arguments = @('--factory-startup', '--offline-mode', '--gpu-backend', 'opengl', '--python-exit-code', '1', '--python', $testScript)
Write-Host 'Starting visible Blender + one Index camera preview for 20 seconds; use one hand in the LEFT camera view.'
$process = Start-Process -FilePath $blender -ArgumentList $arguments -WindowStyle Normal -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$startedAt = Get-Date
$readyReported = $false
while (!$process.HasExited) {
    if (((Get-Date) - $startedAt).TotalSeconds -gt 65) {
        # READY names only the evaluator spawned by this Blender test. Its
        # kill-on-close job stops its own FFmpeg child.
        if (Test-Path -LiteralPath $stdout) {
            $ready = Select-String -LiteralPath $stdout -Pattern '^LIVE_IMAGE_GESTURE_READY .*camera_pid=(\d+)' -ErrorAction SilentlyContinue | Select-Object -Last 1
            if ($ready -and $ready.Matches[0].Groups[1].Value) {
                $cameraProcess = Get-Process -Id ([int]$ready.Matches[0].Groups[1].Value) -ErrorAction SilentlyContinue
                if ($cameraProcess -and $cameraProcess.Path -eq $evaluator) {
                    Stop-Process -Id $cameraProcess.Id -Force -ErrorAction SilentlyContinue
                }
            }
        }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $process.WaitForExit(5000) | Out-Null
        Write-Output 'LIVE_IMAGE_GESTURE_FAIL frames=0 actions=0 opacity=0 reason=outer_timeout optical_world=0 headset_verified=0'
        Write-Host "Probe logs: $runRoot"
        exit 1
    }
    if (!$readyReported -and (Test-Path -LiteralPath $stdout)) {
        $ready = Select-String -LiteralPath $stdout -Pattern '^LIVE_IMAGE_GESTURE_READY ' -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($ready) {
            Write-Output $ready.Line
            $readyReported = $true
        }
    }
    Start-Sleep -Milliseconds 250
    $process.Refresh()
}
$process.WaitForExit()
$marker = Select-String -LiteralPath $stdout -Pattern '^LIVE_IMAGE_GESTURE_(PASS|FAIL) ' | Select-Object -Last 1
if ($marker) { Write-Output $marker.Line }
if ((Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime -ne $globalRuntimeBefore) {
    throw 'Global OpenXR runtime changed unexpectedly.'
}
if (!$marker -or !$marker.Line.StartsWith('LIVE_IMAGE_GESTURE_PASS') -or $process.ExitCode -ne 0) {
    Write-Host "Probe logs: $runRoot"
    exit 1
}
