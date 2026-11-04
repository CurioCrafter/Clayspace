@echo off
setlocal
rem Run by double-click. The PowerShell preflight identifies the Index USB camera;
rem the preview keeps frames in memory and stops when its window closes.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_index_hand_preview.ps1"
if errorlevel 1 pause
