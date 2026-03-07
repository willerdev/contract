# Building the Contract CLI .exe for distribution

Single executable so users can run the app without installing Python or source code.

## Important

- **Build on Windows** to get **ContractCLI.exe** for Windows users. Building on Mac/Linux produces a Mac/Linux binary (not a Windows .exe).
- Users only need the exe (and optionally `run_cli.bat`). No Python or code required.

## One-time setup

```bash
pip install -r requirements-build.txt
```

(`requirements-build.txt` contains `pyinstaller>=6.0`.)

## Build methods

### Option 1: Python script (any OS)

```bash
python build.py
```

Output: **dist/ContractCLI.exe** (Windows) or **dist/ContractCLI** (Mac/Linux).

Custom output folders:

```bash
python build.py --dist dist2 --workpath build_win
```

### Option 2: Batch file (Windows)

**build_exe.bat** – installs deps and builds into `dist/`:

```cmd
build_exe.bat
```

**build_win_exe.bat** – builds into `dist2/` (no auto pip install):

```cmd
build_win_exe.bat
```

### Option 3: PyInstaller directly

```bash
pyinstaller --noconfirm cli.spec
```

Output: **dist/ContractCLI.exe**

## If the exe doesn’t run

1. **Run from Command Prompt** to see errors:
   ```
   cd path\to\dist
   ContractCLI.exe
   ```
2. **Antivirus** may block new .exe files; add an exception for the exe or the folder.
3. **Run as administrator** once if Windows blocks it.
4. UPX is already disabled in `cli.spec` (`upx=False`) to reduce false positives.

## Distributing to users

1. **Give users:**
   - **ContractCLI.exe** (from `dist/` or `dist2/`)
   - Optionally **run_cli.bat** in the same folder (so they can double‑click to run).

2. **Optional:** Zip the folder (e.g. `ContractCLI.zip` with the exe and run_cli.bat) for download.

3. **No Python or source** needed on their machine.

4. **Server URL:** The exe uses `BASE_URL` from the environment or a `.env` file in the same folder. Default is your Render URL. Users can override with their own `.env` if you distribute a different server.

5. **Token:** Login saves `token.txt` in the current directory when they run the exe.

## Spec file

**cli.spec** – PyInstaller spec for a single-file console exe. Entry point: `cli.py`. Hidden imports include `requests`, `dotenv`, etc. Edit the spec to add data files or change the exe name if needed.
