"""The add-on spoken over its wire format, in a real process.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

These run `python -m whistle_addon` and talk JSON lines to it, the way the
host does. They cover every answer the add-on owes the host without needing
the engine or the model.
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

from whistle_addon.engine import LANGUAGES  # noqa: E402

SAMPLE = REPO_ROOT / 'tests' / 'data' / 'jfk.flac'
NO_ASSETS = REPO_ROOT / 'tests' / 'no-such-assets'


def _start(assets: Path = NO_ASSETS) -> subprocess.Popen:
    env = dict(os.environ)
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['WHISTLE_ADDON_ASSETS'] = str(assets)
    return subprocess.Popen([sys.executable, '-m', 'whistle_addon'], cwd=REPO_ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def _send(proc: subprocess.Popen, frame: dict) -> None:
    proc.stdin.write((json.dumps(frame) + '\n').encode('utf-8'))
    proc.stdin.flush()


def _recv(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    assert line, 'the add-on closed its output: ' + proc.stderr.read().decode('utf-8', 'replace')
    return json.loads(line)


def _stop(proc: subprocess.Popen) -> int:
    try:
        _send(proc, {'type': 'shutdown'})
        return proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.fixture
def addon():
    proc = _start()
    hello = _recv(proc)
    _send(proc, {'type': 'ready', 'host': 'subtitld', 'host_version': 'test', 'protocol': 1})
    yield proc, hello
    _stop(proc)


def test_hello_declares_the_task_and_its_languages(addon):
    _, hello = addon
    assert hello['type'] == 'hello'
    assert hello['addon'] == 'whistle'
    assert hello['protocol'] == 1
    (capability,) = hello['capabilities']
    assert capability['task'] == 'asr.transcribe'
    assert capability['languages'] == list(LANGUAGES)
    assert capability['streaming'] is False


def test_an_unknown_task_is_still_answered(addon):
    proc, _ = addon
    _send(proc, {'id': 'u1', 'type': 'asr.stream', 'params': {}})
    frame = _recv(proc)
    assert frame == {'id': 'u1', 'type': 'error', 'code': 'bad_params',
                     'message': "unsupported frame type 'asr.stream'", 'retryable': False}


def test_a_missing_audio_file_is_bad_params(addon):
    proc, _ = addon
    _send(proc, {'id': 'a1', 'type': 'asr.transcribe', 'params': {'audio_path': '/no/such.wav', 'language': 'en'}})
    frame = _recv(proc)
    assert frame['id'] == 'a1' and frame['type'] == 'error' and frame['code'] == 'bad_params'


def test_a_language_outside_the_seven_is_refused_before_decoding(addon):
    proc, _ = addon
    _send(proc, {'id': 'l1', 'type': 'asr.transcribe',
                 'params': {'audio_path': str(SAMPLE), 'language': 'pt-br'}})
    frame = _recv(proc)
    assert frame['id'] == 'l1' and frame['code'] == 'unsupported_language'
    assert 'pt-br' in frame['message']


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='needs ffmpeg to decode the sample')
def test_missing_assets_are_reported_not_downloaded(addon):
    proc, _ = addon
    _send(proc, {'id': 'm1', 'type': 'asr.transcribe', 'params': {'audio_path': str(SAMPLE), 'language': 'en-us'}})
    frames = []
    while True:
        frame = _recv(proc)
        frames.append(frame)
        if frame['type'] in ('result', 'error'):
            break
    assert frames[-1]['type'] == 'error'
    assert frames[-1]['code'] == 'model_missing'
    assert 'whistle.cact' in frames[-1]['message'] or 'libneedle3' in frames[-1]['message']


def test_cancel_of_an_unknown_request_is_ignored(addon):
    proc, _ = addon
    _send(proc, {'id': 'x', 'type': 'cancel', 'target': 'nobody'})
    _send(proc, {'id': 'y', 'type': 'asr.stream', 'params': {}})
    assert _recv(proc)['id'] == 'y'


def test_shutdown_exits_with_status_zero():
    proc = _start()
    _recv(proc)
    assert _stop(proc) == 0
