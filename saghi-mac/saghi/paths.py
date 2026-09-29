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
from pathlib import Path

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
