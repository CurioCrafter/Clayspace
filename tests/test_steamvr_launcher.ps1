# Offline checks: no display changes, runtime starts, cameras or scene writes.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $root 'scripts\launch_steamvr_blender.ps1'
foreach ($script in @($launcher, (Join-Path $root 'launch_xr_workbench.ps1'))) {
    $parseErrors = $null
    $parsed = [Management.Automation.Language.Parser]::ParseFile($script, [ref]$null, [ref]$parseErrors)
    if ($parseErrors) { throw "PowerShell parse failed: $parseErrors" }
    if ($script -eq $launcher) { $launcherAst = $parsed }
}
function Start-Process { throw 'Test forbids process launches' }
$preflight = & $launcher | ConvertFrom-Json
if (!$preflight.preflight_only -or $preflight.blender_started -or $preflight.steamvr_started -or
    $preflight.camera_requested -or $preflight.physical_tracking_verified -or $preflight.registration_changed -or
    $preflight.personal_addons -ne (Test-Path -LiteralPath (Join-Path $root '.xr_addons_profile\xr_addons_profile_ready.json')) -or
    $preflight.runtime -notlike '*steamxr_win64.json') {
    throw 'SteamVR preflight contract failed.'
}
if ((& $launcher -CleanProfile | ConvertFrom-Json).personal_addons) { throw 'CleanProfile did not select the diagnostic profile.' }
function Test-Path {
    param([string]$LiteralPath, [string]$PathType)
    if ($LiteralPath -like '*xr_addons_profile_ready.json') { return $false }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
try {
    if ((& $launcher | ConvertFrom-Json).personal_addons) {
        throw 'A fresh checkout still requires personal add-ons.'
    }
}
finally { Remove-Item Function:\Test-Path }
if ((& $launcher -ControllerProfile QUEST | ConvertFrom-Json).controller_profile -ne 'QUEST') {
    throw 'Quest controller profile preflight was not preserved.'
}
try {
    & $launcher -SmokeTest | Out-Null
    throw 'SmokeTest was accepted without Launch.'
}
catch { if ($_.Exception.Message -notlike 'SmokeTest requires -Launch*') { throw } }
# Force only the display config read; an unsafe fallthrough hits forbidden Start-Process.
function Get-Content {
    param([string]$LiteralPath, [switch]$Raw)
    if ($LiteralPath -like '*steamvr.vrsettings') { return '{"direct_mode":{"enable":false}}' }
    Microsoft.PowerShell.Management\Get-Content @PSBoundParameters
}
function Start-Transcript {
    param([string]$LiteralPath)
    $global:xrLauncherTestTranscript = $LiteralPath
    Microsoft.PowerShell.Host\Start-Transcript @PSBoundParameters
}
try {
    & $launcher -Launch | Out-Null
    throw 'Extended display mode was accepted.'
}
catch {
    if ($_.Exception.Message -notlike 'SteamVR Direct Display Mode is disabled.*') { throw }
}
finally { Remove-Item Function:\Get-Content; Remove-Item Function:\Start-Process; Remove-Item Function:\Start-Transcript }
$log = Get-Content -LiteralPath ($global:xrLauncherTestTranscript -replace '-console.txt$', '.txt') -Raw
if ($log -notmatch 'XRWB_STEAMVR_LAUNCH_FAIL' -or $log -notmatch 'REGISTRATION_UNCHANGED value=1') {
    throw 'Failure was not logged or runtime registration changed.'
}
$console = Get-Content -LiteralPath $global:xrLauncherTestTranscript -Raw
Remove-Variable -Name xrLauncherTestTranscript -Scope Global
if ($console -notmatch 'XRWB_STEAMVR_LAUNCH_FAIL SteamVR Direct Display Mode is disabled\.') {
    throw 'Console transcript omitted the actual failure reason.'
}

# Execute the actual guard with process records; never open or stop a process.
$guard = $launcherAst.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-XrBlenderProfileFree'
}, $true)
if (!$guard) { throw 'Process guard function missing.' }
. ([ScriptBlock]::Create($guard.Extent.Text))
function Get-Process {
    [CmdletBinding()]param([string[]]$Name)
    if (($Name -join ',') -ne 'blender,blender-hands-test') { throw 'Process guard queried unrelated applications.' }
    $script:fakeProcesses
}
$blender = Join-Path $root 'native\build-features\bin\blender.exe'
try {
    $script:fakeProcesses = @()
    Assert-XrBlenderProfileFree $blender
    $script:fakeProcesses = @([pscustomobject]@{Id = 12; Path = 'C:\StockBlender\blender.exe'})
    Assert-XrBlenderProfileFree $blender
    foreach ($case in @(
        @{ Path = $blender; Message = 'A project source-built Blender is already running.*' },
        @{ Path = (Join-Path (Split-Path $blender) 'blender-hands-test.exe'); Message = 'A project source-built Blender is already running.*' },
        @{ Path = $null; Message = 'Cannot read the executable path for Blender PID*' }
    )) {
        $script:fakeProcesses = @([pscustomobject]@{Id = 42; Path = $case.Path})
        try { Assert-XrBlenderProfileFree $blender; throw 'Unsafe Blender process was accepted.' }
        catch { if ($_.Exception.Message -notlike $case.Message) { throw } }
    }
}
finally { Remove-Item Function:\Get-Process; Remove-Item Function:\Assert-XrBlenderProfileFree }

# Execute the real common-launch invocation against a parameter-only stub.
$dispatch = $launcherAst.Find({ param($node)
    $node -is [Management.Automation.Language.CommandAst] -and $node.CommandElements[0].Extent.Text -eq '$workbench'
}, $true)
if (!$dispatch) { throw 'Common launcher dispatch missing.' }
function Test-WorkbenchBinding {
    param([string]$BlenderExe, [switch]$AutoStartXR, [switch]$SteamVR,
          [switch]$UsePersonalAddons, [switch]$SteamVRSmokeTest)
    [pscustomobject]@{Binary=$BlenderExe; Auto=$AutoStartXR.IsPresent; SteamVR=$SteamVR.IsPresent;
        Personal=$UsePersonalAddons.IsPresent; Smoke=$SteamVRSmokeTest.IsPresent}
}
$workbench = 'Test-WorkbenchBinding'
$invoke = [ScriptBlock]::Create($dispatch.Extent.Text)
foreach ($usePersonalAddons in @($false, $true)) {
    foreach ($SmokeTest in @($false, $true)) {
        $bound = & $invoke
        if ($bound.Binary -ne $blender -or !$bound.Auto -or !$bound.SteamVR -or
            $bound.Personal -ne $usePersonalAddons -or $bound.Smoke -ne $SmokeTest) {
            throw 'Common launcher ordinary/smoke arguments were not preserved.'
        }
    }
}
Remove-Item Function:\Test-WorkbenchBinding
'XRWB_STEAMVR_LAUNCHER_TEST_PASS preflight=1 direct_mode_guard=1 failure_log=1 process_guard=1 smoke_binding=1 hardware_started=0'
