"""
Saghi ASR engine.

Loads the CohereLabs/cohere-transcribe-arabic-07-2026 checkpoint (bundled
locally, path given by SAGHI_MODEL_DIR / --model-dir) and exposes a single, stack-agnostic
`SaghiEngine.transcribe()` entry point.

Two load paths, tried in order by `.load()`:

  1. "native" -- the target Apple Silicon stack (transformers>=5.4.0,
     torch>=2.5), straightforward AutoProcessor/AutoModel per the model's
     own README Quick Start. This is the path used on Apple Silicon.

  2. "fallback" -- used where transformers < 5.4 is installed (e.g. Intel
     Macs, which are not supported by the release build). transformers 4.57.6 does not
     natively register the "cohere_asr" architecture (that registration
     is a >=5.4.0-only feature), so this path ports the dynamic-module
     class loading / explicit feature-extractor config / GenerationMixin
     patch needed to run the checkpoint on an older transformers.

Path selection is a cheap import probe (no model weights touched), not a
"try loading, catch, retry" -- on an 8GB machine, partially loading the
~4GB native path before falling back to load the ~4GB fallback path a
second time would be a real memory risk. See `native_stack_available()`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import soundfile as sf

logger = logging.getLogger("saghi.engine")


@dataclass
class TranscribeResult:
    raw_text: str
    text: str
    language: str
    duration_s: float
    inference_s: float


class SaghiEngine:
    """
    Loads the ASR model once, then transcribes audio files.

    Usage:
        engine = SaghiEngine(model_dir)
        engine.load()
        result = engine.transcribe("clip.wav", language="ar")

    Thread-safe: an internal RLock serializes load() and transcribe() calls
    so two transcriptions (or a transcription racing a lazy load) can never
    overlap -- important on low-RAM machines, which have no headroom
    for two concurrent ~8GB inference passes.
    """

    def __init__(self, model_dir: Union[str, Path], device: str = "auto"):
        self.model_dir = Path(model_dir)
        self._requested_device = device
        self.device: Optional[str] = None
        self.stack_path: Optional[str] = None  # "native" or "fallback", set by load()
        self._processor = None
        self._model = None
        self._lock = threading.RLock()
        self._load_time_s: Optional[float] = None

    @property
    def load_time_s(self) -> Optional[float]:
        return self._load_time_s

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    # ---- device selection ------------------------------------------------

    @staticmethod
    def _detect_device() -> str:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _resolve_device(self) -> str:
        if self._requested_device != "auto":
            return self._requested_device
        return self._detect_device()

    # ---- native-path availability (cheap probe, no weights touched) ------

    @staticmethod
    def native_stack_available() -> bool:
        """
        Whether this Python environment's transformers build natively
        registers the cohere_asr architecture (a transformers>=5.4.0
        feature). Import-only check -- safe to call any time, including
        from `cli.py info`, without touching model weights.
        """
        try:
            from transformers import CohereAsrForConditionalGeneration  # noqa: F401

            return True
        except ImportError:
            return False

    # ---- loading -----------------------------------------------------

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return  # already loaded

            self.device = self._resolve_device()
            logger.info("Loading Saghi engine (device=%s, model_dir=%s)", self.device, self.model_dir)

            t0 = time.time()
            if self.native_stack_available():
                try:
                    self._load_native()
                    self.stack_path = "native"
                except (ImportError, ValueError, OSError) as exc:
                    logger.warning("Native load path failed (%s: %s), falling back to compat path",
                                    type(exc).__name__, exc)
                    self._load_fallback()
                    self.stack_path = "fallback"
            else:
                logger.info("Native cohere_asr architecture not registered in this transformers build "
                            "-- using fallback compat path")
                self._load_fallback()
                self.stack_path = "fallback"

            self._load_time_s = time.time() - t0
            logger.info("Engine loaded via %s path on %s in %.2fs", self.stack_path, self.device,
                        self._load_time_s)

    def _load_native(self) -> None:
        """
        Target Apple Silicon stack (transformers>=5.4.0, torch>=2.5).
        Straightforward per the model README's Quick Start section --
        AutoProcessor / AutoModel with trust_remote_code=True from the
        local checkpoint dir.

        Only runs when `native_stack_available()` is True (transformers
        >= 5.4), which is the Apple Silicon build's stack.
        """
        import torch
        from transformers import AutoProcessor, CohereAsrForConditionalGeneration

        model_dir_str = str(self.model_dir)
        processor = AutoProcessor.from_pretrained(model_dir_str, trust_remote_code=True)
        model = CohereAsrForConditionalGeneration.from_pretrained(
            model_dir_str, trust_remote_code=True, torch_dtype=torch.float32
        )
        model.to(self.device)
        model.eval()

        self._processor = processor
        self._model = model

    def _load_fallback(self) -> None:
        """
        Older-stack path: transformers 4.57.6, used where torch is capped
        at 2.2.2 (no newer prebuilt wheel exists for Intel macOS) and
        transformers>=5.0 hard-requires torch>=2.5.
        """
        import torch
        from transformers import GenerationMixin
        from transformers.dynamic_module_utils import get_class_from_dynamic_module

        model_dir_str = str(self.model_dir)

        # config.json only wires auto_map for AutoProcessor, not for
        # AutoConfig/AutoModel/AutoFeatureExtractor -- on a transformers
        # build that doesn't natively know model_type "cohere_asr" (a
        # >=5.4.0-only feature), every Auto* dispatcher fails. Bypass Auto*
        # entirely via transformers' own dynamic-module loader (the same
        # mechanism trust_remote_code=True uses internally) -- it copies
        # the checkpoint's bundled .py files into a proper package under
        # ~/.cache/huggingface/modules/ so their relative imports resolve.
        CohereAsrConfig = get_class_from_dynamic_module(
            "configuration_cohere_asr.CohereAsrConfig", model_dir_str
        )
        CohereAsrFeatureExtractor = get_class_from_dynamic_module(
            "processing_cohere_asr.CohereAsrFeatureExtractor", model_dir_str
        )
        CohereAsrProcessor = get_class_from_dynamic_module(
            "processing_cohere_asr.CohereAsrProcessor", model_dir_str
        )
        CohereAsrTokenizer = get_class_from_dynamic_module(
            "tokenization_cohere_asr.CohereAsrTokenizer", model_dir_str
        )
        CohereAsrForConditionalGeneration = get_class_from_dynamic_module(
            "modeling_cohere_asr.CohereAsrForConditionalGeneration", model_dir_str
        )
        load_preprocessor_buffers = get_class_from_dynamic_module(
            "processing_cohere_asr._maybe_load_preprocessor_buffers_from_checkpoint", model_dir_str
        )

        config = CohereAsrConfig.from_pretrained(model_dir_str)

        # preprocessor_config.json on this checkpoint carries no real
        # frontend params (only auto_map/processor_class), so
        # CohereAsrFeatureExtractor.from_pretrained() would silently fall
        # back to wrong hardcoded class defaults (320-sample window,
        # 64 mel filters instead of the checkpoint's real 400/128). Read
        # the real values from config.json's own "preprocessor" block
        # instead, then still run the buffer-loading step so fb/window
        # tensors come from the checkpoint itself.
        with open(self.model_dir / "config.json") as f:
            pp = json.load(f)["preprocessor"]

        feature_extractor = CohereAsrFeatureExtractor(
            feature_size=pp["features"],
            sampling_rate=pp["sample_rate"],
            n_window_size=round(pp["window_size"] * pp["sample_rate"]),
            n_window_stride=round(pp["window_stride"] * pp["sample_rate"]),
            window=pp["window"],
            normalize=pp["normalize"],
            n_fft=pp["n_fft"],
            log=pp["log"],
            frame_splicing=pp["frame_splicing"],
            dither=pp["dither"],
            pad_to=pp["pad_to"],
            padding_value=pp.get("pad_value", 0.0),
        )
        load_preprocessor_buffers(feature_extractor=feature_extractor, model_dir=self.model_dir)

        tokenizer = CohereAsrTokenizer.from_pretrained(model_dir_str)
        processor = CohereAsrProcessor(feature_extractor=feature_extractor, tokenizer=tokenizer)

        # Since transformers 4.50, PreTrainedModel no longer auto-inherits
        # GenerationMixin -- models must opt in explicitly. This
        # checkpoint's bundled generate() ends with
        # `return super().generate(...)`, assuming the native >=5.4.0
        # integration wires this up. Mix GenerationMixin in locally
        # (without touching the vendored file) -- exactly the fix
        # transformers' own runtime warning suggests.
        class _PatchedCohereAsrForConditionalGeneration(CohereAsrForConditionalGeneration, GenerationMixin):
            pass

        model = _PatchedCohereAsrForConditionalGeneration.from_pretrained(
            model_dir_str, config=config, torch_dtype=torch.float32
        )
        model.to(self.device)
        model.eval()

        if self.device == "cpu":
            # Pin thread count to this machine's logical core
            # count on CPU only -- do not hardcode 2 (that would hobble a
            # bigger machine) and never touch this on
            # mps, where torch's own defaults apply.
            torch.set_num_threads(os.cpu_count() or 2)

        self._processor = processor
        self._model = model

    # ---- transcription -----------------------------------------------

    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: str = "ar",
        cleanup_level: str = "light",
    ) -> TranscribeResult:
        """
        Transcribe one audio file. Uses the checkpoint's own
        `model.transcribe(...)` entry point -- the benchmark found the
        README's simple `processor(...)` + `model.generate(...)` pattern
        does NOT work on the fallback stack (CohereAsrProcessor there
        silently ignores `language=`), while `model.transcribe()` works on
        both stacks and is the supported entry point on both stacks.
        """
        audio_path = Path(audio_path)

        with self._lock:
            if self._model is None:
                self.load()

            import torch

            info = sf.info(str(audio_path))
            duration_s = info.frames / info.samplerate if info.samplerate else 0.0

            needs_prep = info.samplerate != 16000 or info.channels != 1

            t0 = time.time()
            with torch.no_grad():
                if needs_prep:
                    # Not already 16kHz mono -- downmix/resample ourselves
                    # with soundfile+soxr (see _read_and_prepare) and pass
                    # audio_arrays=[...] with the target sample rate
                    # already matched, so the model's own internal
                    # resample branch (which calls librosa.resample, not
                    # covered by the numba shim -- confirmed empirically:
                    # `from numba import guvectorize` fails under the
                    # shim) never runs.
                    arr, sr = self._read_and_prepare(audio_path)
                    texts = self._model.transcribe(
                        self._processor,
                        language=language,
                        audio_arrays=[arr],
                        sample_rates=[sr],
                        punctuation=True,
                        # Explicit batch_size=1, NOT the config default
                        # (config.json's batch_size is 128, meant for GPU
                        # serving) -- this is a single clip on a 2-core
                        # CPU with no RAM headroom.
                        batch_size=1,
                    )
                else:
                    # Already 16kHz mono -- this exact call shape
                    # (audio_files=[path]) is the one the benchmark
                    # validated end-to-end: plain sf.read() internally, no
                    # resample, no librosa involved at all.
                    texts = self._model.transcribe(
                        self._processor,
                        language=language,
                        audio_files=[str(audio_path)],
                        punctuation=True,
                        batch_size=1,
                    )
            inference_s = time.time() - t0

        raw_text = texts[0]

        from .cleanup import clean_transcript

        cleaned = clean_transcript(raw_text, cleanup_level)

        return TranscribeResult(
            raw_text=raw_text,
            text=cleaned,
            language=language,
            duration_s=duration_s,
            inference_s=inference_s,
        )

    @staticmethod
    def _prepare_ndarray(arr: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
        """
        Downmix to mono + resample to 16kHz using soxr, given an array
        already in memory. Shared by `_read_and_prepare` (reading from a
        file) and `transcribe_array` (Phase 3, filejobs.py -- audio blocks
        read directly from a long source file, never written to disk).
        Deliberately NOT librosa.resample() -- see _read_and_prepare's
        docstring below for why.
        """
        import soxr

        arr = np.asarray(arr, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.mean(axis=1).astype(np.float32, copy=False)
        if sr != 16000:
            arr = soxr.resample(arr, sr, 16000).astype(np.float32, copy=False)
            sr = 16000
        return arr, sr

    @staticmethod
    def _read_and_prepare(audio_path: Path) -> tuple[np.ndarray, int]:
        """
        Downmix to mono + resample to 16kHz using soundfile + soxr.
        Deliberately NOT librosa.load()/librosa.resample() -- this dev
        machine's numba shim only provides pass-through jit/njit/prange,
        and librosa's audio-loading/resampling path additionally needs
        numba.guvectorize/numba.stencil, which the shim does not (and
        should not) provide. Confirmed empirically: importing
        librosa.resample under this shim raises
        `ImportError: cannot import name 'guvectorize' from 'numba'`.
        """
        data, sr = sf.read(str(audio_path), always_2d=False)
        return SaghiEngine._prepare_ndarray(np.asarray(data, dtype=np.float32), sr)

    def transcribe_array(
        self,
        audio: np.ndarray,
        sr: int,
        language: str = "ar",
        cleanup_level: str = "light",
    ) -> TranscribeResult:
        """
        Transcribe an in-memory audio array directly -- no file on disk.

        Added in Phase 3 for `filejobs.py`: chunked/segmented long-file
        transcription reads and resamples audio straight from the source
        file in blocks (via soundfile's seek/read, never loading the whole
        file into RAM) and hands each speech segment here, rather than
        writing one temp WAV per segment just to satisfy a path-based API.

        Accepts audio at any sample rate/channel count -- downmixed and
        resampled to 16kHz mono internally via `_prepare_ndarray` (the
        same soxr-based path `transcribe()`'s file-input branch already
        uses), so callers don't need to pre-normalize. Otherwise mirrors
        `transcribe()` exactly: same lock, same `model.transcribe(...)`
        call shape (`audio_arrays=`/`sample_rates=`, `batch_size=1`), same
        cleanup-level pass-through, same `TranscribeResult` fields.
        """
        with self._lock:
            if self._model is None:
                self.load()

            import torch

            arr, prepared_sr = self._prepare_ndarray(audio, sr)
            duration_s = len(arr) / prepared_sr if prepared_sr else 0.0

            t0 = time.time()
            with torch.no_grad():
                texts = self._model.transcribe(
                    self._processor,
                    language=language,
                    audio_arrays=[arr],
                    sample_rates=[prepared_sr],
                    punctuation=True,
                    # See transcribe()'s array-input branch: explicit
                    # batch_size=1, not config.json's GPU-serving default
                    # of 128 -- one segment at a time.
                    batch_size=1,
                )
            inference_s = time.time() - t0

        raw_text = texts[0]

        from .cleanup import clean_transcript

        cleaned = clean_transcript(raw_text, cleanup_level)

        return TranscribeResult(
            raw_text=raw_text,
            text=cleaned,
            language=language,
            duration_s=duration_s,
            inference_s=inference_s,
        )
