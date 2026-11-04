param([Parameter(Mandatory=$true)][string]$Exe)

$ErrorActionPreference = 'Stop'
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = (Resolve-Path -LiteralPath $Exe).ProviderPath
$start.Arguments = '--synthetic-stream-rgba'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$child = [Diagnostics.Process]::Start($start)
if (!$child) { throw 'Synthetic legacy stream did not start.' }
$output = [IO.MemoryStream]::new()
try {
    $copy = $child.StandardOutput.BaseStream.CopyToAsync($output)
    if (!$copy.Wait(10000) -or !$child.WaitForExit(2000)) {
        if (!$child.HasExited) { $child.Kill(); $child.WaitForExit(2000) | Out-Null }
        throw 'Synthetic legacy stream exceeded its offline timeout.'
    }
    if ($child.ExitCode -ne 0) { throw "Synthetic legacy stream exited $($child.ExitCode)." }
    $bytes = $output.ToArray()
    $eyeBytes = 960 * 960 * 4
    $frameBytes = 24 + 2 * $eyeBytes
    if ($bytes.Length -ne 3 * $frameBytes) { throw "Legacy byte count changed: $($bytes.Length)." }
    for ($frame = 0; $frame -lt 3; $frame++) {
        $offset = $frame * $frameBytes
        $header = @(0x42575258, 1, 960, 960, 1, 1)
        for ($word = 0; $word -lt $header.Length; $word++) {
            if ([BitConverter]::ToUInt32($bytes, $offset + 4 * $word) -ne $header[$word]) {
                throw "Legacy header changed at frame $frame word $word."
            }
        }
        $left = $offset + 24
        $right = $left + $eyeBytes
        if ($bytes[$left] -ne 80 -or $bytes[$left + 1] -ne 0 -or
            $bytes[$left + 2] -ne 0 -or $bytes[$left + 3] -ne 255 -or
            $bytes[$right] -ne 0 -or $bytes[$right + 1] -ne 0 -or
            $bytes[$right + 2] -ne 80 -or $bytes[$right + 3] -ne 255) {
            throw "Legacy left/right RGBA order changed at frame $frame."
        }
    }
    Write-Output 'MERCURY_LEGACY_RGBA_PROTOCOL_PASS frames=3 header_bytes=24 eye_bytes=3686400 camera_accessed=0 images_saved=0'
}
finally {
    if (!$child.HasExited) { $child.Kill(); $child.WaitForExit(2000) | Out-Null }
    $child.Dispose()
    $output.Dispose()
}
