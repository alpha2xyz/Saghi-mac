"""
Long-file transcription ("file jobs"): chunked processing, silence-split
segmentation, staged saves with resume, and TXT/SRT/WebVTT export.

Implements the behavior described in packaging/README_AR.md's
"تفريغ ملف وCaptions" ("File transcription and Captions") section:

  * Process a long source file one chunk at a time, WITHOUT ever loading
    the whole file into memory (soundfile.SoundFile seek/read block
    reads). Chunk length is chosen automatically from available machine
    memory (see default_chunk_s()).
  * Inside each chunk, split the speech into short, model-suitable
    segments at silence boundaries (saghi.segmentation.split_into_segments)
    and transcribe each segment individually.
  * After every completed chunk, atomically stage TXT/SRT/WebVTT partial
    outputs into the job's directory under <data dir>/file-jobs/<job-id>/.
  * If the app/process stops mid-job, re-picking the SAME file with the
    SAME settings (language, cleanup level, timestamps) resolves to the
    SAME job id and resumes from the first incomplete chunk automatically
    -- this is the headline reliability feature
    ("افتح صاغي واختر نفس الملف وبالإعدادات نفسها... سيكمل تلقائيًا").
  * Timestamps are estimated from where each segment landed in the
    silence-split timeline, never claimed as word-level alignment (the
    model provides no native word alignment -- see README_AR.md).

Job identity = SHA-256 of (streamed file-content hash + the user-visible
settings: language, cleanup_level, timestamps). Deliberately does NOT
include `chunk_s` in the identity -- chunk_s controls how a *new* job's
chunk plan is built, but once a job exists, its manifest's already-computed
chunk plan is authoritative for resume regardless of what --chunk-s a
later invocation happens to pass (so "re-pick the same file with the same
settings" doesn't also require remembering the exact chunk size used the
first time).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np
import soundfile as sf

from . import history, paths
from .segmentation import split_into_segments
from .timestamps import Segment, format_srt, format_vtt

logger = logging.getLogger("saghi.filejobs")

# Kept safely under the model's ~35s max_audio_clip_s (see engine.py's
# module docstring / "batch_size=1" note) -- speech
# segments handed to the model are never close to that ceiling.
MAX_SEGMENT_S = 20.0
MIN_SEGMENT_S = 0.5

# Memory-aware default chunk length:
# smaller chunks on a memory-constrained machine (e.g. 8GB, well
# under the 12GB line) keep peak RSS bounded chunk-to-chunk regardless of
# how long the source file is; a bigger machine can afford fewer, larger
# reads for less per-chunk overhead.
_LOW_MEM_CHUNK_S = 30.0
_HIGH_MEM_CHUNK_S = 60.0
_MEM_THRESHOLD_BYTES = 12 * 1024**3

ProgressCallback = Callable[[int, int, float, float, Optional[float]], None]


def _total_ram_bytes() -> int:
    """
    Total physical RAM, in bytes. `os.sysconf('SC_PHYS_PAGES')` is POSIX
    and works on macOS (verified on a real Mac: matches
    `sysctl -n hw.memsize` exactly, both report 8589934592 on an 8GB
    machine) -- try it first since it needs no subprocess. Falls back to
    `sysctl -n hw.memsize` for any platform/Python build where the sysconf
    names aren't wired up, and to a conservative 8GB assumption if even
    that fails (better to under-chunk than to crash chunk-sizing itself).
    """
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True, timeout=5
        )
        return int(out.stdout.strip())
    except Exception:
        logger.warning("Could not determine total RAM -- assuming 8GB for chunk sizing")
        return 8 * 1024**3


def default_chunk_s(total_ram_bytes: Optional[int] = None) -> float:
    """Memory-aware default chunk length in seconds -- see module docstring."""
    if total_ram_bytes is None:
        total_ram_bytes = _total_ram_bytes()
    return _LOW_MEM_CHUNK_S if total_ram_bytes < _MEM_THRESHOLD_BYTES else _HIGH_MEM_CHUNK_S


def _hash_file(path: Path, block_size: int = 8 * 1024 * 1024) -> str:
    """Streaming SHA-256 of a file's content -- never reads it all into RAM at once."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def compute_job_id(content_hash: str, language: str, cleanup_level: str, timestamps: bool) -> str:
    """Job identity: SHA-256 of (content hash + the user-visible settings). See module docstring."""
    settings_str = f"{content_hash}|lang={language}|cleanup={cleanup_level}|ts={bool(timestamps)}"
    return hashlib.sha256(settings_str.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _downmix_resample(data: np.ndarray, sr_in: int, target_sr: int = 16000) -> np.ndarray:
    """
    Downmix to mono + resample one already-in-memory chunk block to
    `target_sr`. Same soxr-based approach as engine.py's
    `_prepare_ndarray` (deliberately not librosa -- see engine.py's module
    docstring), duplicated as a small local helper rather than imported so
    this module never needs a SaghiEngine instance just to prepare audio;
    FileJob only touches the engine for the one thing it can't do itself:
    running inference.
    """
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1).astype(np.float32, copy=False)
    if sr_in and sr_in != target_sr:
        import soxr

        arr = soxr.resample(arr, sr_in, target_sr).astype(np.float32, copy=False)
    return arr


