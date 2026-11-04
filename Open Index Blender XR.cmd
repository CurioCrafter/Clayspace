@echo off
setlocal
cd /d "%~dp0"
title Index Blender XR - camera off
echo This starts source-built Blender XR on the guarded Index OpenXR runtime.
echo Camera, SLAM, and Monado hand tracking stay OFF.
echo Put on the Index now. Blender will start after this short countdown.
for /L %%S in (5,-1,1) do (
    echo Starting in %%S...
    timeout /t 1 /nobreak >nul
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_physical_index_blender.ps1" -Launch
if errorlevel 1 (
    echo Index Blender XR did not complete cleanly. Check the text status log.
) else (
    echo Blender closed. No process was forcibly stopped.
)
echo Status logs: %~dp0reports\index-physical-launch
echo Native Blender logs: %~dp0.xr_profile\logs
echo This window closes automatically; no keypress is required.
timeout /t 15 /nobreak >nul
