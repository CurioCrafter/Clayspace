@echo off
cd /d "%~dp0"
echo Opening source-built Blender 5.1.2 with your private add-on snapshot.
echo Original Blender preferences and scenes are not changed.
echo Camera and headset do not start automatically in this desktop check.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_xr_workbench.ps1" -BlenderExe "%~dp0native\build-features\bin\blender.exe" -UsePersonalAddons
if errorlevel 1 echo Launch failed. Text logs are in .xr_addons_profile\logs.
