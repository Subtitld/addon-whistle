# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Subtitld Whistle add-on.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Produces `dist/whistle-addon/` (launcher + `_internal/`). The release step
copies it to `bin/` inside the archive, next to manifest.json.

The engine library and the weights are collected into `_internal/assets/`.
Run tools/fetch_assets.py first: it places both there, verified by sha256.

Run from the add-on root:

    python tools/fetch_assets.py
    pyinstaller pyinstaller.spec --noconfirm
"""

# ruff: noqa: F821  # PyInstaller injects Analysis/PYZ/EXE/COLLECT at runtime.

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SPEC_ROOT = Path(SPECPATH).resolve()
ASSETS = SPEC_ROOT / 'build' / 'assets'

if sys.platform == 'darwin':
    _library_name = 'libneedle3.dylib'
elif sys.platform == 'win32':
    _library_name = 'libneedle3.dll'
else:
    _library_name = 'libneedle3.so'

# Fail the build, loudly, if an asset is missing. Otherwise the frozen add-on
# would answer the handshake and then fail the first transcription.
_MISSING = [name for name in (_library_name, 'whistle.cact') if not (ASSETS / name).is_file()]
if _MISSING:
    raise SystemExit(f'FATAL: missing {", ".join(_MISSING)} in {ASSETS}. '
                     f'Run `python tools/fetch_assets.py` first.')

binaries = [(str(ASSETS / _library_name), 'assets')]
datas = [(str(ASSETS / 'whistle.cact'), 'assets')]


# ---------------------------------------------------------------------------
# libexpat sanity check (from addon-whispercpp).
#
# PyInstaller bundles whatever expat it resolved on the build host, for
# CPython's pyexpat. If that host has an OLD expat shadowing the distro one
# (for example a hand-built 2.5.0 in /usr/local/lib), the frozen binary dies
# before reaching Python:
#     Failed to load libpython3.x.so: undefined symbol: XML_SetReparseDeferralEnabled
# Dropping the bundled copy is not a fix either, because the same stale system
# copy is then used. So verify the bundled copy exports the symbol libpython
# needs, and substitute a known-good system copy if it does not.
# ---------------------------------------------------------------------------
_EXPAT_SYMBOL = 'XML_SetReparseDeferralEnabled'


def _exports_symbol(path: str, symbol: str) -> bool:
    try:
        out = subprocess.run(['nm', '-D', '--defined-only', path],
                             capture_output=True, text=True, timeout=30)
        return symbol in out.stdout
    except Exception:
        return True  # no nm (macOS/Windows): assume fine rather than break the build


def _fix_libexpat(entries):
    if not sys.platform.startswith('linux'):
        return entries
    out = []
    for entry in entries:
        name = entry[0] if entry else ''
        src = entry[1] if len(entry) > 1 else ''
        if not os.path.basename(name).startswith('libexpat.so'):
            out.append(entry)
            continue
        if _exports_symbol(src, _EXPAT_SYMBOL):
            out.append(entry)
            continue
        replacement = None
        for cand in ('/usr/lib/x86_64-linux-gnu/libexpat.so.1',
                     '/usr/lib64/libexpat.so.1',
                     '/lib/x86_64-linux-gnu/libexpat.so.1'):
            if os.path.exists(cand) and _exports_symbol(cand, _EXPAT_SYMBOL):
                replacement = cand
                break
        if replacement:
            print(f'WARNING: build host libexpat at {src} lacks {_EXPAT_SYMBOL}; '
                  f'substituting {replacement}')
            out.append((name, replacement) + tuple(entry[2:]))
        else:
            print(f'ERROR: bundled libexpat lacks {_EXPAT_SYMBOL} and no good '
                  f'system copy was found. The frozen binary will not start. '
                  f'Fix the build host (a stale expat in /usr/local/lib is the '
                  f'usual cause).')
            out.append(entry)
    return out


a = Analysis(
    [str(SPEC_ROOT / 'whistle_addon' / '__main__.py')],
    pathex=[str(SPEC_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PyQt6', 'PyQt5', 'tkinter', 'Tkinter', '_tkinter',
        'numpy', 'setuptools', 'pkg_resources', 'unittest', 'pydoc_data', 'pytest',
        # Nothing in the add-on uses the network. Keep these out of the bundle.
        'huggingface_hub', 'requests', 'urllib3', 'certifi',
        # Standard modules the add-on never calls, reached only through
        # optional imports (shutil's archive formats, random's hash fallback).
        # Each one would bring a native library, and with it a licence to
        # ship: OpenSSL, bzip2, xz, zstd, libmpdec. tests/test_installed.py
        # runs the frozen binary end to end, so a module that turns out to be
        # needed fails the release build, not the user.
        'ssl', '_ssl', 'hashlib', '_hashlib',
        'bz2', '_bz2', 'lzma', '_lzma', 'compression', '_zstd',
        'decimal', '_decimal', '_pydecimal',
    ],
    noarchive=False,
)

a.binaries = _fix_libexpat(a.binaries)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='whistle-addon',
    debug=False,
    bootloader_ignore_signals=False,
    # strip=False: stripping the native libraries saves little and breaks
    # macOS code signatures.
    strip=False,
    upx=False,
    console=True,  # stdio is the protocol channel
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='whistle-addon',
)
