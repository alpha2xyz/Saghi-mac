"""
Engine factory: picks the GGUF engine (default, "Saghi Fast") or the
PyTorch engine (developer machines that still have the 4 GB model folder).

Choice order: explicit argument, then SAGHI_ENGINE, then the `engine`
setting. A saved "pytorch" setting is honoured only when a PyTorch model
folder exists; releases from 0.3.0 no longer ship it, so an updated install
that saved "pytorch" before runs GGUF instead of failing (docs/GGUF_RESULTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union


def pytorch_model_dir() -> Path:
    return Path(
        os.environ.get("SAGHI_MODEL_DIR")
        or (Path.home() / "Applications" / "Saghi.app" / "Contents" / "Resources" / "model")
    )


def pytorch_available() -> bool:
    """True when a PyTorch model folder is present (not shipped from 0.3.0)."""
    return (pytorch_model_dir() / "model.safetensors").is_file()


def engine_choice(choice: Optional[str] = None) -> str:
    if choice is None:
        choice = os.environ.get("SAGHI_ENGINE")
    if choice is None:
        from . import settings

        choice = settings.load().engine
        if str(choice).strip().lower() == "pytorch" and not pytorch_available():
            return "gguf"
    return "pytorch" if str(choice).strip().lower() == "pytorch" else "gguf"


def make_engine(model_dir: Union[str, Path], choice: Optional[str] = None):
    if engine_choice(choice) == "gguf":
        from . import settings
        from .gguf_engine import GgufEngine

        return GgufEngine(model_path=settings.load().gguf_model or None)
    from .engine import SaghiEngine

    return SaghiEngine(model_dir)
