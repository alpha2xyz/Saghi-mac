# Building Saghi-mac

Saghi-mac is a Python app (PySide6 GUI, a local FastAPI server, and a Hugging Face model) that is packaged as an offline, Apple-silicon-only bundle.

## Layout

```
saghi-mac/saghi/        the app (engine, API, UI)
saghi-mac/dev/          tests and requirements-arm64.txt
saghi-mac/packaging/    build_bundle.py, install-saghi.command, Install-Saghi-mac.command, docs
.github/workflows/      build-app.yml
```

## How `build_bundle.py` works

`saghi-mac/packaging/build_bundle.py` assembles `saghi-mac/packaging/output/Saghi-Mac-Portable/` in six stages. Each stage skips work that is already done, so you can re-run it after a failure.

1. Downloads a pinned arm64 python-build-standalone CPython (URL and sha256 are pinned in the script) into the cache and unpacks it to `python-runtime/`.
2. Runs `pip download --platform macosx_13_0_arm64 --python-version 3.12 --only-binary=:all:` for `saghi-mac/dev/requirements-arm64.txt` into `wheels/`. This fetches Apple-silicon wheels even from another machine.
3. Copies `saghi-mac/saghi/` to `saghi/`.
4. Copies the model folder to `model/` and checks the sha256 of `model.safetensors` against the source.
5. Copies `install-saghi.command`, the Arabic docs, the LaunchAgent template and the requirements file.
6. Writes `MANIFEST.json` with checksums.

### Environment variables

| Variable | Meaning |
|---|---|
| `SAGHI_MODEL_SRC_DIR` | Folder holding the model (`model.safetensors` plus the config, tokenizer, README and LICENSE files). Required unless `saghi-mac/model/` exists. |
| `SAGHI_PACKAGING_SCRATCH` | Cache directory for big downloads. Default: `saghi-mac/packaging/.cache`. |
| `SAGHI_PACKAGING_PIP_PYTHON` | Python used for `pip download`. Default: the Python that runs the script. |
| `SAGHI_VERSION` | Version written to `MANIFEST.json`. Default: `0.1.0`. |

Runtime variables used by the app itself: `SAGHI_MODEL_DIR` (where the model is; set by the installed launcher), `SAGHI_DATA_DIR` (data folder, default `~/Library/Application Support/Saghi`).

### Get the model

Download `CohereLabs/cohere-transcribe-arabic-07-2026` from Hugging Face (about 4 GB) into a folder, then:

```bash
SAGHI_MODEL_SRC_DIR=/path/to/model python3 saghi-mac/packaging/build_bundle.py
```

Use Python 3.12 (or any Python whose `pip` can reach PyPI).

## The GitHub workflow

`.github/workflows/build-app.yml` is run by hand (Actions tab, "Run workflow", with a `version` input). On a `macos-15` Apple-silicon runner it:

1. Downloads the model from this repo's `model-v1` release (two `.part` files, `model-config.zip`, `SHA256SUMS`), joins and verifies it.
2. Runs `build_bundle.py`.
3. Smoke-tests: runs the installer (`SAGHI_NO_LAUNCH=1`, no login item), checks the app and that `saghi` imports, starts the local API headless and checks `/api/health`. The health and install steps must pass.
4. Tries an Arabic transcription with the macOS `say` voice. This step is allowed to fail (the runner has about 7 GB of RAM), and prints a clear warning if it does.
5. Zips the bundle, splits it into parts under 2 GB (GitHub's limit per file), and writes `SHA256SUMS`.
6. Creates or updates the release `v<version>` with the parts, `SHA256SUMS`, `Install-Saghi-mac.command` and `Install-Saghi-mac.zip` (the same installer in a zip, which keeps its run permission).

The end-user installer `Install-Saghi-mac.command` downloads those parts with `curl` (32 parallel byte-range connections, because GitHub limits the speed of each single connection), verifies them, joins them and runs the bundled `install-saghi.command`.

## Running the tests

The tests in `saghi-mac/dev/` are plain scripts (no pytest needed). Run them from `saghi-mac/`:

```bash
cd saghi-mac
python3 dev/test_cleanup.py
python3 dev/test_hotkey_logic.py
PYTHONPATH=. python3 dev/test_segmentation.py     # needs numpy
```

The GUI and API tests need the app's dependencies (`pip install -r dev/requirements-arm64.txt`), and set `QT_QPA_PLATFORM=offscreen` themselves. Give them a scratch data folder: `SAGHI_DATA_DIR=$(mktemp -d)`.

Two tests run a real model inference and are skipped unless you provide the model:

- `dev/test_dictation_flow.py`: needs `SAGHI_MODEL_DIR` (a model folder that has `examples/sample1.wav`).
- `dev/test_gui_filejob.py`: needs `SAGHI_MODEL_DIR` and `SAGHI_TEST_WAV` (a short Arabic WAV).
