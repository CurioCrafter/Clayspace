param([switch]$CameraConsent)
$ErrorActionPreference = 'Stop'
if (-not $CameraConsent) { throw 'Explicit local camera-test consent is required.' }
$projectRoot = Split-Path $PSScriptRoot -Parent
$probeRoot = Join-Path $projectRoot ('.tmp_mercury_probe_' + [Guid]::NewGuid().ToString('N'))
$helperPath = Join-Path $projectRoot 'native\providers\openvr-camera-probe\camera_probe.exe'
$trackerPath = Join-Path $projectRoot 'native\providers\mercury_offline\build-isolated\mercury_openvr_camera_eval.exe'
foreach ($executable in @($helperPath, $trackerPath)) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Missing test executable: $executable" }
}
New-Item -ItemType Directory -Path $probeRoot | Out-Null
$powerLog = Join-Path $probeRoot 'power.log'
$trackerLog = Join-Path $probeRoot 'tracker.log'
$helper = Start-Process -FilePath $helperPath -ArgumentList '--consented-keep-awake-only' `
    -WindowStyle Hidden -RedirectStandardOutput $powerLog `
    -RedirectStandardError (Join-Path $probeRoot 'power-errors.log') -PassThru
$tracker = $null
try {
    $readyDeadline = [DateTime]::UtcNow.AddSeconds(3)
    do {
        $powerText = Get-Content -LiteralPath $powerLog -Raw -ErrorAction SilentlyContinue
        if ($powerText -match 'XR_POWER_HOLD seconds=30 camera_accessed=0') { break }
        if ($helper.HasExited -or [DateTime]::UtcNow -ge $readyDeadline) { throw 'Keep-awake helper did not become ready.' }
        Start-Sleep -Milliseconds 50
    } while ($true)
    $tracker = Start-Process -FilePath $trackerPath -ArgumentList '--consented-camera-diagnostic' `
        -WorkingDirectory (Split-Path $trackerPath -Parent) -WindowStyle Hidden `
        -RedirectStandardOutput $trackerLog -RedirectStandardError (Join-Path $probeRoot 'tracker-errors.log') -PassThru
    if (-not $tracker.WaitForExit(22000)) {
        # Only this newly created diagnostic child can be stopped here.
        $tracker.Kill()
        if (-not $tracker.WaitForExit(2500)) {
            throw "Camera diagnostic exceeded its timeout and did not exit after termination; inspect $probeRoot before further tests."
        }
        throw 'Camera diagnostic exceeded its bounded timeout.'
    }
    $tracker.Refresh()
    Get-Content -LiteralPath $trackerLog
    if ($tracker.ExitCode -ne 0) { throw "Camera diagnostic failed with exit code $($tracker.ExitCode); see $probeRoot" }
    if ((Get-Content -LiteralPath $trackerLog -Raw) -notmatch 'XR_MERCURY_CAMERA_EVAL .*fresh_frames=30 ') {
        throw 'Camera diagnostic did not report thirty completed fresh frames.'
    }
}
finally {
    # The camera process never owns power settings: its abort cannot skip this
    # separate helper's natural timeout and restoration. Never forcibly kill it.
    if (-not $helper.WaitForExit(35000)) { throw "Power helper did not finish; inspect $powerLog before further tests." }
    $helper.Refresh()
    $powerText = Get-Content -LiteralPath $powerLog -Raw
    if ($helper.ExitCode -ne 0 -or $powerText -notmatch 'XR_POWER_RESTORE pause_result=0 screen_result=0') {
        throw "Power restoration was not confirmed; inspect $powerLog before further tests."
    }
    Write-Output "XR_CAMERA_PROBE_POWER_RESTORED logs=$probeRoot"
}
