param(
    [Parameter(Mandatory=$true)][string]$BlenderExe,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).ProviderPath
$nativeRoot = (Resolve-Path -LiteralPath (Join-Path $root 'native')).ProviderPath
$BlenderExe = (Resolve-Path -LiteralPath $BlenderExe -ErrorAction Stop).ProviderPath
if (!$BlenderExe.StartsWith($nativeRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The simulator preview requires a workspace-native Blender build.'
}
if ((Get-FileHash -LiteralPath $BlenderExe -Algorithm SHA256).Hash -ne $ExpectedSha256) {
    throw 'Blender executable hash does not match the expected frozen build.'
}
$manifest = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'monado-inproc-build\openxr_monado-dev.json')).ProviderPath
$runtime = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if (!(Test-Path -LiteralPath $runtime.runtime.library_path -PathType Leaf)) {
    throw 'The workspace Monado in-process runtime is not built.'
}
$steam = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue)
if ($steam.Count) { throw 'Stop SteamVR before launching the independent simulator preview.' }
if (@(Get-Process -Name monado-service -ErrorAction SilentlyContinue).Count) {
    throw 'An external Monado service is running; refusing to mix runtimes.'
}
$launcher = (Resolve-Path -LiteralPath (Join-Path $root 'launch_xr_workbench.ps1')).ProviderPath
$config = Join-Path $PSScriptRoot 'simulated-config'
New-Item -ItemType Directory -Path $config -Force | Out-Null
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = (Get-Process -Id $PID).Path
$start.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '" -BlenderExe "' + $BlenderExe + '"'
if ($PreflightOnly) { $start.Arguments += ' -PreflightOnly' }
$start.WorkingDirectory = $root
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$start.RedirectStandardOutput = [bool]$PreflightOnly
$start.RedirectStandardError = [bool]$PreflightOnly
$start.EnvironmentVariables['LOCALAPPDATA'] = $config
$start.EnvironmentVariables['SIMULATED_ENABLE'] = '1'
$start.EnvironmentVariables['XR_RUNTIME_JSON'] = $manifest
$start.EnvironmentVariables['DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough'] = '1'
$activeBefore = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
$process = [Diagnostics.Process]::Start($start)
if (!$process) { throw 'Could not start the isolated simulator preview launcher.' }
if ($PreflightOnly) {
    if (!$process.WaitForExit(25000)) {
        $process.Kill()
        $process.WaitForExit()
        throw 'Simulator preview preflight timed out.'
    }
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $passed = $process.ExitCode -eq 0 -and $stdout.Contains('XRWB_PREFLIGHT_PASS')
    [pscustomobject]@{
        mode = 'preflight'
        passed = $passed
        launcher_exit_code = $process.ExitCode
        isolated_profile = Join-Path $root '.xr_profile'
        stdout = $stdout.Trim()
        stderr = $stderr.Trim()
        global_runtime_unchanged = ((Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime -eq $activeBefore)
        index_display_verified = $false
        optical_hands_verified = $false
    } | ConvertTo-Json -Depth 3
    if (!$passed) { exit 1 }
}
else {
    # The helper remains alive while the visible Blender GUI runs. Only the
    # helper's own PID is returned; this script does not stop user sessions.
    [pscustomobject]@{
        mode = 'desktop_simulator_preview'
        launcher_pid = $process.Id
        blender = $BlenderExe
        blender_sha256 = $ExpectedSha256
        runtime_manifest = $manifest
        isolated_profile = Join-Path $root '.xr_profile'
        camera_enabled = $false
        steamvr_processes_at_launch = 0
        global_runtime_unchanged = ((Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime -eq $activeBefore)
        index_display_verified = $false
        optical_hands_verified = $false
        physical_tracking_verified = $false
    } | ConvertTo-Json -Depth 3
}
