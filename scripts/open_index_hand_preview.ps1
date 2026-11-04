param([switch]$PreflightOnly, [switch]$Headset)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $workspace 'reports/index-headset-preview'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logName = '{0}-{1}-{2}.txt' -f $(if ($Headset) { 'headset' } else { 'desktop' }),
    (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
$logPath = Join-Path $logDir $logName
function Write-PreviewLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Value $Message
    Write-Host $Message
}
Write-PreviewLog "XR_INDEX_PREVIEW_LOG $logPath"
try {
$probePath = Join-Path $workspace 'native/steamvr_free/index_camera_probe/index_camera_probe.ps1'
$preview = Join-Path $workspace 'native/providers/mercury_offline/build-direct-release/mercury_direct_camera_eval.exe'
if (!(Test-Path -LiteralPath $preview)) { throw 'Build mercury_direct_camera_eval before opening the hand preview.' }

# Read-only enumeration checks the physical Index USB ID and refuses to run
# alongside SteamVR. The native preview independently checks the same guard.
$probe = & $probePath | ConvertFrom-Json
if (!$probe.camera_detected -or $probe.index_usb_id -ne 'VID_28DE&PID_2400') {
    throw 'The physical Valve Index camera was not identified.'
}
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
if ($Headset) {
    & $preview --headset-display-probe 2>&1 | ForEach-Object { Write-PreviewLog ([string]$_) }
    $displayExit = $LASTEXITCODE
    if ($displayExit -ne 0) { throw 'No active Index extended display; headset preview will not open the camera.' }
}
if ($PreflightOnly) {
    Write-PreviewLog "XR_INDEX_HAND_PREVIEW_PREFLIGHT_PASS camera_accessed=0 steamvr=0 headset=$([int]$Headset.IsPresent)"
    return
}

Write-PreviewLog 'Opening live Index stereo preview. Esc, Q, or closing the window stops it.'
Write-PreviewLog 'Blue estimated contours require palm and landmark agreement; Mercury hand regions are fallback.'
Write-PreviewLog 'No camera images or hand traces are saved.'
$mode = if ($Headset) { '--consented-headset-preview' } else { '--consented-preview' }
$previousPreference = $ErrorActionPreference
$suppressedGeometryLines = 0
try {
    # Windows PowerShell 5.1 promotes native stderr to an error record; keep it as diagnostic text.
    $ErrorActionPreference = 'Continue'
    & $preview $mode $probe.camera_name $ffmpeg 2>&1 | ForEach-Object {
        $line = [string]$_
        if ($line -match '^ERROR \[sympy\].*Distances') { $script:suppressedGeometryLines++ }
        else { Write-PreviewLog $line }
    }
    $previewExit = $LASTEXITCODE
}
finally { $ErrorActionPreference = $previousPreference }
if ($suppressedGeometryLines) {
    Write-PreviewLog "XR_INDEX_PREVIEW_SUPPRESSED_GEOMETRY_DIAGNOSTICS count=$suppressedGeometryLines"
}
if ($previewExit -ne 0) { throw "Index hand preview failed (exit $previewExit)." }
Write-PreviewLog 'XR_INDEX_HAND_PREVIEW_END exit=0 (see native preview marker for timer or user-close reason)'
}
catch {
    Write-PreviewLog "XR_INDEX_HAND_PREVIEW_FAIL $($_.Exception.Message)"
    throw
}
