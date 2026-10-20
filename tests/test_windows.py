"""Windowing long audio: full coverage, cuts in the quiet, no stub at the end.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whistle_addon.engine import SAMPLE_RATE  # noqa: E402
from whistle_addon.windows import windows  # noqa: E402


def _tone(seconds: float, quiet_at: tuple[float, ...] = ()) -> array:
    """A constant signal, silent for 0.3 s around each time in `quiet_at`."""
    samples = array('f', [0.5]) * int(seconds * SAMPLE_RATE)
    for t in quiet_at:
        first = int((t - 0.15) * SAMPLE_RATE)
        samples[first:first + int(0.3 * SAMPLE_RATE)] = array('f', [0.0]) * int(0.3 * SAMPLE_RATE)
    return samples


def test_short_audio_is_one_window():
    assert windows(_tone(12.0)) == [(0, 12 * SAMPLE_RATE)]


def test_windows_cover_the_audio_end_to_end():
    samples = _tone(200.0)
    ranges = windows(samples)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(samples)
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:]))
    assert all(50 * SAMPLE_RATE <= end - start <= 60 * SAMPLE_RATE for start, end in ranges[:-1])
    assert 5 * SAMPLE_RATE < ranges[-1][1] - ranges[-1][0] <= 65 * SAMPLE_RATE


def test_cuts_land_in_the_quiet():
    ranges = windows(_tone(140.0, quiet_at=(54.0, 111.0)))
    cuts = [end / SAMPLE_RATE for _, end in ranges[:-1]]
    assert len(cuts) == 2
    assert abs(cuts[0] - 54.0) < 0.15 and abs(cuts[1] - 111.0) < 0.15


def test_a_short_tail_joins_the_last_window():
    ranges = windows(_tone(63.0))
    assert ranges == [(0, 63 * SAMPLE_RATE)]
