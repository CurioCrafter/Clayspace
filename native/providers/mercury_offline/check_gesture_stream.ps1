param([switch]$CombinedPreview)
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'build-direct-release\mercury_direct_camera_eval.exe')).ProviderPath
$process = [Diagnostics.Process]::new()
$process.StartInfo.FileName = $exe
$process.StartInfo.Arguments = if ($CombinedPreview) { '--synthetic-gesture-preview' } else { '--synthetic-gesture-stream' }
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.CreateNoWindow = $true
if (!$process.Start()) { throw 'Synthetic gesture process did not start.' }
try {
    $reader = [IO.BinaryReader]::new($process.StandardOutput.BaseStream)
    for ($frame = 0; $frame -lt 3; $frame++) {
        $header = @(for ($word = 0; $word -lt 9; $word++) { $reader.ReadUInt32() })
        if ($header[0] -ne 0x47575258 -or $header[1] -ne 1 -or $header[2] -ne 960 -or
            $header[3] -ne 960 -or $header[4] -ne $frame -or $header[7] -ne 1 -or $header[8] -ne 0) {
            throw "Bad gesture header on frame $frame."
        }
        $timestamp = ([uint64]$header[6] -shl 32) -bor [uint64]$header[5]
        if ($timestamp -ne (1000000000 + $frame * 16666667)) { throw 'Gesture timestamp did not advance.' }
        if ($reader.ReadUInt32() -ne 0 -or $reader.ReadUInt32() -ne 0) { throw 'Unexpected view or candidate.' }
        $presence = $reader.ReadSingle(); $palmScore = $reader.ReadSingle()
        if ($presence -lt 0.98 -or $palmScore -lt 0.89) { throw 'Synthetic confidence mismatch.' }
        for ($point = 0; $point -lt 21; $point++) {
            if ($reader.ReadSingle() -ne (400 + $point) -or $reader.ReadSingle() -ne (480 + $point)) {
                throw "Bad eye-local point $point."
            }
        }
    }
    if ($reader.Read() -ne -1) { throw 'Unexpected trailing gesture bytes.' }
    if (!$process.WaitForExit(3000) -or $process.ExitCode -ne 0) { throw 'Synthetic gesture process failed.' }
    if ($CombinedPreview) {
        'MERCURY_GESTURE_PREVIEW_PASS frames=3 records=3 world_output=0 camera_accessed=0 images_saved=0'
    } else {
        'MERCURY_GESTURE_STREAM_PASS frames=3 records=3 world_output=0 camera_accessed=0 images_saved=0'
    }
}
finally { if (!$process.HasExited) { $process.Kill() }; $process.Dispose() }
