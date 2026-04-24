# -*- mode: python ; coding: utf-8 -*-
"""
rocko.spec -- PyInstaller build specification

Key fix: bridge/ is a proper Python package (has __init__.py).
pathex includes only ROOT -- bridge is imported as bridge.xxx not as bare module names.
hiddenimports use fully qualified package paths.

Build:  python build.py
Output: dist/rocko.exe (Windows) | dist/rocko (Mac/Linux)
"""

from pathlib import Path
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],          # ONLY root -- bridge/ resolved via package, not pathex
    binaries=[],
    datas=[
        # Include bridge package directory as package data
        (str(ROOT / 'bridge'), 'bridge'),
    ],
    hiddenimports=[
        # Bridge as proper package -- fully qualified
        'bridge',
        'bridge.bridge',
        'bridge.model_manager',
        'bridge.task_worker',
        'bridge.scheduler',
        'bridge.orchestrator',
        'bridge.runtime_manager',

        # FastAPI + Starlette
        'fastapi',
        'fastapi.middleware',
        'fastapi.middleware.cors',
        'fastapi.responses',
        'fastapi.staticfiles',
        'fastapi.requests',
        'starlette',
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.routing',
        'starlette.responses',
        'starlette.requests',
        'starlette.staticfiles',
        'starlette.background',
        'starlette.datastructures',
        'starlette.exceptions',
        'starlette.status',
        'starlette.types',

        # Uvicorn
        'uvicorn',
        'uvicorn.main',
        'uvicorn.config',
        'uvicorn.server',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.off',
        'uvicorn.lifespan.on',
        'uvicorn.middleware',
        'uvicorn.middleware.proxy_headers',

        # Pydantic
        'pydantic',
        'pydantic.v1',
        'pydantic_core',
        'pydantic.deprecated',
        'pydantic.deprecated.class_validators',
        'pydantic.fields',
        'pydantic.functional_validators',

        # APScheduler
        'apscheduler',
        'apscheduler.schedulers',
        'apscheduler.schedulers.base',
        'apscheduler.schedulers.background',
        'apscheduler.triggers',
        'apscheduler.triggers.base',
        'apscheduler.triggers.interval',
        'apscheduler.triggers.cron',
        'apscheduler.triggers.date',
        'apscheduler.jobstores',
        'apscheduler.jobstores.base',
        'apscheduler.jobstores.memory',
        'apscheduler.executors',
        'apscheduler.executors.base',
        'apscheduler.executors.pool',
        'apscheduler.events',
        'apscheduler.job',
        'apscheduler.util',
        'tzlocal',

        # Async / HTTP
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'sniffio',
        'h11',
        'h11._readers',
        'h11._writers',
        'httptools',
        'aiofiles',
        'aiofiles.os',
        'aiofiles.threadpool',
        'aiofiles.threadpool.binary',
        'aiofiles.threadpool.text',

        # Standard library
        'email.mime.text',
        'email.mime.multipart',
        'importlib.metadata',
        'importlib.resources',
        'multiprocessing',
        'multiprocessing.pool',
        'concurrent.futures',
        'concurrent.futures.thread',
        'logging.handlers',
        'urllib.request',
        'urllib.error',
        'urllib.parse',
        'http.client',
        'http.server',
        'hashlib',
        'hmac',
        'json',
        'pathlib',
        'threading',
        'subprocess',
        'uuid',
        'ast',
        'inspect',
        'traceback',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', '_tkinter', 'tcl',
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PIL', 'cv2', 'sklearn', 'tensorflow', 'torch',
        'wx', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'jupyter', 'notebook', 'IPython',
        'test', 'tests',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='rocko',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
