@echo off
setlocal
cd /d "%~dp0"
title Index Blender XR - seated camera overlay diagnostic
echo Opt-in Index camera view: a five-minute seated, uncalibrated diagnostic.
echo This first camera-plus-Blender test shows outlines only, not optical tool control.
echo Do not stand or walk with this view. Blender may remain open after the camera feed blanks.
echo The camera is used only for this timed test; no camera images or joint traces are saved.
echo Put on the Index now. Blender will start after this short countdown.
for /L %%S in (5,-1,1) do (
    echo Starting in %%S...
    timeout /t 1 /nobreak >nul
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_physical_index_blender.ps1" -Launch -CameraOverlay
if errorlevel 1 (
    echo Index camera overlay did not complete cleanly. Check the text status log.
) else (
    echo Blender closed. No process was forcibly stopped.
)
echo Status logs: %~dp0reports\index-physical-launch
echo Native Blender logs: %~dp0.xr_profile\logs
echo This window closes automatically; no keypress is required.
timeout /t 15 /nobreak >nul
