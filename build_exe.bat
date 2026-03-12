@echo off
setlocal enabledelayedexpansion
REM Build updated Contract CLI .exe. Run from project folder. Installs PyInstaller then builds.

cd /d "%~dp0"

set "SPEC=%~dp0cli.spec"
if not exist "%SPEC%" (
    echo Error: cli.spec not found at "%SPEC%".
    echo Current folder: %CD%
    dir /b
    pause
    exit /b 1
)

echo Installing build dependencies...
pip install -r requirements-build.txt --quiet

echo.
if exist VERSION (
    set /p VER=<VERSION
    echo Building ContractCLI.exe version !VER!...
) else (
    echo Building ContractCLI.exe...
)
pyinstaller --noconfirm "%SPEC%"

if %ERRORLEVEL% NEQ 0 (
    echo Build failed.
    pause
    exit /b 1
)

if not exist static mkdir static
copy /Y dist\ContractCLI.exe static\ContractCLI.exe >nul

echo.
echo Done. Executable: dist\ContractCLI.exe
echo Copied to static\ContractCLI.exe for deploy. Commit and push to release.
echo Distribute to users: dist\ContractCLI.exe and run_cli.bat (optional)
echo.
pause
