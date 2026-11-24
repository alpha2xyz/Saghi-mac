#!/usr/bin/env python3
"""
Tests for the Home page and the stats behind it (no model, no network).

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) <venv>/bin/python dev/test_home_page.py
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SAGHI_DATA_DIR", tempfile.mkdtemp(prefix="saghi-home-test-"))

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import history, settings  # noqa: E402
from saghi.ui import home_page, strings, theme  # noqa: E402
from saghi.ui.home_page import HomePage, compute_stats  # noqa: E402

app = QApplication.instance() or QApplication([])
_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    assert cond, f"FAILED: {msg}"
    _checks += 1
    print(f"ok: {msg}")


def dark_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#1E1E20"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#F5F5F7"))
    return pal


# ---- formatting ----------------------------------------------------------------
fd = strings.format_duration
check(fd(0) == "0 ث", "0 seconds")
check(fd(6) == "6 ث", "6 seconds")
check(fd(59.4) == "59 ث", "59.4 s stays in seconds")
check(fd(240) == "4 د", "240 s is 4 minutes")
check(fd(59.6 * 60) == "1.0 س", "59.6 minutes rolls over to hours, not '60 د'")
check(fd(4320) == "1.2 س", "4320 s is 1.2 hours")
check(fd(-5) == "0 ث", "negative is floored at 0")
check(strings.hotkey_symbols("ctrl+alt") == "⌃⌥" and strings.hotkey_symbols("ctrl+cmd") == "⌃⌘", "hotkey symbols")

# ---- stats math ------------------------------------------------------------------
st = compute_stats({"words": 400, "spoken_s": 60.0})
check(abs(st["saved_s"] - (600 - 60)) < 1e-9, "400 words at 40 wpm = 10 min typing, minus 1 min spoken")
check(compute_stats({"words": 10, "spoken_s": 600.0})["saved_s"] == 0.0, "time saved never goes below 0")
check(compute_stats({})["words"] == 0, "empty stats are zero")

check(history.stats() == {"entries": 0, "words": 0, "spoken_s": 0.0}, "empty history -> zero stats")
history.add_entry(source="dictation", language="ar", cleanup_level="light", duration_s=10.0,
                  inference_s=1.0, raw_text="a b c", text="كلمة اثنتان ثلاث أربع")
history.add_entry(source="dictation", language="ar", cleanup_level="light", duration_s=0.0,
                  inference_s=1.0, raw_text="x", text="ثلاث كلمات هنا " + "و" * 0)
hs = history.stats()
check(hs["entries"] == 2 and hs["words"] == 7, "words are summed over all entries")
check(abs(hs["spoken_s"] - (10.0 + 3 / 2.3)) < 1e-6, "stored durations are summed; a row without one is estimated at 2.3 words/s")

# ---- page ------------------------------------------------------------------------------
sm = settings.SettingsManager()
for dark in (False, True):
    app.setPalette(dark_palette() if dark else app.style().standardPalette())
    page = HomePage(sm)
    page.setStyleSheet(theme.stylesheet())
    page.resize(520, 480)
    page.show()
    check(page.grab().width() > 0, f"page builds and renders ({'dark' if dark else 'light'})")
    page.close()

page = HomePage(sm)
page.finish_animations()
check(page.words_stat.value.text() == "7", "words card shows 7")
check(page.spoken_stat.value.text() == strings.format_duration(hs["spoken_s"]), "spoken card matches")
check(page.saved_stat.value.text() == "0 ث", "saved card is 0 when speaking is slower than typing")
history.add_entry(source="dictation", language="ar", cleanup_level="light", duration_s=5.0,
                  inference_s=1.0, raw_text="x", text=" ".join(["كلمة"] * 200))
page.refresh()
page.finish_animations()
check(page.words_stat.value.text() == "207", "refresh() picks up a new history entry")
check(page.saved_stat.value.text() != "0 ث", "...and time saved becomes positive")

# count-up: opening the page starts the numbers from 0
page.refresh(count_up=True)
check(page.words_stat._anim.state() == page.words_stat._anim.State.Running, "opening the page counts up")
check(page.words_stat.target == 207, "...towards the real value")
page.finish_animations()
check(page.words_stat.value.text() == "207", "...and lands on it")

# hotkey line
check(strings.hotkey_symbols(sm.current.hotkey) in page.hotkey_cap.text(), "hotkey key-cap shows macOS symbols")
check(page.hotkey_text.text() == strings.HOME_HOWTO_HOLD_AND_TAP, "tap-to-toggle text by default")
sm.update(tap_to_toggle=False)
check(page.hotkey_text.text() == strings.HOME_HOWTO_HOLD_ONLY, "hold-only text when tap-to-toggle is off")
sm.update(hotkey="ctrl+alt", tap_to_toggle=True)
check("⌃⌥" in page.hotkey_cap.text(), "hotkey change updates the key-cap")
sm.update(hotkey="vk:10")
check("§" in page.hotkey_cap.text(), "a custom single-key hotkey shows on the key-cap")
sm.update(hotkey="alt_r")
check("Right" in page.hotkey_cap.text() and "⌥" in page.hotkey_cap.text(), "a right-side modifier shows on the key-cap")

# engine choice: two GGUF variants, Fast = Q6_K, Full = Q8_0
from saghi import engines
_real_pa = engines.pytorch_available
engines.pytorch_available = lambda: False
home_page.gguf_available = lambda gguf_model="": True
home_page.full_available = lambda: True
sm.update(engine="gguf", gguf_variant="fast")
page = HomePage(sm)
check(not page._full_row.isHidden(), "the Full row shows when the Q8 file is available")
check(page.fast_radio.isChecked() and not page.full_radio.isChecked(), "starts on Fast (default variant)")
check(page.restart_row.isHidden(), "no restart note before a change")
restarts = []
page.restart_requested.connect(lambda: restarts.append(1))
page.full_radio.setChecked(True)
check(sm.current.engine == "gguf" and sm.current.gguf_variant == "full", "choosing Full writes gguf_variant=full (engine gguf)")
check(not page.restart_row.isHidden(), "restart note appears when the variant differs from the running one")
page.restart_btn.click()
check(restarts == [1], "the restart button emits restart_requested")
page.fast_radio.setChecked(True)
check(sm.current.gguf_variant == "fast" and page.restart_row.isHidden(), "going back to Fast clears the note")

# Migration: a saved engine "pytorch" without the PyTorch folder behaves as Full.
sm.update(engine="pytorch", gguf_variant="fast")
check(engines.model_choice(sm.current) == "full", "saved pytorch without folder -> model_choice full")
page = HomePage(sm)
check(page.full_radio.isChecked() and page.restart_row.isHidden(), "...the page shows Full, no restart note")
check(sm.current.engine == "pytorch", "...and the settings file is not rewritten")

# Without the Q8 file there is no Full row and Fast is checked.
home_page.full_available = lambda: False
page = HomePage(sm)
check(page._full_row.isHidden(), "no Full row without the Q8 file")
check(page.fast_radio.isChecked(), "...Fast is checked")

_old_env = os.environ.pop("SAGHI_ENGINE", None)
check(engines.engine_choice() == "gguf", "saved pytorch with no model folder resolves to gguf")
engines.pytorch_available = lambda: True
check(engines.engine_choice() == "pytorch", "saved pytorch with a model folder stays pytorch")
check(engines.model_choice(sm.current) == "pytorch", "...model_choice pytorch on a dev machine")
check(engines.engine_choice("gguf") == "gguf", "an explicit choice wins")
engines.pytorch_available = _real_pa
if _old_env is not None:
    os.environ["SAGHI_ENGINE"] = _old_env

home_page.gguf_available = lambda gguf_model="": False
home_page.full_available = lambda: True
sm.update(engine="gguf", gguf_variant="fast")
page = HomePage(sm)
check(page.fast_radio.isEnabled(), "Fast stays usable when it is the saved choice")
sm.update(gguf_variant="full")
page = HomePage(sm)
check(not page.fast_radio.isEnabled(), "Fast is disabled when no GGUF model/runner exists and it is not chosen")
check(strings.HOME_MODEL_FAST_MISSING in page._fast_hint_label.text(), "...with a hint")

print(f"\nALL PASSED ({_checks} checks)")
