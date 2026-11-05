param(
    [string]$BlenderExe,
    [ValidateSet('opengl', 'vulkan')]
    [string]$Backend = 'opengl',
    [switch]$PreflightOnly,
    [switch]$SmokeTest,
    [switch]$AutoStartXR,
    [switch]$UsePersonalAddons,
    [switch]$GuiSmokeTest,
    [switch]$SteamVR,
    [switch]$SteamVRSmokeTest
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
if ($SteamVRSmokeTest -and (!$SteamVR -or !$AutoStartXR -or $SmokeTest -or $GuiSmokeTest -or $PreflightOnly)) {
    throw 'SteamVRSmokeTest requires the real SteamVR auto-start route, without other test modes.'
}
$env:XRWB_STEAMVR_SMOKE = if ($SteamVRSmokeTest) { '1' } else { '0' }
if ($GuiSmokeTest -and (!$UsePersonalAddons -or $SmokeTest -or $AutoStartXR)) {
    throw 'GuiSmokeTest requires UsePersonalAddons and cannot start hardware or a background smoke.'
}
$env:XRWB_AUTO_START_XR = if ($AutoStartXR) { '1' } else { '0' }
$env:XRWB_PERSONAL_ADDONS = if ($UsePersonalAddons) { '1' } else { '0' }
$env:XRWB_STEAMVR = if ($SteamVR) { '1' } else { '0' }
$cameraOverlayRequested = $env:XRWB_INDEX_CAMERA_OVERLAY -eq '1'
if ($SteamVR -and ($env:XRWB_STEAMVR_OPT_IN -ne '1' -or
    !$env:XR_RUNTIME_JSON -or $env:XRWB_PHYSICAL_INDEX_OPT_IN -eq '1' -or
    $cameraOverlayRequested -or $env:XRWB_INDEX_CAMERA_GESTURE -eq '1')) {
    throw 'SteamVR mode requires the separate camera-off SteamVR launcher.'
}
if ($AutoStartXR) {
    if ($SmokeTest -or (!$SteamVR -and $env:XRWB_PHYSICAL_INDEX_OPT_IN -ne '1') -or
        !$env:XR_RUNTIME_JSON -or $env:SIMULATED_ENABLE -or
        !$env:XRWB_AUTO_START_STATUS_PATH) {
        throw 'XR auto-start requires the opt-in physical launcher, isolated runtime, and status log.'
    }
    $physicalBlender = [IO.Path]::GetFullPath((Join-Path $projectRoot 'native\build-features\bin\blender-hands-test.exe'))
    if ($UsePersonalAddons -or $SteamVR) {
        $physicalBlender = [IO.Path]::GetFullPath((Join-Path $projectRoot 'native\build-features\bin\blender.exe'))
    }
    if ($BlenderExe -and [IO.Path]::GetFullPath($BlenderExe) -ine $physicalBlender) {
        throw 'XR auto-start requires the source-built full-feature Blender executable.'
    }
    $statusRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'reports\index-physical-launch'))
    if ($SteamVR) { $statusRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'reports\steamvr-launch')) }
    $statusPath = [IO.Path]::GetFullPath($env:XRWB_AUTO_START_STATUS_PATH)
    if (!$statusPath.StartsWith($statusRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'XR auto-start status log must stay inside the physical-launch reports directory.'
    }
    $env:LH_HANDTRACKING = 'OFF'
    $env:VIVE_SLAM = 'OFF'
    foreach ($name in @('XRWB_GESTURE_EXE', 'XRWB_GESTURE_CAMERA', 'XRWB_GESTURE_FFMPEG',
                       'XRWB_TASK_TEST_ALLOW')) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    if ($cameraOverlayRequested) {
        if ($env:XRWB_INDEX_CAMERA_CONSENT -ne '1' -or
            !$env:XRWB_INDEX_CAMERA_EVAL -or !$env:XRWB_INDEX_CAMERA_FFMPEG -or
            !$env:XRWB_INDEX_CAMERA_NAME) {
            throw 'Opt-in camera overlay requires exact consent and camera preflight values.'
        }
    }
}
# Ordinary launches are camera-off even if the separately installed test layer
# is enabled globally. Consented camera tests use their dedicated probe runner.
$env:DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough = '1'
$stockBlender = if ($env:CLAYSPACE_BLENDER_EXE) { $env:CLAYSPACE_BLENDER_EXE } elseif ($env:VRBLENDER_BLENDER_EXE) { $env:VRBLENDER_BLENDER_EXE } else { 'E:\BlenderBuildsWindows\stable\blender-5.1.2-stable.ec6e62d40fa9\blender.exe' }
$nativeBlender = $null
$nativeManifestPath = Join-Path $projectRoot 'native\runtime-manifest.json'
$nativeManifest = $null
if (Test-Path -LiteralPath $nativeManifestPath) {
    $nativeManifest = Get-Content -LiteralPath $nativeManifestPath -Raw | ConvertFrom-Json
    $nativeRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'native'))
    $nativeBlender = [IO.Path]::GetFullPath((Join-Path $nativeRoot $nativeManifest.executable))
    if (-not $nativeBlender.StartsWith($nativeRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Recorded native executable must stay inside this workspace native directory.'
    }
}
if (-not $BlenderExe) {
    if ($nativeManifest -and $Backend -eq 'opengl') { $BlenderExe = $nativeBlender }
    else { $BlenderExe = $stockBlender }
}
if (-not (Test-Path -LiteralPath $BlenderExe -PathType Leaf)) {
    throw "Blender 5.1.2 was not found: $BlenderExe. Supply -BlenderExe with the pinned build."
}
$BlenderExe = (Resolve-Path -LiteralPath $BlenderExe).Path
if ($AutoStartXR -and $BlenderExe -ine $physicalBlender) {
    throw 'XR auto-start requires the source-built full-feature Blender executable.'
}
$version = & $BlenderExe --version
if ($LASTEXITCODE -ne 0 -or $version[0] -ne 'Blender 5.1.2') {
    throw 'XR Workbench requires the specification baseline: Blender 5.1.2.'
}
$blenderHash = (Get-FileHash -LiteralPath $BlenderExe -Algorithm SHA256).Hash
if ($BlenderExe -eq $nativeBlender) {
    if (-not $nativeManifest -or $nativeManifest.status -ne 'camera-off-smoke-tested' -or
        $nativeManifest.source_commit -ne 'ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832' -or
        $nativeManifest.sha256 -ne $blenderHash) {
        throw 'The native build is not the recorded smoke-tested executable. Re-verify before launching.'
    }
    if ($Backend -ne 'opengl') { throw 'This native development build supports OpenGL only.' }
    Write-Host 'Native XR development build: OpenGL only, reduced optional features, optical hands not qualified.'
}
elseif ($AutoStartXR) { Write-Host "Opt-in source-built Blender 5.1.2 physical XR test; camera_overlay_requested=$([int]$cameraOverlayRequested), simulated input off." }
elseif ($BlenderExe -eq $stockBlender) { Write-Host 'Stock Blender diagnostic prototype: not the native fork.' }
else { Write-Host 'Custom Blender 5.1.2 comparison build: not the launcher-qualified runtime.' }
if ($BlenderExe -eq $stockBlender -and $blenderHash -ne 'A7D09B04DF8F78D432BC45D32C08F25387A78C76D12D2A6F5DE07D8E1066E8F8') {
    throw 'The baseline executable changed. Verify it before updating the recorded hash.'
}
if ($env:XR_RUNTIME_JSON) {
    Write-Host 'Process-scoped OpenXR runtime override active; Index display, tracking, and cameras are unverified.'
}
else {
    Write-Host 'Using the registered OpenXR runtime. No SteamVR-free Index runtime is qualified yet.'
}

