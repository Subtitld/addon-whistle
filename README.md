# Whistle (offline) add-on for Subtitld

Offline **speech-to-text** for [Subtitld](https://github.com/Subtitld/subtitld),
powered by **Whistle**, the speech model from
[Cactus Compute](https://cactuscompute.com/whistle), running on its native
engine.

- Serves the `asr.transcribe` task over Subtitld's add-on protocol.
- **Offline and self-contained.** The 16.9 MB model and its engine are inside
  the add-on. Transcription downloads nothing and sends nothing anywhere: no
  telemetry, no account.
- **CPU only.** No GPU, no torch.
- Word timings from the model become subtitle rows: one row per sentence where
  the speech allows it, otherwise split by length, as the whisper.cpp add-on does.
- Seven languages. For any other language, use the Whisper (offline) add-on.

## Languages

| Code | Language | Code | Language |
|------|----------|------|----------|
| `en` | English  | `it` | Italian  |
| `de` | German   | `nl` | Dutch    |
| `fr` | French   | `pl` | Polish   |
| `es` | Spanish  |      |          |

The language is detected unless the project names one of these. A project in
any other language is refused with a message that says so, rather than guessed.

## Options

Set these in **Settings ▸ Add-ons ▸ Whistle (offline) ▸ Configure**.

| Option | Default | Env var | Notes |
|--------|---------|---------|-------|
| Keywords | empty | `WHISTLE_KEYWORDS` | Names and jargon, separated by commas. Whistle favours them when it is unsure of a word. |

## What is bundled, and where it comes from

| File | Size | Licence | Source |
|------|------|---------|--------|
| `whistle.cact` (weights) | 16.9 MB | Apache-2.0 | [Cactus-Compute/whistle](https://huggingface.co/Cactus-Compute/whistle) at a pinned revision |
| `libneedle3` (engine) | about 1.6 MB | Apache-2.0 | [Cactus-Compute/needle3](https://huggingface.co/Cactus-Compute/needle3), inside the cactus-needle 3.3.1 wheel |

Both are checked against a pinned sha256 when the add-on is built
(`tools/fetch_assets.py`). The sources, hashes and licence texts are in
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES/).

The add-on calls the engine itself. It does not install or import the
`cactus-needle` Python package, which would bring a telemetry client and a
downloader that can fetch native code at runtime.

## Limits

- **Batch only.** One result per request. The live `asr.stream` task is not
  offered.
- **Cancel is best effort.** The engine call cannot be interrupted, so a cancel
  takes effect before decoding starts, or as soon as the engine returns.
- **Punctuation varies.** Whistle does not always put commas and full stops in
  its output. Where it does not, rows split on the length caps (84 characters
  or 7 seconds) rather than at sentence ends.
- **Memory grows with the file.** Decoding holds the whole track: for an hour
  of audio, about 115 MB of PCM plus 230 MB of float samples at the peak. The
  engine's own working memory on long files has not been measured.
- **Platforms.** The Linux build was run here. The macOS and Windows engines
  are pinned but not yet run; the CI release has to prove them.

## Measured here

Linux x86_64, CPU, the frozen build:

| Input | Time to transcribe | Peak memory |
|-------|--------------------|-------------|
| 11 s clip | 0.71 s | 71 MB |
| 66 s clip (windowed by the engine) | 5.9 s | 94 MB |

Word timings stayed monotonic across the 66 s run, with no gap longer than
1.1 s between words.

## Building

```bash
pip install -e .[build,dev]
python tools/fetch_assets.py                 # engine and weights, verified
pyinstaller pyinstaller.spec --noconfirm     # dist/whistle-addon/
```

`tools/fetch_assets.py` takes `--platform` (`linux-x86_64`, `macos-arm64`,
`macos-x86_64`, `windows-x86_64`) when the build is not for this machine. The
archive is made the way the other add-ons are, with `tools/make_archive.py`.

## Tests

```bash
pytest
```

The engine tests need the assets from `tools/fetch_assets.py`, and skip without
them. The protocol tests need ffmpeg for the decode path, and skip without it.
`tests/data/jfk.flac` is a copy of the JFK clip the Whisper (offline) add-on's
tests use.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
