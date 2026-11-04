@echo off
setlocal
rem Uses FFPLAY if set, otherwise ffplay from PATH (e.g. winget install Gyan.FFmpeg.Essentials).
if not defined FFPLAY for /f "delims=" %%F in ('where ffplay 2^>nul') do if not defined FFPLAY set "FFPLAY=%%F"
echo This opens a live view of your Index headset cameras on this PC. Nothing is recorded or sent.
if not exist "%FFPLAY%" (
  echo FFplay was not found. Install FFmpeg or set FFPLAY to ffplay.exe.
  pause
  exit /b 1
)
rem Local DirectShow preview only: no file output, stream server, or SteamVR.
start "" "%FFPLAY%" -hide_banner -loglevel warning -f dshow -video_size 1920x960 -i video=eTronVideo -an -framedrop -x 1200 -y 600 -window_title Index-Cameras-Live-Not-Recording