$profileRoot = Join-Path $projectRoot $(if ($UsePersonalAddons) { '.xr_addons_profile' } else { '.xr_profile' })
if ($UsePersonalAddons -and !(Test-Path -LiteralPath (Join-Path $profileRoot 'xr_addons_profile_ready.json'))) {
    throw 'Prepare the private add-on snapshot before using -UsePersonalAddons.'
}
$locations = @{
    BLENDER_USER_RESOURCES = $profileRoot
    BLENDER_USER_CONFIG = (Join-Path $profileRoot 'config')
    BLENDER_USER_SCRIPTS = (Join-Path $profileRoot 'scripts')
    BLENDER_USER_DATAFILES = (Join-Path $profileRoot 'datafiles')
    BLENDER_USER_EXTENSIONS = (Join-Path $profileRoot 'extensions')
    TEMP = (Join-Path $profileRoot 'temp')
    TMP = (Join-Path $profileRoot 'temp')
}
foreach ($entry in $locations.GetEnumerator()) {
    New-Item -ItemType Directory -Path $entry.Value -Force | Out-Null
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
}
$addonTarget = Join-Path $locations.BLENDER_USER_SCRIPTS 'addons\xr_workbench'
New-Item -ItemType Directory -Path $addonTarget -Force | Out-Null
$addonSource = Join-Path $projectRoot 'xr_workbench'
$addonFiles = @(Get-ChildItem -LiteralPath $addonSource -File |
    Where-Object { $_.Extension -in @('.py', '.toml') })
