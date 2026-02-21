@echo off
REM Build ContractCLI.exe for Windows. Run this on Windows from the project folder.
REM Requires: Python installed, then:  pip install -r requirements-build.txt

echo Building ContractCLI.exe for Windows...
if not exist dist2 mkdir dist2

pyinstaller --noconfirm --distpath dist2 --workpath build_win --specpath . cli.spec

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build complete. Run: dist2\ContractCLI.exe
) else (
    echo Build failed. Install deps with: pip install -r requirements-build.txt
    exit /b 1
)
