"""
Short feedback sounds for dictation (settings.py's `sound_feedback`):
recording started, recording stopped, recording discarded, and failure.

The tones are Saghi's own, synthesized by `dev/make_sounds.py` into
`ui/assets/sounds/` (soft sine plucks, ~0.2 s). Each is played with `afplay`
in a detached child process -- never blocks the UI thread and needs no audio
library. Volumes are low on purpose so the start tone does not bleed into
the recording.

`DONE` (text pasted) is kept for callers but is silent: the stop tone already
marks the end of a dictation, and a second sound right after it is noise.

Does nothing on other platforms, or when a sound file is missing.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("saghi.ui.sounds")

_SOUNDS_DIR = Path(__file__).resolve().parent / "assets" / "sounds"

START = "start"
STOP = "stop"
DISCARD = "discard"
ERROR = "error"
DONE = ""  # silent, see module docstring

_VOLUMES = {START: "0.36", STOP: "0.32", DISCARD: "0.15", ERROR: "0.3"}


def path_for(name: str) -> Path:
    return _SOUNDS_DIR / f"{name}.wav"


def play(name: str) -> None:
    if sys.platform != "darwin" or not name:
        return
    path = path_for(name)
    if not path.exists():
        return
    try:
        subprocess.Popen(
            ["/usr/bin/afplay", "-v", _VOLUMES.get(name, "0.3"), str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.debug("Could not play sound %s", name, exc_info=True)
