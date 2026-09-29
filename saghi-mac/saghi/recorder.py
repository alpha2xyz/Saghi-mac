"""
Microphone capture for live dictation (README_AR.md: "تسجيل لحظي من أي
تطبيق عبر اختصار عام قابل للتغيير" / "موجة حقيقية تتبع مستوى الميكروفون
ولا تتحرك اصطناعيًا عند الصمت").

`Recorder` wraps `sounddevice.InputStream` and exposes:

  - `start(device=None)` -- begin capture. `device` is a device *name* (as
    stored in `settings.Settings.microphone`), resolved to a PortAudio
    index; `None` (or a name that no longer matches any connected device)
    uses the system default input device.
  - `stop() -> np.ndarray` -- stop capture, return the whole recording as a
    16kHz mono float32 array.
  - `cancel()` -- stop capture and discard the buffer (Esc-cancel path).
  - `level() -> (recent_samples, rms)` -- thread-safe tap for the UI's
    waveform: the last ~500ms of captured audio plus its RMS, polled by a
    ~30fps QTimer in `ui/indicator.py`. Cheap (no resampling, no engine
    involvement) so it's safe to call from the Qt main thread at that rate.

Deliberately has ZERO Qt dependency -- same convention as settings.py/
engine.py -- so it can be unit-tested (with a fake/substituted stream) and
reused by any future non-GUI consumer without pulling in PySide6.

Device native sample rate: many built-in mics report a native rate other
than 16kHz (44.1/48kHz is common) and PortAudio can refuse to open a stream
at an unsupported rate on some devices/drivers. `start()` tries 16kHz mono
first; if that specific open fails with a `PortAudioError`, it retries at
the device's own reported default sample rate and `stop()` resamples the
full buffer down to 16kHz with `soxr` (mirrors `engine.py::_prepare_ndarray`
-- deliberately not librosa, see that module's docstring for why). The
public contract (`stop()` always returns 16kHz mono float32) never changes
based on which path was taken.

TCC note (see dev/probe_devices.py): a microphone
access denial on macOS does NOT reliably raise an exception here -- PortAudio
can happily "open" a stream and deliver a callback stream of bit-exact
zeros instead. `start()` can still raise `MicUnavailableError` for real
open failures (bad device, no input devices at all, PortAudio error), but
"permission denied" is a *pattern in the captured audio*, not a raisable
error at open time -- see `is_silent()` below, used by
`ui/dictation.py` to decide whether to surface a permission-style error
message after a recording comes back with real duration but zero signal.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger("saghi.recorder")

TARGET_SAMPLE_RATE = 16000
MAX_DURATION_S = 300.0  # 5 minutes hard cap, see module docstring / _on_audio
LEVEL_WINDOW_S = 0.5  # how much recent audio level()/waveform reads from
SILENCE_PEAK_EPS = 1e-6  # see is_silent()


class RecorderError(Exception):
    """Base class for recorder errors the UI layer can catch and show a message for."""


class MicUnavailableError(RecorderError):
    """The input stream could not be opened at all (bad device / no input devices / PortAudio error)."""


def list_input_devices() -> List[Tuple[str, int]]:
    """[(name, index), ...] for every device with at least one input channel. Never raises."""
    try:
        import sounddevice as sd

        out = []
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                name = dev.get("name")
                if name:
                    out.append((name, idx))
        return out
    except Exception:
        logger.exception("Failed to enumerate input devices")
        return []


def _resolve_device(name: Optional[str]) -> Optional[int]:
    """Device index for `name`, or None (system default) if name is None or no longer matches any device."""
    if name is None:
        return None
    for dev_name, idx in list_input_devices():
        if dev_name == name:
            return idx
    logger.warning("Configured microphone %r not found among current input devices -- using system default", name)
    return None


def is_silent(audio: np.ndarray, eps: float = SILENCE_PEAK_EPS) -> bool:
    """
    True if `audio` is bit-exact (or near-exact) silence -- the observable
    signature of a TCC-denied mic on this platform (see module docstring),
    as opposed to a genuinely quiet room (real ambient noise floor is
    essentially never this clean; confirmed empirically via
    dev/probe_devices.py on a real machine: peak ~0.0075 in a quiet room,
    several orders of magnitude above `eps`).
    """
    if audio.size == 0:
        return True
    return float(np.max(np.abs(audio))) <= eps


class Recorder:
    """
    One recording session at a time. Not reentrant -- call `start()`, then
    exactly one of `stop()`/`cancel()`, before calling `start()` again.
    """

    def __init__(self) -> None:
        self._stream = None
        self._stream_samplerate = TARGET_SAMPLE_RATE
        self._lock = threading.Lock()
        self._full_chunks: List[np.ndarray] = []
        self._total_frames = 0
        self._capped = False
        self._recent_chunks: deque = deque()
        self._recent_frames = 0
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def capped(self) -> bool:
        """True if the hard MAX_DURATION_S cap was hit during this recording (audio past the cap was dropped)."""
        return self._capped

    # ---- capture ---------------------------------------------------------

    def start(self, device: Optional[str] = None) -> None:
        if self._recording:
            raise RecorderError("Recorder.start() called while already recording")

        import sounddevice as sd

        device_index = _resolve_device(device)

        self._full_chunks = []
        self._total_frames = 0
        self._capped = False
        self._recent_chunks = deque()
        self._recent_frames = 0

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("Recorder stream status flags: %s", status)
            mono = np.asarray(indata, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1).astype(np.float32, copy=False)
            else:
                mono = mono.copy()
            self._on_audio(mono)

        try:
            stream = sd.InputStream(
                samplerate=TARGET_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=device_index,
                callback=_callback,
            )
            stream.start()
            self._stream_samplerate = TARGET_SAMPLE_RATE
        except Exception as exc:
            logger.info(
                "Opening input stream at %dHz failed (%s: %s) -- retrying at the device's own default rate",
                TARGET_SAMPLE_RATE, type(exc).__name__, exc,
            )
            try:
                info = sd.query_devices(device_index, "input") if device_index is not None else sd.query_devices(sd.default.device[0], "input")
                native_rate = int(round(info["default_samplerate"]))
                stream = sd.InputStream(
                    samplerate=native_rate,
                    channels=1,
                    dtype="float32",
                    device=device_index,
                    callback=_callback,
                )
                stream.start()
                self._stream_samplerate = native_rate
            except Exception as exc2:
                raise MicUnavailableError(f"Could not open microphone: {exc2}") from exc2

        self._stream = stream
        self._recording = True

    def _on_audio(self, mono: np.ndarray) -> None:
        with self._lock:
            max_frames = int(MAX_DURATION_S * self._stream_samplerate)
            if self._total_frames < max_frames:
                remaining = max_frames - self._total_frames
                if len(mono) > remaining:
                    self._full_chunks.append(mono[:remaining])
                    self._total_frames += remaining
                    self._capped = True
                else:
                    self._full_chunks.append(mono)
                    self._total_frames += len(mono)
            else:
                self._capped = True

            # Recent-audio ring buffer for level()/waveform -- capped at
            # LEVEL_WINDOW_S regardless of the full-buffer cap above, kept
            # in *native* stream sample rate (level() is for a live visual,
            # not exact timing, so no resampling needed here).
            self._recent_chunks.append(mono)
            self._recent_frames += len(mono)
            window_frames = int(LEVEL_WINDOW_S * self._stream_samplerate)
            while self._recent_frames > window_frames and len(self._recent_chunks) > 1:
                popped = self._recent_chunks.popleft()
                self._recent_frames -= len(popped)

    def level(self) -> Tuple[np.ndarray, float]:
        """
        Thread-safe tap: (recent_samples, rms) over the last ~LEVEL_WINDOW_S
        of captured audio. Returns an empty array + 0.0 rms if nothing has
        been captured yet (or recording hasn't started) -- callers must
        render that as flat/silent, never fabricate motion for it.
        """
        with self._lock:
            if not self._recent_chunks:
                return np.zeros(0, dtype=np.float32), 0.0
            recent = np.concatenate(list(self._recent_chunks))
        if recent.size == 0:
            return recent, 0.0
        rms = float(np.sqrt(np.mean(recent.astype(np.float64) ** 2)))
        return recent, rms

    def _full_buffer(self) -> np.ndarray:
        with self._lock:
            if not self._full_chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._full_chunks)

    def stop(self) -> np.ndarray:
        """
        Stop capture and return the whole recording, resampled/downmixed to
        16kHz mono float32 (already the case if the stream opened at
        16kHz directly; resampled via soxr otherwise -- see module
        docstring). Safe to call even if start() was never called (returns
        an empty array).
        """
        self._stop_stream()
        raw = self._full_buffer()
        if raw.size == 0:
            return raw
        if self._stream_samplerate == TARGET_SAMPLE_RATE:
            return raw
        import soxr

        return soxr.resample(raw, self._stream_samplerate, TARGET_SAMPLE_RATE).astype(np.float32, copy=False)

    def cancel(self) -> None:
        """Stop capture and discard everything captured so far (Esc-cancel path)."""
        self._stop_stream()
        with self._lock:
            self._full_chunks = []
            self._total_frames = 0
            self._recent_chunks = deque()
            self._recent_frames = 0

    def _stop_stream(self) -> None:
        self._recording = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Error stopping/closing input stream")

    def duration_s(self) -> float:
        """Duration of the buffer that stop() would currently return, in seconds."""
        with self._lock:
            if self._stream_samplerate == 0:
                return 0.0
            return self._total_frames / self._stream_samplerate
