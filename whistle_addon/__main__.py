"""Whistle add-on for Subtitld — JSON-line protocol entry point.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License"); you may not
use this file except in compliance with the License. You may obtain a copy of
the License at http://www.apache.org/licenses/LICENSE-2.0. Unless required by
applicable law or agreed to in writing, software distributed under the License
is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.

What this add-on is
-------------------
Offline speech-to-text with Whistle, Cactus Compute's speech model, run by
its native engine. Serves `asr.transcribe`. The model and the engine are in
the archive. Transcription downloads nothing and sends nothing anywhere.

Wire format
-----------
One JSON object per line on stdin/stdout. Stderr is free-form logging, which
the host prefixes with `[addon:whistle]` and surfaces.

Options
-------
`keywords`, or $WHISTLE_KEYWORDS: names and jargon, separated by commas or new
lines. Whistle favours them when it is unsure of a word.

Limits
------
Batch only: one result per request. The engine call cannot be interrupted, so
a cancel takes effect before decoding starts, or once the engine returns.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from whistle_addon import ADDON_ID, PROTOCOL_VERSION, __version__
from whistle_addon.engine import LANGUAGES, SAMPLE_RATE, Engine, EngineError
from whistle_addon.phrases import words_to_segments

ADDON_VERSION = __version__
MODEL_ID = 'whistle'
WEIGHTS_FILE = 'whistle.cact'
# Below this there is nothing to transcribe, and the engine would refuse it.
_MIN_SAMPLES = SAMPLE_RATE // 10

ERR_MODEL_MISSING = 'model_missing'
ERR_UNSUPPORTED_LANGUAGE = 'unsupported_language'
ERR_BAD_PARAMS = 'bad_params'
ERR_INTERNAL = 'internal'
ERR_CANCELLED = 'cancelled'
ERR_OOM = 'oom'
ERR_DISK_FULL = 'disk_full'

_LANGUAGE_SET = frozenset(LANGUAGES)

log = logging.getLogger('whistle_addon')


# ---------------------------------------------------------------------------
# stdio ownership + protocol frames
# ---------------------------------------------------------------------------
_PROTO_IN = None   # binary reader for host frames
_PROTO_OUT = None  # binary writer for our frames
_proto_lock = threading.Lock()


def _claim_protocol_stdio() -> None:
    """Give the protocol exclusive use of the real stdin/stdout.

    fd-level, not just `sys.stdout = sys.stderr`: the engine is native code and
    may write to fd 1 directly. Falls back to the Python-level swap if the fd
    dance fails.
    """
    global _PROTO_IN, _PROTO_OUT
    try:
        sys.stdout.flush()
        in_fd = os.dup(0)
        out_fd = os.dup(1)
        devnull = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(devnull, 0)
        finally:
            os.close(devnull)
        os.dup2(2, 1)
        _PROTO_IN = os.fdopen(in_fd, 'rb')
        _PROTO_OUT = os.fdopen(out_fd, 'wb')
    except (OSError, ValueError, AttributeError):
        _PROTO_IN = sys.stdin.buffer
        _PROTO_OUT = sys.stdout.buffer
    sys.stdin = open(os.devnull, encoding='utf-8')  # stray input() -> EOFError
    sys.stdout = sys.stderr                        # stray print() -> the log


def _emit(frame: dict) -> None:
    data = (json.dumps(frame, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    out = _PROTO_OUT if _PROTO_OUT is not None else sys.stdout.buffer
    with _proto_lock:
        try:
            out.write(data)
            out.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # host went away; the frame loop exits on the next empty read


def _emit_progress(req_id: str, value: float, message: str = '') -> None:
    _emit({'id': req_id, 'type': 'progress',
           'value': max(0.0, min(1.0, float(value))), 'message': message})


def _emit_result(req_id: str, data: dict) -> None:
    _emit({'id': req_id, 'type': 'result', 'data': data})


def _emit_error(req_id: str, code: str, message: str, retryable: bool = False) -> None:
    _emit({'id': req_id, 'type': 'error', 'code': code,
           'message': message, 'retryable': retryable})


class _AddonError(Exception):
    """An expected failure that already knows its protocol code."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class _Cancelled(Exception):
    """The host cancelled this request."""


# ---------------------------------------------------------------------------
# Options and environment
# ---------------------------------------------------------------------------
def _env(*names: str) -> str:
    """First non-empty value among `names`, stripped. '' when none are set."""
    for name in names:
        value = (os.environ.get(name) or '').strip()
        if value:
            return value
    return ''


