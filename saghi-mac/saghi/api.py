"""
Saghi local HTTP API server.

Implements a small local HTTP API (bound to 127.0.0.1 only) that
other local tools can call:

    base:  http://127.0.0.1:17865
    GET  /api/health
    POST /api/transcribe   (multipart/form-data: file, language, cleanup_level,
                             save_to_history, timestamps)
    GET  /api/latest
    GET  /api/history?limit=20&query=
    docs at /api/docs

Design notes:

  * Lazy model load -- the server starts instantly. `SaghiEngine(model_dir)`
    is constructed at app-creation time (cheap: no torch/transformers
    import happens until `.load()`/`.transcribe()` is actually called, see
    engine.py). The model only loads on the first `/api/transcribe` call.
    `/api/health` NEVER triggers a load -- it only reads `engine.is_loaded`
    and a `_loading` flag we set/clear ourselves around the transcribe call.

  * Serialization -- `engine.transcribe()` already takes engine.py's own
    RLock internally (so two overlapping calls can never run inference
    concurrently), but this module adds its own `asyncio.Lock` around the
    call too: without it, a burst of concurrent requests would each spin up
    a threadpool worker that just blocks on the engine's RLock, needlessly
    consuming worker-pool slots on an 8GB machine with no headroom for that.
    The asyncio.Lock queues requests at the ASGI layer instead, one at a
    time, before a thread is even claimed.

  * Bad-file handling -- `sf.info()` is used as a cheap pre-check on the
    uploaded file *before* acquiring the transcribe lock or touching the
    engine at all. This both (a) lets a bad upload fail fast as a clean 415
    without waiting behind a queued transcription, and (b) avoids forcing a
    ~70-90s model load just to reject a file that was never going to decode
    (engine.transcribe() would otherwise load the model unconditionally
    before it ever reaches its own sf.info() call -- see the
    engine docs).

  * `timestamps=true` inference cost (Phase 3) -- real segmentation
    (`timestamps.produce_segments`) runs one extra inference call per
    silence-split segment, on top of the one whole-clip call this endpoint
    always makes. Acceptable because API uploads are expected to be short
    clips (this is the local-integration endpoint, e.g. for voice
    notes), not long files -- long files go through `filejobs.py`'s
    chunked job runner instead, which is what the CLI's `filejob`
    subcommand and (later) the GUI's file-transcribe page use.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional, Union

import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import history
from .engine import SaghiEngine
from .timestamps import format_srt, format_timestamped, format_vtt, produce_segments

logger = logging.getLogger("saghi.api")

# Mirrors cli.py's DEFAULT_MODEL_DIR (the bundled checkpoint's default
# location in the installed app). Duplicated rather than imported to keep api.py
# import-independent of cli.py -- avoids any chance of a circular import
# between "cli.py serve lazily imports api.py" and "api.py imports cli.py".
# SAGHI_MODEL_DIR env var override -- see cli.py's and
# ui/app.py's matching comments; mirrors paths.py's SAGHI_DATA_DIR.
DEFAULT_MODEL_DIR = Path(
    os.environ.get("SAGHI_MODEL_DIR")
    or (Path.home() / "Applications" / "Saghi.app" / "Contents" / "Resources" / "model")
)

HOST = "127.0.0.1"
PORT = 17865
API_VERSION = "0.1.0-mac"

_LANGUAGES = {"ar", "en"}
_CLEANUP_LEVELS = {"none", "light", "medium"}

_TRUE_STRINGS = {"1", "true", "t", "yes", "y", "on"}
_FALSE_STRINGS = {"0", "false", "f", "no", "n", "off", ""}


def _sanitize_sf_error(exc: Exception, tmp_path: Path, display_name: str) -> str:
    """
    soundfile's exception text embeds the real filesystem path of the temp
    file we wrote the upload to (e.g. "Error opening
    '/var/folders/.../saghi-upload-xyz.wav': Format not recognised."). Swap
    that internal path back out for the client's own original filename
    before it goes into an HTTP response body.
    """
    return str(exc).replace(str(tmp_path), display_name)


def _parse_bool(value: Union[str, bool, None], default: bool = False) -> bool:
    """
    Robust multipart/form-data boolean parsing. Booleans arrive as plain
    strings in multipart bodies (curl -F "save_to_history=true"), not JSON
    booleans -- accept the common true/false spellings case-insensitively,
    and fail safe to `default` on anything unrecognized rather than raising
    (this is a convenience flag; a typo'd value should not 400 the request).
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    v = value.strip().lower()
    if v in _TRUE_STRINGS:
        return True
    if v in _FALSE_STRINGS:
        return False
    return default


def create_app(model_dir: Union[str, Path, None] = None, engine: Optional[SaghiEngine] = None) -> FastAPI:
    """
    Build the FastAPI app. A factory (not just a module-level `app`) so
    tests / the CLI can point it at a different model dir if ever needed;
    `app = create_app()` below still gives the conventional
    `uvicorn saghi.api:app` import target with the default model dir.

    `engine`: Phase 4 (GUI) addition. Pass an already-constructed
    SaghiEngine to have this server share it instead of building its own.
    The GUI app (saghi/ui/app.py) does this so its own file-job worker and
    this API server never load the ~4-8GB model twice on this memory-
    constrained hardware. When omitted (the
    default -- every Phase 1-3 call site, `python -m saghi.cli serve`, and
    all existing tests), behavior is byte-for-byte unchanged: a new engine
    is constructed here exactly as before.
    """
    resolved_model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    if engine is None:
        engine = SaghiEngine(resolved_model_dir)

    # Set right before engine.load()/.transcribe() is invoked for the first
    # time, cleared in a finally right after -- purely so /api/health can
    # report "loading" instead of "cold" while a first transcribe request is
    # in flight. Never read/written by anything that could trigger a load.
    _loading = threading.Event()
    _transcribe_lock = asyncio.Lock()

    app = FastAPI(
        title="Saghi API",
        version=API_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # ---- error handling: JSON always, no stack traces to the client ------

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error handling %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # ---- health ------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        if engine.is_loaded:
            state = "ready"
        elif _loading.is_set():
            state = "loading"
        else:
            state = "cold"
        return {
            "status": "ok",
            "engine": state,
            "device": engine.device,
            "stack": engine.stack_path,
            "version": API_VERSION,
        }

    # ---- transcribe ----------------------------------------------------

    @app.post("/api/transcribe")
    async def transcribe(
        file: UploadFile = File(...),
        language: str = Form("ar"),
        cleanup_level: str = Form("light"),
        save_to_history: str = Form("false"),
        timestamps: str = Form("false"),
    ) -> dict:
        if language not in _LANGUAGES:
            raise HTTPException(status_code=400, detail=f"invalid language {language!r} (expected ar or en)")
        if cleanup_level not in _CLEANUP_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"invalid cleanup_level {cleanup_level!r} (expected none, light, or medium)",
            )

        save_flag = _parse_bool(save_to_history, default=False)
        timestamps_flag = _parse_bool(timestamps, default=False)

        # Read the upload before opening the temp file, so a client abort
        # mid-upload never leaves an fd open (mkstemp's fd would otherwise
        # need its own except/close handling).
        contents = await file.read()

        suffix = Path(file.filename or "").suffix
        tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=suffix, prefix="saghi-upload-")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(contents)

            # Cheap pre-check, outside the transcribe lock and before any
            # model load: does this file even decode? See module docstring.
            display_name = file.filename or "<upload>"
            try:
                sf.info(str(tmp_path))
            except sf.SoundFileError as exc:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"could not decode audio file {display_name!r}: "
                        f"{_sanitize_sf_error(exc, tmp_path, display_name)}. "
                        "Containers like m4a/aac/mp4 are not readable by this build's "
                        "libsndfile -- ffmpeg-based decoding is planned for a later "
                        "phase."
                    ),
                ) from exc

            async with _transcribe_lock:
                already_loaded = engine.is_loaded
                if not already_loaded:
                    _loading.set()
                try:
                    result = await run_in_threadpool(
                        engine.transcribe,
                        tmp_path,
                        language=language,
                        cleanup_level=cleanup_level,
                    )
                    segments = None
                    if timestamps_flag:
                        # Real (Phase 3) segmentation: re-reads tmp_path
                        # (still on disk here -- deleted only in the outer
                        # `finally` below) and runs one more inference call
                        # per silence-split segment, on top of the
                        # whole-clip call just above. Deliberately still
                        # inside the same lock/threadpool sequence as that
                        # call -- api clips are short, so the extra
                        # per-segment inference cost is acceptable (see
                        # module docstring).
                        segments = await run_in_threadpool(
                            produce_segments,
                            engine,
                            tmp_path,
                            language=language,
                            cleanup_level=cleanup_level,
                        )
                except sf.SoundFileError as exc:
                    # Belt-and-suspenders: the pre-check above catches the
                    # overwhelming majority of bad uploads, but guard the
                    # real call too in case a file passes sf.info() (header
                    # parses) yet fails on the actual decode.
                    raise HTTPException(
                        status_code=415,
                        detail=(
                            f"could not decode audio file {display_name!r}: "
                            f"{_sanitize_sf_error(exc, tmp_path, display_name)}"
                        ),
                    ) from exc
                finally:
                    _loading.clear()
        finally:
            tmp_path.unlink(missing_ok=True)

        response: dict = {
            "text": result.text,
            "raw_text": result.raw_text,
            "language": result.language,
            "duration_s": result.duration_s,
            "inference_s": result.inference_s,
        }

        if timestamps_flag:
            response["timestamped"] = format_timestamped(segments)
            response["srt"] = format_srt(segments)
            response["vtt"] = format_vtt(segments)

        if save_flag:
            try:
                entry = history.add_entry(
                    source="api",
                    language=language,
                    cleanup_level=cleanup_level,
                    duration_s=result.duration_s,
                    inference_s=result.inference_s,
                    raw_text=result.raw_text,
                    text=result.text,
                    audio_filename=file.filename,
                )
                response["history_id"] = entry["id"]
            except Exception:
                # Don't fail a successful transcription just because the
                # history write failed (e.g. disk full) -- log it and
                # still return the transcript.
                logger.exception("Failed to save transcription to history")
                response["history_id"] = None

        return response

    # ---- history ---------------------------------------------------------

    @app.get("/api/latest")
    async def latest() -> dict:
        entry = history.latest()
        if entry is None:
            raise HTTPException(status_code=404, detail="no transcripts yet")
        return entry

    @app.get("/api/history")
    async def history_endpoint(limit: int = 20, query: str = "") -> dict:
        limit = max(1, min(limit, 200))
        entries = history.search(limit=limit, query=query)
        return {"count": len(entries), "results": entries}

    return app


# Conventional `uvicorn saghi.api:app` import target, default model dir.
app = create_app()
