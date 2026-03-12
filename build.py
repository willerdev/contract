#!/usr/bin/env python3
"""
Build the Contract CLI into a single executable for distribution.

Usage (on Windows for .exe):
    pip install -r requirements-build.txt
    python build.py

Output:
    dist/ContractCLI.exe   (or ContractCLI on Mac/Linux)

To use a custom output folder:
    python build.py --dist dist2 --workpath build_win
"""
import os
import subprocess
import sys


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    spec = os.path.join(script_dir, "cli.spec")
    if not os.path.isfile(spec):
        print("Error: cli.spec not found.")
        sys.exit(1)

    distpath = "dist"
    workpath = "build"
    if "--dist" in sys.argv:
        i = sys.argv.index("--dist")
        if i + 1 < len(sys.argv):
            distpath = sys.argv[i + 1]
    if "--workpath" in sys.argv:
        i = sys.argv.index("--workpath")
        if i + 1 < len(sys.argv):
            workpath = sys.argv[i + 1]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        os.path.join(script_dir, distpath),
        "--workpath",
        os.path.join(script_dir, workpath),
        spec,
    ]

    out_dir = os.path.join(script_dir, distpath)
    print("Building Contract CLI executable...")
    print("  Output folder:", os.path.abspath(out_dir))
    result = subprocess.run(cmd, cwd=script_dir)
    if result.returncode != 0:
        print("Build failed. Install deps: pip install -r requirements-build.txt")
        sys.exit(1)

    exe_name = "ContractCLI.exe" if os.name == "nt" else "ContractCLI"
    exe_path = os.path.join(script_dir, distpath, exe_name)
    print("Done. Executable:", os.path.abspath(exe_path))
    print("Distribute:", exe_path, "and optionally run_cli.bat")


if __name__ == "__main__":
    main()
