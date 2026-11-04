param(
    [ValidateSet('Status', 'InstallDisabled', 'InstallEnabled', 'Enable', 'Disable', 'Uninstall')]
    [string]$Action = 'Status',
    [switch]$UserConsented
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$providerDir = Join-Path $projectRoot 'native\providers\rectus-openxr-steamvr-passthrough-v0.4.2-beta\installed\openxr-steamvr-passthrough'
$manifestPath = Join-Path $providerDir 'XR_APILAYER_NOVENDOR_steamvr_passthrough.json'
$dllPath = Join-Path $providerDir 'XR_APILAYER_NOVENDOR_steamvr_passthrough.dll'
$registryPath = 'SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit'
if ($Action -ne 'Status' -and -not $UserConsented) {
    throw 'System-wide layer changes require explicit user consent and -UserConsented.'
}
if ($Action -in @('InstallDisabled', 'InstallEnabled', 'Enable')) {
    if ((Get-FileHash -LiteralPath $manifestPath).Hash -ne '07F7D61C0DCA0D39880F5116A81FF6CF33093C3444C63A60B1CC6D4D3DCC9E0A' -or
        (Get-FileHash -LiteralPath $dllPath).Hash -ne '49D119D7498D5C03FB185C3C14D23826F8885FF17B8CB2A8E444B1078CC59A56') {
        throw 'Pinned provider integrity check failed.'
    }
    $manifestPath = (Resolve-Path -LiteralPath $manifestPath).Path
}
$machine = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
$key = $null
try {
    if ($Action -in @('InstallDisabled', 'InstallEnabled')) {
        $key = $machine.CreateSubKey($registryPath, $true)
        if ($null -eq $key.GetValue($manifestPath, $null) -or $Action -eq 'InstallEnabled') {
            $value = if ($Action -eq 'InstallEnabled') { 0 } else { 1 }
            $key.SetValue($manifestPath, $value, [Microsoft.Win32.RegistryValueKind]::DWord)
        }
    }
    else { $key = $machine.OpenSubKey($registryPath, ($Action -ne 'Status')) }
    if ($Action -in @('Enable', 'Disable')) {
        if ($null -eq $key -or $null -eq $key.GetValue($manifestPath, $null)) {
            throw 'Install the pinned layer in its disabled state first.'
        }
        $value = if ($Action -eq 'Enable') { 0 } else { 1 }
        $key.SetValue($manifestPath, $value, [Microsoft.Win32.RegistryValueKind]::DWord)
    }
    if ($Action -eq 'Uninstall' -and $null -ne $key) {
        # Remove this exact registration only; retain every other layer and all files.
        $key.DeleteValue($manifestPath, $false)
    }
    $registeredValue = if ($null -ne $key) { $key.GetValue($manifestPath, $null) } else { $null }
    [pscustomobject]@{
        provider = 'Rectus v0.4.2-beta'
        registered = ($null -ne $registeredValue)
        enabled = ($null -ne $registeredValue -and $registeredValue -eq 0)
        scope = 'machine-wide implicit OpenXR layer'
        action = $Action
    } | ConvertTo-Json
}
finally {
    if ($null -ne $key) { $key.Dispose() }
    $machine.Dispose()
}
