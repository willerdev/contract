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

**cli.spec** – PyInstaller spec for a single-file console exe. Entry point: `cli.py`. Bundles **VERSION** for in-app version and update checks. Hidden imports include `requests`, `dotenv`, etc.

---

## Releasing a new version (so users get updates themselves)

The CLI is **served from your Render app** at `/download/ContractCLI.exe`, so users never see the GitHub repo URL.

1. **Bump version**  
   Edit **VERSION** in the project root (e.g. `1.0.1`). This is shown in the CLI and used for update checks.

2. **Build the executable**  
   On Windows: `python build.py` (or `build_exe.bat`). Output: `dist/ContractCLI.exe`.

3. **Deploy the exe to Render**  
   - Copy the built exe into the app:  
     `copy dist\ContractCLI.exe static\ContractCLI.exe` (Windows) or  
     `cp dist/ContractCLI.exe static/ContractCLI.exe` (Mac/Linux).  
   - Commit and push (or deploy). The backend serves `static/ContractCLI.exe` at **GET /download/ContractCLI.exe**.

4. **Set backend env (Render)**  
   - `CLI_LATEST_VERSION` = same as in **VERSION** (e.g. `1.0.1`).  
   - Leave `CLI_DOWNLOAD_URL` unset so it defaults to your app URL (uses `RENDER_EXTERNAL_URL` or `PUBLIC_URL`):  
     `https://<your-app>.onrender.com/download/ContractCLI.exe`.  
   Redeploy after changing env.

5. **Tell users**  
   Users run **ContractCLI.exe**, choose **0. Check for updates**. If their version is older, they get a link to **your Render app** to download the new exe (no GitHub link).

**One-time setup for users:** Give them the first **ContractCLI.exe** once. After that, they use “Check for updates” and download new builds from your Render URL only.
