#!/usr/bin/env python3
"""
Regression test: Saghi's quit (the button, and "restart" after an update)
must really end the app, even when a window refuses to close.

The bug: _quit() called QApplication.quit(), which in Qt 6 first asks
every window (and macOS) to close; one refusal silently cancelled it. The
old process kept running, so a light update was installed but never
loaded, and pressing "restart" again did nothing.

Run with:
    QT_QPA_PLATFORM=offscreen SAGHI_DATA_DIR=$(mktemp -d) PYTHONPATH=. \
        <venv>/bin/python dev/test_update_restart.py
"""

import os
import sys
from pathlib import Path

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402

from saghi.ui.app import build  # noqa: E402

_checks = 0


def check(cond, msg):
    global _checks
    _checks += 1
    print(("ok: " if cond else "FAILED: ") + msg)
    if not cond:
        os._exit(1)


class RefusesToClose(QDialog):
    def reject(self):  # what a busy dialog does
        return


ctx = build([])
ctx.show_main_window()
stubborn = RefusesToClose()
stubborn.show()

QTimer.singleShot(500, ctx.quit_fn)
QTimer.singleShot(8_000, lambda: ctx.app.exit(99))  # safety net: quit was cancelled
code = ctx.app.exec()
check(code == 0, f"quit ended the event loop although a window refused to close (exit code {code}, 99 = cancelled)")

print(f"ALL PASSED ({_checks} checks)")
os._exit(0)
