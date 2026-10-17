"""Word timings to subtitle rows: the rules the host's subtitles depend on.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from whistle_addon.phrases import MAX_CHARS, MAX_DURATION_SEC, ends_with_terminator, words_to_segments  # noqa: E402


def word(text: str, start: float, end: float) -> dict:
    return {'word': text, 'start': start, 'end': end, 'probability': 0.9}


def test_sentence_end_closes_a_row():
    rows = words_to_segments([word('Hello.', 0.0, 0.5), word('World', 0.6, 1.0)], 2.0)
    assert [row['text'] for row in rows] == ['Hello.', 'World']
    assert rows[0] == {'start': 0.0, 'end': 0.5, 'text': 'Hello.', 'speaker': 'A'}


def test_a_row_keeps_words_together_mid_sentence():
    rows = words_to_segments([word('ask', 0.0, 0.3), word('not', 0.3, 0.6), word('what', 0.6, 0.9)], 1.0)
    assert rows == [{'start': 0.0, 'end': 0.9, 'text': 'ask not what', 'speaker': 'A'}]


def test_closing_quote_still_counts_as_a_sentence_end():
    assert ends_with_terminator('She said "hello."')
    assert not ends_with_terminator('hello')
    assert not ends_with_terminator('')


def test_character_cap_closes_a_row():
    words = [word('x' * 40, float(i), i + 0.5) for i in range(3)]
    rows = words_to_segments(words, 3.0)
    # 40 + 1 + 40 = 81 fits the cap; a third word would make 122 and so starts a new row.
    assert [len(row['text']) for row in rows] == [81, 40]
    assert all(len(row['text']) <= MAX_CHARS for row in rows)


def test_duration_cap_closes_a_row():
    words = [word('a', float(t), t + 1.0) for t in range(20)]
    rows = words_to_segments(words, 20.0)
    assert sum(len(row['text'].split()) for row in rows) == 20
    assert all(row['end'] - row['start'] <= MAX_DURATION_SEC for row in rows)


def test_missing_word_timings_fall_back_to_the_text():
    rows = words_to_segments([], 4.0, 'Plain text')
    assert rows == [{'start': 0.0, 'end': 4.0, 'text': 'Plain text', 'speaker': 'A'}]


def test_blank_tokens_are_skipped():
    rows = words_to_segments([word(' ', 0.0, 0.1), word('Hi', 0.1, 0.3)], 0.3)
    assert rows == [{'start': 0.1, 'end': 0.3, 'text': 'Hi', 'speaker': 'A'}]


def test_no_words_and_no_text_gives_no_rows():
    assert words_to_segments([], 5.0, '  ') == []
