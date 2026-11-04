param([switch]$Launch, [switch]$CleanProfile, [switch]$SmokeTest,
      [ValidateSet('AUTO', 'INDEX', 'QUEST')][string]$ControllerProfile = 'AUTO')

$ErrorActionPreference = 'Stop'
if ($SmokeTest -and !$Launch) { throw 'SmokeTest requires -Launch; default preflight never starts hardware.' }

function Assert-XrBlenderProfileFree([string]$Blender) {
    $processErrors = @()
    $running = @(Get-Process -Name blender, blender-hands-test -ErrorAction SilentlyContinue -ErrorVariable processErrors)
    if (@($processErrors | Where-Object { $_.FullyQualifiedErrorId -notlike 'NoProcessFoundForGivenName*' }).Count) {
        throw 'Cannot inspect running Blender processes safely. No process was stopped and no Blender was started.'
    }
    foreach ($process in $running) {
        try { $path = $process.Path }
        catch { $path = $null }
        if (!$path) {
            throw "Cannot read the executable path for Blender PID $($process.Id). Close that instance before starting this isolated profile; no process was stopped."
        }
        if ([IO.Path]::GetDirectoryName($path) -ieq [IO.Path]::GetDirectoryName($Blender)) {
            throw 'A project source-built Blender is already running. Save and close it before reusing its isolated profile; normal Blender windows are left alone.'
        }
    }
}

$root = Split-Path $PSScriptRoot -Parent
$blender = Join-Path $root 'native\build-features\bin\blender.exe'
$workbench = Join-Path $root 'launch_xr_workbench.ps1'
$registration = 'HKLM:\SOFTWARE\Khronos\OpenXR\1'
$registeredRuntime = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
$steamRoot = Join-Path ${env:ProgramFiles(x86)} 'Steam'
$candidates = @($registeredRuntime, (Join-Path $steamRoot 'steamapps\common\SteamVR\steamxr_win64.json'))
$manifest = $null
$runtimeLibrary = $null
$statusPath = $null
$savedVariables = @{}
$transcribing = $false

