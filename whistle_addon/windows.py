"""Cut long audio into windows the engine transcribes one at a time.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

One engine call over a whole file gets slower per second of audio as the file
grows (20 minutes took twice as long as forty 30-second calls), and it cannot
report progress or be cancelled. So the audio goes in as windows of about
WINDOW_SEC, each cut at the quietest moment near its end, where no word is
likely to be split.
"""

from __future__ import annotations

from array import array

from whistle_addon.engine import SAMPLE_RATE

WINDOW_SEC = 60.0
# The cut is searched for over this last stretch of each window.
SEARCH_SEC = 10.0
# A tail shorter than this joins the window before it.
MIN_TAIL_SEC = 5.0
# Loudness is measured over frames this long, on every STRIDE-th sample.
_FRAME = SAMPLE_RATE // 10
_STRIDE = 4


def _loudness(samples: array, start: int) -> float:
    return sum(x * x for x in samples[start:start + _FRAME:_STRIDE])


def windows(samples: array, window_sec: float = WINDOW_SEC, search_sec: float = SEARCH_SEC,
            min_tail_sec: float = MIN_TAIL_SEC) -> list[tuple[int, int]]:
    """(start, end) sample ranges that cover `samples` end to end, in order."""
    total = len(samples)
    window = int(window_sec * SAMPLE_RATE)
    search = int(search_sec * SAMPLE_RATE)
    min_tail = int(min_tail_sec * SAMPLE_RATE)
    ranges: list[tuple[int, int]] = []
    start = 0
    while total - start > window + min_tail:
        first = start + window - search
        cut = min(range(first, start + window - _FRAME + 1, _FRAME),
                  key=lambda frame: _loudness(samples, frame))
        cut += _FRAME // 2  # the middle of the quietest frame
        ranges.append((start, cut))
        start = cut
    if start < total:
        ranges.append((start, total))
    return ranges
