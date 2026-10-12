# Saghi-mac — notes for Claude sessions

Offline Arabic voice typing for Apple-silicon Macs (Python 3.12, PySide6, FastAPI, Cohere transcribe-arabic model via transformers). App code: `saghi-mac/saghi/`, tests: `saghi-mac/dev/test_*.py`, build: `.github/workflows/build-app.yml`. Details: `docs/BUILDING.md`.

## Ground rules (from the owner)
- **The repo is the only home for work files.** Notes, logs, reports, test results, plans: commit them here. Nothing work-related may stay only on a Mac or inside one session. The owner works from any device.
- **A Mac is only a test machine.** Use `saghi-mac/dev/mac_session.sh` (start → mic/model/app/tests → report → clean). It works in `~/.saghi-work/`, reuses the installed app's Python and model, and `clean` deletes the folder once everything is pushed. Don't install anything on the Mac outside that folder.
- The model (~4 GB) is never committed; it lives in the `model-v1` GitHub release.
- **Before ending a session, update `docs/STATUS.md`** (what you tried, result, next step) and push.
- Talk to the owner in Saudi Arabic, plain words; they are not an engineer.

## Several sessions at once
Cloud and Mac sessions may run at the same time on the same branch.
- `git pull --rebase origin <branch>` before every commit/push; never force-push.
- Keep each session to its own files where possible; say in `docs/STATUS.md` which area you're working on.

## Running tests
- Cloud (Linux): `.claude/hooks/session-start.sh` installs the deps. Then from `saghi-mac/`:
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) xvfb-run -a python3 dev/test_X.py`
  `test_paste_unit.py` needs a Mac clipboard and fails on Linux (expected).
- Mac: `saghi-mac/dev/mac_session.sh tests` (real model; includes `test_dictation_flow.py`, `test_gui_filejob.py`).

## Diagnostics
The app logs to `~/Library/Application Support/Saghi/saghi.log` (`crash.log` beside it). A watchdog writes every thread's stack there if the UI freezes for over 5 s (`saghi/diagnostics.py`).
