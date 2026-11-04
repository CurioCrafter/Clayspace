$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'runtime_selector.ps1'
$active = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Khronos\OpenXR\1' -ErrorAction Stop).ActiveRuntime
$status = (& $script -Manifest $active | ConvertFrom-Json)
if (!$status.library_exists -or !$status.selected_is_active -or $status.alternate_runtime_qualified) {
    throw 'Runtime discovery or qualification guard regressed.'
}
if ($status.launcher_pid) { throw 'Read-only inspection unexpectedly launched an app.' }
if (!$status.steamvr_path_detected) { throw 'The active SteamVR path was not identified.' }
$renamed = (& $script -Manifest (Join-Path $PSScriptRoot 'testdata\steamvr_named.json') | ConvertFrom-Json)
if (!$renamed.steamvr_path_detected) { throw 'SteamVR runtime name was not identified.' }
if ($status.index_display -ne 'unverified' -or $status.index_6dof -ne 'unverified') {
    throw 'The manifest inspection incorrectly qualified hardware.'
}
try {
    & $script -Manifest $active -Launch -ErrorAction Stop | Out-Null
    throw 'SteamVR launch was accepted as an alternate runtime.'
}
catch {
    if ($_.Exception.Message -eq 'SteamVR launch was accepted as an alternate runtime.') { throw }
    if ($_.Exception.Message -notmatch 'selected manifest points inside SteamVR') { throw }
}
try {
    & $script -Manifest (Join-Path $PSScriptRoot 'missing.json') -ErrorAction Stop | Out-Null
    throw 'Missing manifest was accepted.'
}
catch {
    if ($_.Exception.Message -eq 'Missing manifest was accepted.') { throw }
    if ($_.Exception.Message -notmatch 'Cannot find path|does not exist') { throw }
}
'RUNTIME_SELECTOR_TEST_PASS active=SteamVR read_only=1 launch=0 alternate_qualified=0'
