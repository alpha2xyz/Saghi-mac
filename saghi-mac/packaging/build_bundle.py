#!/usr/bin/env python3
"""
Saghi-mac bundle builder.

Assembles `packaging/output/Saghi-Mac-Portable/` -- a fully offline
distributable folder for Apple Silicon Macs: a bundled arm64 Python, the
pre-downloaded arm64 wheels, the saghi/ source, the GGUF speech model and the
transcribe-cli binary (transcribe.cpp), the installer script, docs, LaunchAgent template, and a MANIFEST.json with
checksums.

It downloads a prebuilt arm64 python-build-standalone CPython and downloads
prebuilt arm64 wheels via `pip download --platform ...` (which fetches
wheels for a *target* platform), so it can run on any host, but it is
built and tested on a macOS arm64 GitHub Actions runner (see
.github/workflows/build-app.yml).

Idempotent / resumable: each stage checks whether its output already
exists (and, for the two expensive ones -- the Python runtime tarball and
the model copy -- verifies a checksum) before redoing the work, so a
re-run after a partial failure only repeats what's missing.

Usage:
    SAGHI_GGUF_SRC=/path/model.gguf SAGHI_TRANSCRIBE_CLI=/path/transcribe-cli \\
      SAGHI_GGUF_NOTICES_DIR=/path/notices python3 packaging/build_bundle.py

Environment variables:
    SAGHI_GGUF_SRC            The Q6_K .gguf file (sha256 is checked against GGUF_SHA256).
    SAGHI_TRANSCRIBE_CLI      The compiled transcribe-cli binary (arm64).
    SAGHI_GGUF_NOTICES_DIR    Folder with the license texts shipped beside them:
                              LICENSE + NOTICE (the model) and LICENSE-transcribe.cpp.
    SAGHI_PACKAGING_SCRATCH   Cache dir for big downloads (default: packaging/.cache).
    SAGHI_PACKAGING_PIP_PYTHON  Python whose pip runs `pip download` (default: the
                              Python running this script).
    SAGHI_VERSION             Version string written to MANIFEST.json (default 0.1.0).

Requires network access (to fetch the PBS tarball and the arm64 wheels).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PACKAGING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGING_DIR.parent  # saghi-mac/

SAGHI_SRC_DIR = PROJECT_ROOT / "saghi"
REQUIREMENTS_ARM64 = PROJECT_ROOT / "dev" / "requirements-arm64.txt"

# The speech model: Q6_K GGUF of cohere-transcribe-arabic-07-2026
# (handy-computer/cohere-transcribe-arabic-07-2026-gguf, Apache 2.0).
GGUF_FILE = "cohere-transcribe-arabic-07-2026-Q6_K.gguf"
GGUF_SHA256 = "f0fc615d27da3236166cd68cf9968c3be7d257b953bbba3b2e10e5d9f3fd1ff4"
GGUF_SIZE = 1972524448
GGUF_SRC = Path(os.environ["SAGHI_GGUF_SRC"]) if os.environ.get("SAGHI_GGUF_SRC") else None
TRANSCRIBE_CLI_SRC = Path(os.environ["SAGHI_TRANSCRIBE_CLI"]) if os.environ.get("SAGHI_TRANSCRIBE_CLI") else None
NOTICES_DIR = Path(os.environ["SAGHI_GGUF_NOTICES_DIR"]) if os.environ.get("SAGHI_GGUF_NOTICES_DIR") else None

OUTPUT_DIR = PACKAGING_DIR / "output" / "Saghi-Mac-Portable"

# Scratch/cache -- large intermediate downloads live here. The final bundle
# content lives only in OUTPUT_DIR. Both are git-ignored.
SCRATCH_DIR = Path(os.environ.get("SAGHI_PACKAGING_SCRATCH") or (PACKAGING_DIR / ".cache"))

# Python used to run `pip download` (needs working HTTPS to PyPI). Defaults
# to the interpreter running this script.
PIP_PYTHON = os.environ.get("SAGHI_PACKAGING_PIP_PYTHON") or sys.executable

VERSION = os.environ.get("SAGHI_VERSION") or "0.1.0"

# ---------------------------------------------------------------------------
# python-build-standalone release (verified via GitHub API + a real
# download + sha256 check).
# The project moved from indygreg/python-build-standalone to
# astral-sh/python-build-standalone; confirmed live via `gh api
# repos/astral-sh/python-build-standalone/releases/latest`.
# ---------------------------------------------------------------------------

PBS_REPO = "astral-sh/python-build-standalone"
PBS_TAG = "20260814"
PBS_ASSET = "cpython-3.12.14+20260814-aarch64-apple-darwin-install_only.tar.gz"
PBS_URL = (
    f"https://github.com/{PBS_REPO}/releases/download/{PBS_TAG}/"
    "cpython-3.12.14%2B20260814-aarch64-apple-darwin-install_only.tar.gz"
)
PBS_SHA256 = "4572133a5542f306b9bdb155da5800f9e38950cd0a98d469b832ce256fe299ea"
PBS_SIZE = 25151480

PY_BIN_NAME = "python3.12"

VERSION_STRING = f"Saghi-mac {VERSION}"

# Platform tags used for wheel resolution. macosx_13_0_arm64 is a
# compatibility ceiling, not a single exact tag -- pip/packaging accepts
# any wheel whose own floor is <= 13.0 (10_x/11_0/12_0/13_0) and rejects
# anything requiring 14.0+. This is what actually enforces "Apple
# Silicon, macOS 13+" as the bundle's real floor -- see the long comment
# in dev/requirements-arm64.txt for why torch is pinned to 2.11.0 instead
# of PyPI-latest to keep this true.
PIP_PLATFORM = "macosx_13_0_arm64"
PIP_PYTHON_VERSION = "3.12"

EXCLUDE_NAMES = {".DS_Store", "__pycache__", ".cache", ".git"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def sha256_of(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def dir_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES]
        for name in files:
            if name in EXCLUDE_NAMES:
                continue
            fp = Path(root) / name
            if fp.is_file():
                total += fp.stat().st_size
    return total


def copytree_clean(src: Path, dst: Path) -> None:
    """copytree that skips EXCLUDE_NAMES, overwriting dst if it exists."""
    if dst.exists():
        shutil.rmtree(dst)

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in EXCLUDE_NAMES}

    shutil.copytree(src, dst, ignore=_ignore)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    log("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kwargs)


# ---------------------------------------------------------------------------
# stage 1: python-build-standalone runtime
# ---------------------------------------------------------------------------


def stage_python_runtime() -> None:
    dest = OUTPUT_DIR / "python-runtime"
    marker = dest / "bin" / PY_BIN_NAME
    if marker.exists():
        log(f"[1/6] python-runtime already staged at {dest} -- skipping.")
        return

    log("[1/6] Staging arm64 python-build-standalone runtime ...")
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    tarball = SCRATCH_DIR / PBS_ASSET

    need_download = True
    if tarball.exists() and tarball.stat().st_size == PBS_SIZE:
        log(f"  cached tarball found ({human_size(tarball.stat().st_size)}), verifying checksum ...")
        if sha256_of(tarball) == PBS_SHA256:
            need_download = False
            log("  cached tarball checksum OK, skipping download.")
        else:
            log("  cached tarball checksum MISMATCH -- re-downloading.")

    if need_download:
        log(f"  downloading {PBS_URL}")
        run(["curl", "-fL", "--retry", "3", "-o", str(tarball), PBS_URL])

    actual_size = tarball.stat().st_size
    if actual_size != PBS_SIZE:
        raise RuntimeError(
            f"python-build-standalone tarball size mismatch: expected {PBS_SIZE}, got {actual_size}"
        )
    digest = sha256_of(tarball)
    if digest != PBS_SHA256:
        raise RuntimeError(
            f"python-build-standalone tarball sha256 mismatch:\n  expected {PBS_SHA256}\n  got      {digest}"
        )
    log(f"  sha256 verified: {digest}")

    log(f"  extracting into {dest} ...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    extract_tmp = OUTPUT_DIR / "_python-runtime-extract-tmp"
    if extract_tmp.exists():
        shutil.rmtree(extract_tmp)
    extract_tmp.mkdir(parents=True)
    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(extract_tmp)
    # PBS install_only tarballs have a single top-level "python/" dir.
    inner = extract_tmp / "python"
    if not inner.exists():
        raise RuntimeError(f"unexpected python-build-standalone layout: no 'python/' dir under {extract_tmp}")
    shutil.move(str(inner), str(dest))
    shutil.rmtree(extract_tmp)

    if not marker.exists():
        raise RuntimeError(f"extraction did not produce {marker}")
    log(f"  staged. {marker} present.")


# ---------------------------------------------------------------------------
# stage 2: arm64 wheels (offline install payload)
# ---------------------------------------------------------------------------


def download_wheels() -> None:
    wheels_dir = OUTPUT_DIR / "wheels"
    req_hash_file = wheels_dir / ".requirements_sha256"
    current_req_hash = sha256_of(REQUIREMENTS_ARM64)

    if wheels_dir.exists() and req_hash_file.exists():
        if req_hash_file.read_text().strip() == current_req_hash:
            existing = list(wheels_dir.glob("*.whl"))
            if existing:
                log(f"[2/6] wheels/ already populated ({len(existing)} wheels) and requirements unchanged -- skipping.")
                return

    log("[2/6] Downloading arm64 wheels (pip download, full dependency resolution) ...")
    if not Path(PIP_PYTHON).exists():
        raise RuntimeError(
            f"PIP_PYTHON not found at {PIP_PYTHON}. Set SAGHI_PACKAGING_PIP_PYTHON to a Python "
            "whose pip can reach PyPI over HTTPS."
        )

    wheels_dir.mkdir(parents=True, exist_ok=True)
    # Clear any stale partial set before a fresh resolution so leftover
    # wheels from a since-changed requirements file can't linger.
    for f in wheels_dir.glob("*.whl"):
        f.unlink()

    run(
        [
            PIP_PYTHON,
            "-m",
            "pip",
            "download",
            "--platform",
            PIP_PLATFORM,
            "--python-version",
            PIP_PYTHON_VERSION,
            "--implementation",
            "cp",
            "--only-binary=:all:",
            "-d",
            str(wheels_dir),
            "-r",
            str(REQUIREMENTS_ARM64),
        ]
    )

    resolved = sorted(p.name for p in wheels_dir.glob("*.whl"))
    if not resolved:
        raise RuntimeError("pip download produced no wheels")
    req_hash_file.write_text(current_req_hash)
    log(f"  {len(resolved)} wheels resolved and downloaded ({human_size(dir_size(wheels_dir))}).")


# ---------------------------------------------------------------------------
# stage 3: saghi/ source
# ---------------------------------------------------------------------------


def copy_saghi_source() -> None:
    dest = OUTPUT_DIR / "saghi"
    log(f"[3/6] Copying saghi/ source -> {dest}")
    copytree_clean(SAGHI_SRC_DIR, dest)
    py_files = list(dest.rglob("*.py"))
    if not py_files:
        raise RuntimeError(f"no .py files found after copying saghi/ source to {dest}")
    log(f"  {len(py_files)} .py files copied.")

    # Version stamp read by saghi/__init__.py -- the in-app updater
    # (saghi/updater.py) compares REQUIREMENTS_SHA256 across releases to
    # decide whether swapping saghi/ alone is a complete update.
    req_sha = sha256_of(REQUIREMENTS_ARM64)
    (dest / "_version.py").write_text(
        "# Written by packaging/build_bundle.py -- do not edit.\n"
        f'__version__ = "{VERSION}"\n'
        f'REQUIREMENTS_SHA256 = "{req_sha}"\n',
        encoding="utf-8",
    )
    log(f"  _version.py written (version {VERSION}, requirements sha256 {req_sha[:12]}...).")


# ---------------------------------------------------------------------------
# stage 4: GGUF model + transcribe-cli (read-only sources -- copy, never modify)
# ---------------------------------------------------------------------------


def copy_gguf() -> tuple[str, str]:
    """Copy the .gguf, the binary and the license texts; return (gguf sha256, cli sha256)."""
    for name, src in (
        ("SAGHI_GGUF_SRC", GGUF_SRC),
        ("SAGHI_TRANSCRIBE_CLI", TRANSCRIBE_CLI_SRC),
        ("SAGHI_GGUF_NOTICES_DIR", NOTICES_DIR),
    ):
        if src is None or not src.exists():
            raise RuntimeError(f"{name} is not set or does not exist: {src}")
    for lic in ("LICENSE", "NOTICE", "LICENSE-transcribe.cpp"):
        if not (NOTICES_DIR / lic).is_file():
            raise RuntimeError(f"license text missing: {NOTICES_DIR / lic}")

    log("[4/6] GGUF model + transcribe-cli ...")
    if GGUF_SRC.stat().st_size != GGUF_SIZE:
        raise RuntimeError(f"{GGUF_SRC} has size {GGUF_SRC.stat().st_size}, expected {GGUF_SIZE}")
    src_hash = sha256_of(GGUF_SRC)
    if src_hash != GGUF_SHA256:
        raise RuntimeError(f"GGUF sha256 mismatch: got {src_hash}, expected {GGUF_SHA256}")
    log(f"  source .gguf sha256 matches the pinned value ({src_hash[:12]}...).")

    model_dest = OUTPUT_DIR / "model-gguf"
    if model_dest.exists():
        shutil.rmtree(model_dest)
    model_dest.mkdir(parents=True)
    shutil.copy2(GGUF_SRC, model_dest / GGUF_FILE)
    shutil.copy2(NOTICES_DIR / "LICENSE", model_dest / "LICENSE")
    shutil.copy2(NOTICES_DIR / "NOTICE", model_dest / "NOTICE")
    if sha256_of(model_dest / GGUF_FILE) != GGUF_SHA256:
        raise RuntimeError("MODEL INTEGRITY CHECK FAILED: bundled .gguf differs from the source.")
    log("  bundled .gguf copy verified.")

    bin_dest = OUTPUT_DIR / "bin"
    if bin_dest.exists():
        shutil.rmtree(bin_dest)
    bin_dest.mkdir(parents=True)
    shutil.copy2(TRANSCRIBE_CLI_SRC, bin_dest / "transcribe-cli")
    (bin_dest / "transcribe-cli").chmod(0o755)
    shutil.copy2(NOTICES_DIR / "LICENSE-transcribe.cpp", bin_dest / "LICENSE-transcribe.cpp")
    return GGUF_SHA256, sha256_of(bin_dest / "transcribe-cli")


# ---------------------------------------------------------------------------
# stage 5: install script, docs, plist, requirements file
# ---------------------------------------------------------------------------


def copy_static_files() -> None:
    log("[5/6] Copying install script, Arabic docs, LaunchAgent template ...")

    install_script_dest = OUTPUT_DIR / "install-saghi.command"
    shutil.copy2(PACKAGING_DIR / "install-saghi.command", install_script_dest)
    install_script_dest.chmod(0o755)

    for name in ("README_AR.md", "INSTALL_AR.txt", "io.github.alpha2xyz.saghi-mac.plist", "AppIcon.icns"):
        shutil.copy2(PACKAGING_DIR / name, OUTPUT_DIR / name)

    shutil.copy2(REQUIREMENTS_ARM64, OUTPUT_DIR / "requirements-arm64.txt")

    log(f"  install-saghi.command copied and chmod 0o755 ({oct(install_script_dest.stat().st_mode)[-3:]}).")


# ---------------------------------------------------------------------------
# stage 6: MANIFEST.json
# ---------------------------------------------------------------------------


def write_manifest(gguf_hash: str, cli_hash: str) -> None:
    log("[6/6] Writing MANIFEST.json ...")

    wheels_dir = OUTPUT_DIR / "wheels"
    wheel_files = sorted(p.name for p in wheels_dir.glob("*.whl"))

    manifest = {
        "product": VERSION_STRING,
        "build_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_on": f"{platform.system()} {platform.machine()} -- bundle targets Apple Silicon (arm64) only",
        "target": {
            "arch": "arm64",
            "min_macos": "13.0",
            "min_macos_note": (
                "Set by PySide6 6.11.2's wheel tag (cp310-abi3-macosx_13_0_universal2), the "
                "highest floor among every resolved wheel. transcribe-cli is built with "
                "MACOSX_DEPLOYMENT_TARGET=13.0 (see .github/workflows/build-app.yml)."
            ),
        },
        "python_runtime": {
            "source": "python-build-standalone",
            "repo": PBS_REPO,
            "release_tag": PBS_TAG,
            "asset": PBS_ASSET,
            "download_url": PBS_URL,
            "sha256": PBS_SHA256,
            "size_bytes": PBS_SIZE,
            "python_version": "3.12.14",
        },
        "wheels": {
            "count": len(wheel_files),
            "platform_tag_used_for_resolution": PIP_PLATFORM,
            "python_version_used_for_resolution": PIP_PYTHON_VERSION,
            "files": wheel_files,
        },
        "model": {
            "file": f"model-gguf/{GGUF_FILE}",
            "source": "handy-computer/cohere-transcribe-arabic-07-2026-gguf (Apache 2.0)",
            "sha256": gguf_hash,
        },
        "transcribe_cli": {
            "file": "bin/transcribe-cli",
            "source": "handy-computer/transcribe.cpp (MIT)",
            "sha256": cli_hash,
        },
        "bundle_size_bytes": dir_size(OUTPUT_DIR),
    }

    manifest_path = OUTPUT_DIR / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    log(f"  wrote {manifest_path} ({human_size(manifest_path.stat().st_size)}).")
    log(f"  total bundle size: {human_size(manifest['bundle_size_bytes'])}")


# ---------------------------------------------------------------------------


def clean_ds_store() -> None:
    """
    This project tree is heavily Finder-browsed and macOS drops a fresh
    .DS_Store into any directory Finder (or Spotlight) touches, including
    ones this script just created -- copytree_clean's EXCLUDE_NAMES filter
    only stops them from being copied FROM a source dir, not from
    reappearing afterward. Sweep them out right before the integrity-
    sensitive steps (checksums, MANIFEST) so they never affect either.
    """
    removed = 0
    for path in OUTPUT_DIR.rglob(".DS_Store"):
        path.unlink()
        removed += 1
    if removed:
        log(f"  swept {removed} stray .DS_Store file(s) left by Finder/Spotlight.")


def main() -> int:
    log(f"Building {VERSION_STRING} -> {OUTPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    stage_python_runtime()
    download_wheels()
    copy_saghi_source()
    gguf_hash, cli_hash = copy_gguf()
    copy_static_files()
    clean_ds_store()
    write_manifest(gguf_hash, cli_hash)

    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
