#!/usr/bin/env python3
"""
Inline unit-style tests for saghi.segmentation -- no pytest dependency,
just plain assertions on synthetic (tone/noise/silence) audio, entirely in
memory (no model load, no soundfile I/O). Run with the project venv (needs
numpy, not in system python3):

    PYTHONPATH=. python3 dev/test_segmentation.py

Fast (<1s) -- pure numpy, no torch/transformers import at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from saghi.segmentation import find_silences, split_into_segments  # noqa: E402

SR = 16000


def tone(duration_s: float, freq: float = 440.0, amp: float = 0.3, sr: int = SR) -> np.ndarray:
    n = int(round(duration_s * sr))
    t = np.arange(n, dtype=np.float64) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(duration_s: float, sr: int = SR) -> np.ndarray:
    n = int(round(duration_s * sr))
    return np.zeros(n, dtype=np.float32)


def noise(duration_s: float, amp: float = 0.2, sr: int = SR, seed: int = 0) -> np.ndarray:
    n = int(round(duration_s * sr))
    rng = np.random.default_rng(seed)
    return (amp * rng.standard_normal(n)).astype(np.float32)


def cat(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts).astype(np.float32)


def s(seconds: float) -> int:
    """seconds -> samples, matching the helpers above."""
    return int(round(seconds * SR))


failures: list[str] = []
total = 0


def check(description: str, condition: bool, detail: str = "") -> None:
    global total
    total += 1
    if not condition:
        failures.append(f"{description}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------
# 1. Leading silence: silence then one tone, nothing after.
# ---------------------------------------------------------------------
audio = cat(silence(1.0), tone(2.0))
segs = split_into_segments(audio, SR)
check("leading silence: exactly one segment", len(segs) == 1, f"got {segs}")
if segs:
    start, end = segs[0]
    check(
        "leading silence: segment starts at/after the tone onset (silence trimmed)",
        s(0.8) <= start <= s(1.2),
        f"start={start} samples ({start / SR:.3f}s)",
    )
    check(
        "leading silence: segment reaches to the end of the tone",
        end >= s(2.9),
        f"end={end} samples ({end / SR:.3f}s) of total {len(audio)}",
    )

# ---------------------------------------------------------------------
# 2. Trailing silence: one tone then silence, nothing before.
# ---------------------------------------------------------------------
audio = cat(tone(2.0), silence(1.0))
segs = split_into_segments(audio, SR)
check("trailing silence: exactly one segment", len(segs) == 1, f"got {segs}")
if segs:
    start, end = segs[0]
    check("trailing silence: segment starts at/near 0", start <= s(0.2), f"start={start}")
    check(
        "trailing silence: segment ends at/before the tone's end (trailing silence trimmed)",
        s(1.8) <= end <= s(2.2),
        f"end={end} samples ({end / SR:.3f}s)",
    )

# ---------------------------------------------------------------------
# 3. No silence at all (constant tone, longer than max_segment_s) ->
#    hard cut, since there is nothing quieter anywhere to cut at.
# ---------------------------------------------------------------------
audio = tone(7.0)
segs = split_into_segments(audio, SR, max_segment_s=3.0, min_segment_s=0.5)
check("no silence (tone): produces multiple segments (forced hard cuts)", len(segs) >= 2, f"got {segs}")
if len(segs) >= 2:
    check("no silence (tone): segments are contiguous (no gap dropped)", segs[0][1] == segs[1][0], f"got {segs}")
    check(
        "no silence (tone): first cut lands within a sample of max_segment_s (true hard cut)",
        segs[0][1] == s(3.0),
        f"first segment end={segs[0][1]} vs expected {s(3.0)}",
    )
    check("no silence (tone): covers the whole clip", segs[-1][1] == len(audio), f"got {segs}, len={len(audio)}")

# Same idea with broadband noise instead of a pure tone -- guards against
# the threshold logic accidentally relying on tone-specific periodicity.
audio = noise(7.0, amp=0.25)
segs = split_into_segments(audio, SR, max_segment_s=3.0, min_segment_s=0.5)
check("no silence (noise): produces multiple segments (forced hard cuts)", len(segs) >= 2, f"got {segs}")

# ---------------------------------------------------------------------
# 4. All-silence input -> no segments.
# ---------------------------------------------------------------------
audio = silence(5.0)
segs = split_into_segments(audio, SR)
check("all-silence input: zero segments", segs == [], f"got {segs}")

sil = find_silences(audio, SR)
check("all-silence input: find_silences reports one interval covering the whole clip", sil == [(0, len(audio))], f"got {sil}")

# Empty input.
segs = split_into_segments(np.zeros(0, dtype=np.float32), SR)
check("empty input: zero segments", segs == [], f"got {segs}")

# ---------------------------------------------------------------------
# 5. Segment merging: a too-short blip surrounded by silence must not
#    survive as its own tiny segment -- it merges into a neighbor.
# ---------------------------------------------------------------------
audio = cat(silence(1.0), tone(0.2), silence(1.0), tone(2.0), silence(1.0))
segs = split_into_segments(audio, SR, min_segment_s=0.5)
check(
    "merging: the 0.2s blip (< 0.5s min) is absorbed, not left standalone",
    all((e - st) / SR >= 0.5 for st, e in segs),
    f"got segment lengths (s): {[(e - st) / SR for st, e in segs]}",
)
check("merging: exactly one merged segment results (blip + 2s tone, joined across the gap)", len(segs) == 1, f"got {segs}")

# Without merging (min_segment_s effectively 0), the blip would be its own
# segment -- confirms the merge step is actually doing something, not that
# the blip was simply undetected.
segs_unmerged = split_into_segments(audio, SR, min_segment_s=0.0)
check(
    "merging: with min_segment_s=0, the blip DOES show up as its own short segment",
    len(segs_unmerged) == 2 and (segs_unmerged[0][1] - segs_unmerged[0][0]) / SR < 0.5,
    f"got {segs_unmerged}",
)

# ---------------------------------------------------------------------
# 6. Multiple clearly-separated utterances -> multiple segments, cuts
#    land inside the silent gaps.
# ---------------------------------------------------------------------
audio = cat(tone(2.0, freq=440), silence(1.0), tone(1.5, freq=550), silence(1.0), tone(2.5, freq=660))
segs = split_into_segments(audio, SR)
check("three utterances: three segments found", len(segs) == 3, f"got {segs}")
if len(segs) == 3:
    gap1_lo, gap1_hi = s(2.0), s(3.0)
    gap2_lo, gap2_hi = s(4.5), s(5.5)
    check(
        "three utterances: cut 1 lands inside the first silent gap",
        gap1_lo <= segs[0][1] <= gap1_hi and gap1_lo <= segs[1][0] <= gap1_hi,
        f"got boundary {segs[0][1]}/{segs[1][0]} vs gap [{gap1_lo},{gap1_hi}]",
    )
    check(
        "three utterances: cut 2 lands inside the second silent gap",
        gap2_lo <= segs[1][1] <= gap2_hi and gap2_lo <= segs[2][0] <= gap2_hi,
        f"got boundary {segs[1][1]}/{segs[2][0]} vs gap [{gap2_lo},{gap2_hi}]",
    )

# ---------------------------------------------------------------------
# 7. Long speech run WITH a silence near the max-length boundary -> the
#    cut should land in that dip, not at a blind hard cut.
# ---------------------------------------------------------------------
# 4s tone, 0.3s silence dip, 3.7s tone -- one continuous "speech span" (no
# gap long enough to count as a real pause at the default min_silence_s),
# max_segment_s=4.0 so the forced cut's target lands close to the dip.
# (Trailing tone deliberately chosen so the remainder after that one cut
# is comfortably under max_segment_s and well over min_segment_s -- this
# case is specifically about the *cut point*, not the merge-vs-max_len
# interaction, which case 9 below covers on its own.)
audio = cat(tone(4.0), silence(0.3), tone(3.7))
segs = split_into_segments(audio, SR, max_segment_s=4.0, min_segment_s=0.5, min_silence_s=1.0)
check("long run with nearby dip: split into 2 segments", len(segs) == 2, f"got {segs}")
if len(segs) == 2:
    dip_lo, dip_hi = s(4.0), s(4.3)
    check(
        "long run with nearby dip: cut lands inside the 0.3s dip, not at a blind hard cut",
        dip_lo <= segs[0][1] <= dip_hi,
        f"cut at {segs[0][1]} ({segs[0][1] / SR:.3f}s), expected within [{dip_lo / SR:.3f}, {dip_hi / SR:.3f}]s "
        f"(a blind hard cut would land at {s(4.0)})",
    )
    check(
        "long run with nearby dip: neither resulting segment exceeds max_segment_s",
        all((e - st) / SR <= 4.0 + 1e-6 for st, e in segs),
        f"got segment lengths (s): {[(e - st) / SR for st, e in segs]}",
    )
    check(
        "long run with nearby dip: no audio dropped/overlapped at the cut seam "
        "(this dip starts exactly at target, so the clamp cuts at target itself -- "
        "pins that the two segments still meet exactly, not off by a sample either way)",
        segs[0][1] == segs[1][0],
        f"seg0 end={segs[0][1]}, seg1 start={segs[1][0]}",
    )

# ---------------------------------------------------------------------
# 8. Long speech run with NO silence anywhere near the boundary (only a
#    gap far away) -> falls back to a hard cut near max_segment_s, does
#    NOT wait for the distant gap.
# ---------------------------------------------------------------------
audio = cat(tone(5.0), tone(5.0), silence(1.0), tone(2.0))  # gap only appears at ~10s
segs = split_into_segments(audio, SR, max_segment_s=3.0, min_segment_s=0.5, cut_search_s=1.0)
check("long run, distant gap: more than one cut happens before the real gap", len(segs) >= 3, f"got {segs}")
if len(segs) >= 2:
    check(
        "long run, distant gap: first cut is a hard cut near max_segment_s, not deferred to the distant gap",
        abs(segs[0][1] - s(3.0)) <= s(0.1),
        f"first cut at {segs[0][1]} ({segs[0][1] / SR:.3f}s), expected near {s(3.0)} ({3.0}s)",
    )

# ---------------------------------------------------------------------
# 9. Merge must NEVER create a segment longer than max_segment_s. Two
#    real speech spans (separated by a genuine, confirmed silence gap --
#    neither span individually exceeds max_segment_s, so the max-length
#    forced-cut path is never involved here, isolating the merge step
#    itself): a normal-length span, then a too-short tail blip. The blip
#    must NOT be merged backward if doing so would push the combined
#    segment past max_segment_s -- a short standalone segment is the
#    correct, safe outcome instead (regression test for a real bug an
#    advisor review caught: the original merge step had no max-length
#    check at all, so a trailing sub-min fragment could get silently
#    glued onto a preceding near-max-length segment, producing a clip
#    longer than the model's own max clip length).
# ---------------------------------------------------------------------
audio = cat(tone(3.9), silence(1.0), tone(0.1))
segs = split_into_segments(audio, SR, max_segment_s=4.0, min_segment_s=0.5)
check(
    "merge respects max_segment_s: no returned segment exceeds max_segment_s",
    all((e - st) / SR <= 4.0 + 1e-6 for st, e in segs),
    f"got segment lengths (s): {[(e - st) / SR for st, e in segs]}",
)
check(
    "merge respects max_segment_s: the tiny tail blip is kept standalone rather than merged into an oversized segment",
    len(segs) == 2,
    f"got {segs}",
)

# ---------------------------------------------------------------------
# Bonus: find_silences directly -- min_silence_s filters out a too-brief
# dip that split_into_segments would otherwise treat as a "real" gap.
# ---------------------------------------------------------------------
audio = cat(tone(1.0), silence(0.05), tone(1.0))  # 50ms dip
sil = find_silences(audio, SR, min_silence_s=0.15)
check("find_silences: a 50ms dip is filtered out at min_silence_s=0.15", sil == [], f"got {sil}")
sil = find_silences(audio, SR, min_silence_s=0.0)
check("find_silences: the same 50ms dip IS reported at min_silence_s=0.0", len(sil) == 1, f"got {sil}")


# ---------------------------------------------------------------------
print(f"\n{total - len(failures)}/{total} passed")
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(f"  FAIL: {f}")
    sys.exit(1)
sys.exit(0)
