#!/usr/bin/env python3
"""
Tests the Phase 4 `FileJob.cancel_event` hook (saghi/filejobs.py) for real,
with ZERO model inference and ZERO Qt -- pure saghi.filejobs, no PySide6
import at all. This is deliberately NOT under dev/test_gui_filejob.py's
"one real inference run" budget.

The trick (also used for the other zero-inference checks): an all-silence WAV produces zero segments per
chunk (segmentation.split_into_segments() returns [] for silent audio),
so FileJob.run() never calls engine.transcribe_array() and therefore never
loads the model at all -- SaghiEngine(None) can stand in as "the engine"
here without ever being touched.

Determinism: cancel_event is set from INSIDE the on_progress callback
itself, right after the first chunk's completion is reported. Since
on_progress() runs synchronously inside run()'s own loop (see
filejobs.py), the very next loop iteration's "check cancel_event at the
top of each pending chunk" sees it set -- no sleep, no thread, no race.

Covers:
  1. A job cancelled after chunk 1/3: result.cancelled is True, manifest
     shows exactly 1/3 chunks done, partial.txt exists, output.txt does
     NOT exist, and history has 0 rows (nothing saved on cancel).
  2. Resuming that same job (fresh FileJob, no cancel_event): first
     progress event reports done==1 (proves resume, not a restart),
     result.cancelled is False, output.txt now exists, and history has
     exactly 1 row (not a duplicate -- reuses the Phase 3 fix for
     re-running an already/partially-processed job).

Run:
    SAGHI_DATA_DIR=<scratch dir> PYTHONPATH=. <venv>/bin/python \
        dev/test_filejob_cancel.py
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from saghi import history  # noqa: E402
from saghi.engine import SaghiEngine  # noqa: E402
from saghi.filejobs import FileJob  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


scratch = Path(os.environ["SAGHI_DATA_DIR"])
scratch.mkdir(parents=True, exist_ok=True)
wav_path = scratch / "silence_for_cancel_test.wav"

sr = 16000
duration_s = 3.0
sf.write(str(wav_path), np.zeros(int(sr * duration_s), dtype=np.float32), sr)

# A stand-in engine object is fine here: silence produces zero segments per
# chunk, so FileJob.run() never calls engine.transcribe_array() at all --
# confirmed below by checking engine.is_loaded stays False throughout.
engine = SaghiEngine(model_dir="/nonexistent")

print("--- Cancel hook test: cancel after chunk 1/3, then resume ---")

cancel_event = threading.Event()
progress_log: list[tuple] = []


def on_progress(done, total, percent, elapsed_s, eta_s):
    progress_log.append((done, total, percent))
    print(f"  progress: {done}/{total} ({percent:.1f}%)")
    if done == 1:
        cancel_event.set()  # cancel right after chunk 1 completes -- deterministic, no race


job = FileJob(engine, wav_path, language="ar", cleanup_level="light", timestamps=False, chunk_s=1.0, cancel_event=cancel_event)
result = job.run(on_progress=on_progress)

check(engine.is_loaded is False, "engine never loaded (silence has zero segments per chunk -- no inference call needed)")
check(result.cancelled is True, "result.cancelled is True after cancel_event was set mid-run")
check(result.total_chunks == 3, f"total_chunks is 3 for a 3.0s file at chunk_s=1.0 (got {result.total_chunks})")

with open(job._manifest_path, "r", encoding="utf-8") as f:
    import json

    manifest = json.load(f)
done_chunks = sum(1 for c in manifest["chunks"] if c["status"] == "done")
check(done_chunks == 1, f"manifest shows exactly 1/3 chunks done after cancel (got {done_chunks})")
check(manifest["status"] == "in_progress", "manifest status is still in_progress (never marked complete)")

check((job.job_dir / "partial.txt").exists(), "partial.txt exists after cancel (staged after the completed chunk)")
check(not (job.job_dir / "output.txt").exists(), "output.txt does NOT exist after cancel (job never completed)")

hist_count_after_cancel = len(history.search(limit=200))
check(hist_count_after_cancel == 0, f"nothing saved to history on cancel (got {hist_count_after_cancel} rows)")

print("\n--- Resuming the cancelled job (fresh FileJob, no cancel_event) ---")

resume_progress_log: list[tuple] = []


def on_resume_progress(done, total, percent, elapsed_s, eta_s):
    resume_progress_log.append((done, total, percent))
    print(f"  progress: {done}/{total} ({percent:.1f}%)")


job2 = FileJob(engine, wav_path, language="ar", cleanup_level="light", timestamps=False, chunk_s=1.0)
check(job2.job_id == job.job_id, "resumed FileJob resolves to the SAME job_id (same file + settings)")

result2 = job2.run(on_progress=on_resume_progress)

check(resume_progress_log[0][0] == 1, f"first progress event of the resumed run reports done=1 (proves resume, not a restart) -- got {resume_progress_log[0]}")
check(result2.cancelled is False, "resumed run completes normally (cancelled is False)")
check(result2.resumed is True, "result2.resumed is True")
check(result2.resumed_from_chunk == 1, f"result2.resumed_from_chunk == 1 (got {result2.resumed_from_chunk})")
check((job.job_dir / "output.txt").exists(), "output.txt exists after the resumed run completes")

hist_count_after_resume = len(history.search(limit=200))
check(hist_count_after_resume == 1, f"history has exactly 1 row after resume completes (not a duplicate) -- got {hist_count_after_resume}")
check(result2.history_id is not None, "result2.history_id is set")

print(f"\nALL PASSED ({_checks} checks)")
