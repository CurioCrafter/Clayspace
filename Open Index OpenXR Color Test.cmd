@echo off
setlocal
cd /d "%~dp0"
echo This 5-second color test uses the isolated Monado OpenXR runtime, not SteamVR.
echo Sit still, put the Valve Index on, and look for dim red in the left eye and dim blue in the right eye.
echo A green corner marker should appear in both eyes. No camera is used.
echo The test starts in five seconds so you can put the headset on.
echo.
timeout /t 5 /nobreak >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0native\steamvr_free\run_monado_index_frame_probe.ps1" -Probe -DisplayPatternSeen
echo.
echo The window above reports API status only. Note what you actually saw in the lenses.
echo This status window closes in 30 seconds; no keyboard is needed.
timeout /t 30 /nobreak >nul
endlocal
