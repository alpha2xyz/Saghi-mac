"""
Energy-based silence detection and speech-segment splitting for long-file
transcription (Phase 3, filejobs.py).

Pure numpy, deterministic, no VAD library and no librosa/numba dependency
(see engine.py's module docstring for why librosa is avoided on this dev
machine's stack) -- operates on a plain mono float32 array already in
memory, at whatever sample rate it was given.

Two building blocks:

  find_silences()       -- windowed-RMS silence-interval detection against
                            an adaptive threshold, relative to the clip's
                            own "loud" (speech) energy level.
  split_into_segments()  -- turns one audio array into a list of
                            (start_sample, end_sample) speech segments: cut
                            in the silence gaps between utterances, and for
                            any speech run longer than max_segment_s, cut at
                            the quietest nearby point (falling back to a
                            hard cut only when no quieter point exists in
                            the search window). Tiny fragments are merged
                            into a neighboring segment.

No knowledge of chunk boundaries here -- filejobs.py calls
split_into_segments() once per audio chunk it has already read into memory;
this module has no opinion about chunking/memory and never touches a file.

## Threshold design

RMS energy is computed over consecutive, non-overlapping windows
(`window_ms`, default 25ms -- a standard analysis-window length for speech
energy). The silence threshold is set **relative to the clip's own loud
(speech) energy level**, not to its noise floor: `loud_level` is a high
percentile (default 90th) of the per-window RMS values, and a window
counts as silent if its RMS is at or below `threshold_ratio` (default 0.15)
of that loud level.

This is the deliberate choice over the more obvious-sounding "compare
against the noise floor / a low percentile": a low-percentile-based
threshold breaks down on any *steady, uniform-amplitude* signal (a
constant test tone, or in principle a droning background sound) because
every window then has essentially the same RMS as the "noise floor" itself
-- there's no floor/ceiling gap to detect, and a low-percentile threshold
scaled up by a factor ends up sitting *above* the actual level, so the
whole thing gets misclassified as silence. Anchoring to a high percentile
(the loud parts) instead means a uniform signal with no genuine quiet
stretch is correctly reported as having zero silence, while any real gap
in an otherwise-loud clip (a window at or near true silence) is still far
enough below 0.15x of the loud level to be caught. Verified directly by
`dev/test_segmentation.py`'s "no silence at all" cases (both a constant
tone and broadband noise).

`absolute_floor` (default 1e-4, in the same units as the input samples --
i.e. roughly -80dBFS for typically-normalized float audio) is a hard
minimum on the threshold, and is checked first against the *entire* clip's
RMS: a clip that is at/near digital silence throughout has no "loud" part
to derive `loud_level` from at all, so it is reported as one silence
interval covering the whole clip rather than running the relative-ratio
logic against a meaningless reference.

## Chunk sizing note (used by filejobs.py, not by this module)

This module takes no position on chunk length -- filejobs.py decides that
based on available RAM (see filejobs.DEFAULT_CHUNK_S) and simply calls
split_into_segments() once per chunk it has already read.
"""

from __future__ import annotations

import numpy as np

Interval = tuple[int, int]


def _window_rms(audio: np.ndarray, sr: int, window_ms: float) -> np.ndarray:
    """RMS energy of consecutive, non-overlapping windows of `window_ms`."""
    win = max(1, int(round(sr * window_ms / 1000.0)))
    n = len(audio)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    n_windows = (n + win - 1) // win  # ceil -- last window may be short
    rms = np.empty(n_windows, dtype=np.float64)
    audio64 = audio.astype(np.float64, copy=False)
    for i in range(n_windows):
        start = i * win
        end = min(start + win, n)
        chunk = audio64[start:end]
        rms[i] = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
    return rms


