# Offline stand-in process only: no Blender, OpenXR, display, or camera access.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\invoke_xr_text_process.ps1')
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$log = Join-Path ([IO.Path]::GetTempPath()) ('xrwb-stream-' + [guid]::NewGuid().ToString('N') + '.log')
try {
    $command = '[Console]::Out.WriteLine("XR_STDOUT_MARKER"); [Console]::Error.WriteLine("XR_STDERR_MARKER"); exit 23'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $arguments = @('-NoProfile', '-EncodedCommand', $encoded)
    $exitCode = Invoke-XrLoggedProcess -Executable $shell -Arguments $arguments -LogPath $log
    $content = Get-Content -LiteralPath $log -Raw
    if ($exitCode -ne 23 -or !$content.Contains('XR_STDOUT_MARKER') -or
        !$content.Contains('XR_STDERR_MARKER') -or $ErrorActionPreference -ne 'Stop') {
        throw "Merged text log or native exit propagation failed: exit=$exitCode"
    }
    $exitCode = Invoke-XrLoggedProcess -Executable $shell -Arguments $arguments -LogPath $log -TimeoutSeconds 10
    $content = Get-Content -LiteralPath $log -Raw
    if ($exitCode -ne 23 -or !$content.Contains('XR_STDOUT_MARKER') -or !$content.Contains('XR_STDERR_MARKER')) {
        throw 'Bounded test process lost its output or exit code.'
    }
    $values = @('two words', 'with"quote', 'trailing\', '')
    $arguments = @('-c', 'import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(7)') + $values
    $exitCode = Invoke-XrLoggedProcess -Executable (Get-Command python).Source -Arguments $arguments -LogPath $log -TimeoutSeconds 10
    $received = ConvertFrom-Json -InputObject (Get-Content -LiteralPath $log -Raw)
    if ($exitCode -ne 7 -or $received.Count -ne $values.Count) {
        throw "Bounded argv/exit contract failed: exit=$exitCode values=$($received.Count) expected=$($values.Count)"
    }
    for ($i = 0; $i -lt $values.Count; $i++) {
        if ($received[$i] -cne $values[$i]) { throw "Windows argv quoting failed at argument $i" }
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('Start-Sleep -Seconds 30'))
    $exitCode = Invoke-XrLoggedProcess -Executable $shell -Arguments @('-NoProfile', '-EncodedCommand', $encoded) -LogPath $log -TimeoutSeconds 1
    if ($exitCode -ne 124 -or !(Select-String -LiteralPath $log -Pattern '^XRWB_PROCESS_TIMEOUT' -Quiet)) {
        throw 'Stalled owned test process did not time out.'
    }
    $physical = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\scripts\launch_physical_index_blender.ps1') -Raw
    if ($physical -match 'XR_PHYSICAL_INDEX_BLENDER_CLOSED' -or
        $physical -notmatch 'XR_PHYSICAL_INDEX_BLENDER_EXIT exit_code=0' -or
        $physical -notmatch 'XR_PHYSICAL_INDEX_BLENDER_EXIT exit_code=\$\(\$matches\[1\]\)') {
        throw 'Physical status no longer distinguishes clean and nonzero Blender exits.'
    }
    'XRWB_TEXT_LOG_TEST_PASS stdout=1 stderr=1 nonzero_exit=23 owned_timeout=124 eap_restored=1 physical_launch=0'
}
finally {
    foreach ($path in @($log, ($log + '.stdout'), ($log + '.stderr'))) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
}