@dataclass
class FileJobResult:
    job_id: str
    job_dir: Path
    text: str
    raw_text: str
    segments: list[Segment]
    total_duration_s: float
    total_chunks: int
    resumed: bool
    resumed_from_chunk: int  # count of chunks that were already "done" when run() started
    history_id: Optional[int]
    # Phase 4 addition (GUI cancel button) -- True if run() stopped early
    # because cancel_event was set, rather than finishing every chunk.
    # Defaults to False so every Phase 1-3 call site (CLI, tests) that
    # constructs FileJobResult without this keyword is unaffected.
    cancelled: bool = False


class FileJob:
    """
    One long-file transcription job. Construct with the source file and
    settings (this immediately hashes the file's content -- streaming, may
    take a few seconds for a large file, but touches no model/engine yet),
    then call `.run(on_progress=...)` to actually process it.

    Re-constructing a FileJob for the same source file + same settings
    resolves to the same job_id / job_dir every time, so calling `.run()`
    again (e.g. after a previous run was killed) resumes automatically.
    """

    def __init__(
        self,
        engine,  # saghi.engine.SaghiEngine -- untyped here to avoid an import-cycle risk for no benefit
        source_path: Union[str, Path],
        language: str = "ar",
        cleanup_level: str = "light",
        timestamps: bool = False,
        chunk_s: Optional[float] = None,
        data_dir: Optional[Union[str, Path]] = None,
        save_to_history: bool = True,
        cancel_event: Optional[threading.Event] = None,
    ):
        # cancel_event: Phase 4 (GUI) addition -- the smallest clean cancel
        # hook, added because run()'s per-chunk loop had no way to stop
        # early before this. Cooperative only: checked once at the top of
        # each pending chunk's iteration (never mid-chunk), so "cancel"
        # always means "finish the chunk already in flight, then stop" --
        # matching the task's "stops after the current chunk" requirement
        # and, just as importantly, never leaving a chunk half-transcribed
        # in the manifest. A cancelled run leaves the manifest exactly as
        # if the process had been killed after that chunk -- fully
        # resumable by constructing a new FileJob for the same file/
        # settings and calling .run() again, same as any other interrupted
        # run. See FileJobResult.cancelled and run()'s docstring below.
        self.engine = engine
        self.source_path = Path(source_path).expanduser().resolve()
        if not self.source_path.is_file():
            raise FileNotFoundError(f"source file not found: {self.source_path}")

        self.language = language
        self.cleanup_level = cleanup_level
        self.timestamps = bool(timestamps)
        self._requested_chunk_s = chunk_s
        self.save_to_history = save_to_history
        self._cancel_event = cancel_event

        base_data_dir = Path(data_dir) if data_dir else paths.data_dir()
        base_data_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Hashing source file (streaming) for job identity: %s", self.source_path)
        self.content_hash = _hash_file(self.source_path)
        self.job_id = compute_job_id(self.content_hash, self.language, self.cleanup_level, self.timestamps)
        self.job_dir = base_data_dir / "file-jobs" / self.job_id
        self._manifest_path = self.job_dir / "manifest.json"
        self.manifest: Optional[dict] = None

    # ---- manifest / chunk plan ----------------------------------------

    def _load_or_create_manifest(self) -> bool:
        """Returns True if an existing manifest was found (resume), False if freshly created."""
        if self._manifest_path.exists():
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                self.manifest = json.load(f)
            done = sum(1 for c in self.manifest["chunks"] if c["status"] == "done")
            total = len(self.manifest["chunks"])
            logger.info(
                "Resuming file job %s: found existing manifest (%d/%d chunks already done, "
                "same content hash + settings as a previous run)",
                self.job_id, done, total,
            )
            return True

        info = sf.info(str(self.source_path))
        sr_in = info.samplerate
        total_frames = info.frames
        total_duration_s = total_frames / sr_in if sr_in else 0.0

        chunk_s = self._requested_chunk_s if self._requested_chunk_s else default_chunk_s()
        chunk_frames = max(1, int(round(chunk_s * sr_in))) if sr_in else max(1, total_frames)

        chunks = []
        start = 0
        idx = 0
        while start < total_frames:
            end = min(start + chunk_frames, total_frames)
            chunks.append(
                {
                    "index": idx,
                    "start_frame": start,
                    "end_frame": end,
                    "status": "pending",
                    "inference_s": 0.0,
                    "segments": [],
                }
            )
            start = end
            idx += 1

        now = _now_iso()
        self.manifest = {
            "job_id": self.job_id,
            "source_path": str(self.source_path),
            "source_size_bytes": self.source_path.stat().st_size,
            "content_hash": self.content_hash,
            "language": self.language,
            "cleanup_level": self.cleanup_level,
            "timestamps": self.timestamps,
            "sample_rate_in": sr_in,
            "total_frames": total_frames,
            "total_duration_s": total_duration_s,
            "chunk_s": chunk_s,
            "status": "in_progress",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "history_id": None,
            "chunks": chunks,
        }
        self._save_manifest()
        logger.info(
            "Created new file job %s: %d chunk(s) of ~%.1fs each (%.1fs total audio, source sr=%dHz)",
            self.job_id, len(chunks), chunk_s, total_duration_s, sr_in,
        )
        return False

    def _save_manifest(self) -> None:
        self.manifest["updated_at"] = _now_iso()
        _atomic_write_json(self._manifest_path, self.manifest)

    # ---- staged output files --------------------------------------------

    def _all_segments(self) -> list[Segment]:
        segs: list[Segment] = []
        for chunk in self.manifest["chunks"]:
            if chunk["status"] == "done":
                for s in chunk["segments"]:
                    segs.append(Segment(start_s=s["start_s"], end_s=s["end_s"], text=s["text"]))
        return segs

    def _all_raw_text(self) -> str:
        parts = [
            s["raw_text"]
            for chunk in self.manifest["chunks"]
            if chunk["status"] == "done"
            for s in chunk["segments"]
            if s.get("raw_text")
        ]
        return " ".join(parts).strip()

    def _write_staged_outputs(self, prefix: str) -> tuple[str, list[Segment]]:
        """
        Write <prefix>.txt (always) and <prefix>.srt/<prefix>.vtt (only if
        this job has timestamps enabled) into the job dir, atomically
        (tmp file + os.replace). `prefix` is "partial" after each
        completed chunk, "output" once the whole job is done. Returns the
        full joined text and the segment list, for the caller to build the
        final result / history entry from without redoing the work.
        """
        segments = self._all_segments()
        full_text = " ".join(seg.text for seg in segments if seg.text).strip()

        _atomic_write_text(self.job_dir / f"{prefix}.txt", full_text + ("\n" if full_text else ""))
        if self.timestamps:
            _atomic_write_text(self.job_dir / f"{prefix}.srt", format_srt(segments))
            _atomic_write_text(self.job_dir / f"{prefix}.vtt", format_vtt(segments))

        return full_text, segments

    # ---- run --------------------------------------------------------------

    def run(self, on_progress: Optional[ProgressCallback] = None) -> FileJobResult:
        """
        Process every pending chunk (skipping any already marked "done" --
        the resume path), staging outputs after each one, then write the
        final output.* files and (optionally) save to history.

        `on_progress(done_chunks, total_chunks, percent, elapsed_s, eta_s)`
        is called once immediately (reflecting any chunks already done from
        a previous run, elapsed_s=0.0, eta_s=None -- no timing data for
        *this* run yet) and again after every chunk this run actually
        processes. `eta_s` is None until at least one chunk has completed
        in this run (an average needs at least one sample); it's computed
        from this run's own per-chunk timings only, not from timings
        persisted in the manifest by an earlier, possibly differently-
        loaded run.

        If `cancel_event` was passed to the constructor and gets set while
        chunks remain pending, processing stops after the chunk currently
        in flight finishes (never mid-chunk) and this returns a
        `FileJobResult` with `cancelled=True`, reflecting only the chunks
        done so far -- no `output.*` files are written and nothing is
        saved to history. The job stays fully resumable: constructing a new
        `FileJob` for the same file + settings and calling `.run()` again
        continues from the first still-pending chunk, identically to
        resuming after a kill -9.
        """
        self.job_dir.mkdir(parents=True, exist_ok=True)
        resumed = self._load_or_create_manifest()

        chunks = self.manifest["chunks"]
        total_chunks = len(chunks)
        done_at_start = sum(1 for c in chunks if c["status"] == "done")

        if resumed and 0 < done_at_start < total_chunks:
            logger.info(
                "File job %s: resuming -- %d/%d chunk(s) already done, continuing from chunk %d",
                self.job_id, done_at_start, total_chunks, done_at_start + 1,
            )
        elif resumed and done_at_start == total_chunks and total_chunks > 0:
            logger.info("File job %s: already fully complete, nothing to do", self.job_id)

        run_start = time.time()
        run_durations: list[float] = []

        def _report(done: int) -> None:
            if on_progress is None:
                return
            percent = (done / total_chunks * 100.0) if total_chunks else 100.0
            avg = (sum(run_durations) / len(run_durations)) if run_durations else None
            eta_s = avg * (total_chunks - done) if avg is not None else None
            on_progress(done, total_chunks, percent, time.time() - run_start, eta_s)

        _report(done_at_start)

        sr_in = self.manifest["sample_rate_in"]
        cancelled = False

        if done_at_start < total_chunks:
            with sf.SoundFile(str(self.source_path)) as f:
                for chunk in chunks:
                    if chunk["status"] == "done":
                        continue

                    if self._cancel_event is not None and self._cancel_event.is_set():
                        logger.info(
                            "File job %s: cancelled before chunk %d -- manifest already reflects "
                            "%d/%d done chunks, resumable exactly like an interrupted run",
                            self.job_id, chunk["index"],
                            sum(1 for c in chunks if c["status"] == "done"), total_chunks,
                        )
                        cancelled = True
                        break

                    t0 = time.time()
                    f.seek(chunk["start_frame"])
                    n_frames = chunk["end_frame"] - chunk["start_frame"]
                    raw = f.read(frames=n_frames, dtype="float32", always_2d=False)
                    audio16 = _downmix_resample(raw, sr_in, 16000)

                    chunk_start_time_s = chunk["start_frame"] / sr_in if sr_in else 0.0

                    seg_bounds = split_into_segments(
                        audio16, 16000, max_segment_s=MAX_SEGMENT_S, min_segment_s=MIN_SEGMENT_S
                    )

                    seg_records = []
                    for seg_start, seg_end in seg_bounds:
                        seg_audio = audio16[seg_start:seg_end]
                        result = self.engine.transcribe_array(
                            seg_audio, 16000, language=self.language, cleanup_level=self.cleanup_level
                        )
                        seg_records.append(
                            {
                                "start_s": chunk_start_time_s + seg_start / 16000,
                                "end_s": chunk_start_time_s + seg_end / 16000,
                                "text": result.text,
                                "raw_text": result.raw_text,
                            }
                        )

                    chunk_elapsed = time.time() - t0
                    chunk["status"] = "done"
                    chunk["inference_s"] = chunk_elapsed
                    chunk["segments"] = seg_records

                    # Staged save -- happens after EVERY chunk, so a kill
                    # -9 right after this point still leaves a resumable
                    # manifest + up-to-date partial outputs on disk.
                    self._save_manifest()
                    self._write_staged_outputs("partial")

                    run_durations.append(chunk_elapsed)
                    done_now = sum(1 for c in chunks if c["status"] == "done")
                    _report(done_now)

        if cancelled:
            # Do NOT write output.*/mark the manifest complete/save history
            # -- a cancelled run is exactly an interrupted run from the
            # manifest's point of view (every completed chunk was already
            # staged to partial.* right after it finished; see the loop
            # above), and must stay resumable via a fresh FileJob() + run()
            # for the same file/settings. Build the result from whatever
            # chunks are already "done" so far.
            partial_text = " ".join(seg.text for seg in self._all_segments() if seg.text).strip()
            return FileJobResult(
                job_id=self.job_id,
                job_dir=self.job_dir,
                text=partial_text,
                raw_text=self._all_raw_text(),
                segments=self._all_segments(),
                total_duration_s=self.manifest["total_duration_s"],
                total_chunks=total_chunks,
                resumed=resumed,
                resumed_from_chunk=done_at_start,
                history_id=self.manifest.get("history_id"),
                cancelled=True,
            )

        full_text, segments = self._write_staged_outputs("output")
        full_raw_text = self._all_raw_text()

        self.manifest["status"] = "complete"
        self.manifest["completed_at"] = _now_iso()
        self._save_manifest()

        # Idempotent across re-runs: a job that is already fully complete
        # (all chunks "done" when run() was called -- e.g. the user
        # re-picks a finished file, or a GUI calls .run() again on a job
        # that's already done) must NOT append a second history row every
        # time. The history_id, once assigned, is persisted in the
        # manifest itself and reused on any later completion of the same
        # job rather than re-calling history.add_entry().
        history_id = self.manifest.get("history_id")
        if self.save_to_history and history_id is None:
            try:
                entry = history.add_entry(
                    source="filejob",
                    language=self.language,
                    cleanup_level=self.cleanup_level,
                    duration_s=self.manifest["total_duration_s"],
                    inference_s=sum(c["inference_s"] for c in chunks),
                    raw_text=full_raw_text,
                    text=full_text,
                    audio_filename=self.source_path.name,
                )
                history_id = entry["id"]
                self.manifest["history_id"] = history_id
                self._save_manifest()
            except Exception:
                # Same policy as api.py: a history-write failure doesn't
                # throw away a transcription that already succeeded.
                logger.exception("Failed to save file job transcription to history")

        return FileJobResult(
            job_id=self.job_id,
            job_dir=self.job_dir,
            text=full_text,
            raw_text=full_raw_text,
            segments=segments,
            total_duration_s=self.manifest["total_duration_s"],
            total_chunks=total_chunks,
            resumed=resumed,
            resumed_from_chunk=done_at_start,
            history_id=history_id,
        )
