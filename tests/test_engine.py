"""Whistle itself, on the bundled engine and weights.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

These need the engine and the weights, which tools/fetch_assets.py places in
build/assets (or the folder named by WHISTLE_ADDON_ASSETS). Without them the
tests are skipped, so a plain checkout still runs the rest of the suite.

The sample is the JFK clip whisper.cpp ships for its own tests. Its sentence
is the reference: Whistle should get the words right.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from whistle_addon import __main__ as addon  # noqa: E402
from whistle_addon.engine import SAMPLE_RATE, Engine  # noqa: E402
from whistle_addon.phrases import MAX_CHARS  # noqa: E402

SAMPLE = REPO_ROOT / 'tests' / 'data' / 'jfk.flac'
SENTENCE = 'ask not what your country can do for you'
ASSETS = Path(os.environ.get('WHISTLE_ADDON_ASSETS') or REPO_ROOT / 'build' / 'assets')
LIBRARY = ASSETS / addon._library_name()
WEIGHTS = ASSETS / 'whistle.cact'
FFMPEG = shutil.which('ffmpeg')

needs_assets = pytest.mark.skipif(not (LIBRARY.is_file() and WEIGHTS.is_file()),
                                  reason='no engine or weights; run tools/fetch_assets.py')
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason='needs ffmpeg')


@pytest.fixture(scope='module')
def engine():
    return Engine(str(LIBRARY), str(WEIGHTS))


@pytest.fixture(scope='module')
def sample():
    return addon._decode(FFMPEG, str(SAMPLE), threading.Event())


@needs_assets
@needs_ffmpeg
def test_transcribes_the_sample_with_word_timings(engine, sample):
    result = engine.transcribe(sample, language='en')
    assert SENTENCE in ' '.join(result['text'].lower().split())
    words = result['words']
    assert len(words) >= 15
    assert all(w['end'] >= w['start'] >= 0.0 for w in words)
    assert [w['start'] for w in words] == sorted(w['start'] for w in words)
    assert all(0.0 <= w['probability'] <= 1.0 for w in words)


@needs_assets
@needs_ffmpeg
def test_detects_the_language_when_none_is_given(engine, sample):
    assert engine.transcribe(sample)['language'] == 'en'


@needs_assets
def test_silence_is_an_empty_transcript(engine):
    assert engine.transcribe(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))['text'] == ''


@needs_assets
@needs_ffmpeg
def test_keywords_do_not_break_the_transcript(engine, sample):
    result = engine.transcribe(sample, language='en', keywords=['Kennedy', 'Americans'])
    assert SENTENCE in ' '.join(result['text'].lower().split())


@needs_assets
@needs_ffmpeg
def test_the_protocol_transcribes_the_sample_end_to_end():
    env = dict(os.environ)
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['WHISTLE_ADDON_ASSETS'] = str(ASSETS)
    proc = subprocess.Popen([sys.executable, '-m', 'whistle_addon'], cwd=REPO_ROOT, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        json.loads(proc.stdout.readline())  # hello
        request = {'id': 'e2e', 'type': 'asr.transcribe',
                   'params': {'audio_path': str(SAMPLE), 'language': 'en-us',
                              'options': {'keywords': 'Kennedy'}}}
        proc.stdin.write((json.dumps(request) + '\n').encode('utf-8'))
        proc.stdin.flush()
        frames = []
        while True:
            frame = json.loads(proc.stdout.readline())
            frames.append(frame)
            if frame['type'] in ('result', 'error'):
                break
    finally:
        proc.stdin.write(b'{"type":"shutdown"}\n')
        proc.stdin.flush()
        proc.wait(timeout=15)

    assert frames[-1]['type'] == 'result', frames[-1]
    data = frames[-1]['data']
    assert data['model'] == 'whistle'
    assert data['language'] == 'en'
    assert 10.0 < data['duration_sec'] < 12.0
    segments = data['segments']
    assert segments and segments[0]['text'].startswith('And so')
    assert all(s['end'] > s['start'] and len(s['text']) <= MAX_CHARS for s in segments)
    assert SENTENCE in ' '.join(s['text'].lower() for s in segments)
