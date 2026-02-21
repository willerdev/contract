@echo off
REM Build Contract CLI into a single .exe (no Python or source needed for users)
REM Run this from the project folder after: pip install -r requirements-build.txt

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
echo Give users: dist\ContractCLI.exe and run_cli.bat (optional)
echo.
pause
