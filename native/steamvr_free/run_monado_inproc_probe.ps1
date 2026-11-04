param(
    [ValidateSet('session', 'task')][string]$Mode = 'session',
    [string]$BlenderExe,
    [string]$ExpectedSha256
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).ProviderPath
$manifest = Join-Path $PSScriptRoot 'monado-inproc-build\openxr_monado-dev.json'
$runner = Join-Path $root 'scripts\run_xr_probe.ps1'
$config = Join-Path $PSScriptRoot 'simulated-config'
if (!$BlenderExe) {
    $native = Get-Content -LiteralPath (Join-Path $root 'native\runtime-manifest.json') -Raw | ConvertFrom-Json
    $BlenderExe = Join-Path (Join-Path $root 'native') $native.executable
    $ExpectedSha256 = $native.sha256
}
if (!$ExpectedSha256) { throw 'An explicit Blender executable requires -ExpectedSha256.' }
$BlenderExe = (Resolve-Path -LiteralPath $BlenderExe -ErrorAction Stop).ProviderPath
if ((Get-FileHash -LiteralPath $BlenderExe -Algorithm SHA256).Hash -ne $ExpectedSha256) {
    throw 'Blender executable hash does not match the expected frozen build.'
}
$manifest = (Resolve-Path -LiteralPath $manifest -ErrorAction Stop).ProviderPath
$runtime = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if (!(Test-Path -LiteralPath $runtime.runtime.library_path -PathType Leaf)) {
    throw 'Monado OpenXR library is missing.'
}
$steam = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue)
if ($steam.Count) { throw 'SteamVR must be stopped for this independent runtime test.' }
$service = @(Get-Process -Name monado-service -ErrorAction SilentlyContinue)
if ($service.Count) { throw 'An existing Monado service means this would not test the in-process runtime.' }

New-Item -ItemType Directory -Path $config -Force | Out-Null
$saved = @{}
foreach ($key in @('LOCALAPPDATA', 'SIMULATED_ENABLE', 'XR_RUNTIME_JSON')) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
$activeBefore = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
$probe = $null
$timedOut = $false
try {
    [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $config, 'Process')
    [Environment]::SetEnvironmentVariable('SIMULATED_ENABLE', '1', 'Process')
    [Environment]::SetEnvironmentVariable('XR_RUNTIME_JSON', $manifest, 'Process')
    $launch = & $runner -Mode $Mode -BlenderExe $BlenderExe -LoaderDebug | ConvertFrom-Json
    try { $probe = [Diagnostics.Process]::GetProcessById([int]$launch.pid) }
    catch { throw "Blender exited before the owned process could be inspected: $($launch.pid)" }
    $limitMs = if ($Mode -eq 'task') { 75000 } else { 25000 }
    if (!$probe.WaitForExit($limitMs)) {
        $timedOut = $true
        $probe.Kill()
        $probe.WaitForExit()
    }
}
finally {
    foreach ($key in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process')
    }
    if ($probe -and !$probe.HasExited) { $probe.Kill(); $probe.WaitForExit() }
}
$activeAfter = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
$steamAfter = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue).Count
$stdoutPath = Join-Path $launch.run_directory 'stdout.log'
$stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath -Raw } else { '' }
$marker = if ($Mode -eq 'task') { 'XR_TASK_CARD_PASS simulator=1' } else { 'XRWB_SESSION_RECOVERY_PASS' }
$normalQuit = $stdout.Contains('Blender quit')
$failureMarker = if ($Mode -eq 'task') { 'XR_TASK_CARD_FAIL' } else { 'XRWB_SESSION_RECOVERY_FAIL' }
$passed = !$timedOut -and $normalQuit -and $stdout.Contains($marker) -and
    !$stdout.Contains($failureMarker) -and $activeAfter -eq $activeBefore -and $steamAfter -eq 0
[pscustomobject]@{
    mode = $Mode
    blender = $BlenderExe
    blender_sha256 = $ExpectedSha256
    probe_directory = $launch.run_directory
    runtime_manifest = $manifest
    runtime_library_sha256 = (Get-FileHash -LiteralPath $runtime.runtime.library_path -Algorithm SHA256).Hash
    # run_xr_probe.ps1 launches Blender in a separate PowerShell child; its PID
    # does not provide a reliable exit code to this process after termination.
    blender_exit_code_observed = $false
    normal_quit_marker_present = $normalQuit
    timed_out = $timedOut
    expected_marker_present = $stdout.Contains($marker)
    passed = $passed
    xr_runtime_global_unchanged = ($activeAfter -eq $activeBefore)
    steamvr_processes_after = $steamAfter
    simulated = $true
    index_display_verified = $false
    physical_tracking_verified = $false
} | ConvertTo-Json -Depth 3
