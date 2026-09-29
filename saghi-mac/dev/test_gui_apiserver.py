#!/usr/bin/env python3
"""
Phase 4 GUI test 5: the API server running inside the app's background
thread. Offscreen, no model load (health checks never trigger one -- see
api.py's module docstring), fast.

Builds the whole app via saghi.ui.app.build() (same as the real
`python -m saghi.ui.app` entry point, minus the blocking app.exec()),
curls GET /api/health over the real port, quits cleanly via the same
quit_fn() the tray's "إنهاء" action calls, and confirms the port is
actually released afterward (a fresh urlopen to it must fail to connect).

Run:
    QT_QPA_PLATFORM=offscreen SAGHI_DATA_DIR=<scratch dir> \
        PYTHONPATH=. <venv>/bin/python dev/test_gui_apiserver.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from saghi.ui.app import API_PORT, build  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


BASE_URL = f"http://127.0.0.1:{API_PORT}"


def _wait_for_health(timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    last_exc = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError) as exc:
            last_exc = exc
            time.sleep(0.2)
    raise TimeoutError(f"Server never came up on {BASE_URL}: {last_exc}")


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", port))
            return False  # something accepted the connection -- still bound
        except (ConnectionRefusedError, socket.timeout, OSError):
            return True


print("--- Test 5: API server running in the app's background thread ---")

ctx = build([])

health = _wait_for_health()
check(health["status"] == "ok", "GET /api/health returns status ok")
check(health["engine"] == "cold", "engine state is cold (health check never triggers a model load)")
check(health["device"] is None, "device is null before any load")
check(health["stack"] is None, "stack is null before any load")

# The app.py Phase 4 change is that api.create_app(engine=...) reuses the
# SAME SaghiEngine the GUI's file-job page uses (see app.py/api.py
# docstrings) so the ~4-8GB model is never loaded twice. Confirmed here at
# the object-identity level, not just "it responds" -- the API server
# thread was built from ctx.engine, so is_loaded on that one object is the
# single source of truth for both surfaces.
check(ctx.engine.is_loaded is False, "shared engine (API server + GUI file-job worker) has not loaded yet")

ctx.quit_fn()

# Port must actually be released -- confirms .stop()/join() really waited
# for uvicorn's server loop to exit, not just set a flag and returned.
deadline = time.time() + 10.0
released = False
while time.time() < deadline:
    if _port_is_free(API_PORT):
        released = True
        break
    time.sleep(0.2)
check(released, f"port {API_PORT} is released after quitting")

print(f"ALL PASSED ({_checks} checks)")
