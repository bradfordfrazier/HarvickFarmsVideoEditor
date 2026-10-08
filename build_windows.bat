@echo off
setlocal
title Harvick Farms Video Studio - Windows Packaging

echo =====================================================================
echo   Harvick Farms Video Studio - Windows Build ^& Packaging
echo =====================================================================
echo.

cd /d "%~dp0"

echo [1/3] Ensuring build dependencies are installed...
python -m pip install -q pyinstaller pywebview

echo [2/3] Executing build pipeline...
python scripts\build_package.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build encountered an error. Review the console logs above.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo =====================================================================
echo   Packaging Finished!
echo   - Standalone Portable App: dist\HarvickFarmsVideoStudio\
echo   - Executable:              dist\HarvickFarmsVideoStudio\HarvickFarmsVideoStudio.exe
echo   - Installer (if ISCC ran): installer_output\HarvickFarmsVideoStudio_Setup_v1.0.0.exe
echo =====================================================================
echo.
pause
