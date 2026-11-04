param([switch]$Launch, [switch]$CameraOverlay, [switch]$CameraGestureHud, [switch]$PersonalWorkspace)

# Default invocation is a static preflight. Only -Launch may query the physical
# display and start Blender; neither path creates the user-confirmation marker.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$blender = Join-Path $root 'native\build-features\bin\blender-hands-test.exe'
if ($PersonalWorkspace) { $blender = Join-Path $root 'native\build-features\bin\blender.exe' }
$runtimeBuild = Join-Path $root 'native\steamvr_free\monado-index-experimental-build'
$manifest = Join-Path $runtimeBuild 'openxr_monado-dev.json'
$runtimeDll = Join-Path $runtimeBuild 'src\xrt\targets\openxr\openxr_monado.dll'
$cache = Join-Path $runtimeBuild 'CMakeCache.txt'
$displayProbe = Join-Path $root 'native\providers\mercury_offline\build-direct-release\mercury_direct_camera_eval.exe'
$gestureProbe = Join-Path $root 'native\providers\mercury_offline\build-gesture-msvc\mercury_direct_camera_eval.exe'
$cameraProbe = Join-Path $root 'native\steamvr_free\index_camera_probe\index_camera_probe.ps1'
$workbench = Join-Path $root 'launch_xr_workbench.ps1'
# Root creates this file only after the user reports seeing the OpenXR stereo
# color test in both lenses. JSON fields: confirmation, runtime_sha256, and
# observed_at_utc (UTC ISO seconds, e.g. 2026-09-23T21:00:00Z).
$marker = Join-Path $root 'reports\index-openxr-color-pattern-confirmed.json'
$expectedBlender = '6870107F3CE7E0760A0175C176793C66453E31D45D260499E37026F0F9956795'
if ($PersonalWorkspace) { $expectedBlender = 'FBEF1148907F1A2EBB7D3A7CECE91DE206E5FEC5CBC10E94EDFBDBCB09A4C71C' }
$expectedRuntime = '86CC2FD522D82FBEBEFA61032C80853B3584AE65942CAE0D50C7E953C821359C'
$expectedDisplayProbe = '16E8C220030FCFD2F6D8F425FDF4E2B384B4A5B685E118CB940381D2C5A77F56'
$expectedGestureProbe = 'A7AAB2BB5B063085CD3EAD5BA1888778FDBD2795FE6B20A138B455C0B209BC90'
$expectedFfmpeg = '228D7A8556258DE907FDB55F36850078EBC7680B84EC30D84EA02E99BEC1D1EB'
$confirmation = 'INDEX_OPENXR_STEREO_COLOR_SEEN'

function Assert-ExactHash([string]$Path, [string]$Expected, [string]$Label) {
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
    if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash -ne $Expected) {
        throw "$Label hash changed; physical launch is not qualified."
    }
    return $resolved
}

function Test-ConfirmationData($Data, [string]$RuntimeHash) {
    try {
        if ($Data.confirmation -cne $confirmation -or
            $Data.runtime_sha256 -cne $RuntimeHash) {
            return $false
        }
        # PowerShell 7 converts ISO JSON timestamps to DateTime; Windows
        # PowerShell 5.1 leaves them as strings. Both must represent UTC.
        if ($Data.observed_at_utc -is [DateTime]) {
            if ($Data.observed_at_utc.Kind -ne [DateTimeKind]::Utc) { return $false }
            $observed = [DateTimeOffset]$Data.observed_at_utc
        }
        else {
            $stamp = [string]$Data.observed_at_utc
            if ($stamp -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') { return $false }
            $observed = [DateTimeOffset]::ParseExact(
                $stamp, 'yyyy-MM-ddTHH:mm:ssZ',
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeUniversal)
        }
        $age = [DateTimeOffset]::UtcNow - $observed
        return $age.TotalMinutes -ge 0 -and $age.TotalHours -le 24
    }
    catch { return $false }
}

function Test-Confirmation([string]$Path, [string]$RuntimeHash) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        $data = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        return Test-ConfirmationData $data $RuntimeHash
    }
    catch { return $false }
}

