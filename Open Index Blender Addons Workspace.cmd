@echo off
cd /d "%~dp0"
echo Source-built Blender 5.1.2 with separated native panels and private add-ons.
echo Sit still. The experimental camera is uncalibrated and stops after five minutes.
echo Only the image-local ADD CUBE button currently accepts camera pinches.
echo Native add-on controls are available; camera hand pointing at them is not yet qualified.
echo No room images or joint traces are saved. Text logs are automatic.
for /L %%S in (5,-1,1) do (
  echo Starting in %%S...
  timeout /t 1 /nobreak >nul
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_physical_index_blender.ps1" -Launch -PersonalWorkspace -CameraOverlay -CameraGestureHud
if errorlevel 1 echo Launch failed. Check reports\index-physical-launch and .xr_addons_profile\logs.
