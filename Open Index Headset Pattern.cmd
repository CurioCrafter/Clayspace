@echo off
setlocal
cd /d "%~dp0"
title Index headset display test - no camera
echo Put on the Index now. The blue X and box will appear in five seconds.
echo This test uses no camera and saves no images.
timeout /t 5 /nobreak >nul
"native\providers\mercury_offline\build-direct-release\mercury_direct_camera_eval.exe" --headset-test-pattern
if errorlevel 1 (
    echo The Index display test refused to run. Check the message above.
) else (
    echo Test window closed. Note what you saw through the lenses.
)
timeout /t 15 /nobreak >nul
