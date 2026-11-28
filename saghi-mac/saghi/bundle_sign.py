"""
Keep Saghi.app's ad-hoc code signature valid after files inside it change.

Why: a light update (or install_local.sh) rewrites files and Info.plist inside
the signed bundle. The signature then no longer matches, and macOS (TCC)
silently drops the app's Microphone, Accessibility and Input Monitoring
permissions -- recordings become pure silence, with no prompt.

Re-signing needs every symlink in the bundle to point inside it. `python -m
venv` makes `venv/bin/python3.12` an ABSOLUTE link to the install path, which
codesign rejects ("invalid destination for symbolic link in bundle"), so those
links are made relative first.

Usage: python -m saghi.bundle_sign /path/to/Saghi.app
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List

logger = logging.getLogger("saghi.bundle_sign")

CODESIGN = "codesign"


def fix_absolute_symlinks(app: Path) -> List[Path]:
    """Rewrite absolute symlinks that point inside `app` as relative ones. Returns the fixed links."""
    app = Path(app)
    root = os.path.realpath(app)
    fixed: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(app):
        for name in dirnames + filenames:
            link = Path(dirpath) / name
            if not link.is_symlink():
                continue
            dest = os.readlink(link)
            if not os.path.isabs(dest):
                continue
            real_dest = os.path.realpath(dest)
            if not (real_dest == root or real_dest.startswith(root + os.sep)):
                logger.warning("Symlink points outside the app, left as is: %s -> %s", link, dest)
                continue
            rel = os.path.relpath(real_dest, os.path.realpath(link.parent))
            link.unlink()
            os.symlink(rel, link)
            fixed.append(link)
    return fixed


def resign(app: Path) -> bool:
    """Fix symlinks, re-sign ad-hoc, verify. Returns True when the signature verifies."""
    app = Path(app)
    fixed = fix_absolute_symlinks(app)
    if fixed:
        logger.info("Made %d bundle symlink(s) relative: %s", len(fixed), [str(p) for p in fixed])
    try:
        sign = subprocess.run([CODESIGN, "--force", "-s", "-", str(app)],
                              capture_output=True, text=True)
        if sign.returncode != 0:
            logger.warning("WARNING: re-signing %s failed: %s", app, sign.stderr.strip())
            return False
        check = subprocess.run([CODESIGN, "--verify", "--strict", str(app)],
                               capture_output=True, text=True)
    except OSError as exc:
        logger.warning("WARNING: codesign could not run (%s); the app signature may be broken", exc)
        return False
    if check.returncode != 0:
        logger.warning("WARNING: app signature does not verify after update -- macOS may block "
                       "the microphone and keyboard permissions: %s", check.stderr.strip())
        return False
    logger.info("App re-signed and signature verified: %s", app)
    return True


def main(argv: List[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) != 2:
        print("usage: python -m saghi.bundle_sign /path/to/Saghi.app", file=sys.stderr)
        return 2
    return 0 if resign(Path(argv[1])) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