def _resolve_language(raw) -> str | None:
    """A Whistle language code, or None to detect it.

    The host sends BCP-47-ish tags (`pt-br`, `en_US`). Whistle takes the bare
    primary subtag. A language outside its seven is an error, not a guess.
    """
    tag = str(raw or '').strip().lower().replace('_', '-')
    if not tag or tag in ('auto', 'und', 'mul'):
        return None
    primary = tag.split('-', 1)[0]
    if primary in _LANGUAGE_SET:
        return primary
    raise _AddonError(ERR_UNSUPPORTED_LANGUAGE,
                      f'Whistle does not transcribe {raw!r}. It transcribes '
                      f'{", ".join(LANGUAGES)}; pick another engine for other languages.')


def _resolve_keywords(options: dict) -> list[str]:
    """Keywords from the request, else the environment. Blank and repeated entries drop out."""
    raw = options.get('keywords') if isinstance(options, dict) else None
    if raw is None or raw == '' or raw == []:
        raw = _env('WHISTLE_KEYWORDS')
    items = raw if isinstance(raw, list) else str(raw or '').replace('\n', ',').split(',')
    seen: set[str] = set()
    keywords: list[str] = []
    for item in items:
        word = str(item).strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            keywords.append(word)
    return keywords


# ---------------------------------------------------------------------------
# Bundled assets
# ---------------------------------------------------------------------------
def _asset_dir() -> Path:
    """Where the engine and the weights are. Source runs use `assets/` at the repo root."""
    override = _env('WHISTLE_ADDON_ASSETS')
    if override:
        return Path(os.path.expanduser(override))
    bundled = getattr(sys, '_MEIPASS', None)  # PyInstaller onedir: the `_internal` folder
    if bundled:
        return Path(bundled) / 'assets'
    return Path(__file__).resolve().parent.parent / 'assets'


def _library_name() -> str:
    if sys.platform == 'darwin':
        return 'libneedle3.dylib'
    if sys.platform == 'win32':
        return 'libneedle3.dll'
    return 'libneedle3.so'


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
def _find_ffmpeg() -> str | None:
    for candidate in (_env('SUBTITLD_FFMPEG_EXECUTABLE'), _env('FFMPEG_EXECUTABLE')):
        if candidate and (os.path.isfile(candidate) or shutil.which(candidate)):
            return candidate
    return shutil.which('ffmpeg')


def _no_window_startupinfo():
    """Keep ffmpeg from flashing a console window on Windows GUI hosts."""
    if sys.platform != 'win32':
        return None
    si = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    si.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    return si


def _child_env() -> dict | None:
    """Environment for ffmpeg, or None to inherit ours unchanged.

    The frozen Linux bootloader prepends the bundle's `_internal/` to
    LD_LIBRARY_PATH, and a system ffmpeg that inherits it loads the bundle's
    libraries instead of its own. PyInstaller saves the host value in
    LD_LIBRARY_PATH_ORIG, so restore it for the child.
    """
    if not (getattr(sys, 'frozen', False) and sys.platform.startswith('linux')):
        return None
    env = dict(os.environ)
    original = env.get('LD_LIBRARY_PATH_ORIG')
    if original is not None:
        env['LD_LIBRARY_PATH'] = original
    else:
        env.pop('LD_LIBRARY_PATH', None)
    return env


