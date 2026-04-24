"""
build.py -- Build RockoAgents into a standalone executable

Usage:
    python build.py

Output:
    dist/rocko.exe    (Windows)
    dist/rocko        (Mac / Linux)

Requirements:
    pip install pyinstaller
"""
import subprocess, sys, shutil, os
from pathlib import Path

ROOT = Path(__file__).parent

def main():
    print("RockoAgents build script")
    print("=" * 40)

    # Install deps
    print("Installing bridge dependencies...")
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
        'fastapi', 'uvicorn[standard]', 'pydantic', 'aiofiles',
        'apscheduler', 'python-dotenv', 'pyinstaller'],
        check=True)

    # Clean previous build
    for d in ['dist', 'build']:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
            print(f"Cleaned {d}/")

    # Verify bridge package has __init__.py
    init_file = ROOT / 'bridge' / '__init__.py'
    if not init_file.exists():
        init_file.write_text('# bridge package\n')
        print("Created bridge/__init__.py")

    print("\nRunning PyInstaller...")
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', 'rocko.spec', '--clean', '-y'],
        cwd=ROOT
    )

    if result.returncode != 0:
        print("\nFAIL: Build failed. Check output above.")
        sys.exit(1)

    ext = '.exe' if sys.platform == 'win32' else ''
    src = ROOT / 'dist' / f'rocko{ext}'
    dst = ROOT / f'rocko{ext}'

    if src.exists():
        shutil.copy2(src, dst)
        size_mb = dst.stat().st_size / 1024 / 1024
        print(f"\nBuild complete!")
        print(f"  Output: {dst}")
        print(f"  Size:   {size_mb:.1f} MB")
        print(f"\nUsers run: rocko{ext} run")
    else:
        print(f"\nFAIL: Output not found at {src}")
        sys.exit(1)

if __name__ == '__main__':
    main()