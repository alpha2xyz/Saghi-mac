"""
Short feedback sounds for dictation (settings.py's `sound_feedback`):
start of recording, text pasted, and failure.

Uses the sounds that ship with every Mac in /System/Library/Sounds, played
with `afplay` in a detached child process -- never blocks the UI thread,
needs no audio library, and adds no file to the app. Quiet on purpose
(volume 0.25) so the start sound does not bleed into the recording.

Does nothing on other platforms, or when a sound file is missing.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("saghi.ui.sounds")

_SOUNDS_DIR = Path("/System/Library/Sounds")

START = "Tink"
DONE = "Pop"
ERROR = "Basso"

_VOLUME = "0.25"


def play(name: str) -> None:
    if sys.platform != "darwin":
        return
    path = _SOUNDS_DIR / f"{name}.aiff"
    if not path.exists():
        return
    try:
        subprocess.Popen(
            ["/usr/bin/afplay", "-v", _VOLUME, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.debug("Could not play sound %s", name, exc_info=True)
