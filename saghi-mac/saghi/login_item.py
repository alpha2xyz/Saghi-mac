"""
Launch-at-login, for real: settings.py's `launch_at_login` flag used to be
saved and never read. This module makes it do something by managing the
same per-user LaunchAgent the installer offers
(packaging/io.github.alpha2xyz.saghi-mac.plist, installed to
~/Library/LaunchAgents/ -- no admin rights needed).

  enable()   writes the LaunchAgent plist (RunAtLoad) for the Python that is
             running right now, so it starts Saghi at the next login.
  disable()  deletes the plist.
  is_enabled()  whether the plist exists.

Deliberately NO `launchctl load/unload` here: RunAtLoad only matters at the
next login anyway, and unloading a job launchd started is exactly what
would kill the Saghi process the user is clicking in. Removing the file is
enough -- launchd does not start a job whose plist is gone at the next
login.

Plain Python, zero Qt dependency (same rule as settings.py). Only macOS has
LaunchAgents; callers check `is_supported()` first.
"""

from __future__ import annotations

import logging
import os
import plistlib
import sys
from pathlib import Path
from typing import Optional

from . import paths

logger = logging.getLogger("saghi.login_item")

LABEL = "io.github.alpha2xyz.saghi-mac"


def is_supported() -> bool:
    return sys.platform == "darwin"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_enabled() -> bool:
    return plist_path().exists()


def build_plist(executable: Optional[str] = None, model_dir: Optional[str] = None) -> dict:
    """
    The LaunchAgent contents, mirroring packaging/io.github.alpha2xyz.saghi-mac.plist
    but filled in from the running interpreter instead of install-time
    placeholders.
    """
    exe = Path(executable or sys.executable)
    package_parent = Path(__file__).resolve().parent.parent  # the dir that holds saghi/
    resources = paths.app_resources_dir(str(exe))

    env = {"PYTHONPATH": str(package_parent)}
    model = model_dir or os.environ.get("SAGHI_MODEL_DIR")
    if model:
        env["SAGHI_MODEL_DIR"] = str(model)

    log_file = (resources / "launchagent.log") if resources else (paths.data_dir() / "launchagent.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [str(exe), "-m", "saghi.ui.app"],
        "EnvironmentVariables": env,
        "WorkingDirectory": str(resources or package_parent),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }


def enable(model_dir: Optional[str] = None) -> None:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "wb") as f:
        plistlib.dump(build_plist(model_dir=model_dir), f)
    os.replace(tmp, path)
    logger.info("Launch at login enabled (%s)", path)


def disable() -> None:
    try:
        plist_path().unlink()
        logger.info("Launch at login disabled")
    except FileNotFoundError:
        pass


def set_enabled(enabled: bool, model_dir: Optional[str] = None) -> bool:
    """Apply the setting. Returns whether it worked (never raises)."""
    try:
        if enabled:
            enable(model_dir=model_dir)
        else:
            disable()
        return True
    except OSError:
        logger.exception("Could not change launch-at-login")
        return False
