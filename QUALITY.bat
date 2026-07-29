@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CHECK_QUALITY.ps1"
if errorlevel 1 (
    echo.
    echo QUALITY GATE FAILED
    pause
    exit /b 1
)
echo.
echo QUALITY GATE PASSED
pause
