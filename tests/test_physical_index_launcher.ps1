# Offline-only assertions. This test never creates a user confirmation marker,
# queries the physical display, starts Blender, or touches OpenXR registration.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$launcher = Join-Path $root 'scripts\launch_physical_index_blender.ps1'
$wrapper = Get-Content -LiteralPath (Join-Path $root 'Open Index Blender XR.cmd') -Raw
$physicalSource = Get-Content -LiteralPath $launcher -Raw
$bootstrapSource = Get-Content -LiteralPath (Join-Path $root 'scripts\xr_launch_bootstrap.py') -Raw
if ($wrapper -notmatch 'launch_physical_index_blender\.ps1" -Launch' -or
    $wrapper -notmatch 'for /L %%S in \(5,-1,1\)' -or
    $wrapper -match '(?i)\b(taskkill|pause)\b' -or
    $physicalSource -notmatch '& \$workbench -BlenderExe \$blender -AutoStartXR' -or
    $bootstrapSource -notmatch 'bpy\.app\.timers\.register\(_auto_start_timer\(\)') {
    throw 'One-click auto-start wiring or no-keyboard safety contract changed.'
}
$preflight = (. $launcher | ConvertFrom-Json)
if (!$preflight.preflight_only -or $preflight.physical_display_queried -or
    $preflight.blender_started -or $preflight.camera_requested) {
    throw 'Static preflight unexpectedly requested physical resources.'
}
$personalPreflight = (& $launcher -PersonalWorkspace | ConvertFrom-Json)
if (!$personalPreflight.static_files_valid -or $personalPreflight.physical_display_queried -or
    $personalPreflight.blender_started -or $personalPreflight.camera_requested) {
    throw 'Personal workspace preflight must remain hardware-free.'
}
$runtimeHash = [string]$preflight.runtime_sha256
$valid = [pscustomobject]@{
    confirmation = 'INDEX_OPENXR_STEREO_COLOR_SEEN'
    runtime_sha256 = $runtimeHash
    observed_at_utc = [DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('yyyy-MM-ddTHH:mm:ssZ')
}
if (!(Test-ConfirmationData $valid $runtimeHash)) { throw 'Fresh exact confirmation was rejected.' }
$valid.observed_at_utc = [DateTimeOffset]::UtcNow.AddHours(-25).ToString('yyyy-MM-ddTHH:mm:ssZ')
if (Test-ConfirmationData $valid $runtimeHash) { throw 'Stale confirmation was accepted.' }
$valid.observed_at_utc = [DateTimeOffset]::UtcNow.AddMinutes(1).ToString('yyyy-MM-ddTHH:mm:ssZ')
if (Test-ConfirmationData $valid $runtimeHash) { throw 'Future confirmation was accepted.' }
$valid.observed_at_utc = [DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('yyyy-MM-ddTHH:mm:ssZ')
$valid.runtime_sha256 = '0' * 64
if (Test-ConfirmationData $valid $runtimeHash) { throw 'Wrong runtime confirmation was accepted.' }
$valid.runtime_sha256 = $runtimeHash
$valid.confirmation = 'INDEX_WINDOWS_PATTERN_SEEN'
if (Test-ConfirmationData $valid $runtimeHash) { throw 'Wrong test confirmation was accepted.' }
if (!(Test-Path -LiteralPath $marker)) {
    try {
        & $launcher -Launch | Out-Null
        throw 'Physical launch unexpectedly accepted a missing confirmation marker.'
    }
    catch {
        if ($_.Exception.Message -notmatch 'requires a fresh INDEX_OPENXR_STEREO_COLOR_SEEN marker') {
            throw
        }
    }
}
'XR_PHYSICAL_INDEX_LAUNCHER_TEST_PASS static_preflight=1 marker_parser=1 stale_rejected=1 wrong_runtime_rejected=1 explorer_autostart=1 physical_launch=0'
