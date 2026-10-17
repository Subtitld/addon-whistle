#!/usr/bin/env python3
"""Fetch and verify the Whistle engine and weights that the build bundles.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Both files come from Hugging Face at a pinned revision, and each is checked
against its sha256 before it is used. Nothing here runs when the add-on runs:
the frozen add-on only ever reads the files this script placed in the output
folder.

The engine is the `needle/libneedle3.*` library inside the platform wheel that
Cactus-Compute/needle3 publishes for cactus-needle 3.3.1. The weights are
`whistle.cact` from Cactus-Compute/whistle, Apache-2.0.

    python tools/fetch_assets.py                         # this machine
    python tools/fetch_assets.py --platform windows-x86_64
    python tools/fetch_assets.py --out build/assets
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

ENGINE_VERSION = '3.3.1'
ENGINE_BASE = 'https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/'

WEIGHTS_REVISION = 'ca5287601bef25af26dcf2e1b2bdc0843a7c19e5'
WEIGHTS_URL = f'https://huggingface.co/Cactus-Compute/whistle/resolve/{WEIGHTS_REVISION}/whistle.cact'
WEIGHTS_FILE = 'whistle.cact'
WEIGHTS_SIZE = 16919407
WEIGHTS_SHA256 = 'b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb'

# platform key -> the engine wheel, its sha256, and the library inside it.
PLATFORMS = {
    'linux-x86_64': {
        'wheel': f'cactus_needle-{ENGINE_VERSION}-py3-none-manylinux2014_x86_64.whl',
        'sha256': '3de03a183f56e28f8f1945fbbc5048efca0699a7f3ed0d66a244ca7e30442c22',
        'member': 'needle/libneedle3.so',
        'library': 'libneedle3.so',
    },
    'macos-arm64': {
        'wheel': f'cactus_needle-{ENGINE_VERSION}-py3-none-macosx_11_0_arm64.whl',
        'sha256': '19534f411ad71312031d6df5418d0e55b8d16075c28a217aef4b921349bdfb4a',
        'member': 'needle/libneedle3.dylib',
        'library': 'libneedle3.dylib',
    },
    'macos-x86_64': {
        'wheel': f'cactus_needle-{ENGINE_VERSION}-py3-none-macosx_11_0_x86_64.whl',
        'sha256': 'e74ac1204ffe29d75db1215d889ad46c8add39e52042df22cd99c2e37855c273',
        'member': 'needle/libneedle3.dylib',
        'library': 'libneedle3.dylib',
    },
    'windows-x86_64': {
        'wheel': f'cactus_needle-{ENGINE_VERSION}-py3-none-win_amd64.whl',
        'sha256': '4e50c5fa983c8737de200d9e96af937dd8135371ca557fe8c7c9eb1adba39f5a',
        'member': 'needle/libneedle3.dll',
        'library': 'libneedle3.dll',
    },
}


def detect_platform() -> str:
    if sys.platform == 'darwin':
        return 'macos-arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'macos-x86_64'
    if sys.platform == 'win32':
        return 'windows-x86_64'
    if sys.platform.startswith('linux') and platform.machine().lower() in ('x86_64', 'amd64'):
        return 'linux-x86_64'
    raise SystemExit(f'FATAL: no pinned engine for {sys.platform} {platform.machine()}; '
                     f'pass --platform with one of: {", ".join(PLATFORMS)}')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path, expected_sha: str, expected_size: int | None = None) -> Path:
    """Fetch `url` into `dest` unless a verified copy is already there."""
    if dest.is_file() and _sha256(dest) == expected_sha:
        print(f'cached: {dest.name}')
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + '.part')
    print(f'downloading: {url}')
    with urllib.request.urlopen(url, timeout=60) as response, part.open('wb') as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    if expected_size is not None and part.stat().st_size != expected_size:
        part.unlink()
        raise SystemExit(f'FATAL: {dest.name} is {part.stat().st_size} bytes, expected {expected_size}')
    actual = _sha256(part)
    if actual != expected_sha:
        part.unlink()
        raise SystemExit(f'FATAL: {dest.name} sha256 is {actual}, expected {expected_sha}')
    part.replace(dest)
    return dest


def fetch(platform_key: str, out: Path, cache: Path) -> None:
    target = PLATFORMS[platform_key]
    wheel = _download(ENGINE_BASE + target['wheel'], cache / target['wheel'], target['sha256'])
    with zipfile.ZipFile(wheel) as archive:
        library = out / target['library']
        library.parent.mkdir(parents=True, exist_ok=True)
        library.write_bytes(archive.read(target['member']))
    weights = _download(WEIGHTS_URL, cache / WEIGHTS_FILE, WEIGHTS_SHA256, WEIGHTS_SIZE)
    (out / WEIGHTS_FILE).write_bytes(weights.read_bytes())
    print(f'assets for {platform_key} in {out}:')
    for path in sorted(out.iterdir()):
        if path.is_file():
            print(f'  {path.name} {path.stat().st_size:,} bytes sha256={_sha256(path)}')


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--platform', choices=sorted(PLATFORMS), default=None)
    parser.add_argument('--out', type=Path, default=Path('build') / 'assets')
    parser.add_argument('--cache', type=Path, default=Path('build') / 'downloads')
    args = parser.parse_args(argv)
    fetch(args.platform or detect_platform(), args.out, args.cache)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
