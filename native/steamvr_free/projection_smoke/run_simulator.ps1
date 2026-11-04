param(
    [string]$OfflineScreenshot,
    [switch]$SyntheticStream,
    [switch]$ConsentedStream,
    [string]$CameraName,
    [string]$FfmpegPath
)
$ErrorActionPreference = 'Stop'
if (@([bool]$OfflineScreenshot, [bool]$SyntheticStream, [bool]$ConsentedStream).Where({ $_ }).Count -gt 1) {
    throw 'Select only one input mode.'
}
if ($ConsentedStream -and (!$CameraName -or !$FfmpegPath)) {
    throw 'ConsentedStream requires CameraName and FfmpegPath.'
}
if (!$ConsentedStream -and ($CameraName -or $FfmpegPath)) {
    throw 'CameraName and FfmpegPath require ConsentedStream.'
}
if ($PSBoundParameters.ContainsKey('OfflineScreenshot') -and -not $OfflineScreenshot) {
    throw 'OfflineScreenshot must be a non-empty file path.'
}
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).ProviderPath
$exe = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'build\monado_projection_smoke.exe')).ProviderPath
$manifest = (Resolve-Path -LiteralPath (Join-Path $root 'native\steamvr_free\monado-inproc-build\openxr_monado-dev.json')).ProviderPath
$config = (Resolve-Path -LiteralPath (Join-Path $root 'native\steamvr_free\simulated-config')).ProviderPath
$annotator = $null
if ($OfflineScreenshot -or $SyntheticStream -or $ConsentedStream) {
    $annotator = (Resolve-Path -LiteralPath (Join-Path $root 'native\providers\mercury_offline\build-direct-release\mercury_direct_camera_eval.exe')).ProviderPath
}
if ($OfflineScreenshot) {
    $OfflineScreenshot = (Resolve-Path -LiteralPath $OfflineScreenshot).ProviderPath
}
if ($ConsentedStream) { $FfmpegPath = (Resolve-Path -LiteralPath $FfmpegPath).ProviderPath }
if (@(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue).Count) {
    throw 'SteamVR is running; simulator-only projection smoke aborted.'
}
$registration = 'HKLM:\SOFTWARE\Khronos\OpenXR\1'
$activeBefore = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
$old = @{}
foreach ($name in @('SIMULATED_ENABLE', 'XR_RUNTIME_JSON', 'LOCALAPPDATA')) {
    $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$exitCode = 1
try {
    [Environment]::SetEnvironmentVariable('SIMULATED_ENABLE', '1', 'Process')
    [Environment]::SetEnvironmentVariable('XR_RUNTIME_JSON', $manifest, 'Process')
    [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $config, 'Process')
    if ($OfflineScreenshot) { & $exe --offline-screenshot $OfflineScreenshot $annotator }
    elseif ($SyntheticStream) { & $exe --synthetic-stream $annotator }
    elseif ($ConsentedStream) { & $exe --consented-stream $CameraName $FfmpegPath $annotator }
    else { & $exe }
    $exitCode = $LASTEXITCODE
}
finally {
    foreach ($name in $old.Keys) {
        [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process')
    }
    $activeAfter = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
    if ($activeAfter -ne $activeBefore) {
        throw 'System OpenXR registration changed during simulator smoke.'
    }
}
if ($exitCode -ne 0) { throw "Simulator projection smoke failed with exit code $exitCode." }
Write-Output 'MONADO_SIM_PROJECTION_LAUNCH_PASS registration_unchanged=1 environment_restored=1'
