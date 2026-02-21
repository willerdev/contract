# Building the Contract CLI .exe

Users get a single executable with no Python install and no source code.

## Important

- **Build on Windows** to get **ContractCLI.exe** that runs on Windows. Building on Mac produces a Mac binary (not a Windows .exe).
- If the exe doesn’t run, open **Command Prompt**, `cd` to the folder with the exe, run `ContractCLI.exe` and check the error message.

## One-time setup

```bash
pip install -r requirements-build.txt
```

## Build (Windows)

Double-click **build_exe.bat** or run:

```bash
pyinstaller --noconfirm cli.spec
```

Output: **dist/ContractCLI.exe**

## If the exe doesn’t run

1. **Run from CMD** to see the real error:
   ```
   cd path\to\dist
   ContractCLI.exe
   ```
2. **Antivirus** may block new .exe files; add an exception for `dist\` or the exe.
3. **Right‑click → Run as administrator** once if Windows blocks it.
4. Rebuild with **UPX off** (already set in `cli.spec`: `upx=False`).

## Distributing to users

1. Give users **dist/ContractCLI.exe** (and optionally **run_cli.bat** in the same folder).
2. They double-click the .exe or the .bat. No Python or source code required.
3. Optional: they can set `BASE_URL` via environment variable or a `.env` file next to the exe if you need a different server.

## Notes

- Built exe reads `BASE_URL` from environment or `.env` in the same folder (if present).
- Token is saved as `token.txt` in the current working directory when they run the exe.
