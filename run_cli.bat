@echo off
REM Run the Contract CLI (use this in the same folder as ContractCLI.exe)
cd /d "%~dp0"

if exist ContractCLI.exe (
    ContractCLI.exe
) else if exist dist\ContractCLI.exe (
    dist\ContractCLI.exe
) else (
    echo ContractCLI.exe not found. Run build_exe.bat first.
)

pause