function Assert-IndexCameraProbeReport($Report) {
    if ($null -eq $Report -or
        $Report.steamvr_processes -ne 0 -or
        $Report.backend -cne 'Windows DirectShow' -or
        $Report.index_usb_id -cne 'VID_28DE&PID_2400' -or
        $Report.camera_detected -cne $true -or
        $Report.capture_attempted -cne $false -or
        $Report.capture_succeeded -cne $false -or
        $Report.frames -ne 0 -or
        $Report.image_output -cne 'none; FFmpeg null muxer' -or
        [string]$Report.camera_name -cnotmatch '^[\w -]+$') {
        throw 'Exact read-only Index USB/DirectShow camera probe failed; overlay was not enabled.'
    }
    return [string]$Report.camera_name
}

if ($CameraOverlay -and !$Launch) {
    throw 'CameraOverlay requires explicit -Launch; static preflight never queries the camera.'
}
if ($CameraGestureHud -and (!$Launch -or !$CameraOverlay)) {
    throw 'CameraGestureHud requires explicit -Launch -CameraOverlay.'
}

$blender = Assert-ExactHash $blender $expectedBlender 'Source-built Blender'
$runtimeDll = Assert-ExactHash $runtimeDll $expectedRuntime 'Experimental OpenXR runtime'
$displayProbe = Assert-ExactHash $displayProbe $expectedDisplayProbe 'Read-only Index display probe'
if ($CameraGestureHud) {
    $gestureProbe = Assert-ExactHash $gestureProbe $expectedGestureProbe 'Stereo hand evaluator'
}
$manifest = (Resolve-Path -LiteralPath $manifest -ErrorAction Stop).ProviderPath
$cache = (Resolve-Path -LiteralPath $cache -ErrorAction Stop).ProviderPath
$workbench = (Resolve-Path -LiteralPath $workbench -ErrorAction Stop).ProviderPath
$runtime = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if ($runtime.file_format_version -ne '1.0.0' -or
    ![IO.Path]::IsPathRooted([string]$runtime.runtime.library_path) -or
    [IO.Path]::GetFullPath([string]$runtime.runtime.library_path) -ine $runtimeDll) {
    throw 'The isolated OpenXR manifest does not point exactly to the pinned runtime DLL.'
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
        throw "Experimental runtime configuration changed: $setting"
    }
}
$confirmed = Test-Confirmation $marker $expectedRuntime
if (!$Launch) {
    [pscustomobject]@{
        preflight_only = $true
        blender_sha256 = $expectedBlender
        runtime_sha256 = $expectedRuntime
        display_probe_sha256 = $expectedDisplayProbe
        color_pattern_marker_valid = $confirmed
        static_files_valid = $true
        physical_display_check_pending = $true
        physical_display_queried = $false
        blender_started = $false
        camera_requested = $false
        global_runtime_changed = $false
    } | ConvertTo-Json -Depth 3
    return
}
if (!$confirmed) {
    throw "Physical launch requires a fresh $confirmation marker at $marker, created only after the user confirms seeing the OpenXR stereo color test in both lenses."
}
$conflicts = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup,monado-service -ErrorAction SilentlyContinue)
if ($conflicts.Count) { throw 'Stop SteamVR and Monado services before launching this independent runtime.' }

