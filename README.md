# Whistle (offline) add-on for Subtitld

Offline **speech-to-text** for [Subtitld](https://github.com/Subtitld/subtitld),
powered by **Whistle**, the speech model from
[Cactus Compute](https://cactuscompute.com/whistle), running on its native
engine.

- Serves the `asr.transcribe` task over Subtitld's add-on protocol.
- **Offline and self-contained.** The 16.9 MB model and its engine are inside
  the add-on. Transcription downloads nothing and sends nothing anywhere: no
  telemetry, no account.
- **CPU only.** No GPU, no torch, and no Python packages beyond the standard
  library.
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

Each release archive also carries CPython and the few native libraries its
standard modules need (such as libffi and zlib), frozen by PyInstaller. On
Windows that includes the Microsoft C runtime Python ships with. Its
`THIRD_PARTY_LICENSES/` folder is assembled at build time by
`tools/assemble_licences.py`, with a README that names the notice for every
native file in the archive. The build fails if any native file has none.

## Limits

- **Batch only.** One result per request. The live `asr.stream` task is not
  offered.
- **Cancel is best effort.** The engine call cannot be interrupted, so a cancel
  takes effect before decoding starts, or as soon as the engine returns.
- **Punctuation varies.** Whistle does not always put commas and full stops in
  its output. Where it does not, rows split on the length caps (84 characters
  or 7 seconds) rather than at sentence ends.
- **Memory grows with the file.** The whole track is held as float samples:
  about 230 MB for an hour of audio. The engine's own working memory on long
  files has not been measured.
- **Platforms.** Releases ship Linux x86_64, macOS arm64 and Windows x86_64.
  The macOS archive is arm64 only, as the other add-ons' are. The Windows
  engine needs only the Universal C Runtime, which Windows 10 and later include.

## Measured here

Linux x86_64, CPU, the frozen build:

| Input | Time to transcribe | Peak memory |
|-------|--------------------|-------------|
| 11 s clip | 0.68 s | 57 MB |
| 66 s clip (windowed by the engine) | 5.0 to 5.8 s | 80 MB |

Word timings stayed monotonic across the 66 s run, with no gap longer than
1.1 s between words.

Release 0.0.1, as built by the release workflow:

| Platform | Download | Installed |
|----------|----------|-----------|
| Linux x86_64 | 29.3 MiB | 49.6 MiB |
| macOS arm64 | 27.4 MiB | 31.7 MiB |
| Windows x86_64 | 21.5 MiB | 29.9 MiB |

## Building

```bash
pip install -e .[build,dev]
python tools/fetch_assets.py                 # engine and weights, verified
pyinstaller pyinstaller.spec --noconfirm     # dist/whistle-addon/
python tools/assemble_licences.py --bundle dist/whistle-addon --out release-staging/THIRD_PARTY_LICENSES
```

`tools/fetch_assets.py` takes `--platform` (`linux-x86_64`, `macos-arm64`,
`macos-x86_64`, `windows-x86_64`) when the build is not for this machine. The
archive is made the way the other add-ons are, with `tools/make_archive.py`.

## Releasing

Push a tag `v<version>` that matches `manifest.json`. The release workflow
runs the tests, then builds each platform and installs its zip the way Subtitld
does. The installed binary has to transcribe the sample before anything is
published. It then publishes the zips with their `.sha256` files and asks the
add-ons catalog to rebuild. Without the `SUBTITLD_BOT_TOKEN` secret, the
catalog picks the release up on its six-hourly rebuild instead.

## Tests

```bash
pytest
```

The engine tests need the assets from `tools/fetch_assets.py`, and skip without
them. The protocol tests need ffmpeg for the decode path, and skip without it.
`tests/test_installed.py` runs the add-on as a process over its protocol: from
source by default, or the executable named by `WHISTLE_TEST_BINARY`, which is
how the release workflow tests each installed archive.
`tests/data/jfk.flac` is a copy of the JFK clip the Whisper (offline) add-on's
tests use.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
