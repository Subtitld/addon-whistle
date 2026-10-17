"""Whistle speech-to-text, called straight into Needle's native engine.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Whistle is the speech model of Cactus Compute's Needle family, released under
Apache-2.0. Its weights and its engine library are two files, both pinned by
sha256 in tools/fetch_assets.py and bundled into the add-on. This module loads
them with ctypes and makes the calls transcription needs.

The signatures are the ones cactus-needle 3.2.4 (needle/agent/whistle.py) uses
for the same functions. The package itself is not imported: it brings a
telemetry client, a downloader that can fetch native code at runtime, and a
dependency tree this add-on does not need.
"""

from __future__ import annotations

import ctypes
import json
import threading
from array import array

SAMPLE_RATE = 16000
LANGUAGES = ('en', 'de', 'fr', 'es', 'it', 'nl', 'pl')

# The engine writes one JSON document, at most this many bytes per second of
# audio. The package reserves the same amount. The buffer only ever grows.
_OUTPUT_BYTES_PER_SECOND = 4096
_MIN_BUFFER_BYTES = 1 << 18


class EngineError(Exception):
    """The engine refused a call. The message is the engine's own."""


class Engine:
    """One Whistle model, loaded once. Calls are serialised: the engine is not thread-safe."""

    def __init__(self, library_path: str, weights_path: str):
        self._lock = threading.Lock()
        lib = ctypes.CDLL(library_path)
        samples = ctypes.POINTER(ctypes.c_float)
        lib.needle_load.argtypes = [ctypes.c_char_p, ctypes.c_uint64]
        lib.needle_load.restype = ctypes.c_int
        lib.needle_transcribe.argtypes = [samples, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                                          ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        lib.needle_transcribe.restype = ctypes.c_int
        lib.needle_last_error.argtypes = []
        lib.needle_last_error.restype = ctypes.c_char_p
        self._lib = lib
        with open(weights_path, 'rb') as handle:
            data = handle.read()
        if lib.needle_load(data, len(data)) < 0:
            raise EngineError(self._last_error())
        self._buffer = ctypes.create_string_buffer(_MIN_BUFFER_BYTES)

    def _last_error(self) -> str:
        message = self._lib.needle_last_error()
        return message.decode('utf-8', 'replace') if message else 'unknown engine error'

    def transcribe(self, samples: array, language: str | None = None,
                   keywords: list[str] | None = None) -> dict:
        """Transcribe 16 kHz mono float samples in [-1, 1], of any length.

        `samples` is an array('f'). Returns the engine's dict: "text", "language",
        "ttft_ms", "decode_tps" and "words". Each word has "word", "start", "end"
        and "probability", in seconds from the start of the audio. `language` is
        one of LANGUAGES, or None to detect it. `keywords` are names or phrases
        to favour.
        """
        if samples.typecode != 'f':
            raise TypeError("samples must be array('f')")
        count = len(samples)
        if count == 0:
            raise ValueError('there are no samples to transcribe')
        language_arg = language.encode('utf-8') if language else None
        keywords_arg = '\n'.join(keywords).encode('utf-8') if keywords else None
        needed = count // SAMPLE_RATE * _OUTPUT_BYTES_PER_SECOND
        # The array's own buffer, not a copy: it stays alive for the call.
        pointer = (ctypes.c_float * count).from_buffer(samples)
        with self._lock:
            if needed > len(self._buffer):
                self._buffer = ctypes.create_string_buffer(needed)
            code = self._lib.needle_transcribe(pointer, count, language_arg, keywords_arg, 1,
                                               self._buffer, len(self._buffer))
            if code < 0:
                raise EngineError(self._last_error())
            return json.loads(self._buffer.value.decode('utf-8', 'replace'))
