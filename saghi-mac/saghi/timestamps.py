"""
Timestamp formatting for the `timestamps=true` transcribe option.

`produce_segments()` is the real (Phase 3) segment producer, replacing the
Phase 2 stub (`build_stub_segments`, which always returned exactly one
segment spanning the whole clip -- removed now that real segmentation
exists). It
splits the clip into speech segments at silence boundaries
(`saghi.segmentation.split_into_segments`) and transcribes each one
individually, so the resulting timestamps reflect where each piece of
speech actually landed in the silence-split timeline -- estimated from
those boundaries, never claimed as word-level alignment (the model
provides no native word alignment).

The three formatters below (`format_timestamped`, `format_srt`,
`format_vtt`) are written against the generic `Segment` dataclass and were
unaffected by this swap, exactly as planned in Phase 2's notes.

filejobs.py (Phase 3's long-file job runner) does its own segmentation
loop directly against `saghi.segmentation` + `engine.transcribe_array` --
chunked, staged, and resumable -- rather than calling `produce_segments()`
here, which is deliberately simple (reads the whole clip at once) and only
meant for api.py's short-clip `timestamps=true` path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

import numpy as np
import soundfile as sf


@dataclass
class Segment:
    start_s: float
    end_s: float
    text: str


def produce_segments(
    engine,  # saghi.engine.SaghiEngine -- untyped to avoid an import-cycle risk for no benefit
    audio_path: Union[str, Path],
    language: str = "ar",
    cleanup_level: str = "light",
    max_segment_s: float = 20.0,
    min_segment_s: float = 0.5,
) -> list[Segment]:
    """
    Real silence-split segmentation for api.py's `timestamps=true` path.
    Reads the whole clip into memory (API clips are short -- see api.py's
    module docstring on inference cost budget), splits it into
    model-suitable speech segments at silence boundaries, and transcribes
    each segment individually via `engine.transcribe_array` so the
    returned segments carry real, distinct per-segment timestamps instead
    of one whole-clip span.

    If no speech is detected at all (e.g. a near-silent clip), returns one
    empty-text segment spanning the clip rather than an empty list, so the
    timestamped/srt/vtt fields are never empty/malformed.
    """
    from .segmentation import split_into_segments

    data, sr = sf.read(str(audio_path), always_2d=False)
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1).astype(np.float32, copy=False)
    if sr != 16000:
        import soxr

        audio = soxr.resample(audio, sr, 16000).astype(np.float32, copy=False)
        sr = 16000

    bounds = split_into_segments(audio, sr, max_segment_s=max_segment_s, min_segment_s=min_segment_s)
    if not bounds:
        duration_s = len(audio) / sr if sr else 0.0
        return [Segment(start_s=0.0, end_s=duration_s, text="")]

    segments = []
    for start, end in bounds:
        seg_audio = audio[start:end]
        result = engine.transcribe_array(seg_audio, sr, language=language, cleanup_level=cleanup_level)
        segments.append(Segment(start_s=start / sr, end_s=end / sr, text=result.text))
    return segments


def _format_hhmmss_dot(seconds: float) -> str:
    """HH:MM:SS.mmm -- used by the human-readable `timestamped` field."""
    seconds = max(seconds, 0.0)
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def _format_hhmmss_comma(seconds: float) -> str:
    """HH:MM:SS,mmm -- SubRip's comma-decimal timestamp format."""
    return _format_hhmmss_dot(seconds).replace(".", ",")


def format_timestamped(segments: list[Segment]) -> str:
    """Human-readable `[HH:MM:SS.mmm --> HH:MM:SS.mmm] text` lines, one per segment."""
    lines = [
        f"[{_format_hhmmss_dot(seg.start_s)} --> {_format_hhmmss_dot(seg.end_s)}] {seg.text}"
        for seg in segments
    ]
    return "\n".join(lines)


def format_srt(segments: list[Segment]) -> str:
    """Valid SubRip (.srt): 1-based index, comma-decimal timerange, text, blank line."""
    blocks = []
    for i, seg in enumerate(segments, start=1):
        start = _format_hhmmss_comma(seg.start_s)
        end = _format_hhmmss_comma(seg.end_s)
        blocks.append(f"{i}\n{start} --> {end}\n{seg.text}\n")
    return "\n".join(blocks) + "\n"


def format_vtt(segments: list[Segment]) -> str:
    """Valid WebVTT: 'WEBVTT' header, then one cue per segment (dot-decimal timerange)."""
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _format_hhmmss_dot(seg.start_s)
        end = _format_hhmmss_dot(seg.end_s)
        lines.append(f"{start} --> {end}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
