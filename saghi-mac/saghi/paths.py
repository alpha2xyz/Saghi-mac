"""
Saghi data-directory resolution.

Default data dir: `~/Library/Application Support/Saghi/`. Overridable via
the `SAGHI_DATA_DIR` env var so tests (and anything else that shouldn't
touch the real user data dir) can point elsewhere.

Directories are created lazily -- importing this module, or even calling
`data_dir()`/`db_path()`, never touches the filesystem. Only `ensure_dirs()`
(called once at server/CLI startup, before anything needs to read/write)
creates them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_ENV_VAR = "SAGHI_DATA_DIR"


def data_dir() -> Path:
    """
    Root data directory. `SAGHI_DATA_DIR` wins if set (used by tests);
    otherwise `~/Library/Application Support/Saghi/`.
    """
    override = os.environ.get(_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "Saghi"


def db_path() -> Path:
    return data_dir() / "saghi.db"


def recordings_dir() -> Path:
    return data_dir() / "recordings"


def log_path() -> Path:
    return data_dir() / "saghi.log"


def ensure_dirs() -> Path:
    """Create the data dir (and recordings/ subdir) if missing. Returns data_dir()."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    recordings_dir().mkdir(parents=True, exist_ok=True)
    return d


def app_resources_dir(executable: Optional[str] = None) -> Optional[Path]:
    """
    `.../Saghi.app/Contents/Resources` when running from the installed app
    bundle (the interpreter lives at Resources/venv/bin/python3.12), else
    None -- e.g. when running from a source checkout. Pure path arithmetic,
    never touches the filesystem.
    """
    exe = Path(executable or sys.executable)
    for parent in exe.parents:
        if parent.name == "Resources" and parent.parent.name == "Contents" and parent.parent.parent.suffix == ".app":
            return parent
    return None
