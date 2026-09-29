"""
`DictationController` -- owns the whole live-dictation flow end to end:

    hotkey hold_started -> start Recorder -> show indicator (recording)
    hotkey hold_ended    -> stop Recorder -> indicator (processing)
                            -> engine.transcribe_array() on a worker thread
                            -> cleanup (already applied inside
                               transcribe_array, per engine.py)
                            -> optional OpenRouter rephrase (Phase 6, same
                               worker thread, README_AR.md's «مساعد
                               OpenRouter الاختياري»; never blocks on
                               failure -- see _TranscribeWorker) -> paste.py
                               (clipboard + optional autopaste) -> save to
                               history (source="dictation") -> optionally
                               save the raw recording -> hide indicator
    hotkey cancelled      -> stop+discard Recorder, hide indicator

This is the Phase 5 counterpart to `filejob_page.py`'s `FileJobPage` +
`FileJobWorker` (Phase 4) -- same shape: real work happens on a background
`QThread`, never the UI thread; the shared `SaghiEngine` instance (see
ui/app.py's module docstring on why there is exactly one) is reused, never
constructed here.

THREADING: `saghi.hotkey.HotkeyListener`'s three callbacks run on pynput's
own listener thread, not the Qt main thread (see hotkey.py's module
docstring). `DictationController` marshals them onto the main thread the
same way Qt always does this: the callbacks passed to `HotkeyListener` do
nothing but call `Signal.emit()` on this QObject's own private signals
(`_hold_started_raw` etc.) -- Qt's signal/slot machinery detects that the
connected slot lives on a different thread than the emitting call and
auto-queues delivery, so `_on_hold_started`/`_on_hold_ended`/`_on_cancelled`
below always run on the Qt main thread, safe to touch widgets/Recorder/
Engine state directly.

Guard rails (see class docstring on each for detail):
  - `_busy` ignores a new hold_started while a previous dictation is still
    being processed (recording -> transcribing -> pasting).
  - Recordings under `MIN_RECORDING_S` are discarded silently (accidental
    taps on the hotkey).
  - A recording that comes back bit-exact silent (see
    `recorder.is_silent()`) is treated as a likely missing-Microphone-
    permission case and shown as an error rather than sent to the engine
    for an expensive, useless transcription.
  - Any transcription failure shows a brief error state in the indicator
    and logs -- this controller never lets an exception escape into Qt's
    event loop and crash the app.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QThread, Signal

from .. import history, openrouter, paste, paths
from ..hotkey import HotkeyListener
from ..recorder import MicUnavailableError, Recorder, is_silent
from ..settings import SettingsManager
from . import strings
from .engine_status import EngineStatusBridge
from .indicator import IndicatorWindow

logger = logging.getLogger("saghi.ui.dictation")

# Recordings shorter than this are treated as an accidental tap on the
# hotkey and discarded silently -- no indicator error, no transcription,
# nothing saved. Per the task spec ("~0.4s").
MIN_RECORDING_S = 0.4


class _TranscribeWorker(QThread):
    """
    Runs exactly one `engine.transcribe_array()` call on a background
    thread -- mirrors `filejob_page.py`'s `FileJobWorker` in shape and
    intent (never block the UI thread on inference). `cleanup_level` is
    applied inside `transcribe_array()` itself (see engine.py), so the
    returned `TranscribeResult.text` is already the cleaned transcript.

    Phase 6: if `openrouter_enabled`, an `openrouter.rephrase()` call runs
    right here too, on this same background thread -- straight after
    transcription, still before `finished_ok` is emitted -- rather than
    adding a second worker/thread hop for it. Per README_AR.md's explicit
    promise ("إذا تعذرت إعادة الصياغة، يلصق صاغي النص المحلي بدل فقدانه"),
    `openrouter.rephrase()` never raises for an expected failure mode: on
    success, `result.text` is replaced with the rephrased text in place; on
    ANY failure (missing key, no model, network/timeout, bad response), the
    already-cleaned local `result.text` is left untouched and the failure is
    only logged -- never surfaced as a blocking error. `result.raw_text`
    (the pre-cleanup ASR text) is never touched either way. `TranscribeResult`
    itself is unmodified (still just engine.py's dataclass) so every other
    caller (filejobs.py, api.py) is unaffected by this change.
    """

    finished_ok = Signal(object)  # engine.TranscribeResult
    failed = Signal(str)

    def __init__(
        self,
        engine,
        audio: np.ndarray,
        language: str,
        cleanup_level: str,
        openrouter_enabled: bool = False,
        openrouter_model: str = "",
        openrouter_instructions: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._audio = audio
        self._language = language
        self._cleanup_level = cleanup_level
        self._openrouter_enabled = openrouter_enabled
        self._openrouter_model = openrouter_model
        self._openrouter_instructions = openrouter_instructions

    def run(self) -> None:  # noqa: N802 -- QThread override
        try:
            result = self._engine.transcribe_array(
                self._audio, 16000, language=self._language, cleanup_level=self._cleanup_level
            )

            if self._openrouter_enabled:
                rephrase_result = openrouter.rephrase(
                    result.text, self._openrouter_model, self._openrouter_instructions
                )
                if rephrase_result.ok:
                    result.text = rephrase_result.text
                else:
                    logger.warning(
                        "OpenRouter rephrase failed (%s) -- pasting/saving the local transcript instead",
                        rephrase_result.error,
                    )

            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001 -- surface to the UI instead of crashing the app
            logger.exception("Dictation transcription failed")
            self.failed.emit(str(exc))


class DictationController(QObject):
    # Internal signals -- emitted from the hotkey listener thread, marshaled
    # to the Qt main thread by Qt's own queued-connection machinery. See
    # module docstring.
    _hold_started_raw = Signal()
    _hold_ended_raw = Signal()
    _cancelled_raw = Signal()

    def __init__(
        self,
        engine,
        engine_status: EngineStatusBridge,
        settings_manager: SettingsManager,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._engine_status = engine_status
        self._settings_manager = settings_manager

        self.recorder = Recorder()
        self.indicator = IndicatorWindow()
        self._hotkey = HotkeyListener(
            combo=settings_manager.current.hotkey,
            on_hold_started=self._hold_started_raw.emit,
            on_hold_ended=self._hold_ended_raw.emit,
            on_cancelled=self._cancelled_raw.emit,
        )
        self._hold_started_raw.connect(self._on_hold_started)
        self._hold_ended_raw.connect(self._on_hold_ended)
        self._cancelled_raw.connect(self._on_cancelled)
        engine_status.status_changed.connect(self._on_engine_status_changed)

        self._enabled = True
        # True from a successfully-started recording until the whole flow
        # (recording -> processing -> paste/save/history -> hide) settles,
        # whichever branch it takes (including error/cancel exits). Guards
        # against overlapping dictations, see module docstring.
        self._busy = False
        self._recording_active = False
        self._pending_audio: Optional[np.ndarray] = None
        self._worker: Optional[_TranscribeWorker] = None

        settings_manager.on_change(self._on_settings_changed)

    # ---- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the global hotkey listener. Call once, at app startup."""
        if self._enabled:
            self._hotkey.start()

    def stop(self) -> None:
        """Stop the hotkey listener and tear down any in-flight recording. Call at app quit."""
        self._hotkey.stop()
        if self._recording_active:
            self._recording_active = False
            self.recorder.cancel()
        self.indicator.hide_indicator()
        self._busy = False

    def set_enabled(self, enabled: bool) -> None:
        """Tray menu toggle (strings.TRAY_DICTATION_TOGGLE, checkable, default on)."""
        self._enabled = enabled
        if enabled:
            self._hotkey.start()
            return
        self._hotkey.stop()
        if self._recording_active:
            self._recording_active = False
            self.recorder.cancel()
            self.indicator.hide_indicator()
            self._busy = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---- settings reactivity --------------------------------------------------

    def _on_settings_changed(self, settings) -> None:
        if settings.hotkey != self._hotkey.combo:
            self._hotkey.set_combo(settings.hotkey)

    # ---- engine status reactivity -----------------------------------------

    def _on_engine_status_changed(self, state: str, device: str, stack_path: str) -> None:
        # Once the model finishes loading mid-flow, swap the indicator's
        # "جارٍ تحميل النموذج…" text for the normal "جارٍ المعالجة…" one --
        # only meaningful while we're actually in the processing state for
        # THIS controller's own flow.
        if state == "ready" and self.indicator.state == "processing":
            self.indicator.show_processing(strings.INDICATOR_PROCESSING)

    # ---- hotkey-driven flow (main-thread slots, see module docstring) ------

    def _on_hold_started(self) -> None:
        if self._busy:
            logger.debug("hold_started ignored -- a dictation is already in progress")
            return
        self._busy = True

        settings = self._settings_manager.current
        try:
            self.recorder.start(device=settings.microphone)
        except MicUnavailableError as exc:
            logger.warning("Could not start recording: %s", exc)
            self.indicator.set_color(settings.waveform_color)
            self.indicator.show_error(strings.INDICATOR_ERROR_MIC)
            self._busy = False
            return

        self._recording_active = True
        self.indicator.set_style(settings.waveform_style)
        self.indicator.set_color(settings.waveform_color)
        self.indicator.show_recording(self.recorder.level)

    def _on_hold_ended(self) -> None:
        if not self._recording_active:
            return
        self._recording_active = False

        audio = self.recorder.stop()
        duration_s = (len(audio) / 16000.0) if audio.size else 0.0

        if duration_s < MIN_RECORDING_S:
            logger.debug(
                "Discarding recording shorter than %.1fs (accidental tap, %.2fs captured)",
                MIN_RECORDING_S, duration_s,
            )
            self.indicator.hide_indicator()
            self._busy = False
            return

        if is_silent(audio):
            logger.warning(
                "Recording captured (%.2fs) but is bit-exact silence -- likely a missing "
                "Microphone permission grant, not a real quiet room (see recorder.is_silent())",
                duration_s,
            )
            self.indicator.show_error(strings.INDICATOR_ERROR_MIC)
            self._busy = False
            return

        settings = self._settings_manager.current
        self._pending_audio = audio

        if not self._engine.is_loaded:
            self._engine_status.mark_loading()
            self.indicator.show_processing(strings.INDICATOR_LOADING_MODEL)
        else:
            self.indicator.show_processing(strings.INDICATOR_PROCESSING)

        self._worker = _TranscribeWorker(
            self._engine,
            audio,
            settings.language,
            settings.cleanup_level,
            openrouter_enabled=settings.openrouter_enabled,
            openrouter_model=settings.openrouter_model,
            openrouter_instructions=settings.openrouter_instructions,
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_transcribe_finished)
        self._worker.failed.connect(self._on_transcribe_failed)
        self._worker.finished.connect(self._on_worker_thread_finished)
        self._worker.start()

    def _on_cancelled(self) -> None:
        if not self._recording_active:
            return
        self._recording_active = False
        self.recorder.cancel()
        self._pending_audio = None
        self.indicator.hide_indicator()
        self._busy = False

    # ---- transcription completion -------------------------------------------

    def _on_transcribe_finished(self, result) -> None:
        settings = self._settings_manager.current

        paste_result = paste.paste_text(result.text, autopaste=settings.autopaste)
        if settings.autopaste and not paste_result.pasted:
            logger.info(
                "Autopaste did not complete (likely a missing Accessibility permission grant) -- "
                "text is on the clipboard as the documented fallback"
            )

        try:
            history.add_entry(
                source="dictation",
                language=result.language,
                cleanup_level=settings.cleanup_level,
                duration_s=result.duration_s,
                inference_s=result.inference_s,
                raw_text=result.raw_text,
                text=result.text,
            )
        except Exception:
            # Same policy as api.py/filejobs.py: a history-write failure
            # doesn't throw away a transcription that already succeeded.
            logger.exception("Failed to save dictation transcript to history")

        if settings.save_recordings and self._pending_audio is not None and self._pending_audio.size:
            self._save_recording(self._pending_audio)

        self._pending_audio = None
        self.indicator.hide_indicator()
        self._busy = False

    def _on_transcribe_failed(self, message: str) -> None:
        logger.error("Dictation transcription failed: %s", message)
        self._pending_audio = None
        self.indicator.show_error(strings.INDICATOR_ERROR_GENERIC)
        self._busy = False

    def _on_worker_thread_finished(self) -> None:
        # QThread housekeeping only -- UI state is already fully handled by
        # _on_transcribe_finished/_on_transcribe_failed, which always fire
        # before this (same pattern as filejob_page.py).
        self._worker = None

    def _save_recording(self, audio: np.ndarray) -> None:
        try:
            paths.ensure_dirs()
            # Millisecond resolution, not just seconds: on a slow machine a
            # dictation takes 20-40s+ so second-resolution
            # filenames never collide, but on Apple Silicon
            # (near-realtime transcription) two short
            # back-to-back dictations could easily complete inside the same
            # second -- second-resolution names would silently overwrite
            # each other (sf.write has no "don't clobber" mode). Caught in
            # review, not by a test (this phase's test budget allows only
            # one recording per run, which can never collide with itself).
            now = time.time()
            ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int((now % 1) * 1000):03d}"
            out_path = paths.recordings_dir() / f"{ts}.wav"
            sf.write(str(out_path), audio, 16000)
            logger.info("Saved dictation recording to %s", out_path)
        except Exception:
            logger.exception("Failed to save dictation recording")
