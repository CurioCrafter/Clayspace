$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'build-direct-release\mercury_direct_camera_eval.exe')).ProviderPath
$parent = [Diagnostics.Process]::new()
$parent.StartInfo.FileName = $exe
$parent.StartInfo.Arguments = '--synthetic-job-parent'
$parent.StartInfo.UseShellExecute = $false
$parent.StartInfo.RedirectStandardOutput = $true
$parent.StartInfo.CreateNoWindow = $true
$child = $null
try {
    if (!$parent.Start()) { throw 'Job test parent did not start.' }
    $lineTask = $parent.StandardOutput.ReadLineAsync()
    if (!$lineTask.Wait(5000) -or $lineTask.Result -notmatch '^MERCURY_JOB_CHILD_PID=(\d+)$') {
        throw 'Job test did not report its child PID.'
    }
    $child = [Diagnostics.Process]::GetProcessById([int]$Matches[1])
    if ([IO.Path]::GetFullPath($child.MainModule.FileName) -ne [IO.Path]::GetFullPath($exe)) {
        throw 'Job child identity mismatch.'
    }
    $parent.Kill()
    if (!$parent.WaitForExit(5000)) { throw 'Job test parent did not stop.' }
    if (!$child.WaitForExit(5000)) { throw 'Job child survived abrupt parent termination.' }
    'MERCURY_CHILD_JOB_PASS parent_terminated=1 child_terminated=1 camera_accessed=0'
}
finally {
    if (!$parent.HasExited) { $parent.Kill() }
    if ($null -ne $child -and !$child.HasExited -and
        [IO.Path]::GetFullPath($child.MainModule.FileName) -eq [IO.Path]::GetFullPath($exe)) {
        $child.Kill()
    }
    if ($null -ne $child) { $child.Dispose() }
    $parent.Dispose()
}
