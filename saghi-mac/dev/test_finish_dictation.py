#!/usr/bin/env python3
"""
Tests for saghi.cleanup.finish_dictation: each dictated take gets an end
mark when it has none, plus one trailing space so the next take joins
cleanly. Run with: python dev/test_finish_dictation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saghi.cleanup import finish_dictation  # noqa: E402

CASES = [
    ("plain Arabic gets a full stop", "أنا رايح البيت", "أنا رايح البيت. "),
    ("Arabic question word gets «؟»", "هل خلصت الشغل", "هل خلصت الشغل؟ "),
    ("Saudi question word gets «؟»", "وش رايك في الفكرة", "وش رايك في الفكرة؟ "),
    ("existing full stop kept", "تم الموضوع.", "تم الموضوع. "),
    ("existing «؟» kept", "كيف حالك؟", "كيف حالك؟ "),
    ("existing Arabic comma kept", "أول شي،", "أول شي، "),
    ("mixed text ending in English gets a stop", "شغلت الـ server", "شغلت الـ server. "),
    ("English question", "what is this", "what is this? "),
    ("English statement", "this works", "this works. "),
    ("punctuation before a closing quote kept", "قال «تمام.»", "قال «تمام.» "),
    ("surrounding spaces trimmed", "  مرحبا  ", "مرحبا. "),
    ("empty stays empty", "", ""),
    ("whitespace only stays empty", "   ", ""),
]

failed = 0
for desc, given, want in CASES:
    got = finish_dictation(given)
    ok = got == want
    failed += not ok
    print(("PASS" if ok else "FAIL"), desc, "" if ok else f"-> got {got!r}, want {want!r}")

two_takes = finish_dictation("أول جملة") + finish_dictation("ثاني جملة")
ok = two_takes == "أول جملة. ثاني جملة. "
failed += not ok
print(("PASS" if ok else "FAIL"), "two takes join with a stop and a space", "" if ok else repr(two_takes))

print(f"\n{len(CASES) + 1 - failed}/{len(CASES) + 1} passed")
sys.exit(1 if failed else 0)
