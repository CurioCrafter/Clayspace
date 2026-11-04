param([switch]$ConsentedStream)

$ErrorActionPreference = 'Stop'
if (!$ConsentedStream) { throw 'Explicit consented stream switch required.' }

$workspace = Split-Path -Parent $PSScriptRoot
$probePath = Join-Path $workspace 'native/steamvr_free/index_camera_probe/index_camera_probe.ps1'
$evaluator = Join-Path $workspace 'native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe'
if (!(Test-Path -LiteralPath $evaluator)) { throw 'Build mercury_direct_camera_eval first.' }

# Enumeration verifies the USB ID and checks that SteamVR is stopped; the
# evaluator repeats the process guard during every in-memory frame read.
$probe = & $probePath | ConvertFrom-Json
if (!$probe.camera_detected -or $probe.index_usb_id -ne 'VID_28DE&PID_2400') {
    throw 'The physical Valve Index camera was not identified.'
}
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
& $evaluator --consented-dshow $probe.camera_name $ffmpeg
if ($LASTEXITCODE -ne 0) { throw "Direct Mercury diagnostic failed (exit $LASTEXITCODE)." }
