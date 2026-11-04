@echo off
setlocal
cd /d "%~dp0"
title Index Blender XR - camera hands Add Cube
echo Opt-in seated Index camera view with a 2D Add Cube hand button.
echo Stay seated. This is uncalibrated passthrough, not room-scale tracking.
echo Hold one whole hand in the LEFT camera view over ADD CUBE, then open, pinch, open.
echo A qualifying release attempts one real, undoable Blender cube. No keyboard or controller is needed.
echo The camera stops after five minutes or when Blender closes. No images or joint traces are saved.
echo Put on the Index now. Blender will start after this short countdown.
for /L %%S in (5,-1,1) do (
    echo Starting in %%S...
    timeout /t 1 /nobreak >nul
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_physical_index_blender.ps1" -Launch -CameraOverlay -CameraGestureHud
if errorlevel 1 (
    echo Index gesture Blender did not complete cleanly. Check the text status log.
) else (
    echo Blender closed. No process was forcibly stopped.
)
echo Status logs: %~dp0reports\index-physical-launch
echo Native Blender logs: %~dp0.xr_profile\logs
echo This window closes automatically; no keypress is required.
timeout /t 15 /nobreak >nul
