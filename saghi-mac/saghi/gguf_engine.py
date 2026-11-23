"""
GGUF engine: the same Cohere Transcribe Arabic model, quantized to GGUF and
run by transcribe.cpp (https://github.com/handy-computer/transcribe.cpp,
MIT) instead of PyTorch.

Why: same accuracy at a fraction of the memory (~2-3 GB instead of ~10 GB)
and a few times faster. See docs/GGUF_RESULTS.md for the measurements.

How:
  * No torch import anywhere in this module.
  * transcribe.cpp is called as a subprocess (`transcribe-cli`). With the
    weights memory-mapped, one call costs ~0.3 s including the model load, so
    there is no resident model and no native Python binding to ship.
  * Long audio is split exactly like the PyTorch path's feature extractor
    does today (`split_like_processor`): 35 s windows cut at the quietest
    0.1 s block inside the last 5 s, no split at or below 30 s. All chunks of
    one call go to ONE `--batch` run, so the model loads once per call.
    transcribe.cpp does not chunk on its own: a single 90 s clip starts to
    repeat itself after ~45 s, so this split is required, not optional.
  * The model can emit `<hesitation>` markers: stripped from the raw text.
  * Optional name corrections (`vocabulary.py`) run on the raw text. The
    model has no decoder biasing in transcribe.cpp (`--vocabulary` and
    `--prompt` are ignored for cohere_asr), so this is a post-correction.

Public surface mirrors `SaghiEngine` (load, transcribe, transcribe_array,
is_loaded, load_time_s, device, stack_path), so the UI, the API and file
jobs work with either engine unchanged.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Union

import numpy as np
import soundfile as sf

from .engine import SaghiEngine, TranscribeResult

logger = logging.getLogger("saghi.gguf_engine")

SAMPLE_RATE = 16000

# Mirrors the checkpoint's config.json / the transformers feature extractor.
MAX_AUDIO_CLIP_S = 35.0
OVERLAP_CHUNK_S = 5.0
MIN_ENERGY_WINDOW = 1600  # samples (0.1 s at 16 kHz)

_HESITATION_RE = re.compile(r"\s*<hesitation>\s*", re.IGNORECASE)

_APP_RES = Path.home() / "Applications" / "Saghi.app" / "Contents" / "Resources"


class GgufEngineError(RuntimeError):
    """transcribe.cpp is missing, failed, or returned no text for a chunk."""


# ---- chunking (port of the transformers CohereAsr feature extractor) ----

def _find_split_point_energy(wave: np.ndarray, start: int, end: int) -> int:
    segment = wave[start:end]
    if segment.shape[0] <= MIN_ENERGY_WINDOW:
        return (start + end) // 2
    min_energy = float("inf")
    quietest = start
    upper = segment.shape[0] - MIN_ENERGY_WINDOW
    for i in range(0, upper, MIN_ENERGY_WINDOW):
        w = segment[i : i + MIN_ENERGY_WINDOW].astype(np.float64)
        energy = float(np.sqrt(np.mean(w * w)))
        if energy < min_energy:
            min_energy = energy
            quietest = start + i
    return quietest


def split_like_processor(
    wave: np.ndarray,
    sr: int = SAMPLE_RATE,
    max_clip_s: float = MAX_AUDIO_CLIP_S,
    overlap_s: float = OVERLAP_CHUNK_S,
) -> list[tuple[int, int]]:
    """(start, end) sample ranges, same rule as the PyTorch path today."""
    total = int(wave.shape[0])
    if total == 0:
        return []
    if total / sr <= max(0.0, max_clip_s - overlap_s):
        return [(0, total)]
    chunk = max(1, int(round(max_clip_s * sr)))
    context = max(1, int(round(overlap_s * sr)))
    if total <= chunk:
        return [(0, total)]
    out: list[tuple[int, int]] = []
    idx = 0
    while idx < total:
        if idx + chunk >= total:
            out.append((idx, total))
            break
        s0 = max(idx, idx + chunk - context)
        s1 = min(idx + chunk, total)
        split = idx + chunk if s1 <= s0 else _find_split_point_energy(wave, s0, s1)
        split = max(idx + 1, min(split, total))
        out.append((idx, split))
        idx = split
    return [(a, b) for a, b in out if b > a]


def strip_hesitation(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _HESITATION_RE.sub(" ", text)).strip()


# ---- path discovery -------------------------------------------------------

def find_cli(explicit: Optional[str] = None) -> Optional[Path]:
    for cand in (explicit, os.environ.get("SAGHI_GGUF_CLI")):
        if cand:
            return Path(cand).expanduser()
    bundled = _APP_RES / "bin" / "transcribe-cli"
    if bundled.exists():
        return bundled
    found = shutil.which("transcribe-cli")
    return Path(found) if found else None


def find_model(explicit: Optional[str] = None) -> Optional[Path]:
    for cand in (explicit, os.environ.get("SAGHI_GGUF_MODEL")):
        if cand:
            return Path(cand).expanduser()
    hits = sorted(glob.glob(str(_APP_RES / "model-gguf" / "*.gguf")))
    return Path(hits[0]) if hits else None


# ---- engine ---------------------------------------------------------------

class GgufEngine:
    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        cli_path: Optional[Union[str, Path]] = None,
        vocabulary_path: Optional[Union[str, Path]] = None,
        threads: Optional[int] = None,
    ):
        self._model_arg = str(model_path) if model_path else None
        self._cli_arg = str(cli_path) if cli_path else None
        self._vocab_arg = str(vocabulary_path) if vocabulary_path else None
        self.threads = threads
        self.model_path: Optional[Path] = None
        self.cli_path: Optional[Path] = None
        self.device: Optional[str] = None
        self.stack_path: Optional[str] = None
        self._vocab = None
        self._ready = False
        self._lock = threading.RLock()
        self._load_time_s: Optional[float] = None

    # Kept for code that reads engine.model_dir (logs, API health).
    @property
    def model_dir(self) -> Optional[Path]:
        return self.model_path

    @property
    def load_time_s(self) -> Optional[float]:
        return self._load_time_s

    @property
    def is_loaded(self) -> bool:
        return self._ready

    def load(self) -> None:
        with self._lock:
            if self._ready:
                return
            t0 = time.time()
            cli = find_cli(self._cli_arg)
            model = find_model(self._model_arg)
            if cli is None or not cli.exists():
                raise GgufEngineError(f"transcribe-cli not found ({cli}); set SAGHI_GGUF_CLI")
            if model is None or not model.exists():
                raise GgufEngineError(f"GGUF model not found ({model}); set SAGHI_GGUF_MODEL")
            from .vocabulary import Vocabulary

            self._vocab = Vocabulary.load(self._vocab_arg)
            self.cli_path, self.model_path = cli, model
            self.device = "metal"
            self.stack_path = "gguf"
            self._ready = True
            self._load_time_s = time.time() - t0
            logger.info("GGUF engine ready: %s via %s (%d vocabulary rules)",
                        model.name, cli.name, len(self._vocab.rules))

    def unload(self) -> None:
        with self._lock:
            self._ready = False

    # ---- core -------------------------------------------------------------

    def _run_batch(self, wavs: list[Path], language: str, timeout_s: float) -> dict[str, str]:
        with tempfile.TemporaryDirectory(prefix="saghi-gguf-") as td:
            lst = Path(td) / "files.list"
            lst.write_text("".join(f"{p}\n" for p in wavs), encoding="utf-8")
            cmd = [str(self.cli_path), "-m", str(self.model_path), "-l", language, "-q",
                   "--batch", str(lst), "--batch-jsonl"]
            if self.threads:
                cmd += ["--threads", str(self.threads)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise GgufEngineError(f"transcribe-cli timed out after {timeout_s:.0f}s") from exc
        if proc.returncode != 0:
            raise GgufEngineError(f"transcribe-cli exited {proc.returncode}: {proc.stderr.strip()[-400:]}")
        texts: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("file"):
                err = row.get("error") or ""
                if err and not err.startswith("output repetition"):
                    raise GgufEngineError(f"transcribe-cli failed on a chunk: {err}")
                # The repetition guard stops decoding and keeps the text so
                # far: usable, but maybe short. Flag it for one retry.
                texts[row["file"]] = (row.get("text", ""), bool(err))
        missing = [p for p in wavs if str(p) not in texts]
        if missing:
            raise GgufEngineError(f"transcribe-cli returned no result for {len(missing)} chunk(s)")
        return texts

    def _transcribe_spans(self, arr: np.ndarray, spans, language: str, td: Path, tag: str):
        wavs = []
        for i, (a, b) in enumerate(spans):
            p = td / f"{tag}{i:04d}.wav"
            sf.write(str(p), arr[a:b], SAMPLE_RATE, subtype="PCM_16")
            wavs.append(p)
        audio_s = sum(b - a for a, b in spans) / SAMPLE_RATE
        res = self._run_batch(wavs, language, timeout_s=60.0 + 2.0 * audio_s)
        return [res[str(p)] for p in wavs]

    def _infer_array(self, arr: np.ndarray, language: str) -> str:
        spans = split_like_processor(arr)
        if not spans:
            return ""
        with tempfile.TemporaryDirectory(prefix="saghi-gguf-wav-") as tdname:
            td = Path(tdname)
            results = self._transcribe_spans(arr, spans, language, td, "chunk")
            # A chunk stopped by the repetition guard (music, laughter, a
            # stuck phrase) is retried once as two halves cut at the quietest
            # point near its middle; the halves win if they give more words.
            retry = [i for i, (_, rep) in enumerate(results) if rep and spans[i][1] - spans[i][0] > 8 * SAMPLE_RATE]
            if retry:
                halves = []
                for i in retry:
                    a, b = spans[i]
                    mid = (a + b) // 2
                    cut = _find_split_point_energy(arr, max(a, mid - 3 * SAMPLE_RATE), min(b, mid + 3 * SAMPLE_RATE))
                    halves += [(a, cut), (cut, b)]
                again = self._transcribe_spans(arr, halves, language, td, "retry")
                for k, i in enumerate(retry):
                    two = " ".join(t.strip() for t, _ in again[2 * k : 2 * k + 2] if t.strip())
                    if len(two.split()) > len(results[i][0].split()):
                        results[i] = (two, False)
                logger.info("GGUF: %d chunk(s) hit the repetition guard, retried as halves", len(retry))
        joined = " ".join(t.strip() for t, _ in results if t and t.strip())
        raw = strip_hesitation(joined)
        if self._vocab is not None:
            raw = self._vocab.apply(raw)
        return raw

    def _result(self, raw_text, language, duration_s, inference_s, cleanup_level) -> TranscribeResult:
        from .cleanup import clean_transcript

        return TranscribeResult(
            raw_text=raw_text,
            text=clean_transcript(raw_text, cleanup_level),
            language=language,
            duration_s=duration_s,
            inference_s=inference_s,
        )

    def transcribe(self, audio_path: Union[str, Path], language: str = "ar",
                   cleanup_level: str = "light") -> TranscribeResult:
        with self._lock:
            if not self._ready:
                self.load()
            arr, sr = SaghiEngine._read_and_prepare(Path(audio_path))
            t0 = time.time()
            raw = self._infer_array(arr, language)
            inference_s = time.time() - t0
        return self._result(raw, language, len(arr) / sr if sr else 0.0, inference_s, cleanup_level)

    def transcribe_array(self, audio: np.ndarray, sr: int, language: str = "ar",
                         cleanup_level: str = "light") -> TranscribeResult:
        with self._lock:
            if not self._ready:
                self.load()
            arr, psr = SaghiEngine._prepare_ndarray(audio, sr)
            t0 = time.time()
            raw = self._infer_array(arr, language)
            inference_s = time.time() - t0
        return self._result(raw, language, len(arr) / psr if psr else 0.0, inference_s, cleanup_level)
