#!/usr/bin/env python3
"""
Generates PNG screenshots of the Saghi GUI's pages for review, without
any screen-recording tools. Uses Qt's own offscreen rendering (`widget.grab().save(...)`),
which works fine under QT_QPA_PLATFORM=offscreen -- no real display, no
screen-recording permission needed.

Builds a MainWindow directly (not the full app via saghi.ui.app.build())
-- screenshots don't need the API server thread running at all, so this
script skips it entirely to stay fast and avoid touching port 17865.

Run:
    QT_QPA_PLATFORM=offscreen \
        PYTHONPATH=. <venv>/bin/python dev/grab_screens.py

Produces (in dev/screenshots/), each verified non-blank before returning:
    page-history.png          -- history page, seeded with 3 fake Arabic
                                  entries (one with English words mixed in)
    page-filejob-idle.png     -- file transcription page, nothing picked yet
    page-filejob-running.png  -- same page, mocked mid-progress values
    page-settings.png         -- settings page

tray-menu.png is intentionally skipped -- QSystemTrayIcon has no
renderable widget to grab under an offscreen platform (there's no real
system tray to draw into offscreen).

Uses its own scratch SAGHI_DATA_DIR (dev/_screenshot_scratch/) so this
never touches the real user data dir or another test's scratch dir.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SCRATCH_DATA_DIR = Path(__file__).resolve().parent / "_screenshot_scratch"
os.environ["SAGHI_DATA_DIR"] = str(SCRATCH_DATA_DIR)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import history  # noqa: E402
from saghi.engine import SaghiEngine  # noqa: E402
from saghi.settings import SettingsManager  # noqa: E402
from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402
from saghi.ui.mainwindow import MainWindow  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "screenshots"
DEFAULT_MODEL_DIR = Path(os.environ.get("SAGHI_MODEL_DIR") or (Path.home() / "Applications" / "Saghi.app" / "Contents" / "Resources" / "model"))  # only used to construct the engine; the model is never loaded here
MIN_PNG_BYTES = 2048  # sanity floor -- a genuinely rendered 1100x720 page is always far larger than this

_SEED_ENTRIES = [
    dict(
        source="cli", language="ar", cleanup_level="light", duration_s=12.4, inference_s=3.1,
        raw_text="ففي الحالة دي المسألة دي يعني um more safe",
        text="ففي الحالة دي المسألة دي يعني more safe",
        audio_filename="sample1.wav",
    ),
    dict(
        source="filejob", language="ar", cleanup_level="light", duration_s=340.0, inference_s=88.2,
        raw_text="هذا اجتماع طويل يتحدث عن خطة التسويق لعام 2026",
        text="هذا اجتماع طويل يتحدث عن خطة التسويق لعام 2026",
        audio_filename="quarterly-review.m4a",
    ),
    dict(
        source="api", language="ar", cleanup_level="medium", duration_s=5.6, inference_s=1.9,
        raw_text="جدولة اجتماع مع فريق Zoom يوم الأحد الساعة 10 صباحًا",
        text="جدولة اجتماع مع فريق Zoom يوم الأحد الساعة 10 صباحًا",
        audio_filename="voice-note.wav",
    ),
]


def _seed_history() -> None:
    for entry in _SEED_ENTRIES:
        history.add_entry(**entry)


def _grab(widget, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if not pixmap.save(str(path)):
        raise RuntimeError(f"Qt failed to save screenshot: {path}")
    size = path.stat().st_size
    if size < MIN_PNG_BYTES:
        raise RuntimeError(f"Screenshot suspiciously small ({size} bytes, expected a real rendered page): {path}")
    print(f"wrote {path.name}  ({size:,} bytes)")


def main() -> int:
    if SCRATCH_DATA_DIR.exists():
        import shutil

        shutil.rmtree(SCRATCH_DATA_DIR)
    _seed_history()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    settings_manager = SettingsManager()
    engine = SaghiEngine(DEFAULT_MODEL_DIR)
    engine_status = EngineStatusBridge(engine)

    window = MainWindow(settings_manager, engine, engine_status)
    window.resize(1100, 720)
    window.show()
    app.processEvents()

    # History page
    window.nav.setCurrentRow(0)
    app.processEvents()
    _grab(window, OUT_DIR / "page-history.png")

    # File job page -- idle state
    window.nav.setCurrentRow(1)
    app.processEvents()
    _grab(window, OUT_DIR / "page-filejob-idle.png")

    # File job page -- mocked mid-progress state (no real worker/inference)
    fj = window.filejob_page
    fj.preview_picked("quarterly-review-long.wav")
    fj.set_running(True)
    fj.resume_label.setVisible(False)
    fj.apply_progress(2, 5, 40.0, 96.0, 144.0)
    app.processEvents()
    _grab(window, OUT_DIR / "page-filejob-running.png")

    # Settings page
    window.nav.setCurrentRow(2)
    app.processEvents()
    _grab(window, OUT_DIR / "page-settings.png")

    print(f"\nAll screenshots written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