def _decode(ffmpeg: str, src: str, cancel: threading.Event) -> np.ndarray:
    """Decode anything ffmpeg reads into 16 kHz mono float32 samples in [-1, 1].

    The raw PCM comes back on a pipe, so no temporary file is written.
    `-nostdin` plus stdin=DEVNULL: ffmpeg polls stdin for keystrokes.
    """
    cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
           '-i', src, '-vn', '-sn', '-dn',
           '-ac', '1', '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1']
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=_child_env(),
                            startupinfo=_no_window_startupinfo())
    while True:
        try:
            raw, err = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                proc.kill()
                proc.communicate()
                raise _Cancelled() from None
    if proc.returncode != 0 or not raw:
        tail = ' / '.join((err or b'').decode('utf-8', 'replace').strip().splitlines()[-5:])
        if 'No space left' in tail:
            raise _AddonError(ERR_DISK_FULL, f'Out of disk space while decoding the audio: {tail}')
        raise _AddonError(ERR_INTERNAL,
                          f'Could not decode the audio with ffmpeg '
                          f'(exit {proc.returncode}): {tail or "no output"}')
    # One float copy, scaled in place: an hour of audio is already ~115 MB of PCM.
    samples = np.frombuffer(raw, dtype='<i2', count=len(raw) // 2).astype(np.float32)
    del raw
    samples /= 32768.0
    return samples


# ---------------------------------------------------------------------------
# The engine: loaded on first use, shared by every request
# ---------------------------------------------------------------------------
_engine: Engine | None = None
_engine_lock = threading.Lock()


def _load_engine(req_id: str) -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            assets = _asset_dir()
            library = assets / _library_name()
            weights = assets / WEIGHTS_FILE
            for path in (library, weights):
                if not path.is_file():
                    raise _AddonError(ERR_MODEL_MISSING,
                                      f'{path.name} is missing from the add-on ({path}). '
                                      f'Reinstall the add-on.')
            _emit_progress(req_id, 0.1, 'Loading Whistle...')
            started = time.monotonic()
            try:
                _engine = Engine(str(library), str(weights))
            except (OSError, EngineError) as exc:
                raise _AddonError(ERR_INTERNAL, f'The Whistle engine failed to load: {exc}') from exc
            log.info('whistle loaded in %.2fs from %s', time.monotonic() - started, assets)
        return _engine


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------
class _Request:
    def __init__(self, req_id: str):
        self.req_id = req_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None


_active: dict[str, _Request] = {}
_active_lock = threading.Lock()
_shutting_down = threading.Event()


def _handle_transcribe(state: _Request, params: dict) -> None:
    req_id = state.req_id
    options = params.get('options')
    if not isinstance(options, dict):
        options = {}
    audio_path = params.get('audio_path')
    if not isinstance(audio_path, str) or not audio_path.strip():
        _emit_error(req_id, ERR_BAD_PARAMS, 'audio_path is required')
        return
    if not os.path.isfile(audio_path):
        _emit_error(req_id, ERR_BAD_PARAMS, f'audio_path is not a file: {audio_path!r}')
        return
    try:
        language = _resolve_language(params.get('language'))
        keywords = _resolve_keywords(options)
    except _AddonError as exc:
        _emit_error(req_id, exc.code, exc.message)
        return
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        _emit_error(req_id, ERR_BAD_PARAMS,
                    'ffmpeg not found. Set SUBTITLD_FFMPEG_EXECUTABLE or put ffmpeg on PATH.')
        return

    outcome: tuple = ('error', ERR_INTERNAL, 'no outcome', False)
    try:
        log.info('transcribe %s: language=%s keywords=%d', req_id, language or 'auto', len(keywords))
        if state.cancel.is_set():
            raise _Cancelled()

        # 1. Decode: 0.00 -> 0.05
        _emit_progress(req_id, 0.0, 'Decoding audio...')
        samples = _decode(ffmpeg, audio_path, state.cancel)
        duration = samples.size / float(SAMPLE_RATE)
        if state.cancel.is_set():
            raise _Cancelled()
        _emit_progress(req_id, 0.05, 'Decoded audio')

        if samples.size < _MIN_SAMPLES:
            log.info('transcribe %s: %.3fs of audio, nothing to transcribe', req_id, duration)
            segments: list[dict] = []
            detected = language or ''
        else:
            # 2. Engine: 0.05 -> 0.20, then transcribe: 0.20 -> 1.00
            engine = _load_engine(req_id)
            if state.cancel.is_set():
                raise _Cancelled()
            _emit_progress(req_id, 0.2, 'Transcribing...')
            started = time.monotonic()
            result = engine.transcribe(samples, language=language, keywords=keywords)
            log.info('transcribe %s: %.1fs of audio in %.2fs', req_id, duration, time.monotonic() - started)
            segments = words_to_segments(result.get('words') or [], duration, str(result.get('text') or ''))
            detected = str(result.get('language') or language or '')
        if state.cancel.is_set():
            raise _Cancelled()
        outcome = ('result', {
            'segments': segments,
            'duration_sec': round(duration, 3),
            'model': MODEL_ID,
            'language': detected,
        })
    except _Cancelled:
        log.info('transcribe %s: cancelled', req_id)
        outcome = ('error', ERR_CANCELLED, 'cancelled', False)
    except _AddonError as exc:
        log.warning('transcribe %s failed: [%s] %s', req_id, exc.code, exc.message)
        outcome = ('error', exc.code, exc.message, exc.retryable)
    except EngineError as exc:
        log.warning('transcribe %s failed in the engine: %s', req_id, exc)
        outcome = ('error', ERR_INTERNAL, f'Whistle failed: {exc}', False)
    except MemoryError:
        outcome = ('error', ERR_OOM, 'Out of memory while transcribing. Try a shorter file.', False)
    except Exception as exc:  # noqa: BLE001 — every path must end in one terminal frame
        log.exception('transcribe %s failed', req_id)
        outcome = ('error', ERR_INTERNAL, f'{type(exc).__name__}: {exc}', False)

    if outcome[0] == 'result':
        _emit_progress(req_id, 1.0, '')
        _emit_result(req_id, outcome[1])
    else:
        _emit_error(req_id, *outcome[1:])


def _spawn(req_id: str, target, *args) -> None:
    state = _Request(req_id)
    with _active_lock:
        _active[req_id] = state

    def _run():
        try:
            target(state, *args)
        except BaseException:  # noqa: BLE001
            log.exception('worker crashed')
            tail = traceback.format_exc().strip().splitlines()[-1]
            _emit_error(req_id, ERR_INTERNAL, f'worker crashed: {tail}')
        finally:
            with _active_lock:
                _active.pop(req_id, None)

    thread = threading.Thread(target=_run, name=f'whistle-{req_id}', daemon=True)
    state.thread = thread
    thread.start()


# ---------------------------------------------------------------------------
# Handshake + frame loop
# ---------------------------------------------------------------------------
def _send_hello() -> None:
    _emit({
        'type': 'hello',
        'protocol': PROTOCOL_VERSION,
        'addon': ADDON_ID,
        'version': ADDON_VERSION,
        'capabilities': [{
            'task': 'asr.transcribe',
            'languages': list(LANGUAGES),
            # Batch transcription only: the live `asr.stream` task is not offered.
            'streaming': False,
            'voice_clone': False,
        }],
    })


def _read_frame() -> dict | None:
    source = _PROTO_IN if _PROTO_IN is not None else sys.stdin.buffer
    line = source.readline()
    if not line:
        return None  # stdin closed: the host went away
    text = line.decode('utf-8', errors='replace').strip().lstrip('﻿')
    if not text:
        return {}
    try:
        frame = json.loads(text)
    except ValueError as exc:
        log.warning('malformed frame %r: %s', line[:200], exc)
        return {}
    return frame if isinstance(frame, dict) else {}


def _main_loop() -> int:
    while True:
        frame = _read_frame()
        if frame is None:
            log.info('stdin closed; exiting')
            return 0
        if not frame:
            continue
        ftype = frame.get('type')

        if ftype == 'shutdown':
            log.info('shutdown requested')
            return 0

        if ftype == 'ready':
            log.info('handshake complete; host=%s host_version=%s',
                     frame.get('host'), frame.get('host_version'))
            continue

        if ftype == 'cancel':
            target = frame.get('target')
            with _active_lock:
                state = _active.get(target) if isinstance(target, str) else None
            if state is not None:
                log.info('cancel %s', target)
                state.cancel.set()
            continue

        if ftype == 'asr.transcribe':
            req_id = frame.get('id')
            params = frame.get('params')
            if not isinstance(req_id, str) or not req_id:
                log.warning('asr.transcribe without id')
                continue
            if params is None:
                params = {}
            if not isinstance(params, dict):
                _emit_error(req_id, ERR_BAD_PARAMS, 'params must be an object')
                continue
            _spawn(req_id, _handle_transcribe, params)
            continue

        # Unknown task: still answer, so the host's future resolves instead of
        # hanging until its timeout.
        req_id = frame.get('id')
        if isinstance(req_id, str) and req_id:
            _emit_error(req_id, ERR_BAD_PARAMS, f'unsupported frame type {ftype!r}')


def _stop_workers(budget_sec: float = 3.5) -> bool:
    """Cancel and join every worker. True when none is left running.

    The host allows 5 s between `shutdown` and SIGTERM, so stay under it.
    """
    _shutting_down.set()
    with _active_lock:
        states = list(_active.values())
    for state in states:
        state.cancel.set()
    deadline = time.monotonic() + budget_sec
    for state in states:
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return not any(s.thread is not None and s.thread.is_alive() for s in states)


def main() -> int:
    # No-op unless frozen. Keeps the executable from starting a second protocol
    # loop if anything in the frozen graph re-launches it.
    multiprocessing.freeze_support()
    _claim_protocol_stdio()
    logging.basicConfig(
        level=(_env('WHISTLE_ADDON_LOG_LEVEL') or 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        stream=sys.stderr,
    )
    log.info('whistle-addon %s on %s %s, python %s',
             ADDON_VERSION, platform.system(), platform.machine(), platform.python_version())
    if _find_ffmpeg() is None:
        # Not fatal: better to appear in the engine list and fail the first
        # request with a clear message than to vanish from the UI.
        log.warning('ffmpeg not found; requests will fail until one is available')

    _send_hello()
    rc = 1
    try:
        rc = _main_loop()
    except KeyboardInterrupt:
        rc = 130
    except Exception:  # noqa: BLE001
        log.exception('main loop crashed')
        rc = 1
    finally:
        stopped = _stop_workers()
    if not stopped:
        # A worker is still inside the engine. Normal teardown would free state
        # under it, so leave without teardown.
        log.warning('a transcription is still running; exiting without cleanup')
        try:
            sys.stderr.flush()
        finally:
            os._exit(rc)
    return rc


if __name__ == '__main__':
    sys.exit(main())
