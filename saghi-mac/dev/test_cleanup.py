#!/usr/bin/env python3
"""
Inline unit-style tests for saghi.cleanup.clean_transcript -- no pytest
dependency, just plain assertions. Run with:

    python dev/test_cleanup.py

Two of these cases (SAMPLE1_TEXT / SAMPLE2_TEXT) are the *real* transcripts
produced by the model on the two bundled sample WAVs -- they contain no filler words or tags, so they
must survive `light` cleanup byte-identical. That's the strongest guard
against "never delete real words": these aren't synthetic strings, they're
what the model actually said.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saghi.cleanup import clean_transcript  # noqa: E402

SAMPLE1_TEXT = "ففي الحالة دي المسألة دي يعني more safe"
SAMPLE2_TEXT = (
    "الله يطول عمرك ويخليك يا بابا لكن أنا ما ودي أنك تشيل هم ولا تقلق من ناحية هذا الموضوع "
    "وإن شاء الله كل اللي تبيه راح يتحقق"
)

# (description, input_text, level, expected_output)
CASES = [
    (
        "none: strips tag but leaves filler word untouched",
        "hello <hesitation> um world",
        "none",
        "hello um world",
    ),
    (
        "none: tag glued to text (no surrounding spaces) still strips cleanly",
        "hi<hesitation>there",
        "none",
        "hi there",
    ),
    (
        "light: tag + standalone Arabic filler removed together",
        "<hesitation> اه انا بخير",
        "light",
        "انا بخير",
    ),
    (
        "light: elongated hesitation vowel token removed",
        "اااا شيء غريب",
        "light",
        "شيء غريب",
    ),
    (
        "light: real word containing an 'اه' substring is NEVER touched",
        "أحضر مياه للجميع",
        "light",
        "أحضر مياه للجميع",
    ),
    (
        "light: standalone آه removed, real words around it kept",
        "آه يا قلبي",
        "light",
        "يا قلبي",
    ),
    (
        "light: English filler with trailing comma removed, comma kept on neighbor",
        "you know, um, whatever",
        "light",
        "you know, whatever",
    ),
    (
        "light: 'uhm' variant removed",
        "uhm let's go",
        "light",
        "let's go",
    ),
    (
        "light: consecutive fillers removed with no stray leading space",
        "um uhm hello",
        "light",
        "hello",
    ),
    (
        "light: English words (code-switching) are never touched",
        SAMPLE1_TEXT,
        "light",
        SAMPLE1_TEXT,
    ),
    (
        "light: real sample2 transcript (no fillers/tags) survives byte-identical",
        SAMPLE2_TEXT,
        "light",
        SAMPLE2_TEXT,
    ),
    (
        "light: light does NOT tidy pre-existing double spaces",
        "hello   world",
        "light",
        "hello   world",
    ),
    (
        "medium: DOES tidy pre-existing double spaces",
        "hello   world",
        "medium",
        "hello world",
    ),
    (
        "light: light does NOT remove space before punctuation",
        "hello , world",
        "light",
        "hello , world",
    ),
    (
        "medium: DOES tidy space before punctuation",
        "hello , world",
        "medium",
        "hello, world",
    ),
    (
        "medium: collapses one immediately-duplicated word",
        "يعني يعني هذا صعب",
        "medium",
        "يعني هذا صعب",
    ),
    (
        "medium: filler removal + duplicate collapse together",
        "يعني يعني um whatever",
        "medium",
        "يعني whatever",
    ),
]


def main() -> int:
    failures = []
    for description, input_text, level, expected in CASES:
        actual = clean_transcript(input_text, level)
        if actual != expected:
            failures.append((description, input_text, level, expected, actual))

    total = len(CASES)
    passed = total - len(failures)
    for description, input_text, level, expected, actual in failures:
        print(f"FAIL [{level}] {description}")
        print(f"  input:    {input_text!r}")
        print(f"  expected: {expected!r}")
        print(f"  actual:   {actual!r}")

    print(f"\n{passed}/{total} passed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
