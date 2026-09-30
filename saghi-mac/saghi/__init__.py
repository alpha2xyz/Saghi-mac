"""Saghi for macOS -- transcription engine + text cleanup + CLI (Phase 1)."""

__all__ = ["engine", "cleanup", "cli"]

# packaging/build_bundle.py writes saghi/_version.py into the built bundle
# (release version + the sha256 of the pinned requirements file, which the
# in-app updater compares to decide whether a code-only update is enough).
# A source checkout has no such file.
try:
    from ._version import REQUIREMENTS_SHA256, __version__
except ImportError:
    __version__ = "0.0.0"
    REQUIREMENTS_SHA256 = ""
