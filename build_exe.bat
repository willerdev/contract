@echo off
REM Build Contract CLI into a single .exe for distribution.
REM Run from project folder. Installs PyInstaller then builds.

cd /d "%~dp0"

echo Installing build dependencies...
pip install -r requirements-build.txt --quiet

echo.
echo Building ContractCLI.exe...
pyinstaller --noconfirm cli.spec

if %ERRORLEVEL% NEQ 0 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Done. Executable: dist\ContractCLI.exe
echo Distribute to users: dist\ContractCLI.exe and run_cli.bat (optional)
echo.
pause
