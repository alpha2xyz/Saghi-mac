"""
Saghi command-line interface.

Usage:
    python -m saghi.cli transcribe <audio> [--language ar|en] [--cleanup none|light|medium] [--json]
    python -m saghi.cli info
    python -m saghi.cli serve [--port 17865]
    python -m saghi.cli filejob <audio> [--language ar] [--cleanup light] [--timestamps]
                                          [--chunk-s N] [--output-dir DIR]

`transcribe` prints the cleaned transcript to stdout (or, with --json, a
JSON object with every TranscribeResult field) and timing info to stderr.
`info` prints the chosen device, which stack path this build would use,
installed torch/transformers versions, and the model dir -- without
loading the model (native_stack_available() is an import-only probe).
`serve` runs the local HTTP API server (saghi/api.py), binding
127.0.0.1:<port> only. The model itself still loads lazily on the first
/api/transcribe request, not at server startup -- see api.py.
`filejob` runs a long-file transcription job (saghi/filejobs.py):
chunked, resumable, with real progress lines on stderr and staged
TXT/SRT/WebVTT output under the data dir's file-jobs/<job-id>/. Resume is
automatic -- rerunning the identical command against the same file with
the same --language/--cleanup/--timestamps picks up from the last
completed chunk rather than starting over.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from pathlib import Path

from .engine import SaghiEngine

# Default location of the bundled model (the installed app's copy).
# Overridable with --model-dir, or with the SAGHI_MODEL_DIR env var (mirrors paths.py's
# SAGHI_DATA_DIR pattern -- same override the packaged mac installer's
# launcher script relies on for saghi.ui.app, which has no --model-dir
# flag to pass).
DEFAULT_MODEL_DIR = Path(
    os.environ.get("SAGHI_MODEL_DIR")
    or (Path.home() / "Applications" / "Saghi.app" / "Contents" / "Resources" / "model")
)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_transcribe(args: argparse.Namespace) -> int:
    _configure_logging()
    engine = SaghiEngine(args.model_dir)

    print(f"Loading model from {args.model_dir} ...", file=sys.stderr)
    engine.load()
    print(
        f"Loaded via {engine.stack_path} path on device={engine.device} in {engine.load_time_s:.2f}s",
        file=sys.stderr,
    )

    result = engine.transcribe(args.audio, language=args.language, cleanup_level=args.cleanup)

    rtf = result.inference_s / result.duration_s if result.duration_s else float("nan")
    print(
        f"Inference: {result.inference_s:.2f}s for {result.duration_s:.2f}s audio (RTF={rtf:.2f}x)",
        file=sys.stderr,
    )

    if args.json:
        print(json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2))
    else:
        print(result.text)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    import torch
    import transformers

    device = SaghiEngine._detect_device()
    native_ok = SaghiEngine.native_stack_available()

    print(f"model_dir: {args.model_dir}")
    print(f"device: {device}")
    print(f"stack_path (prospective, not loaded): {'native' if native_ok else 'fallback'}")
    print(f"torch: {torch.__version__}")
    print(f"transformers: {transformers.__version__}")
    return 0


def cmd_filejob(args: argparse.Namespace) -> int:
    _configure_logging()

    from .filejobs import FileJob

    engine = SaghiEngine(args.model_dir)
    job = FileJob(
        engine,
        args.audio,
        language=args.language,
        cleanup_level=args.cleanup,
        timestamps=args.timestamps,
        chunk_s=args.chunk_s,
    )

    def on_progress(done: int, total: int, percent: float, elapsed_s: float, eta_s) -> None:
        eta_str = f"{eta_s:.0f}s" if eta_s is not None else "?"
        print(
            f"[chunk {done}/{total}] {percent:.1f}% elapsed={elapsed_s:.0f}s eta={eta_str}",
            file=sys.stderr,
        )

    result = job.run(on_progress=on_progress)

    print(
        f"Job {result.job_id}: {result.total_chunks} chunk(s) total, "
        f"resumed={result.resumed} (skipped {result.resumed_from_chunk} already-done chunk(s)), "
        f"{len(result.segments)} segment(s), history_id={result.history_id}",
        file=sys.stderr,
    )
    print(f"Job dir: {result.job_dir}", file=sys.stderr)

    if args.output_dir:
        import shutil

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in ("output.txt", "output.srt", "output.vtt"):
            src = result.job_dir / name
            if src.exists():
                shutil.copy2(src, out_dir / name)
        print(f"Copied outputs to {out_dir}", file=sys.stderr)

    print(result.text)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    _configure_logging()

    # Deferred import: fastapi/uvicorn are only needed for `serve`, not for
    # `transcribe`/`info` -- keeps those commands free of the extra
    # dependency and fast to start.
    import uvicorn

    from .api import HOST, create_app

    app = create_app(args.model_dir)
    print(
        f"Starting Saghi API server on http://{HOST}:{args.port} "
        f"(docs at http://{HOST}:{args.port}/api/docs) ...",
        file=sys.stderr,
    )
    print("Model loads lazily on the first /api/transcribe request, not now.", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m saghi.cli")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        dest="model_dir",
        help="Path to the local model checkpoint directory",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_transcribe = subparsers.add_parser("transcribe", help="Transcribe an audio file")
    p_transcribe.add_argument("audio", type=Path, help="Path to a WAV (or other soundfile-readable) file")
    p_transcribe.add_argument("--language", choices=["ar", "en"], default="ar")
    p_transcribe.add_argument("--cleanup", choices=["none", "light", "medium"], default="light")
    p_transcribe.add_argument("--json", action="store_true", help="Print a JSON TranscribeResult instead of plain text")
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_info = subparsers.add_parser("info", help="Show device/stack/version info")
    p_info.set_defaults(func=cmd_info)

    p_serve = subparsers.add_parser("serve", help="Run the local HTTP API server")
    # 17865 mirrors api.PORT -- duplicated as a literal here (not imported)
    # so `--help`/argparse setup never pulls in fastapi/uvicorn.
    p_serve.add_argument("--port", type=int, default=17865, help="Port to bind on 127.0.0.1 (default: 17865)")
    p_serve.set_defaults(func=cmd_serve)

    p_filejob = subparsers.add_parser(
        "filejob", help="Transcribe a long audio file: chunked, resumable, TXT/SRT/WebVTT export"
    )
    p_filejob.add_argument("audio", type=Path, help="Path to the source audio file")
    p_filejob.add_argument("--language", choices=["ar", "en"], default="ar")
    p_filejob.add_argument("--cleanup", choices=["none", "light", "medium"], default="light")
    p_filejob.add_argument("--timestamps", action="store_true", help="Also produce SRT/WebVTT + timestamped output")
    p_filejob.add_argument(
        "--chunk-s", type=float, default=None, dest="chunk_s",
        help="Chunk length in seconds (default: memory-aware, see filejobs.default_chunk_s)",
    )
    p_filejob.add_argument(
        "--output-dir", type=Path, default=None, dest="output_dir",
        help="Also copy the final output.txt/.srt/.vtt into this directory",
    )
    p_filejob.set_defaults(func=cmd_filejob)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