# This exact, hash-pinned mode only checks the active non-cloned VLV91A8 display.
# It does not open the Index camera or start a compositor.
$priorErrorAction = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $displayText = (& $displayProbe --headset-display-probe 2>&1 | Out-String)
    $displayExit = $LASTEXITCODE
}
finally { $ErrorActionPreference = $priorErrorAction }
if ($displayExit -ne 0 -or $displayText -notmatch '(?m)^XR_INDEX_DISPLAY_READY camera_accessed=0 steamvr=0\s*$') {
    throw 'The active, non-cloned Index VLV91A8 display guard failed; Blender was not launched.'
}
# Recheck after the display probe: a long-running user confirmation or runtime
# replacement cannot silently turn this into a different launch.
if (!(Test-Confirmation $marker $expectedRuntime) -or
    (Get-FileHash -LiteralPath $runtimeDll -Algorithm SHA256).Hash -ne $expectedRuntime) {
    throw 'Confirmation expired or runtime changed during preflight.'
}
$cameraName = $null
$ffmpeg = $null
if ($CameraOverlay) {
    $cameraProbe = (Resolve-Path -LiteralPath $cameraProbe -ErrorAction Stop).ProviderPath
    # Match the first PATH resolution used by index_camera_probe.ps1 itself.
    # More than one FFmpeg installation may be present on this machine.
    $ffmpegCommand = @(Get-Command ffmpeg -CommandType Application -ErrorAction Stop)[0]
    $ffmpeg = Assert-ExactHash $ffmpegCommand.Source $expectedFfmpeg 'DirectShow FFmpeg'
    if ([IO.Path]::GetFileName($ffmpeg) -cne 'ffmpeg.exe') {
        throw 'The pinned DirectShow FFmpeg executable was not resolved.'
    }
    if (@(Get-Command ffmpeg -ErrorAction Stop)[0].Source -ine $ffmpeg) {
        throw 'The camera probe would resolve a different FFmpeg executable.'
    }
    # No -ConsentedStream: this enumerates DirectShow devices but captures no
    # frames. Native overlay owns the short, explicitly consented capture.
    $cameraText = (& $cameraProbe 2>&1 | Out-String)
    # The probe's intentionally invalid FFmpeg dummy input returns nonzero
    # after listing devices, so LASTEXITCODE is not the probe script result.
    try { $cameraReport = $cameraText | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Read-only Index camera probe did not return valid JSON.' }
    $cameraName = Assert-IndexCameraProbeReport $cameraReport
    if ((Get-FileHash -LiteralPath $ffmpeg -Algorithm SHA256).Hash -ne $expectedFfmpeg -or
        (Get-FileHash -LiteralPath $displayProbe -Algorithm SHA256).Hash -ne $expectedDisplayProbe -or
        ($CameraGestureHud -and (Get-FileHash -LiteralPath $gestureProbe -Algorithm SHA256).Hash -ne $expectedGestureProbe) -or
        !(Test-Confirmation $marker $expectedRuntime)) {
        throw 'Camera evaluator, FFmpeg, or confirmation changed during overlay preflight.'
    }
}
$registration = 'HKLM:\SOFTWARE\Khronos\OpenXR\1'
$activeBefore = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
$localData = Join-Path $root 'native\steamvr_free\physical-blender-config'
$statusRoot = Join-Path $root 'reports\index-physical-launch'
New-Item -ItemType Directory -Path $localData,$statusRoot -Force | Out-Null
$statusPath = Join-Path $statusRoot ('launch-{0}-{1}.txt' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), [guid]::NewGuid().ToString('N').Substring(0, 8))
$status = @(
    'XR_PHYSICAL_INDEX_PREFLIGHT_PASS',
    "blender_sha256=$expectedBlender",
    "runtime_sha256=$expectedRuntime",
    'index_display_guard=pass',
    'stereo_color_user_confirmation=fresh',
    $(if ($CameraOverlay) { "camera_overlay_requested=1 gesture_hud=$([int][bool]$CameraGestureHud) seated_uncalibrated=1 capture_limit_seconds=300 slam=off monado_hand_tracking=off simulated=off" }
      else { 'camera=off slam=off monado_hand_tracking=off simulated=off' }),
    'xr_auto_start=armed headset_frame_verified=0'
)
[IO.File]::WriteAllLines($statusPath, [string[]]$status)
$launchVariables = @{
    XR_RUNTIME_JSON = $manifest
    XRT_COMPOSITOR_INDEX_EXTENDED = '1'
    LH_HANDTRACKING = 'OFF'
    VIVE_SLAM = 'OFF'
    DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough = '1'
    LOCALAPPDATA = $localData
    XRWB_PHYSICAL_INDEX_OPT_IN = '1'
    XRWB_AUTO_START_STATUS_PATH = $statusPath
    SIMULATED_ENABLE = $null
    XRWB_TASK_TEST_ALLOW = $null
    XRWB_GESTURE_EXE = $null
    XRWB_GESTURE_CAMERA = $null
    XRWB_GESTURE_FFMPEG = $null
    XRWB_INDEX_CAMERA_OVERLAY = $(if ($CameraOverlay) { '1' } else { $null })
    XRWB_INDEX_CAMERA_CONSENT = $(if ($CameraOverlay) { '1' } else { $null })
    XRWB_INDEX_CAMERA_EVAL = $(if ($CameraGestureHud) { $gestureProbe } elseif ($CameraOverlay) { $displayProbe } else { $null })
    XRWB_INDEX_CAMERA_GESTURE = $(if ($CameraGestureHud) { '1' } else { $null })
    XRWB_PHYSICAL_GESTURE_OPT_IN = $(if ($CameraGestureHud) { '1' } else { $null })
    XRWB_INDEX_CAMERA_FFMPEG = $(if ($CameraOverlay) { $ffmpeg } else { $null })
    XRWB_INDEX_CAMERA_NAME = $(if ($CameraOverlay) { $cameraName } else { $null })
    XRWB_TASK_CAPTURE_PATH = $null
    XRWB_TASK_CURSOR_CAPTURE_PATH = $null
    XRWB_EDITOR_EYE_CAPTURE_PATH = $null
    XRWB_EDITOR_CAPTURE_DIR = $null
}
$savedVariables = @{}
foreach ($name in $launchVariables.Keys) {
    $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $launchVariables[$name], 'Process')
}
foreach ($name in @('BLENDER_USER_RESOURCES', 'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS',
                   'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'TEMP', 'TMP',
                   'XRWB_EXPECTED_INSTALL', 'XRWB_REPORT_PATH', 'XRWB_NATIVE_LOG_SESSION',
                   'XRWB_NATIVE_LOG', 'XRWB_AUTO_START_XR', 'XRWB_PERSONAL_ADDONS', 'XRWB_STEAMVR')) {
    $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Write-Host "XR_PHYSICAL_INDEX_LAUNCHER_READY camera_overlay_requested=$([int][bool]$CameraOverlay) auto_start=1"
Write-Host "Status log: $statusPath"
if ($CameraOverlay) {
    Write-Host 'Seated, uncalibrated camera diagnostic only. Do not stand or walk; the feed blanks after five minutes.'
}
Write-Host 'Blender will remain open until you close it. No keyboard prompt is required.'
try {
    # A visible Explorer-started shell owns Blender. No hidden helper or forced
    # process kill: the workbench returns only when the user closes Blender.
    & $workbench -BlenderExe $blender -AutoStartXR -UsePersonalAddons:$PersonalWorkspace
    Add-Content -LiteralPath $statusPath -Value 'XR_PHYSICAL_INDEX_BLENDER_EXIT exit_code=0'
}
catch {
    if ($_.Exception.Message -match '^Isolated Blender exited with code (\d+)\.$') {
        Add-Content -LiteralPath $statusPath -Value "XR_PHYSICAL_INDEX_BLENDER_EXIT exit_code=$($matches[1])"
    }
    else {
        Add-Content -LiteralPath $statusPath -Value ('XR_PHYSICAL_INDEX_LAUNCH_FAIL reason=' + $_.Exception.GetType().Name)
    }
    throw
}
finally {
    foreach ($name in $savedVariables.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedVariables[$name], 'Process')
    }
    $activeAfter = (Get-ItemProperty -LiteralPath $registration -ErrorAction SilentlyContinue).ActiveRuntime
    $unchanged = [string]$activeBefore -ceq [string]$activeAfter
    Add-Content -LiteralPath $statusPath -Value "XR_PHYSICAL_INDEX_RUNTIME_REGISTRATION_UNCHANGED value=$([int]$unchanged)"
}
