#!/usr/bin/env python3
"""
One-shot probe: does THIS process (e.g. a background job or CI runner,
likely holding none of macOS's Microphone / Accessibility / Input Monitoring
TCC grants) actually have access to a mic stream, a pynput global keyboard
listener, and CGEventPost?

Run ONCE; this is not something to retry-loop around. A `False`/blocked
result here is expected and not a code bug; it just means the corresponding
saghi/*.py module has to be verified some other way (a real listener/stream
substituted with synthetic input in the unit tests) and the real end-to-end
behavior is checked by hand from a normal, permission-granted Terminal session.

Three probes, each independent (one failing doesn't skip the others):

  1. Mic: open a real sounddevice.InputStream for ~1.5s, capture real
     samples. IMPORTANT (per advisor review): a TCC-denied mic on macOS
     usually does NOT raise an exception -- PortAudio happily "opens" and
     hands back a stream of bit-exact zeros. So the diagnostic is
     max(abs(samples)), not whether .start() raised. Report BOTH: did the
     stream open without error, AND was the captured audio non-silent
     (max amplitude > a tiny epsilon).
  2. Listener: start a pynput.keyboard.Listener for 3s and report whether
     ANY on_press/on_release callback fired at all (the test harness has no
     way to physically press a key on this headless-ish session, so this
     mostly checks whether the listener *starts* without an
     ApplicationServices/Accessibility permission error -- macOS raises
     immediately on listener start if Input Monitoring is denied on newer
     macOS, but on macOS 12 this can also fail silently by just never
     delivering events).
  3. CGEventPost: post a completely harmless key event (key code for the
     Escape key, kVK_Escape=53, with NO modifier flags -- deliberately not
     Cmd+V, so this probe can never actually paste anything into whatever
     window happens to be frontmost) and report whether the call itself
     raised/returned an error code. CGEventPost has no return value to
     check in pyobjc (it's void) -- "success" here only means the call
     didn't raise; whether the event was actually delivered anywhere
     requires the Accessibility grant and is NOT independently observable
     from inside this same process, hence the manual checklist step 6
     later asks a human to check the *effect* (does typing an Escape key
     do anything) once run under a permission-granted Terminal.

Never crashes even if a probe's exception is something unusual --  each
probe is wrapped so all three always attempt to run and report.
"""

from __future__ import annotations

import sys
import time

import numpy as np

RESULTS = {}


def probe_mic() -> None:
    print("--- Probe 1: microphone (sounddevice.InputStream) ---")
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        default_in = sd.default.device[0]
        print(f"  default input device index: {default_in}")
        if default_in is not None and default_in >= 0:
            print(f"  default input device name: {devices[default_in]['name']}")

        captured = []

        def _callback(indata, frames, time_info, status):
            if status:
                print(f"  stream status flags: {status}")
            captured.append(indata.copy())

        duration_s = 1.5
        with sd.InputStream(
            samplerate=16000, channels=1, dtype="float32", callback=_callback
        ):
            time.sleep(duration_s)

        if not captured:
            print("  RESULT: stream opened but delivered ZERO callback blocks in 1.5s")
            RESULTS["mic_opened"] = True
            RESULTS["mic_delivered_blocks"] = False
            RESULTS["mic_nonsilent"] = False
            return

        audio = np.concatenate(captured, axis=0).flatten()
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
        nonsilent = peak > 1e-6

        print(f"  stream opened: True, blocks delivered: {len(captured)}, samples: {audio.size}")
        print(f"  peak amplitude: {peak:.8f}, RMS: {rms:.8f}")
        print(f"  RESULT: {'NON-SILENT (real mic access)' if nonsilent else 'BIT-EXACT SILENCE (TCC-denied or muted input, no exception raised)'}")

        RESULTS["mic_opened"] = True
        RESULTS["mic_delivered_blocks"] = True
        RESULTS["mic_nonsilent"] = nonsilent
        RESULTS["mic_peak"] = peak
    except Exception as exc:  # noqa: BLE001 -- probe must never crash the script
        print(f"  RESULT: EXCEPTION opening/reading stream: {type(exc).__name__}: {exc}")
        RESULTS["mic_opened"] = False
        RESULTS["mic_delivered_blocks"] = False
        RESULTS["mic_nonsilent"] = False
        RESULTS["mic_error"] = f"{type(exc).__name__}: {exc}"


def probe_listener() -> None:
    print("\n--- Probe 2: pynput global keyboard listener ---")
    try:
        from pynput import keyboard

        events = []

        def on_press(key):
            events.append(("press", key))

        def on_release(key):
            events.append(("release", key))

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        started_ok = listener.running or True  # start() itself didn't raise
        print("  listener.start() did not raise")
        time.sleep(3.0)
        listener.stop()
        listener.join(timeout=2.0)

        print(f"  events received in 3s (no real key presses possible in this session): {len(events)}")
        RESULTS["listener_started"] = True
        RESULTS["listener_events_received"] = len(events)
    except Exception as exc:  # noqa: BLE001
        print(f"  RESULT: EXCEPTION starting/running listener: {type(exc).__name__}: {exc}")
        RESULTS["listener_started"] = False
        RESULTS["listener_events_received"] = 0
        RESULTS["listener_error"] = f"{type(exc).__name__}: {exc}"


def probe_cgevent_post() -> None:
    print("\n--- Probe 3: CGEventPost (harmless Escape key, no modifiers) ---")
    try:
        import Quartz

        kVK_Escape = 0x35  # 53 -- deliberately NOT Cmd+V, this probe must never paste anything
        down = Quartz.CGEventCreateKeyboardEvent(None, kVK_Escape, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, kVK_Escape, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.02)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

        print("  CGEventCreateKeyboardEvent + CGEventPost calls completed without raising")
        print("  (whether the event was actually DELIVERED anywhere requires the Accessibility")
        print("   grant and is not independently observable from inside this same process --")
        print("   verify by hand on a real Mac)")
        RESULTS["cgevent_post_call_succeeded"] = True
    except Exception as exc:  # noqa: BLE001
        print(f"  RESULT: EXCEPTION posting CGEvent: {type(exc).__name__}: {exc}")
        RESULTS["cgevent_post_call_succeeded"] = False
        RESULTS["cgevent_error"] = f"{type(exc).__name__}: {exc}"


def probe_pasteboard() -> None:
    print("\n--- Bonus probe: NSPasteboard set/read (clipboard, no Accessibility needed) ---")
    try:
        import AppKit

        pb = AppKit.NSPasteboard.generalPasteboard()
        marker = "saghi-probe-marker-صاغي"
        pb.clearContents()
        ok = pb.setString_forType_(marker, AppKit.NSPasteboardTypeString)
        readback = pb.stringForType_(AppKit.NSPasteboardTypeString)
        matches = readback == marker
        print(f"  setString_forType_ returned: {ok}, readback matches: {matches}")
        RESULTS["pasteboard_roundtrip_ok"] = bool(ok) and matches
    except Exception as exc:  # noqa: BLE001
        print(f"  RESULT: EXCEPTION using NSPasteboard: {type(exc).__name__}: {exc}")
        RESULTS["pasteboard_roundtrip_ok"] = False
        RESULTS["pasteboard_error"] = f"{type(exc).__name__}: {exc}"


def main() -> int:
    print("Saghi Phase 5 device probe -- this process's TCC grant status\n")
    probe_mic()
    probe_listener()
    probe_cgevent_post()
    probe_pasteboard()

    print("\n=== SUMMARY ===")
    for key in sorted(RESULTS):
        print(f"  {key}: {RESULTS[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
