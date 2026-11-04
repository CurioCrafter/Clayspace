@echo off
cd /d "%~dp0"
echo Source-built Blender 5.1.2 - Quest 3 PC VR controller profile.
echo Connect your Quest to this PC through Steam Link or another SteamVR-capable PC VR connection.
echo This is not a standalone Quest app. Cameras and passthrough stay OFF.
echo A opens menus. Trigger clicks, grip edits, hold B to move panels. X undo, Y cancel.
echo Redo is in the Edit menu. Logs are saved automatically under reports\steamvr-launch.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_steamvr_blender.ps1" -Launch -ControllerProfile QUEST
if errorlevel 1 (
  echo SteamVR Blender did not finish cleanly. Check reports\steamvr-launch.
  timeout /t 15 /nobreak >nul
)
