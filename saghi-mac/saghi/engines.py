"""
Engine factory: picks the PyTorch engine (default) or the GGUF engine.

Choice order: explicit argument, then SAGHI_ENGINE, then the `engine`
setting. The default stays "pytorch"; "gguf" is opt-in (see
docs/GGUF_RESULTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union


def engine_choice(choice: Optional[str] = None) -> str:
    if choice is None:
        choice = os.environ.get("SAGHI_ENGINE")
    if choice is None:
        from . import settings

        choice = settings.load().engine
    return "gguf" if str(choice).strip().lower() == "gguf" else "pytorch"


def make_engine(model_dir: Union[str, Path], choice: Optional[str] = None):
    if engine_choice(choice) == "gguf":
        from . import settings
        from .gguf_engine import GgufEngine

        return GgufEngine(model_path=settings.load().gguf_model or None)
    from .engine import SaghiEngine

    return SaghiEngine(model_dir)
