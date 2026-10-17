"""Whistle itself, on the bundled engine and weights.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

These need the engine and the weights, which tools/fetch_assets.py places in
build/assets (or the folder named by WHISTLE_ADDON_ASSETS). Without them the
tests are skipped, so a plain checkout still runs the rest of the suite.

The sample's sentence is the reference: Whistle should get the words right.
The add-on process itself is tested in test_installed.py.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from array import array
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from whistle_addon import __main__ as addon  # noqa: E402
from whistle_addon.engine import SAMPLE_RATE, Engine  # noqa: E402

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
    assert engine.transcribe(array('f', [0.0]) * (SAMPLE_RATE * 2))['text'] == ''


@needs_assets
@needs_ffmpeg
def test_keywords_do_not_break_the_transcript(engine, sample):
    result = engine.transcribe(sample, language='en', keywords=['Kennedy', 'Americans'])
    assert SENTENCE in ' '.join(result['text'].lower().split())
