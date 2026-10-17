"""The add-on as Subtitld runs it: a process spoken to over stdio, transcribing for real.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

By default the process is the source tree (`python -m whistle_addon`) with the
assets from tools/fetch_assets.py. With WHISTLE_TEST_BINARY set it is that
executable instead, which is how the release workflow runs these tests: on the
frozen binary unpacked from the release zip the way Subtitld installs it, with
nothing but the engine and weights inside its own bundle.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from whistle_addon import __main__ as addon  # noqa: E402
from whistle_addon.phrases import MAX_CHARS  # noqa: E402

MANIFEST = json.loads((REPO_ROOT / 'manifest.json').read_text(encoding='utf-8'))
SAMPLE = REPO_ROOT / 'tests' / 'data' / 'jfk.flac'
SENTENCE = 'ask not what your country can do for you'
BINARY = os.environ.get('WHISTLE_TEST_BINARY')
ASSETS = Path(os.environ.get('WHISTLE_ADDON_ASSETS') or REPO_ROOT / 'build' / 'assets')
HAVE_ASSETS = bool(BINARY) or ((ASSETS / addon._library_name()).is_file() and (ASSETS / 'whistle.cact').is_file())

needs_assets = pytest.mark.skipif(not HAVE_ASSETS, reason='no engine or weights; run tools/fetch_assets.py')
needs_ffmpeg = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='needs ffmpeg')


def _command() -> tuple[list[str], dict]:
    env = dict(os.environ)
    if BINARY:
        # The bundle's own assets and code, nothing from this checkout.
        env.pop('WHISTLE_ADDON_ASSETS', None)
        env.pop('PYTHONPATH', None)
        return [BINARY], env
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['WHISTLE_ADDON_ASSETS'] = str(ASSETS)
    return [sys.executable, '-m', 'whistle_addon'], env


class Session:
    def __init__(self, cwd: Path):
        command, env = _command()
        self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.hello = self.recv()
        self.send({'type': 'ready', 'host': 'subtitld', 'host_version': 'test', 'protocol': 1})

    def send(self, frame: dict) -> None:
        self.proc.stdin.write((json.dumps(frame) + '\n').encode('utf-8'))
        self.proc.stdin.flush()

    def recv(self) -> dict:
        line = self.proc.stdout.readline()
        assert line, 'the add-on closed its output: ' + self.proc.stderr.read().decode('utf-8', 'replace')
        return json.loads(line)

    def request(self, req_id: str, params: dict) -> list[dict]:
        """Every frame for one request, up to and including its terminal frame."""
        self.send({'id': req_id, 'type': 'asr.transcribe', 'params': params})
        frames = []
        while True:
            frame = self.recv()
            assert frame.get('id') == req_id, frame
            frames.append(frame)
            if frame['type'] in ('result', 'error'):
                return frames

    def stop(self) -> int:
        try:
            self.send({'type': 'shutdown'})
            return self.proc.wait(timeout=15)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()


@pytest.fixture
def session(tmp_path):
    running = Session(tmp_path)
    yield running
    running.stop()


def test_hello_names_this_release(session):
    hello = session.hello
    assert hello['type'] == 'hello'
    assert hello['addon'] == MANIFEST['id']
    assert hello['version'] == MANIFEST['version']
    (capability,) = hello['capabilities']
    assert capability['task'] == 'asr.transcribe'
    assert capability['languages'] == MANIFEST['languages']


@needs_assets
@needs_ffmpeg
def test_transcribes_the_sample(session):
    frames = session.request('t1', {'audio_path': str(SAMPLE), 'language': 'en-us',
                                    'options': {'keywords': 'Kennedy, Americans'}})
    progress = [f['value'] for f in frames if f['type'] == 'progress']
    assert progress == sorted(progress) and progress[-1] == 1.0
    result = frames[-1]
    assert result['type'] == 'result', result
    data = result['data']
    assert data['model'] == 'whistle'
    assert data['language'] == 'en'
    assert 10.0 < data['duration_sec'] < 12.0
    segments = data['segments']
    assert segments and segments[0]['text'].startswith('And so')
    assert all(s['end'] > s['start'] and len(s['text']) <= MAX_CHARS for s in segments)
    assert SENTENCE in ' '.join(s['text'].lower() for s in segments)


@needs_assets
@needs_ffmpeg
def test_detects_the_language_when_the_project_names_none(session):
    result = session.request('t2', {'audio_path': str(SAMPLE), 'language': ''})[-1]
    assert result['type'] == 'result', result
    assert result['data']['language'] == 'en'


def test_refuses_a_language_outside_the_seven(session):
    result = session.request('t3', {'audio_path': str(SAMPLE), 'language': 'pt-br'})[-1]
    assert result['type'] == 'error' and result['code'] == 'unsupported_language'


def test_shutdown_exits_with_status_zero(tmp_path):
    assert Session(tmp_path).stop() == 0
