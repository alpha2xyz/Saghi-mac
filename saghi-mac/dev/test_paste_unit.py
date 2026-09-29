#!/usr/bin/env python3
"""
Unit test for saghi.paste (no Qt, no engine, no recorder). Two parts:

  1. Clipboard round-trip via NSPasteboard, verified readable back -- this
     is a real, deterministic assertion (no TCC grant needed for clipboard
     access).
  2. CGEvent Cmd+V posting: the call is attempted and its outcome (raised /
     did not raise) is REPORTED, not asserted pass/fail -- whether the
     Accessibility permission is held by this process is TCC-dependent and
     out of this test's control (see paste.py's module docstring). What IS asserted is that paste_text() never raises
     regardless of whether the underlying CGEventPost succeeds, and that
     clipboard_set is independent of the autopaste outcome.

Run:
    PYTHONPATH=. <venv>/bin/python dev/test_paste_unit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from saghi import paste  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


print("--- Part 1: clipboard set/read round-trip (NSPasteboard) ---")

marker = "saghi-paste-unit-test-صاغي-٤٢"
ok = paste.set_clipboard(marker)
check(ok is True, "set_clipboard() reports success")
readback = paste.read_clipboard()
check(readback == marker, f"clipboard readback matches exactly (got {readback!r})")

# Overwriting works too (not just the first write).
marker2 = "second marker — تجربة"
paste.set_clipboard(marker2)
check(paste.read_clipboard() == marker2, "clipboard readback reflects the SECOND write, not the first")

print("\n--- Part 2: paste_text() -- clipboard always set, autopaste outcome reported ---")

result_no_autopaste = paste.paste_text("hello بدون لصق", autopaste=False)
check(result_no_autopaste.clipboard_set is True, "paste_text(autopaste=False) still sets the clipboard")
check(result_no_autopaste.pasted is False, "paste_text(autopaste=False) never attempts to post a paste event")
check(paste.read_clipboard() == "hello بدون لصق", "clipboard content matches what paste_text(autopaste=False) set")

result_autopaste = paste.paste_text("hello مع محاولة لصق", autopaste=True)
check(result_autopaste.clipboard_set is True, "paste_text(autopaste=True) sets the clipboard")
check(isinstance(result_autopaste.pasted, bool), "paste_text(autopaste=True).pasted is a plain bool (never raises)")
print(
    f"  REPORTED (not asserted -- TCC-dependent): CGEventPost call outcome = "
    f"pasted={result_autopaste.pasted} "
    f"({'CGEvent calls completed without raising' if result_autopaste.pasted else 'CGEvent post raised, or an earlier step failed'})"
)
print(
    "  Note: pasted=True only proves the CGEvent calls didn't raise, NOT that any app actually "
    "received the paste -- that requires the Accessibility grant and is not observable from this "
    "process. Verify by hand on a real Mac."
)

print(f"\nALL PASSED ({_checks} checks)")
