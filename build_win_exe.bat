@echo off
setlocal enabledelayedexpansion
REM Build updated ContractCLI.exe for Windows. Run on Windows from the project folder.
REM Requires: Python installed, then:  pip install -r requirements-build.txt
REM Output: dist2\ContractCLI.exe (and optionally copied to static\ for deploy)

cd /d "%~dp0"

set "SPEC=%~dp0cli.spec"
if not exist "%SPEC%" (
    echo Error: cli.spec not found at "%SPEC%".
    echo Current folder: %CD%
    dir /b
    pause
    exit /b 1
)

if exist VERSION (
    set /p VER=<VERSION
    echo Building ContractCLI.exe version !VER!...
) else (
    echo Building ContractCLI.exe...
)

if not exist dist2 mkdir dist2
if not exist static mkdir static

pyinstaller --noconfirm --distpath dist2 --workpath build_win "%SPEC%"

if %ERRORLEVEL% NEQ 0 (
    echo Build failed. Install deps with: pip install -r requirements-build.txt
    exit /b 1
)

copy /Y dist2\ContractCLI.exe static\ContractCLI.exe >nul
echo.
echo Build complete. Executable: dist2\ContractCLI.exe
echo Copied to static\ContractCLI.exe for deploy. Commit and push to release.
echo.
pause
