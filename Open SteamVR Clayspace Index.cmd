@echo off
cd /d "%~dp0"
echo Source-built Blender 5.1.2 - SteamVR, Index controllers and native VR tools.
echo No third-party add-ons are required. Your private add-on snapshot is optional.
echo Close the camera-only Blender test first. Enable Direct Display Mode in SteamVR.
echo Complete SteamVR Room Setup, keep its boundary on, and check all tracking indicators.
echo Cameras and the experimental passthrough layer are OFF for this version.
echo Text logs are automatic in reports\steamvr-launch; each status log links the full Blender log.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_steamvr_blender.ps1" -Launch -ControllerProfile INDEX
if errorlevel 1 (
  echo SteamVR Clayspace did not finish cleanly. Its error is saved in reports\steamvr-launch.
  timeout /t 15 /nobreak >nul
)
