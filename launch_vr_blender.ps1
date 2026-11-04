param(
    [switch]$NoAutoStart,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$blenderCandidates = @(
    $env:VRBLENDER_BLENDER_EXE,
    'E:\BlenderBuildsWindows\stable\blender-4.5.11-lts.4db51e9d1e1e\blender.exe',
    'H:\layeringtextures\.tools\blender-4.5.11-windows-x64\blender.exe'
)
$blenderExe = $blenderCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $blenderExe) {
    throw 'Blender 4.5.11 LTS was not found. Set VRBLENDER_BLENDER_EXE to your blender.exe.'
}
$expectedBlenderSha256 = '0949E462F677C3E341913A838C6E2F54CC1C811CCB6F281AE9B3FF5926A2B255'
$actualBlenderSha256 = (Get-FileHash -LiteralPath $blenderExe -Algorithm SHA256).Hash
if ($actualBlenderSha256 -ne $expectedBlenderSha256) {
    throw "The Blender executable does not match the verified 4.5.11 LTS build: $blenderExe"
}

$profileRoot = Join-Path $projectRoot '.vr_profile'
$configRoot = Join-Path $profileRoot 'config'
$scriptsRoot = Join-Path $profileRoot 'scripts'
$dataRoot = Join-Path $profileRoot 'datafiles'
$extensionsRoot = Join-Path $profileRoot 'extensions'
foreach ($path in @($configRoot, $scriptsRoot, $dataRoot, $extensionsRoot)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$addonSource = Join-Path $projectRoot 'vr_blender_workbench'
$addonTarget = Join-Path $scriptsRoot 'addons\vr_blender_workbench'
New-Item -ItemType Directory -Path $addonTarget -Force | Out-Null
$addonFiles = Get-ChildItem -LiteralPath $addonSource -File |
    Where-Object { $_.Extension -in @('.py', '.toml') }
$addonFiles | Copy-Item -Destination $addonTarget -Force
foreach ($sourceFile in $addonFiles) {
    $targetFile = Join-Path $addonTarget $sourceFile.Name
    $sourceHash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash
    $targetHash = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
    if ($sourceHash -ne $targetHash) {
        throw "Isolated add-on copy verification failed: $($sourceFile.Name)"
    }
}

$env:BLENDER_USER_CONFIG = $configRoot
$env:BLENDER_USER_SCRIPTS = $scriptsRoot
$env:BLENDER_USER_DATAFILES = $dataRoot
$env:BLENDER_USER_EXTENSIONS = $extensionsRoot
$env:VRW_AUTO_START = if ($NoAutoStart) { '0' } else { '1' }

$runtime = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -ErrorAction SilentlyContinue).ActiveRuntime
if ($runtime -notlike '*SteamVR*') {
    throw "SteamVR is not the active OpenXR runtime. Current: $runtime"
}
$steamVrMonitor = 'C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrmonitor.exe'
if (-not (Get-Process vrserver -ErrorAction SilentlyContinue)) {
    if (-not $PreflightOnly -and (Test-Path -LiteralPath $steamVrMonitor)) {
        Write-Host 'SteamVR is not running; starting it now.'
        Start-Process -FilePath $steamVrMonitor
        Start-Sleep -Seconds 3
    }
    else {
        Write-Warning 'SteamVR is not running. Power on the headset/controllers before starting the XR session.'
    }
}

if ($PreflightOnly) {
    Write-Host "VRW_LAUNCH_PREFLIGHT_PASS blender=$blenderExe sha256=$actualBlenderSha256 profile=$profileRoot addon=$addonTarget files=$($addonFiles.Count)"
    exit 0
}

Write-Host "Starting isolated VR Blender 4.5.11: $blenderExe"
Write-Host "Profile: $profileRoot"
Write-Host "Isolated add-on copy: $addonTarget"
& $blenderExe --python (Join-Path $projectRoot 'scripts\launch_bootstrap.py')
exit $LASTEXITCODE
