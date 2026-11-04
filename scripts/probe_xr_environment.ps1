[CmdletBinding()]
param(
    [switch]$IncludeSteamVrLogs,
    [switch]$IncludeSensitiveDetails,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

function Protect-Path([string]$Path) {
    if (-not $Path) { return $null }
    if ($IncludeSensitiveDetails) { return $Path }
    return '<redacted>/' + [IO.Path]::GetFileName($Path)
}

function Read-JsonFile([string]$Path) {
    if (-not $Path) { return $null }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not [IO.Path]::IsPathRooted($expanded)) { return $null }
    if (-not (Test-Path -LiteralPath $expanded -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $expanded -Raw | ConvertFrom-Json }
    catch { return $null }
}

function Get-RegistryValues([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $item = Get-Item -LiteralPath $Path
    foreach ($name in $item.GetValueNames()) {
        [pscustomobject]@{ Name = $name; Value = $item.GetValue($name) }
    }
}

function Get-BinaryEvidence([string]$Path) {
    if (-not $Path) { return $null }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not [IO.Path]::IsPathRooted($expanded)) { return $null }
    $exists = Test-Path -LiteralPath $expanded -PathType Leaf
    $version = $null
    if ($exists) { $version = (Get-Item -LiteralPath $expanded).VersionInfo.FileVersion }
    [ordered]@{
        name = [IO.Path]::GetFileName($expanded)
        path = Protect-Path $expanded
        exists = $exists
        file_version = $version
    }
}

$runtimeRegistry = 'HKLM:\SOFTWARE\Khronos\OpenXR\1'
$activeRuntimePath = if (Test-Path -LiteralPath $runtimeRegistry) {
    (Get-ItemProperty -LiteralPath $runtimeRegistry -Name ActiveRuntime -ErrorAction SilentlyContinue).ActiveRuntime
} else { $null }
$runtimeManifest = Read-JsonFile $activeRuntimePath
$runtimeLibrary = $null
if ($runtimeManifest -and $runtimeManifest.runtime.library_path) {
    $candidate = [Environment]::ExpandEnvironmentVariables([string]$runtimeManifest.runtime.library_path)
    if (-not [IO.Path]::IsPathRooted($candidate) -and $activeRuntimePath) {
        $candidate = Join-Path ([IO.Path]::GetDirectoryName($activeRuntimePath)) $candidate
    }
    $runtimeLibrary = $candidate
}

$layers = @()
foreach ($kind in @('Implicit', 'Explicit')) {
    $key = "HKLM:\SOFTWARE\Khronos\OpenXR\1\ApiLayers\$kind"
    foreach ($entry in Get-RegistryValues $key) {
        $manifest = Read-JsonFile ([string]$entry.Name)
        $loaderEnabled = $null
        if ($kind -eq 'Implicit' -and $entry.Value -is [int]) {
            $loaderEnabled = $entry.Value -eq 0
        }
        $layers += [ordered]@{
            kind = $kind.ToLowerInvariant()
            name = if ($manifest) { $manifest.api_layer.name } else { $null }
            registered = $true
            loader_registry_enabled = $loaderEnabled
            active = $null
            manifest_path = Protect-Path ([string]$entry.Name)
        }
    }
}

$gpu = @()
try {
    $gpu = @(Get-CimInstance Win32_VideoController -ErrorAction Stop | ForEach-Object {
        [ordered]@{
            name = $_.Name
            adapter_compatibility = $_.AdapterCompatibility
            driver_version = $_.DriverVersion
            driver_date = if ($_.DriverDate) { $_.DriverDate.ToUniversalTime().ToString('o') } else { $null }
            status = $_.Status
            pnp_device_id = if ($IncludeSensitiveDetails) { $_.PNPDeviceID } else { '<redacted>' }
            evidence_source = 'Win32_VideoController'
        }
    })
} catch {
    $videoKeys = @(Get-ChildItem -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Video\*\0000' -ErrorAction SilentlyContinue)
    $gpu = @($videoKeys | ForEach-Object {
        $properties = Get-ItemProperty -LiteralPath $_.PSPath
        if (-not $properties.DriverDesc) { return }
        [ordered]@{
            name = $properties.DriverDesc
            adapter_compatibility = $properties.ProviderName
            driver_version = $properties.DriverVersion
            driver_date = $properties.DriverDate
            status = $null
            pnp_device_id = $null
            evidence_source = 'Windows display-driver registry'
        }
    } | Sort-Object -Property name, driver_version -Unique)
}

$cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
$cmake = $null
if ($cmakeCommand) {
    $cmakeVersionLine = (& $cmakeCommand.Source --version 2>$null | Select-Object -First 1)
    $cmake = [ordered]@{
        name = 'cmake.exe'
        version = $cmakeVersionLine
        path = Protect-Path $cmakeCommand.Source
    }
}

$knownBlenderCandidates = @(
    $env:VRBLENDER_BLENDER_EXE,
    'E:\BlenderBuildsWindows\stable\blender-5.1.2-stable.ec6e62d40fa9\blender.exe',
    'H:\layeringtextures\.tools\blender-5.1.2-windows-x64\blender.exe'
)
$blenderBinaries = @($knownBlenderCandidates | Where-Object {
    $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
} | ForEach-Object { Get-BinaryEvidence $_ })

$steamVrClues = @()
if ($IncludeSteamVrLogs) {
    $steamRoots = @((Join-Path ${env:ProgramFiles(x86)} 'Steam'))
    $steamRegistry = Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue
    if ($steamRegistry.SteamPath) { $steamRoots += [string]$steamRegistry.SteamPath }
    $logCandidates = @($steamRoots | Select-Object -Unique | ForEach-Object {
        Join-Path $_ 'logs\vrserver.txt'
        Join-Path $_ 'logs\vrcompositor.txt'
    })
    $patterns = 'No connected devices|HmdNotFound|OpenXR|headset|driver|camera'
    foreach ($logPath in $logCandidates) {
        if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) { continue }
        $matches = @(Get-Content -LiteralPath $logPath -Tail 300 | Select-String -Pattern $patterns)
        $categories = @()
        if ($matches -match 'No connected devices|HmdNotFound') { $categories += 'headset_not_found' }
        if ($matches -match 'OpenXR') { $categories += 'openxr_mentioned' }
        if ($matches -match 'camera') { $categories += 'camera_mentioned' }
        if ($matches -match 'driver') { $categories += 'driver_mentioned' }
        if ($matches -match 'headset') { $categories += 'headset_mentioned' }
        $steamVrClues += [ordered]@{
            log_path = Protect-Path $logPath
            matching_line_count = $matches.Count
            categories = @($categories | Select-Object -Unique)
        }
    }
}

$report = [ordered]@{
    schema_version = '1.0.0'
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    privacy = [ordered]@{
        paths_redacted = -not $IncludeSensitiveDetails
        device_identifiers_redacted = -not $IncludeSensitiveDetails
        collector_camera_access_attempted = $false
        machine_state_changed = $false
    }
    cmake = $cmake
    gpu = $gpu
    blender_binaries = $blenderBinaries
    openxr = [ordered]@{
        runtime = [ordered]@{
            registration_status = if ($activeRuntimePath) { 'registered' } else { 'not_registered' }
            manifest_path = Protect-Path $activeRuntimePath
            manifest_readable = $null -ne $runtimeManifest
            name = if ($runtimeManifest) { $runtimeManifest.runtime.name } else { $null }
            library = Get-BinaryEvidence $runtimeLibrary
            active_session = $null
        }
        api_layers = $layers
    }
    steamvr_log_clues = $steamVrClues
}

$json = $report | ConvertTo-Json -Depth 8
if ($OutputPath) {
    $fullOutputPath = [IO.Path]::GetFullPath($OutputPath)
    $outputDirectory = [IO.Path]::GetDirectoryName($fullOutputPath)
    if ($outputDirectory -and -not (Test-Path -LiteralPath $outputDirectory)) {
        New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    }
    [IO.File]::WriteAllText(
        $fullOutputPath,
        $json + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
}
$json
