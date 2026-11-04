@echo off
setlocal
cd /d "%~dp0"
title Index headset hand preview - no recording
echo Sit still and put on the Index. Raw camera passthrough starts in five seconds.
echo Blue outlines appear only when the hand detector qualifies a hand.
echo No room images or hand traces are saved. The preview ends after 15 seconds.
timeout /t 5 /nobreak >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_index_hand_preview.ps1" -Headset
if errorlevel 1 (
    echo The headset hand preview refused or failed. Check the newest text log in reports\index-headset-preview.
    timeout /t 30 /nobreak >nul
) else (
    echo The 15-second headset diagnostic ended normally. This is a timed test, not a crash.
    echo Text logs: %~dp0reports\index-headset-preview
    timeout /t 15 /nobreak >nul
)
