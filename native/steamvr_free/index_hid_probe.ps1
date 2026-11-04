<#
Read-only Windows PnP inventory for the Index HMD HID interfaces.
No HID handle is opened and no feature report, camera, display, or runtime is touched.
#>
$ErrorActionPreference = 'Stop'
$indexVendorProduct = 'VID_28DE&PID_2300'
$raw = & pnputil /enum-devices /connected /class HIDClass
if ($LASTEXITCODE -ne 0) { throw "PnP HID inventory failed with exit code $LASTEXITCODE." }
$blocks = ($raw -join "`n") -split "(?:`r?`n){2,}"
$found = @()
foreach ($block in $blocks) {
    if ($block -notmatch [regex]::Escape($indexVendorProduct)) { continue }
    $instance = [regex]::Match($block, '(?m)^Instance ID:\s*(.+)$').Groups[1].Value.Trim()
    if (!$instance -or $instance -notmatch [regex]::Escape($indexVendorProduct)) { continue }
    $status = [regex]::Match($block, '(?m)^Status:\s*(.+)$').Groups[1].Value.Trim()
    $interface = [regex]::Match($instance, '&MI_([0-9A-Fa-f]{2})').Groups[1].Value.ToUpperInvariant()
    $found += [pscustomobject]@{ interface=$interface; status=$status }
}
$steam = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue)
[pscustomobject]@{
    expected_index_hmd_usb_id = $indexVendorProduct
    connected_index_hid_nodes = $found.Count
    interfaces = @($found | Sort-Object interface,status)
    steamvr_processes = $steam.Count
    hid_opened = $false
    imu_packets_read = $false
    index_display_verified = $false
    physical_tracking_verified = $false
} | ConvertTo-Json -Depth 4
