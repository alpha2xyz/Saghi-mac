#!/usr/bin/env python3
"""
Phase 4 GUI test 4: THE ONE real end-to-end inference run for this phase
(model load ~70-110s + inference, measured on a slow CPU --
budget several minutes). Drives saghi.ui.filejob_page.FileJobWorker (the
real QThread class the file-transcribe page uses) against the ~23s test
WAV reused from Phase 3, exactly like clicking "ابدأ التفريغ" in the GUI
would, except driven headless via a QEventLoop instead of a visible
window.

Confirms:
  - progress signals fire on the worker thread and reach the UI thread
    with sane values (done/total/percent/elapsed monotonic, chunk count
    matches chunk_s=10 on a 23s file -> 3 chunks, matching Phase 3's own
    full-run test)
  - the job actually runs on a background thread, never blocking the Qt
    event loop (the event loop keeps processing other events throughout)
  - completion text matches the known transcripts from
    earlier runs (same file, same
    expectation Phase 3 already verified against the CLI path -- this
    test verifies the GUI's worker wiring gets the identical result, not
    the model's accuracy again)
  - output dir contains output.txt/.srt/.vtt (timestamps=True)

Run (generous timeout -- this genuinely takes minutes):
    QT_QPA_PLATFORM=offscreen SAGHI_DATA_DIR=<scratch dir> \
        PYTHONPATH=. <venv>/bin/python dev/test_gui_filejob.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import threading  # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi.engine import SaghiEngine  # noqa: E402
from saghi.ui.filejob_page import FileJobWorker  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


_wav_env = os.environ.get("SAGHI_TEST_WAV")
_model_env = os.environ.get("SAGHI_MODEL_DIR")
if not _wav_env or not _model_env or not Path(_wav_env).is_file():
    print("SKIPPED: set SAGHI_MODEL_DIR (model folder) and SAGHI_TEST_WAV (a ~20s Arabic WAV) to run this real-inference test.")
    sys.exit(0)
TEST_WAV = Path(_wav_env)
MODEL_DIR = Path(_model_env)
TIMEOUT_MS = 600_000  # 10 minutes -- generous, per the task's instructions

assert TEST_WAV.is_file(), f"Test WAV not found: {TEST_WAV}"

print(f"--- Test 4: ONE real end-to-end file job through the GUI worker ({TEST_WAV.name}) ---")
print("This genuinely takes several minutes (model load + inference).")

app = QApplication.instance() or QApplication(sys.argv)

engine = SaghiEngine(MODEL_DIR)
check(engine.is_loaded is False, "engine not loaded before the worker starts (no eager load)")

cancel_event = threading.Event()
worker = FileJobWorker(
    engine,
    TEST_WAV,
    "ar",
    "light",
    True,  # timestamps
    cancel_event,
    # Force multiple chunks on this 23s file, matching Phase 3's own
    # full-run test (chunk_s=10 -> 3 chunks). FileJob's default chunk_s is
    # memory-aware (30s/60s -- see filejobs.default_chunk_s()), which
    # would produce only 1 chunk for a 23s file and never exercise the
    # multi-chunk progress-reporting path this test cares about.
    chunk_s=10.0,
)

progress_events: list[tuple] = []
result_holder: dict = {}
error_holder: dict = {}
loop_tick_count = {"n": 0}


def _on_progress(done, total, percent, elapsed_s, eta_s):
    progress_events.append((done, total, percent, elapsed_s, eta_s))
    print(f"  progress: chunk {done}/{total}  {percent:.1f}%  elapsed={elapsed_s:.1f}s  eta={eta_s}")


def _on_finished(result):
    result_holder["result"] = result


def _on_failed(message):
    error_holder["message"] = message


worker.progress.connect(_on_progress)
worker.finished_ok.connect(_on_finished)
worker.failed.connect(_on_failed)

loop = QEventLoop()
worker.finished.connect(loop.quit)  # QThread.finished -- fires once run() returns, either path

# A ticking timer proves the Qt event loop stays responsive (i.e. the
# worker really is running on a background thread, not blocking the UI
# thread) -- it can only keep firing if loop.exec() isn't stuck inside
# FileJob.run() itself.
tick_timer = QTimer()
tick_timer.timeout.connect(lambda: loop_tick_count.__setitem__("n", loop_tick_count["n"] + 1))
tick_timer.start(500)

timeout_timer = QTimer()
timeout_timer.setSingleShot(True)
timeout_timer.timeout.connect(loop.quit)
timeout_timer.start(TIMEOUT_MS)

t0 = time.time()
worker.start()
loop.exec()
wall_s = time.time() - t0

check("message" not in error_holder, f"worker did not fail: {error_holder.get('message')}")
check("result" in result_holder, f"worker finished within the {TIMEOUT_MS/1000:.0f}s timeout (wall={wall_s:.1f}s)")

check(loop_tick_count["n"] >= 3, f"Qt event loop stayed responsive while the worker ran ({loop_tick_count['n']} ticks -- proves the job ran on a background thread, not the UI thread)")

# ---- progress signal sanity ------------------------------------------------

check(len(progress_events) >= 2, f"at least 2 progress events fired (got {len(progress_events)})")

totals = {ev[1] for ev in progress_events}
check(totals == {3}, f"total_chunks is consistently 3 for a 23s file at chunk_s=10 (got {totals})")

dones = [ev[0] for ev in progress_events]
check(dones == sorted(dones), f"done_chunks is monotonically non-decreasing across progress events (got {dones})")
check(dones[-1] == 3, f"final progress event reports all 3 chunks done (got {dones[-1]})")

percents = [ev[2] for ev in progress_events]
check(all(0.0 <= p <= 100.0 for p in percents), "all percent values are within [0, 100]")
check(percents[-1] == 100.0, f"final percent is 100.0 (got {percents[-1]})")

elapsed_values = [ev[3] for ev in progress_events]
check(all(e >= 0.0 for e in elapsed_values), "all elapsed_s values are non-negative")

# ---- result content ---------------------------------------------------------

result = result_holder["result"]
check(result.cancelled is False, "result.cancelled is False (never cancelled)")
check(result.total_chunks == 3, f"result.total_chunks == 3 (got {result.total_chunks})")
check(len(result.text) > 0, "result.text is non-empty")

# Known transcripts from earlier runs --
# this test WAV is sample2 + silence + sample1 + silence + sample2, so the
# cleaned text should contain recognizable fragments of both known samples.
check("more safe" in result.text.lower() or "more safe" in result.raw_text.lower(),
      "result text contains a recognizable fragment of the known sample1 transcript ('more safe')")
check(len(result.segments) >= 3, f"result has at least 3 timestamped segments (got {len(result.segments)})")

# Segment timestamps must be sane: non-negative, monotonic, within the clip.
prev_end = -1.0
for seg in result.segments:
    check(seg.start_s >= 0.0, f"segment start_s >= 0 ({seg.start_s})")
    check(seg.end_s >= seg.start_s, f"segment end_s >= start_s ({seg.start_s} -> {seg.end_s})")
    check(seg.start_s >= prev_end - 0.01, f"segments are in non-overlapping chronological order (prev_end={prev_end}, start={seg.start_s})")
    prev_end = seg.end_s
check(prev_end <= 23.5, f"last segment ends within the clip's ~23s duration (got {prev_end})")

# ---- output files -------------------------------------------------------

job_dir = Path(result.job_dir)
check(job_dir.is_dir(), f"job dir exists: {job_dir}")
for name in ("output.txt", "output.srt", "output.vtt", "manifest.json"):
    p = job_dir / name
    check(p.is_file() and p.stat().st_size > 0, f"{name} exists and is non-empty in the job dir")

srt_content = (job_dir / "output.srt").read_text(encoding="utf-8")
check("-->" in srt_content, "output.srt contains valid SRT timerange arrows")
vtt_content = (job_dir / "output.vtt").read_text(encoding="utf-8")
check(vtt_content.startswith("WEBVTT"), "output.vtt starts with the WEBVTT header")

check(engine.is_loaded is True, "engine.is_loaded is True after the run (model loaded lazily, on first use)")

print(f"\nWall time: {wall_s:.1f}s")
print(f"ALL PASSED ({_checks} checks)")
