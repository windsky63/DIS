@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Drawing Topology Marker - Service Console

echo ============================================================
echo   Drawing Topology Marker
echo   This window owns both services. Closing it stops both.
echo ============================================================
echo.

if not exist "scripts\start-supervisor.ps1" (
  echo [ERROR] Missing scripts\start-supervisor.ps1
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-supervisor.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo [ERROR] Startup or runtime failure. Exit code: %EXIT_CODE%
) else (
  echo [DONE] Backend and frontend have stopped.
)
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%
