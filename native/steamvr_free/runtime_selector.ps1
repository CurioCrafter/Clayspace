param(
    [string]$Manifest,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'

function Get-RuntimeManifestInfo([string]$Path) {
    if (!$Path) { throw 'No OpenXR runtime manifest was supplied or registered.' }
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
    if ([IO.Path]::GetExtension($resolved) -ine '.json') { throw 'Runtime manifest must be a JSON file.' }
    $data = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json
    if ($data.file_format_version -ne '1.0.0' -or !$data.runtime.library_path) {
        throw 'Unsupported or incomplete OpenXR runtime manifest.'
    }
    $library = [string]$data.runtime.library_path
    if (![IO.Path]::IsPathRooted($library)) {
        $library = [IO.Path]::GetFullPath((Join-Path (Split-Path $resolved) $library))
    }
    $exists = Test-Path -LiteralPath $library -PathType Leaf
    $steam = ($resolved -match '(?i)[\\/]SteamVR[\\/]') -or
        ($library -match '(?i)[\\/]SteamVR[\\/]') -or
        ([IO.Path]::GetFileName($library) -imatch '^vrclient(_x64)?\.dll$') -or
        ([string]$data.runtime.name -match '(?i)steam\s*vr')
    [pscustomobject]@{
        manifest = $resolved
        runtime_name = [string]$data.runtime.name
        library = $library
        library_exists = [bool]$exists
        steamvr_path_detected = [bool]$steam
    }
}

$active = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -ErrorAction SilentlyContinue).ActiveRuntime
if (!$Manifest) { $Manifest = [string]$active }
$info = Get-RuntimeManifestInfo $Manifest
$result = [ordered]@{
    manifest = $info.manifest
    runtime_name = $info.runtime_name
    library = $info.library
    library_exists = $info.library_exists
    active_windows_runtime = [string]$active
    selected_is_active = ($info.manifest -ieq [string]$active)
    steamvr_path_detected = $info.steamvr_path_detected
    alternate_runtime_qualified = $false
    index_display = 'unverified'
    index_6dof = 'unverified'
    index_camera = 'unverified'
    launcher_pid = $null
}

if ($Launch) {
    if ($info.steamvr_path_detected) { throw 'This launcher is for an alternate runtime; selected manifest points inside SteamVR.' }
    if (!$info.library_exists) { throw 'The selected runtime library does not exist.' }
    $launcher = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\launch_xr_workbench.ps1') -ErrorAction Stop).ProviderPath
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $start.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"'
    $start.WorkingDirectory = Split-Path $launcher
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.EnvironmentVariables['XR_RUNTIME_JSON'] = $info.manifest
    # The installed Rectus layer consumes SteamVR. Disable it in this child only.
    $start.EnvironmentVariables['DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough'] = '1'
    $process = [Diagnostics.Process]::Start($start)
    if (!$process) { throw 'Isolated Blender launcher did not start.' }
    $result.launcher_pid = $process.Id
}

[pscustomobject]$result | ConvertTo-Json -Depth 3
