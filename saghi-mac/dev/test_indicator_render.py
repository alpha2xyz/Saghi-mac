#!/usr/bin/env python3
"""
Offscreen Qt test for saghi.ui.indicator.IndicatorWindow. No engine, no
recorder, no hotkey -- pure widget behavior.

Core behavior-contract check (README_AR.md: "موجة حقيقية تتبع مستوى
الميكروفون ولا تتحرك اصطناعيًا عند الصمت" -- must never move artificially
during silence): for EACH of the four waveform styles (bars/line/dots/
pulse), grab the widget's rendered pixels twice, with a real time delay
between the two grabs and the QTimer actually ticking in between (an
event-loop spin, not just calling paintEvent twice back-to-back) while
`level_fn()` keeps returning an all-zero buffer -- the two renders must be
byte-identical. This is a much stronger check than "it looks flat"; a
sin(t)-based animation (rejected during design, see indicator.py's
_draw_pulse docstring) would fail it immediately.

Also grabs one PNG per style with REAL (non-silent) sample audio into
dev/screenshots/ for a human to eyeball, and does a light smoke pass over
state transitions (recording -> processing -> error -> hidden) and the
style/color setters.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_indicator_render.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi.settings import VALID_WAVEFORM_STYLES  # noqa: E402
from saghi.ui.indicator import IndicatorWindow  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


app = QApplication.instance() or QApplication(sys.argv)

SILENCE = (np.zeros(0, dtype=np.float32), 0.0)


def _silence_level_fn():
    return np.zeros(1600, dtype=np.float32), 0.0


MODEL_DIR = Path(os.environ.get("SAGHI_MODEL_DIR") or "/nonexistent-saghi-model-dir")
SAMPLE_PATH = MODEL_DIR / "examples" / "sample1.wav"
_sample_audio = None
if SAMPLE_PATH.exists():
    data, sr = sf.read(str(SAMPLE_PATH), always_2d=False)
    _sample_audio = np.asarray(data, dtype=np.float32)
    # Pick the loudest 1600-sample (100ms) window in the whole clip, not
    # just the first one -- sample1.wav (like the other bundled samples,
    # per Phase 3's segmentation notes) has real leading silence before
    # speech starts, so grabbing samples [0:1600] would screenshot a
    # near-flat waveform for every style and defeat the point of a
    # "reviewable, real-audio" screenshot.
    if _sample_audio.size >= 1600:
        win = 1600
        best_start, best_rms = 0, -1.0
        for start in range(0, len(_sample_audio) - win, win // 2):
            chunk = _sample_audio[start:start + win]
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            if rms > best_rms:
                best_rms = rms
                best_start = start
        _sample_audio = _sample_audio[best_start:best_start + win]


def _real_audio_level_fn():
    if _sample_audio is None:
        return np.zeros(1600, dtype=np.float32), 0.0
    chunk = _sample_audio
    rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) if chunk.size else 0.0
    return chunk, rms


print("--- Silence-identical-render check, per waveform style ---")

screenshots_dir = REPO_ROOT / "dev" / "screenshots"
screenshots_dir.mkdir(parents=True, exist_ok=True)

for style in VALID_WAVEFORM_STYLES:
    win = IndicatorWindow()
    win.set_style(style)
    win.set_color("#4A90D9")
    win.show_recording(_silence_level_fn)
    app.processEvents()

    img1 = win.grab().toImage()

    # Advance real wall-clock + event-loop time so the ~30fps QTimer
    # actually ticks one or more times in between the two grabs -- this is
    # what makes the check meaningful (not just "paintEvent is
    # deterministic if called twice with no time passing").
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(150, loop.quit)
    loop.exec()

    img2 = win.grab().toImage()

    check(img1 == img2, f"style={style!r}: two renders of an all-zero (silent) buffer, ~150ms apart with real QTimer ticks, are pixel-identical")

    win.hide_indicator()
    check(win.state == "hidden", f"style={style!r}: hide_indicator() sets state to hidden")

    # Bonus: also produce a reviewable screenshot with real, non-silent
    # sample audio (if SAGHI_MODEL_DIR points at a model folder
    # with examples/) so a human can eyeball each style.
    if _sample_audio is not None:
        win2 = IndicatorWindow()
        win2.set_style(style)
        win2.set_color("#4A90D9")
        win2.show_recording(_real_audio_level_fn)
        app.processEvents()
        out_path = screenshots_dir / f"indicator-recording-{style}.png"
        win2.grab().save(str(out_path))
        check(out_path.stat().st_size > 200, f"style={style!r}: real-audio screenshot written ({out_path.name}, {out_path.stat().st_size} bytes)")
        win2.hide_indicator()

if _sample_audio is None:
    print(f"  (skipped real-audio screenshots -- {SAMPLE_PATH} not found from this checkout)")

print("\n--- State transition + setter smoke ---")

win = IndicatorWindow()
check(win.state == "hidden", "IndicatorWindow starts hidden")

win.show_recording(_silence_level_fn)
check(win.state == "recording", "show_recording() sets state to recording")
check(win.isVisible(), "show_recording() makes the widget visible")

win.show_processing()
check(win.state == "processing", "show_processing() sets state to processing")
check(win._processing_text == __import__("saghi.ui.strings", fromlist=["strings"]).INDICATOR_PROCESSING, "default processing text is strings.INDICATOR_PROCESSING")

win.show_processing("جارٍ تحميل النموذج…")
check(win._processing_text == "جارٍ تحميل النموذج…", "show_processing() accepts a text override (cold-engine case)")

win.show_error("تعذر الوصول إلى الميكروفون", auto_hide_ms=50)
check(win.state == "error", "show_error() sets state to error")
check(win.isVisible(), "show_error() keeps the widget visible")

loop = QEventLoop()
QTimer.singleShot(300, loop.quit)
loop.exec()
check(win.state == "hidden", "show_error()'s auto_hide_ms fires and hides the indicator on its own")

win2 = IndicatorWindow()
win2.set_style("dots")
check(win2._style == "dots", "set_style() accepts a valid style")
win2.set_style("not-a-real-style")
check(win2._style == "dots", "set_style() silently ignores an invalid style (keeps the previous one)")

win2.set_color("#ff0000")
check(win2._color.name() == "#ff0000", "set_color() accepts a valid hex color")
win2.set_color("not-a-color")
check(win2._color.name() == "#ff0000", "set_color() silently ignores an invalid color string")

flags = win2.windowFlags()
from PySide6.QtCore import Qt

check(bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus), "window flags include WindowDoesNotAcceptFocus (never steals focus)")
check(bool(flags & Qt.WindowType.FramelessWindowHint), "window flags include FramelessWindowHint")
check(bool(flags & Qt.WindowType.WindowStaysOnTopHint), "window flags include WindowStaysOnTopHint")
check(win2.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating), "WA_ShowWithoutActivating attribute is set")

print(f"\nALL PASSED ({_checks} checks)")