try {
    if ($Launch) {
        $logRoot = Join-Path $root 'reports\steamvr-launch'
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        $runId = 'launch-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), [guid]::NewGuid().ToString('N').Substring(0, 8)
        $statusPath = Join-Path $logRoot ($runId + '.txt')
        Start-Transcript -LiteralPath (Join-Path $logRoot ($runId + '-console.txt')) | Out-Null
        $transcribing = $true
        Add-Content -LiteralPath $statusPath -Value 'XRWB_STEAMVR_LAUNCH_BEGIN camera_requested=0 physical_tracking_verified=0'
    }
    foreach ($candidate in $candidates) {
        if (!$candidate -or !(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try { $data = Get-Content -LiteralPath $candidate -Raw | ConvertFrom-Json }
        catch { continue }
        if ($data.runtime.VALVE_runtime_is_steamvr -ne $true -or (Split-Path $candidate -Leaf) -ine 'steamxr_win64.json') { continue }
        $runtimeRoot = Split-Path ([IO.Path]::GetFullPath($candidate)) -Parent
        $library = [IO.Path]::GetFullPath((Join-Path $runtimeRoot $data.runtime.library_path))
        if (!$library.StartsWith($runtimeRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
            !(Test-Path -LiteralPath $library -PathType Leaf)) { continue }
        $manifest = [IO.Path]::GetFullPath($candidate)
        $runtimeLibrary = $library
        break
    }
    if (!$manifest) { throw 'Installed 64-bit SteamVR OpenXR runtime was not found. Install or repair SteamVR first.' }
    if (!(Test-Path -LiteralPath $blender -PathType Leaf)) { throw "Source-built Blender 5.1.2 is missing: $blender" }
    # Personal add-ons are optional compatibility, never a runtime dependency.
    $usePersonalAddons = !$CleanProfile -and (Test-Path -LiteralPath (Join-Path $root '.xr_addons_profile\xr_addons_profile_ready.json'))
    $configuration = Join-Path $steamRoot 'config\steamvr.vrsettings'
    $directModeDisabled = $false
    if (Test-Path -LiteralPath $configuration) {
        $settings = Get-Content -LiteralPath $configuration -Raw | ConvertFrom-Json
        $directModeDisabled = $settings.direct_mode.enable -eq $false
    }
    $blenderHash = (Get-FileHash -LiteralPath $blender -Algorithm SHA256).Hash
    $runtimeHash = (Get-FileHash -LiteralPath $runtimeLibrary -Algorithm SHA256).Hash
    if (!$Launch) {
        [pscustomobject]@{
            preflight_only = $true; blender = $blender; blender_sha256 = $blenderHash
            runtime = $manifest; runtime_sha256 = $runtimeHash
            camera_requested = $false; steamvr_started = $false; blender_started = $false
            physical_tracking_verified = $false; direct_mode_disabled = $directModeDisabled
            personal_addons = $usePersonalAddons; registration_changed = $false
            controller_profile = $ControllerProfile
        } | ConvertTo-Json
        return
    }
    Add-Content -LiteralPath $statusPath -Value @("blender_sha256=$blenderHash", "runtime_sha256=$runtimeHash", "runtime=$manifest", "direct_mode_disabled=$([int]$directModeDisabled)", "controller_profile=$ControllerProfile")
    if ($directModeDisabled -and $ControllerProfile -ne 'QUEST') {
        throw 'SteamVR Direct Display Mode is disabled. In SteamVR Settings > Developer choose Enable Direct Display Mode, restart SteamVR, then run this launcher again. This replaces the extended-display camera route; no display setting has been changed automatically.'
    }
    if (Get-Process -Name monado-service -ErrorAction SilentlyContinue) {
        throw 'Close the separate Monado test before starting the SteamVR version. No process was stopped.'
    }
    Assert-XrBlenderProfileFree $blender

    # Scope the runtime to this child. Never edit Windows OpenXR registration,
    # SteamVR preferences, Chaperone, or the user's normal Blender profile.
    $variables = @{
        XR_RUNTIME_JSON = $manifest
        XRWB_STEAMVR_OPT_IN = '1'
        XRWB_CONTROLLER_PROFILE = $ControllerProfile
        XRWB_PHYSICAL_INDEX_OPT_IN = $null
        XRWB_AUTO_START_STATUS_PATH = $statusPath
        DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough = '1'
        LH_HANDTRACKING = 'OFF'; VIVE_SLAM = 'OFF'
        SIMULATED_ENABLE = $null; XRT_COMPOSITOR_INDEX_EXTENDED = $null
        XRWB_TASK_TEST_ALLOW = $null; XRWB_EDITOR_TEST_ALLOW = $null
        XRWB_GESTURE_EXE = $null; XRWB_GESTURE_CAMERA = $null; XRWB_GESTURE_FFMPEG = $null
        XRWB_INDEX_CAMERA_OVERLAY = $null; XRWB_INDEX_CAMERA_GESTURE = $null
        XRWB_INDEX_CAMERA_CONSENT = $null; XRWB_PHYSICAL_GESTURE_OPT_IN = $null
        XRWB_INDEX_CAMERA_EVAL = $null; XRWB_INDEX_CAMERA_FFMPEG = $null; XRWB_INDEX_CAMERA_NAME = $null
        XRWB_TASK_CAPTURE_PATH = $null; XRWB_TASK_CURSOR_CAPTURE_PATH = $null
        XRWB_EDITOR_EYE_CAPTURE_PATH = $null; XRWB_EDITOR_CAPTURE_DIR = $null
    }
    foreach ($name in $variables.Keys) {
        $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $variables[$name], 'Process')
    }
    foreach ($name in @('BLENDER_USER_RESOURCES', 'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS',
        'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP', 'XRWB_EXPECTED_INSTALL',
        'XRWB_REPORT_PATH', 'XRWB_NATIVE_LOG_SESSION', 'XRWB_NATIVE_LOG', 'XRWB_AUTO_START_XR',
        'XRWB_PERSONAL_ADDONS', 'XRWB_STEAMVR', 'XRWB_STEAMVR_SMOKE')) {
        $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    if (!(Get-Process -Name vrserver -ErrorAction SilentlyContinue)) {
        $monitor = Join-Path (Split-Path $manifest -Parent) 'bin\win64\vrmonitor.exe'
        if (!(Test-Path -LiteralPath $monitor -PathType Leaf)) { throw 'SteamVR monitor executable is missing.' }
        Start-Process -FilePath $monitor -WindowStyle Hidden
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while (!(Get-Process -Name vrserver -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 500
        }
        if (!(Get-Process -Name vrserver -ErrorAction SilentlyContinue)) { throw 'SteamVR did not start within 30 seconds. Open it from Steam and check its error message.' }
    }
    Write-Host "SteamVR controllers: $ControllerProfile. Either trigger clicks/edits; right A opens one menu on the right hand."
    Write-Host 'Left stick click shows/closes desktop. Right stick click opens Settings. Origin tools are on Edit. Index left pad teleports.'
    Write-Host 'Use room-scale only after SteamVR Room Setup and green tracked headset/controllers. Keep its boundary enabled.'
    Write-Host "Status log: $statusPath"
    & $workbench -BlenderExe $blender -AutoStartXR -SteamVR -UsePersonalAddons:$usePersonalAddons -SteamVRSmokeTest:$SmokeTest
    Add-Content -LiteralPath $statusPath -Value 'XRWB_STEAMVR_LAUNCH_END blender_exit=0 physical_tracking_verified=0'
}
catch {
    $failure = 'XRWB_STEAMVR_LAUNCH_FAIL ' + $_.Exception.Message
    # Print before rethrow/finally so the transcript contains the actual error too.
    Write-Host $failure -ForegroundColor Red
    if ($statusPath) { Add-Content -LiteralPath $statusPath -Value $failure }
    throw
}
finally {
    foreach ($name in $savedVariables.Keys) { [Environment]::SetEnvironmentVariable($name, $savedVariables[$name], 'Process') }
    if ($statusPath) {
        $after = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
        Add-Content -LiteralPath $statusPath -Value "XRWB_STEAMVR_REGISTRATION_UNCHANGED value=$([int]([string]$after -ceq [string]$registeredRuntime))"
    }
    if ($transcribing) { Stop-Transcript | Out-Null }
}
