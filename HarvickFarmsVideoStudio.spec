# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

block_cipher = None

BASE_DIR = Path(SPECPATH).resolve()

datas = [
    (str(BASE_DIR / 'frontend'), 'frontend'),
    (str(BASE_DIR / 'assets'), 'assets'),
    (str(BASE_DIR / 'presets'), 'presets'),
]

# Bundle FFmpeg & FFprobe binaries from bin/ if present
binaries = []
bin_dir = BASE_DIR / 'bin'
if (bin_dir / 'ffmpeg.exe').exists():
    binaries.append((str(bin_dir / 'ffmpeg.exe'), 'bin'))
if (bin_dir / 'ffprobe.exe').exists():
    binaries.append((str(bin_dir / 'ffprobe.exe'), 'bin'))

hidden_imports = [
    'uvicorn',
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
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'fastapi',
    'fastapi.staticfiles',
    'fastapi.responses',
    'fastapi.middleware.cors',
    'pydantic',
    'multipart',
    'python_multipart',
    'webview',
    'webview.platforms.winforms',
    'clr',
    'clr_loader',
    'pythonnet',
    'backend',
    'backend.app',
    'backend.config',
    'backend.routes.api',
    'backend.core.audio_master',
    'backend.core.branding_engine',
    'backend.core.exporter',
    'backend.core.silence_detector',
    'backend.core.subtitle_generator',
    'backend.core.video_reframer',
    'backend.core.rough_cut',
    'backend.core.take_detector'
]

# Exclude heavy unnecessary machine learning & scientific libraries from bloated global python env
heavy_excludes = [
    'torch',
    'torchaudio',
    'torchvision',
    'scipy',
    'matplotlib',
    'tensorboard',
    'transformers',
    'tokenizers',
    'spacy',
    'thinc',
    'nltk',
    'tkinter',
    'pandas',
    'pytest',
    '_pytest'
]

a = Analysis(
    ['desktop_app.py'],
    pathex=[str(BASE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(BASE_DIR / 'scripts' / 'pyinstaller_runtime_hook.py')],
    excludes=heavy_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = str(BASE_DIR / 'assets' / 'app_icon.ico') if (BASE_DIR / 'assets' / 'app_icon.ico').exists() else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HarvickFarmsVideoStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Launch as GUI application without console window popup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='HarvickFarmsVideoStudio',
)
