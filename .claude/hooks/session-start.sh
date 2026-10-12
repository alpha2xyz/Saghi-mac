#!/bin/bash
# Cloud sessions only: install what the dev tests need on Linux, so any
# device can open a session and run saghi-mac/dev/test_*.py right away.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y -q xvfb xauth xclip libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libportaudio2 >/dev/null 2>&1 \
    || { apt-get update -q >/dev/null 2>&1 && apt-get install -y -q xvfb xauth xclip libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libportaudio2 >/dev/null 2>&1; } \
    || echo "session-start: apt install failed (GUI tests may not run)" >&2
fi
python3 -m pip install -q --disable-pip-version-check \
  numpy PySide6 soundfile sounddevice soxr pynput fastapi uvicorn httpx python-multipart keyring keyrings.alt \
  >/dev/null 2>&1 || echo "session-start: pip install failed" >&2
exit 0