def find_silences(
    audio: np.ndarray,
    sr: int,
    window_ms: float = 25.0,
    loud_percentile: float = 90.0,
    threshold_ratio: float = 0.15,
    min_silence_s: float = 0.15,
    absolute_floor: float = 1e-4,
) -> list[Interval]:
    """
    Detect silence intervals via windowed RMS against a threshold relative
    to the clip's own loud (speech) energy level -- see module docstring
    for the full reasoning.

    Args:
        audio: mono float array (any sample rate; caller resamples if it
            cares about a specific rate -- this function just needs `sr`
            to convert `window_ms`/`min_silence_s` to sample counts).
        sr: sample rate of `audio`, Hz.
        window_ms: analysis window length in milliseconds.
        loud_percentile: percentile of per-window RMS used as the "loud"
            reference level.
        threshold_ratio: a window is silent if its RMS <=
            threshold_ratio * loud_level.
        min_silence_s: minimum duration (seconds) for a run of silent
            windows to be reported -- shorter dips (natural micro-pauses
            within speech, not real gaps) are not reported here. Pass 0.0
            to report every silent window run, however brief (used
            internally by split_into_segments' local cut-point search).
        absolute_floor: hard minimum on the threshold, and the reference
            used to detect a clip that is at/near digital silence
            throughout (no loud part to derive a ratio-based threshold
            from at all).

    Returns:
        Merged, non-overlapping (start_sample, end_sample) intervals, in
        order. Empty audio -> []. A clip whose overall RMS is at/below
        `absolute_floor` -> [(0, len(audio))] (the whole clip is silence).
    """
    n = len(audio)
    if n == 0:
        return []

    audio = np.asarray(audio, dtype=np.float32)
    global_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if global_rms <= absolute_floor:
        return [(0, n)]

    win = max(1, int(round(sr * window_ms / 1000.0)))
    rms = _window_rms(audio, sr, window_ms)

    loud_level = float(np.percentile(rms, loud_percentile))
    threshold = max(loud_level * threshold_ratio, absolute_floor)

    is_silent = rms <= threshold

    raw_intervals: list[Interval] = []
    i = 0
    n_windows = len(rms)
    while i < n_windows:
        if is_silent[i]:
            j = i
            while j < n_windows and is_silent[j]:
                j += 1
            start = i * win
            end = min(j * win, n)
            raw_intervals.append((start, end))
            i = j
        else:
            i += 1

    min_silence_samples = int(min_silence_s * sr)
    return [(s, e) for s, e in raw_intervals if (e - s) >= min_silence_samples]


def _speech_spans(n: int, silences: list[Interval]) -> list[Interval]:
    """Complement of merged, ordered silence intervals within [0, n)."""
    spans: list[Interval] = []
    pos = 0
    for s, e in silences:
        if s > pos:
            spans.append((pos, s))
        pos = max(pos, e)
    if pos < n:
        spans.append((pos, n))
    return spans


def _best_cut_point(
    audio: np.ndarray,
    sr: int,
    lo: int,
    hi: int,
    target: int,
    window_ms: float,
    loud_percentile: float,
    threshold_ratio: float,
    absolute_floor: float,
) -> int | None:
    """
    Look for a silence within audio[lo:hi] (a bounded search window around
    `target`, already clamped to the enclosing speech span) to use as a
    cut point instead of a hard cut exactly at `target`. Re-uses
    find_silences on just this slice, with min_silence_s=0.0 so even a
    single quiet window counts -- a deliberately more lenient search than
    the "real gap between utterances" detection above, since a run that's
    too long specifically because it has no *confirmed* gap may still have
    a locally-quieter dip worth cutting at.

    Returns a sample index (absolute, not relative to `lo`), or None if no
    silence at all was found in the window -- the caller should hard-cut
    at `target` in that case.
    """
    if hi <= lo:
        return None

    local_silences = find_silences(
        audio[lo:hi],
        sr,
        window_ms=window_ms,
        loud_percentile=loud_percentile,
        threshold_ratio=threshold_ratio,
        min_silence_s=0.0,
        absolute_floor=absolute_floor,
    )
    if not local_silences:
        return None

    # Prefer the silence interval whose midpoint lands closest to target.
    def _distance(iv: Interval) -> int:
        mid = lo + (iv[0] + iv[1]) // 2
        return abs(mid - target)

    best = min(local_silences, key=_distance)
    iv_start = lo + best[0]
    iv_end = lo + best[1]

    # Cut as close to `target` as possible while staying inside the
    # chosen interval -- NOT always the interval's midpoint. If `target`
    # itself already falls inside the silence (the common case: a real
    # gap happens to straddle the max-length boundary), cut exactly
    # there. Only when `target` falls outside the interval do we clamp to
    # the nearest edge. Cutting at the midpoint unconditionally (the
    # original approach) could push the resulting segment past
    # `max_segment_s` whenever the interval extends past `target` --
    # caught by dev/test_segmentation.py's "long run with nearby dip"
    # case after an advisor review flagged the related merge-side bug and
    # prompted a closer look at this function too.
    return max(iv_start, min(target, iv_end))


