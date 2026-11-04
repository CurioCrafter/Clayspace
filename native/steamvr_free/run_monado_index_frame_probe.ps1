param(
    [switch]$Probe,
    [switch]$DisplayPatternSeen,
    [switch]$SelfTestTimeout
)

$ErrorActionPreference = 'Stop'
$info = & (Join-Path $PSScriptRoot 'launch_monado_index_experimental.ps1') | ConvertFrom-Json
$expectedRuntimeSha256 = '86CC2FD522D82FBEBEFA61032C80853B3584AE65942CAE0D50C7E953C821359C'
if ($info.library_sha256 -ne $expectedRuntimeSha256) { throw 'The isolated OpenXR DLL changed since this frame probe was prepared.' }
$exe = Join-Path $PSScriptRoot 'projection_smoke\build\index_runtime_frame_probe.exe'
if (!(Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Missing frame probe: $exe" }
if ($Probe -and $SelfTestTimeout) { throw 'Choose physical probe or offline timeout self-test.' }
if (!$Probe -and !$SelfTestTimeout) {
    [pscustomobject]@{
        ready = $true
        physical_probe_started = $false
        manifest = $info.manifest
        runtime_sha256 = $info.library_sha256
        timeout_ms = 10000
        pattern_seconds = 5
        camera_requested = $false
    } | ConvertTo-Json
    return
}
if ($Probe) {
    if (!$DisplayPatternSeen) { throw 'The previously verified Index application pattern must be seen in the lenses first.' }
    $conflicts = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup,monado-service -ErrorAction SilentlyContinue)
    if ($conflicts.Count) { throw 'Stop SteamVR and Monado services before the physical OpenXR frame probe.' }
}

$config = Join-Path $PSScriptRoot 'physical-probe-config'
if ($Probe) { New-Item -ItemType Directory -Path $config -Force | Out-Null }
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = if ($SelfTestTimeout) { Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe' } else { $exe }
$start.Arguments = if ($SelfTestTimeout) { '-NoProfile -Command "Start-Sleep -Seconds 2"' } else { '--probe-frame' }
$start.WorkingDirectory = Split-Path $exe
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.EnvironmentVariables['XR_RUNTIME_JSON'] = $info.manifest
$start.EnvironmentVariables['XRT_COMPOSITOR_INDEX_EXTENDED'] = '1'
$start.EnvironmentVariables['LH_HANDTRACKING'] = 'OFF'
$start.EnvironmentVariables['VIVE_SLAM'] = 'OFF'
$start.EnvironmentVariables['DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough'] = '1'
$start.EnvironmentVariables['LOCALAPPDATA'] = $config
[void]$start.EnvironmentVariables.Remove('SIMULATED_ENABLE')

$registration = 'HKLM:\SOFTWARE\Khronos\OpenXR\1'
$activeBefore = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
$child = [Diagnostics.Process]::Start($start)
if (!$child) { throw 'Could not start the isolated OpenXR frame probe.' }
$childId = $child.Id
$timedOut = $false
$pipeDrainFailed = $false
$timeoutMs = if ($SelfTestTimeout) { 250 } else { 10000 }
try {
    $outTask = $child.StandardOutput.ReadToEndAsync()
    $errTask = $child.StandardError.ReadToEndAsync()
    if (!$child.WaitForExit($timeoutMs)) {
        $timedOut = $true
        if (!$child.HasExited) { $child.Kill() }
        $child.WaitForExit(2000) | Out-Null
    }
    $drainDeadline = [DateTime]::UtcNow.AddMilliseconds(2000)
    while ((!$outTask.IsCompleted -or !$errTask.IsCompleted) -and [DateTime]::UtcNow -lt $drainDeadline) {
        Start-Sleep -Milliseconds 10
    }
    $pipeDrainFailed = !$outTask.IsCompleted -or !$errTask.IsCompleted -or $outTask.IsFaulted -or $errTask.IsFaulted
    $stdout = if ($outTask.IsCompleted -and !$outTask.IsFaulted -and !$outTask.IsCanceled) { $outTask.Result } else { '[stdout pipe did not drain]' }
    $stderr = if ($errTask.IsCompleted -and !$errTask.IsFaulted -and !$errTask.IsCanceled) { $errTask.Result } else { '[stderr pipe did not drain]' }
    $exitCode = if ($child.HasExited) { $child.ExitCode } else { $null }
}
finally {
    if (!$child.HasExited) { $child.Kill(); $child.WaitForExit(2000) | Out-Null }
    $childExited = $child.HasExited
    $child.Dispose()
}
$activeAfter = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
$safeStdout = (($stdout -split '\r?\n') | ForEach-Object {
    if (!$_.Trim()) { '' }
    elseif ($_.Length -le 512 -and $_ -match '^MONADO_INDEX_FRAME_(STEP|PASS|FAIL)\b') { $_ }
    else { '[redacted non-probe stdout]' }
}) -join [Environment]::NewLine
$safeStderr = (($stderr -split '\r?\n') | ForEach-Object {
    if ($_.Length -gt 512 -or $_ -match '(?i)serial|tracked_cameras|JSON config|camera') {
        '[redacted device metadata]'
    } else { $_ }
}) -join [Environment]::NewLine
$passed = if ($SelfTestTimeout) {
    $timedOut -and $childExited -and !$pipeDrainFailed -and $activeAfter -eq $activeBefore
} else {
    !$timedOut -and $childExited -and !$pipeDrainFailed -and $exitCode -eq 0 -and
        $safeStdout -match '(?m)^MONADO_INDEX_FRAME_PASS\b' -and $activeAfter -eq $activeBefore
}
$logPath = $null
if ($Probe) {
    $logDir = Join-Path $PSScriptRoot 'physical-probe-logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $logPath = Join-Path $logDir ("frame-probe-{0}-{1}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $childId)
    [IO.File]::WriteAllText($logPath, "stdout:`n$safeStdout`nstderr:`n$safeStderr")
}
$stdoutPreview = if ($safeStdout.Length -gt 4096) { $safeStdout.Substring(0, 4096) + "`n[more in diagnostic log]" } else { $safeStdout }
$stderrPreview = if ($safeStderr.Length -gt 4096) {
    $safeStderr.Substring(0, 2048) + "`n[truncated; full sanitized log: $logPath]`n" +
        $safeStderr.Substring($safeStderr.Length - 2048)
} else { $safeStderr }
[pscustomobject]@{
    passed = $passed
    physical_probe_started = [bool]$Probe
    offline_timeout_self_test = [bool]$SelfTestTimeout
    timeout_ms = $timeoutMs
    child_exited = $childExited
    timed_out = $timedOut
    pipes_drained = !$pipeDrainFailed
    exit_code = $exitCode
    stdout = $stdoutPreview
    stderr = $stderrPreview
    diagnostic_log = $logPath
    global_runtime_unchanged = ($activeAfter -eq $activeBefore)
    camera_requested = $false
    lens_visibility_unverified = $true
} | ConvertTo-Json -Depth 3
if (!$passed) { throw 'The isolated OpenXR frame probe did not pass; inspect the stage and result above.' }
