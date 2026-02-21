@echo off
REM Build ContractCLI.exe for Windows and place it in dist2/
REM Run this on Windows (e.g. in Command Prompt or PowerShell from the project root).
REM Prerequisites: pip install -r requirements-build.txt

if not exist dist2 mkdir dist2

pyinstaller --noconfirm --distpath dist2 --workpath build_win --specpath . cli.spec

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build complete. Output: dist2\ContractCLI.exe
) else (
    echo Build failed.
    exit /b 1
)