def _merge_short_segments(segments: list[Interval], min_len: int, max_len: int) -> list[Interval]:
    """
    Merge any segment shorter than `min_len` samples into a neighbor
    (forward if it's the first segment or the previous one was also too
    short and hasn't grown past min_len yet, otherwise backward into the
    segment already accumulated) -- UNLESS doing so would produce a
    segment longer than `max_len`, in which case the merge is skipped and
    the short fragment is kept standalone instead. A too-short segment
    handed to the model on its own is a minor cost; a merged segment past
    `max_len` risks exceeding the model's own max clip length (see
    filejobs.MAX_SEGMENT_S) -- a short segment is always the safer of the
    two outcomes. A single standalone segment that is itself too short is
    returned as-is -- there is no neighbor to merge into.
    """
    if not segments:
        return segments

    merged: list[Interval] = [segments[0]]
    for start, end in segments[1:]:
        prev_start, prev_end = merged[-1]
        too_short = (end - start) < min_len or (prev_end - prev_start) < min_len
        if too_short and (end - prev_start) <= max_len:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def split_into_segments(
    audio: np.ndarray,
    sr: int,
    max_segment_s: float = 30.0,
    min_segment_s: float = 0.5,
    window_ms: float = 25.0,
    loud_percentile: float = 90.0,
    threshold_ratio: float = 0.15,
    min_silence_s: float = 0.15,
    cut_search_s: float = 3.0,
    absolute_floor: float = 1e-4,
) -> list[Interval]:
    """
    Split one in-memory audio array into speech segments.

    Returns (start_sample, end_sample) tuples, in order, covering only the
    speech portions -- leading/trailing/internal silence is excluded from
    every segment. Each segment is at most `max_segment_s` long; a speech
    run longer than that is cut at the quietest point within
    `cut_search_s` seconds of the target length (found via a lenient local
    silence search, see _best_cut_point), falling back to a hard cut
    exactly at the target length when no quieter point exists nearby.
    Segments shorter than `min_segment_s` (after silence-trimming) are
    merged into a neighbor rather than kept standalone.

    All-silence input (or empty input) -> [].
    """
    n = len(audio)
    if n == 0:
        return []

    audio = np.asarray(audio, dtype=np.float32)

    silences = find_silences(
        audio,
        sr,
        window_ms=window_ms,
        loud_percentile=loud_percentile,
        threshold_ratio=threshold_ratio,
        min_silence_s=min_silence_s,
        absolute_floor=absolute_floor,
    )
    if silences == [(0, n)]:
        return []  # entire clip is silence

    spans = _speech_spans(n, silences)
    max_len = int(max_segment_s * sr)
    min_len = int(min_segment_s * sr)
    search_samples = int(cut_search_s * sr)

    segments: list[Interval] = []
    for span_start, span_end in spans:
        pos = span_start
        while span_end - pos > max_len:
            target = pos + max_len
            lo = max(pos, target - search_samples)
            hi = min(span_end, target + search_samples)
            cut = _best_cut_point(
                audio, sr, lo, hi, target,
                window_ms, loud_percentile, threshold_ratio, absolute_floor,
            )
            if cut is None or cut <= pos or cut >= span_end:
                cut = target  # no usable silence nearby -- hard cut
            segments.append((pos, cut))
            pos = cut
        segments.append((pos, span_end))

    return _merge_short_segments(segments, min_len, max_len)
