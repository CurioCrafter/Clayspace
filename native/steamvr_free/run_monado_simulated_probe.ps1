param(
    [ValidateSet('session', 'task')][string]$Mode = 'session',
    [string]$BlenderExe,
    [string]$ExpectedSha256
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).ProviderPath
$manifestPath = Join-Path $PSScriptRoot 'monado-build\openxr_monado-dev.json'
$serviceExe = Join-Path $PSScriptRoot 'monado-build\src\xrt\targets\service\monado-service.exe'
$runner = Join-Path $root 'scripts\run_xr_probe.ps1'
$native = Get-Content -LiteralPath (Join-Path $root 'native\runtime-manifest.json') -Raw | ConvertFrom-Json
if (!$BlenderExe) {
    $BlenderExe = Join-Path (Join-Path $root 'native') $native.executable
    $ExpectedSha256 = $native.sha256
}
if (!$ExpectedSha256) { throw 'An explicit Blender executable requires -ExpectedSha256.' }
$BlenderExe = (Resolve-Path -LiteralPath $BlenderExe -ErrorAction Stop).ProviderPath
if ((Get-FileHash -LiteralPath $BlenderExe -Algorithm SHA256).Hash -ne $ExpectedSha256) {
    throw 'Blender executable hash does not match the expected frozen build.'
}
if (!(Test-Path -LiteralPath $serviceExe -PathType Leaf)) { throw 'Monado service is not built.' }
if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Monado runtime manifest is missing.' }
$steam = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue)
if ($steam.Count) { throw 'SteamVR must be stopped for this independent runtime test.' }
$existing = @(Get-Process -Name monado-service -ErrorAction SilentlyContinue)
if ($existing.Count) { throw 'A Monado service already exists; refusing to attach or stop it.' }

$id = [Guid]::NewGuid().ToString('N')
$runDir = Join-Path $PSScriptRoot ('simulated-runs\' + $id)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$serviceOut = Join-Path $runDir 'service.stdout.log'
$serviceErr = Join-Path $runDir 'service.stderr.log'
$oldSim = [Environment]::GetEnvironmentVariable('SIMULATED_ENABLE', 'Process')
$oldRuntime = [Environment]::GetEnvironmentVariable('XR_RUNTIME_JSON', 'Process')
$service = $null
$blender = $null
$result = [ordered]@{
    run_directory = $runDir
    mode = $Mode
    blender = $BlenderExe
    service_started = $false
    service_stopped = $false
    blender_exited = $false
    blender_timed_out = $false
    xr_runtime_global_unchanged = $false
    probe_directory = $null
}
$activeBefore = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
try {
    [Environment]::SetEnvironmentVariable('SIMULATED_ENABLE', '1', 'Process')
    $service = Start-Process -FilePath $serviceExe -WorkingDirectory (Split-Path $serviceExe) `
        -WindowStyle Hidden -RedirectStandardOutput $serviceOut -RedirectStandardError $serviceErr -PassThru
    Start-Sleep -Milliseconds 800
    if ($service.HasExited) { throw "Monado service exited at startup ($($service.ExitCode))." }
    $result.service_started = $true
    [Environment]::SetEnvironmentVariable('XR_RUNTIME_JSON', (Resolve-Path -LiteralPath $manifestPath).ProviderPath, 'Process')
    $launch = & $runner -Mode $Mode -BlenderExe $BlenderExe -LoaderDebug | ConvertFrom-Json
    $result.probe_directory = $launch.run_directory
    try { $blender = [Diagnostics.Process]::GetProcessById([int]$launch.pid) }
    catch { $result.blender_exited = $true }
    if ($blender) {
        if ($blender.WaitForExit(25000)) { $result.blender_exited = $true }
        else {
            $result.blender_timed_out = $true
            $blender.Kill()
            $blender.WaitForExit()
        }
    }
}
finally {
    [Environment]::SetEnvironmentVariable('SIMULATED_ENABLE', $oldSim, 'Process')
    [Environment]::SetEnvironmentVariable('XR_RUNTIME_JSON', $oldRuntime, 'Process')
    if ($service) {
        if (!$service.HasExited) { $service.Kill(); $service.WaitForExit() }
        $result.service_stopped = $service.HasExited
    }
    $activeAfter = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1').ActiveRuntime
    $result.xr_runtime_global_unchanged = ($activeAfter -eq $activeBefore)
    [pscustomobject]$result | ConvertTo-Json -Depth 3
}
