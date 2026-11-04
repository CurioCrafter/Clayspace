param(
    [ValidateSet('session', 'soak', 'gui', 'editor', 'hand-menu', 'task', 'editing', 'hands')][string]$Mode = 'session',
    [string]$BlenderExe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'native\releases\m0-camera-off\blender.exe'),
    [ValidateSet('opengl', 'vulkan')][string]$Backend = 'opengl',
    [ValidateRange(1, 1000)][int]$Cycles = 1,
    [switch]$CameraConsent,
    [switch]$LoaderDebug,
    [switch]$RequireVisible
)
$ErrorActionPreference = 'Stop'
if ($CameraConsent -and $Mode -ne 'session') { throw 'Camera probes cannot take GUI screenshots or run the camera-off soak.' }
$projectRoot = Split-Path $PSScriptRoot -Parent
$runId = [Guid]::NewGuid().ToString('N')
$probeRoot = Join-Path $projectRoot ('.tmp_xr_probe_' + $runId)
$scriptName = switch ($Mode) {
    'session' { 'xr_session_smoke.py' }
    'soak' { 'xr_acceptance.py' }
    'gui' { 'xr_gui_lifecycle_smoke.py' }
    'editor' { '..\native\test_editor_surface.py' }
    'hand-menu' { '..\native\test_hand_menu_surface.py' }
    'task' { '..\native\test_task_card.py' }
    'editing' { 'xr_editing_smoke.py' }
    'hands' { 'xr_hands_smoke.py' }
}
$locations = @{
    BLENDER_USER_RESOURCES = $probeRoot
    BLENDER_USER_CONFIG = (Join-Path $probeRoot 'config')
    BLENDER_USER_SCRIPTS = (Join-Path $projectRoot '.xr_profile\scripts')
    BLENDER_USER_EXTENSIONS = (Join-Path $probeRoot 'extensions')
    BLENDER_USER_DATAFILES = (Join-Path $probeRoot 'datafiles')
    TEMP = (Join-Path $probeRoot 'temp')
    TMP = (Join-Path $probeRoot 'temp')
    XRWB_TEST_OUTPUT = (Join-Path $probeRoot 'output')
}
foreach ($entry in $locations.GetEnumerator()) {
    New-Item -ItemType Directory -Path $entry.Value -Force | Out-Null
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
}
$env:XRWB_EXPECTED_INSTALL = Join-Path $locations.BLENDER_USER_SCRIPTS 'addons\xr_workbench'
$env:XRWB_REPORT_PATH = Join-Path $probeRoot 'capabilities.json'
$env:XRWB_NATIVE_LOG = Join-Path $probeRoot 'stdout.log'
$env:XRWB_NATIVE_LOG_SESSION = $runId
$env:XRWB_SOAK_OUTPUT = Join-Path $probeRoot 'soak.json'
$env:XRWB_SOAK_CYCLES = "$Cycles"
$env:XRWB_REQUIRE_RUNNING = '1'
$env:XRWB_REQUIRE_CONTINUOUS_DRAW = if ($RequireVisible) { '1' } else { '0' }
$env:XRWB_CAMERA_TEST_CONSENT = if ($CameraConsent) { '1' } else { '0' }
$env:XRWB_EDITOR_TEST_ALLOW = if ($Mode -in @('editor', 'hand-menu')) { '1' } else { '0' }
$env:XRWB_HAND_MENU_SIMULATOR = if ($Mode -eq 'hand-menu') { '1' } else { '0' }
$env:XRWB_TASK_TEST_ALLOW = if ($Mode -in @('task', 'editor', 'hand-menu')) { '1' } else { '0' }
$env:XRWB_TASK_CAPTURE_PATH = if ($Mode -eq 'task') { Join-Path $locations.XRWB_TEST_OUTPUT 'native-task-eye.png' } else { '' }
$env:XRWB_EDITOR_CAPTURE_DIR = if ($Mode -eq 'editor') { $locations.XRWB_TEST_OUTPUT } else { '' }
[Environment]::SetEnvironmentVariable('DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough', $(if($CameraConsent){$null}else{'1'}), 'Process')
[Environment]::SetEnvironmentVariable('XR_LOADER_DEBUG', $(if($LoaderDebug){'all'}else{$null}), 'Process')
$arguments = @('--factory-startup', '--offline-mode', '--gpu-backend', $Backend, '--python-exit-code', '1', '--python', (Join-Path $PSScriptRoot $scriptName))
if ($Mode -eq 'hand-menu') { $arguments = @('--enable-event-simulate') + $arguments }
if ($Mode -eq 'hands') { $arguments = @('--background') + $arguments }
if ($LoaderDebug) { $arguments += '--debug-xr' }
if ($Mode -eq 'editing') { $arguments += @('--', '--staged-smoke') }
$probe = Start-Process -FilePath $BlenderExe -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput $env:XRWB_NATIVE_LOG -RedirectStandardError (Join-Path $probeRoot 'stderr.log') -PassThru
[pscustomobject]@{ pid=$probe.Id; run_directory=$probeRoot; camera_requested=[bool]$CameraConsent; mode=$Mode } | ConvertTo-Json
