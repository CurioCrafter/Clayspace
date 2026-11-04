param(
    [switch]$Launch,
    [switch]$DisplayPatternSeen
)

$ErrorActionPreference = 'Stop'
$build = Join-Path $PSScriptRoot 'monado-index-experimental-build'
$manifest = Join-Path $build 'openxr_monado-dev.json'
$library = Join-Path $build 'src\xrt\targets\openxr\openxr_monado.dll'
$cache = Join-Path $build 'CMakeCache.txt'
$selector = Join-Path $PSScriptRoot 'runtime_selector.ps1'

foreach ($path in @($manifest, $library, $cache, $selector)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing experimental runtime file: $path" }
}
$data = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if ($data.file_format_version -ne '1.0.0' -or
    [IO.Path]::GetFullPath([string]$data.runtime.library_path) -ine [IO.Path]::GetFullPath($library)) {
    throw 'The experimental manifest does not point to its isolated DLL.'
}
$configured = Get-Content -LiteralPath $cache -Raw
foreach ($setting in @(
    'CMAKE_BUILD_TYPE:STRING=Release',
    'XRT_BUILD_DRIVER_VIVE:BOOL=ON',
    'XRT_VIVE_WINDOWS_EXPERIMENTAL:BOOL=ON',
    'XRT_FEATURE_SERVICE:BOOL=OFF',
    'XRT_FEATURE_STEAMVR_PLUGIN:BOOL=OFF'
)) {
    if ($configured -notmatch "(?m)^$([regex]::Escape($setting))`r?$") {
        throw "Unexpected experimental build setting: $setting"
    }
}
$info = & $selector -Manifest $manifest | ConvertFrom-Json
if (!$info.library_exists -or $info.steamvr_path_detected) {
    throw 'The selected runtime is missing or points into SteamVR.'
}

if ($Launch) {
    if (!$DisplayPatternSeen) { throw 'The Index application pattern must be seen in the headset before launching this unqualified runtime.' }
    $steam = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue)
    if ($steam.Count) { throw 'Stop SteamVR before launching this independent runtime.' }
    $previous = [Environment]::GetEnvironmentVariable('XRT_COMPOSITOR_INDEX_EXTENDED', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('XRT_COMPOSITOR_INDEX_EXTENDED', '1', 'Process')
        $info = & $selector -Manifest $manifest -Launch | ConvertFrom-Json
    }
    finally {
        [Environment]::SetEnvironmentVariable('XRT_COMPOSITOR_INDEX_EXTENDED', $previous, 'Process')
    }
}

[pscustomobject]@{
    manifest = $manifest
    library = $library
    library_sha256 = (Get-FileHash -LiteralPath $library -Algorithm SHA256).Hash
    vive_windows_driver_compiled = $true
    index_extended_display_opt_in = $true
    launched = [bool]$Launch
    launcher_pid = $info.launcher_pid
    physical_index_session_verified = $false
} | ConvertTo-Json -Depth 3
