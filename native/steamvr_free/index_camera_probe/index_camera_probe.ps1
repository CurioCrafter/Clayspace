param([switch]$ConsentedStream)

$ErrorActionPreference = 'Stop'

function Assert-SteamVrStopped {
    $running = @(Get-Process -Name vrserver,vrcompositor,vrmonitor,vrwebhelper,steamvr,vrstartup -ErrorAction SilentlyContinue)
    if ($running.Count) { throw 'SteamVR is running; stop it before this independent camera probe.' }
}

Assert-SteamVrStopped
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
# DirectShow enumeration is read-only. Its expected exit code is nonzero because
# "dummy" is not a capture target; the listed devices are the useful result.
# Windows PowerShell 5.1 surfaces native stderr as NativeCommandError under
# Stop, so allow that expected listing while still validating the USB ID below.
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $listing = (& $ffmpeg -nostdin -hide_banner -list_devices true -f dshow -i dummy 2>&1 | Out-String)
}
finally { $ErrorActionPreference = $previousErrorActionPreference }
$cameraName = $null
$candidateName = $null
foreach ($line in ($listing -split "`r?`n")) {
    if ($line -match '"([^"]+)" \(video\)') { $candidateName = $Matches[1] }
    elseif ($candidateName -and $line -match '(?i)usb#vid_28de&pid_2400') {
        $cameraName = $candidateName
        break
    }
}

$report = [ordered]@{
    steamvr_processes = 0
    backend = 'Windows DirectShow'
    index_usb_id = 'VID_28DE&PID_2400'
    camera_detected = [bool]$cameraName
    camera_name = $cameraName
    capture_attempted = $false
    capture_succeeded = $false
    frames = 0
    distinct_capture_timestamps = 0
    capture_timestamps_monotonic = $false
    capture_age = 'unknown'
    frame_size = 'unverified'
    pixel_format = 'unverified'
    image_output = 'none; FFmpeg null muxer'
}

if ($ConsentedStream) {
    if (!$cameraName) { throw 'No physical Index camera matched VID_28DE&PID_2400.' }
    if ($cameraName -notmatch '^[\w -]+$') { throw 'Camera name contains unsupported command characters.' }
    Assert-SteamVrStopped
    $report.capture_attempted = $true
    $id = [Guid]::NewGuid().ToString('N')
    $stdout = Join-Path $env:TEMP ("xr-index-$id.stdout")
    $stderr = Join-Path $env:TEMP ("xr-index-$id.stderr")
    try {
        $inputName = '"video=' + $cameraName + '"'
        $args = @('-nostdin', '-hide_banner', '-loglevel', 'info', '-debug_ts', '-f', 'dshow',
                  '-i', $inputName, '-frames:v', '5', '-f', 'null', 'NUL')
        $probe = Start-Process -FilePath $ffmpeg -ArgumentList $args -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        if (!$probe.WaitForExit(8000)) {
            $probe.Kill()
            $probe.WaitForExit()
            throw 'Camera read exceeded the 8-second limit.'
        }
        $details = Get-Content -LiteralPath $stderr -Raw
        $frameMatches = [regex]::Matches($details, 'frame=\s*(\d+)')
        if ($frameMatches.Count) { $report.frames = [int]$frameMatches[$frameMatches.Count - 1].Groups[1].Value }
        $ptsMatches = [regex]::Matches($details, 'demuxer ->[^\r\n]*pkt_pts:(\d+)')
        $pts = @($ptsMatches | Select-Object -First 5 | ForEach-Object { [long]$_.Groups[1].Value })
        $report.distinct_capture_timestamps = @($pts | Select-Object -Unique).Count
        $report.capture_timestamps_monotonic = ($pts.Count -ge 5)
        for ($i = 1; $i -lt $pts.Count; $i++) {
            if ($pts[$i] -le $pts[$i - 1]) { $report.capture_timestamps_monotonic = $false }
        }
        if ($details -match 'Video: rawvideo \(YUY2[^\r\n]*?,\s*(\d{3,5}x\d{3,5}),') {
            $report.pixel_format = 'YUY2'
            $report.frame_size = $Matches[1]
        }
        $report.capture_succeeded = ($probe.ExitCode -eq 0 -and $report.frames -eq 5 -and
            $report.distinct_capture_timestamps -eq 5 -and $report.capture_timestamps_monotonic)
        if (!$report.capture_succeeded) { throw "Camera read failed (exit $($probe.ExitCode), frames $($report.frames))." }
    }
    finally {
        Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
    }
}

[pscustomobject]$report | ConvertTo-Json -Depth 2
