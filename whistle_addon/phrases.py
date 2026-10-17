"""Turn Whistle's word timings into subtitle rows.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The rules are the whisper.cpp add-on's phrase regrouping, applied to words
rather than raw segments. Whistle reports word times directly, so there is no
segment to split first. A row closes at sentence-final punctuation, and early
when it would pass the readability caps.
"""

from __future__ import annotations

from collections.abc import Iterable

# Same sets as the whisper.cpp add-on. Its comments explain the choices.
PHRASE_TERMINATORS = frozenset('.?!。？！．।॥؟')
TRAILING_CLOSERS = frozenset('"\')]}»›”’')

# Captioning readability caps: about two lines of 42 characters, or 7 seconds.
MAX_CHARS = 84
MAX_DURATION_SEC = 7.0


def ends_with_terminator(text: str) -> bool:
    """True if `text` ends with sentence-final punctuation.

    Closing quotes and brackets may follow it, so `She said "hello."` still counts.
    """
    t = text.rstrip()
    for _ in range(4):
        if not t:
            return False
        if t[-1] in TRAILING_CLOSERS:
            t = t[:-1].rstrip()
            continue
        break
    return bool(t) and t[-1] in PHRASE_TERMINATORS


def words_to_segments(words: Iterable[dict], duration_sec: float, text: str = '') -> list[dict]:
    """Group word timings into rows of {start, end, text, speaker}.

    With no word timings but some text, the whole text becomes one row over the
    full duration. Rows keep the order of the words.
    """
    rows: list[dict] = []
    current: dict | None = None
    for word in words:
        token = str(word.get('word') or '').strip()
        if not token:
            continue
        start = float(word.get('start') or 0.0)
        end = float(word.get('end') or start)
        if current is not None and (
                ends_with_terminator(current['text'])
                or len(current['text']) + 1 + len(token) > MAX_CHARS
                or end - current['start'] > MAX_DURATION_SEC):
            rows.append(current)
            current = None
        if current is None:
            current = {'start': round(start, 3), 'end': round(end, 3), 'text': token, 'speaker': 'A'}
        else:
            current['text'] += ' ' + token
            current['end'] = round(end, 3)
    if current is not None:
        rows.append(current)
    if not rows and text.strip():
        rows.append({'start': 0.0, 'end': round(duration_sec, 3), 'text': text.strip(), 'speaker': 'A'})
    return rows
