"""
build.py — Build RockoAgents into a standalone executable

Usage:
    python build.py

Output:
    dist/rocko.exe    (Windows)
    dist/rocko        (Mac / Linux)

After building, copy the exe to the root of RockoAgentHub
so it sits next to index.html.

Requirements:
    pip install pyinstaller
"""
import subprocess, sys, shutil
from pathlib import Path

ROOT = Path(__file__).parent

def main():
    print("RockoAgents build script")
    print("=" * 40)

    # Install deps if needed
    print("Installing bridge dependencies...")
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
        'fastapi', 'uvicorn[standard]', 'pydantic', 'aiofiles',
        'apscheduler', 'python-dotenv', 'pyinstaller'],
        check=True)

    # Run PyInstaller
    print("\nRunning PyInstaller...")
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', 'rocko.spec', '--clean', '-y'],
        cwd=ROOT
    )

    if result.returncode != 0:
        print("\nFAIL Build failed. Check output above.")
        sys.exit(1)

    # Copy exe to root
    ext    = '.exe' if sys.platform == 'win32' else ''
    src    = ROOT / 'dist' / f'rocko{ext}'
    dst    = ROOT / f'rocko{ext}'

    if src.exists():
        shutil.copy2(src, dst)
        size_mb = dst.stat().st_size / 1024 / 1024
        print("\nBuild complete!")
        print("  Output: " + str(dst))
        print("  Size: %.1f MB" % size_mb)
        print("\nUsers can now run: rocko run")
        print(f"(copy rocko{ext} to wherever they clone the repo)")
    else:
        print(f"\nFAIL Expected output not found at {src}")
        sys.exit(1)

if __name__ == '__main__':
    main()