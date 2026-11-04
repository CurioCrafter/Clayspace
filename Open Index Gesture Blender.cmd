@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_index_gesture_blender.ps1"
if errorlevel 1 pause