if (-not ($addonFiles.Name -contains '__init__.py')) { throw 'XR Workbench source is incomplete.' }
foreach ($file in $addonFiles) {
    $destination = Join-Path $addonTarget $file.Name
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) {
        throw "Isolated copy mismatch: $($file.Name)"
    }
}
$env:XRWB_EXPECTED_INSTALL = $addonTarget
$env:XRWB_REPORT_PATH = Join-Path $projectRoot 'reports\xr-capabilities.json'
if ($PreflightOnly) {
    Write-Output "XRWB_PREFLIGHT_PASS version=5.1.2 backend=$Backend sha256=$blenderHash profile=$profileRoot files=$($addonFiles.Count)"
    exit 0
}

$arguments = @('--factory-startup', '--disable-autoexec', '--offline-mode', '--gpu-backend', $Backend, '--python-exit-code', '1')
if ($SteamVRSmokeTest) {
    $arguments += '--enable-event-simulate'
    $arguments += @('--python', (Join-Path $projectRoot 'scripts\xr_steamvr_smoke.py'))
}
elseif ($GuiSmokeTest) {
    $arguments += @('--python', (Join-Path $projectRoot 'scripts\xr_personal_addons_gui_smoke.py'))
}
elseif ($SmokeTest) {
    $arguments += '--background'
    if ($UsePersonalAddons) {
        $arguments += @('--python', (Join-Path $projectRoot 'scripts\xr_personal_addons.py'))
    }
    $arguments += @('--python', (Join-Path $projectRoot 'scripts\xr_workbench_smoke.py'))
}
else {
    $arguments += @('--python', (Join-Path $projectRoot 'scripts\xr_launch_bootstrap.py'))
}
if ($AutoStartXR) {
    Add-Content -LiteralPath $statusPath -Value "XRWB_ISOLATED_BOOTSTRAP_STARTED auto_start=1 camera_overlay_requested=$([int]$cameraOverlayRequested)"
    Write-Host 'Opening the isolated Blender 5.1.2 workbench; XR will start automatically when its window is ready.'
}
else {
    Write-Host 'Opening the Blender 5.1.2 XR capability workbench. Start XR explicitly from its panel.'
}
Write-Host "Build: $BlenderExe"
$logRoot = Join-Path $profileRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$env:XRWB_NATIVE_LOG_SESSION = [Guid]::NewGuid().ToString('N')
$env:XRWB_NATIVE_LOG = Join-Path $logRoot ($env:XRWB_NATIVE_LOG_SESSION + '.log')
. (Join-Path $projectRoot 'scripts\invoke_xr_text_process.ps1')
if ($AutoStartXR) {
    Add-Content -LiteralPath $statusPath -Value "XRWB_NATIVE_TEXT_LOG path=$env:XRWB_NATIVE_LOG"
}
$timeout = if ($SteamVRSmokeTest) { 180 } else { 0 }
$blenderExitCode = Invoke-XrLoggedProcess -Executable $BlenderExe -Arguments $arguments -LogPath $env:XRWB_NATIVE_LOG -TimeoutSeconds $timeout
if ($AutoStartXR) {
    Add-Content -LiteralPath $statusPath -Value "XRWB_BLENDER_EXIT exit_code=$blenderExitCode"
}
if ($SteamVRSmokeTest -and (!(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XRWB_STEAMVR_SMOKE_PASS ' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XRWB_CONTROLLER_FEATURES_PASS ' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XRWB_HAND_MENU_FEATURES_PASS ' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XRWB_SCENE_TRIGGER_FEATURES_PASS ' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XR_NATIVE_GESTURE_FEATURES_PASS ' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XR_COMMAND_CONTROLS_PASS ' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XR_OPERATOR_REPORT$' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^Warning: Another XR Workbench gesture is active$' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XR_WORKBENCH runtime_name=SteamVR/OpenXR$' -Quiet) -or
    !(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XR_WORKBENCH session_state=5$' -Quiet) -or
    (Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^Traceback |^XRWB_STEAMVR_SMOKE_FAIL ' -Quiet))) {
    throw "Actual SteamVR startup smoke failed; inspect $env:XRWB_NATIVE_LOG"
}
if ($GuiSmokeTest -and (!(Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^XRWB_PERSONAL_ADDONS_GUI_PASS ' -Quiet) -or
    (Select-String -LiteralPath $env:XRWB_NATIVE_LOG -Pattern '^Traceback ' -Quiet))) {
    throw "Personal add-on GUI smoke failed; inspect $env:XRWB_NATIVE_LOG"
}
if ($AutoStartXR) {
    if ($blenderExitCode -ne 0) { throw "Isolated Blender exited with code $blenderExitCode." }
    if ($SteamVR -and ((Select-String -LiteralPath $statusPath -Pattern '^XRWB_AUTO_START_(FAIL|EDITOR_UNAVAILABLE)' -Quiet) -or
        !(Select-String -LiteralPath $statusPath -Pattern '^XRWB_AUTO_START_(EDITOR_REQUESTED|PANELS_READY) ' -Quiet))) {
        throw 'SteamVR session or native panels did not start successfully; inspect the status and native logs.'
    }
    return
}
exit $blenderExitCode
