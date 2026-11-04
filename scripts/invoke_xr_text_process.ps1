function Invoke-XrLoggedProcess {
    param(
        [Parameter(Mandatory=$true)][string]$Executable,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory=$true)][string]$LogPath,
        [ValidateRange(0, 3600)][int]$TimeoutSeconds = 0
    )

    if ($TimeoutSeconds) {
        # Explicit automated tests only; ordinary user sessions have no watchdog.
        # Quote Windows argv (including embedded quotes and trailing backslashes).
        $quoted = @($Arguments | ForEach-Object {
            '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
        })
        $child = Start-Process -FilePath $Executable -ArgumentList ($quoted -join ' ') -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput ($LogPath + '.stdout') -RedirectStandardError ($LogPath + '.stderr')
        # Retain the handle before exit; Windows PowerShell can otherwise lose ExitCode.
        $null = $child.Handle
        Write-Host "XRWB_TEST_PROCESS pid=$($child.Id) timeout_seconds=$TimeoutSeconds stdout=$LogPath.stdout stderr=$LogPath.stderr"
        $elapsed = [Diagnostics.Stopwatch]::StartNew()
        $timedOut = $false
        try {
            while (!$child.WaitForExit(250)) {
                if ($elapsed.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                    $timedOut = $true
                    $child.CloseMainWindow() | Out-Null
                    if (!$child.WaitForExit(3000)) { $child.Kill() }
                    if (!$child.WaitForExit(5000)) { throw 'Owned test process did not stop after timeout.' }
                    break
                }
            }
            $exitCode = if ($timedOut) { 124 } else { $child.ExitCode }
            if ($null -eq $exitCode) { throw 'Owned test process did not report an exit code.' }
        }
        finally {
            if (!$child.HasExited) { $child.Kill(); $child.WaitForExit(5000) | Out-Null }
            $writer = [IO.StreamWriter]::new($LogPath, $false, [Text.UTF8Encoding]::new($false))
            try {
                # Keep original streams too: the combined test log is not temporal order.
                foreach ($stream in @('stdout', 'stderr')) {
                    foreach ($line in [IO.File]::ReadLines($LogPath + '.' + $stream)) {
                        $writer.WriteLine($line)
                        Write-Host $line
                    }
                }
                if ($timedOut) { $writer.WriteLine('XRWB_PROCESS_TIMEOUT owned_test_only=1') }
            }
            finally { $writer.Dispose(); $child.Dispose() }
        }
        return [int]$exitCode
    }

    # Native stderr becomes an ErrorRecord under Windows PowerShell 5.1.
    # Keep it as diagnostic text; a nonzero child exit is handled separately.
    $previousErrorAction = $ErrorActionPreference
    $writer = [IO.StreamWriter]::new($LogPath, $false, [Text.UTF8Encoding]::new($false))
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object {
            $line = [string]$_
            $writer.WriteLine($line)
            $writer.Flush()
            Write-Host $line
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
        $writer.Dispose()
    }
    if ($null -eq $exitCode) { throw 'Native process did not report an exit code.' }
    return [int]$exitCode
}
