# Status

Newest first. Every session adds an entry before it ends.

## 2026-10-12 — Mac session (owner's MacBook Air M5, branch `claude/friendly-planck-ygkqn4`)
**Solved:** "since v0.2.0 no text, pill stops at 0:03, flat waveform". Not a permission problem and not the model.

**What was checked (installed v0.2.0's Python and model, branch code):**
- `dev/mic_check.py`: built-in mic OK (peak 0.27). Per device: "MacBook Air Microphone" OK, "Samsung USB C Earphones" OK, **"Alfa Microphone" = bit-exact silence** (peak 0.0).
- `saghi.cli transcribe` on a `say -v Majed` WAV: correct Arabic in 0.72 s (model load 8.8 s, MPS). The model is fine.
- App from the terminal + `saghi.log`: Settings had `"microphone": "Alfa Microphone"`. Every hold from it came back as pure silence ("تعذر الوصول إلى الميكروفون"). The watchdog caught a **5 s UI freeze inside the mic open/stop** on Alfa.
- Timing per mic (open): Samsung 0.10 s, default 0.08 s, **Alfa 1.2-10.1 s** ("Audio Hardware Not Running"), then silence.
- "Alfa Microphone" is the owner's iPhone (Continuity mic). It sends silence while the iPhone is plugged into the Mac by cable; it works once the cable is out (owner confirmed).
- Separate bug: the Samsung earphones plugged in after Saghi started never appeared. PortAudio reads the device list once at `import sounddevice`.
- v0.1.1 worked because it used the default mic (owner confirmed). Nothing in 0.2.0 writes the mic setting on its own; a scroll-wheel theory was tested and ruled out (the macOS style ignores wheel on combos).

**Root cause:** the chosen mic (iPhone Continuity) opened slowly on the UI thread, freezing the whole app (pill stuck mid-count, flat wave), then delivered silence, which the app reported as a permission problem. Plus: hot-plugged mics were invisible.

**Fixes (commits `bcbcc2d`, `5894ade`):**
- `recorder.refresh_devices()` re-reads the device list (~3 ms) before every recording and whenever the Settings mic list is shown or opened; never while a stream is open.
- The mic opens on a helper thread; the UI waits at most 0.25 s, so a slow mic can no longer freeze the app. Release/Esc during a slow open are handled.
- A silent chosen mic is named in the pill («Alfa Microphone» لا يرسل صوتًا); an unplugged saved mic stays listed as "(غير متصل)".

**Verified live by the owner:** Samsung shows up after re-plugging and dictates (log: peak 0.17, text out); Alfa opens in 4.3 s / 2.6 s with no freeze, the silent case shows the named message, and Alfa dictates once the iPhone's cable is out.

**Tests:** new `dev/test_mic_devices.py` (39 checks, fake PortAudio: hot-plug, refresh guard, Settings list, error text, slow mic). New `dev/test_e2e_dictation.py` (real hotkey state machine -> real Recorder on a real mic -> real model -> text, with the Mac voice through the speakers). It ran end to end on the M5 (mic heard the voice, model loaded and returned text, history and clipboard filled) but the transcript was wrong because the speakers were at 6% in a loud room (room rms 0.02-0.05). The test now checks volume >= 50% and room noise first and says ROOM/VOLUME when that is the problem. **Not yet passed in a quiet room** -- owner asked to run it later. All other `dev/test_*.py` pass on the Mac except `test_sf_symbols.py`, which already fails on unchanged code (not related).

**Next:**
- Run `dev/test_e2e_dictation.py` once with the volume at 50%+ in a quiet room.
- Release v0.2.1 (run the build workflow) so the installed app gets the fixes. Until then the installed 0.2.0 still freezes on a slow mic: choose Samsung or the default mic in Settings.
- Look at the pre-existing `test_sf_symbols.py` failure.
- Speech said while a slow mic is still opening (Alfa: 1-10 s) is lost; an option is a short "opening mic" hint in the pill.

## 2026-10-12 — cloud session (branch `claude/friendly-planck-ygkqn4`)
**Open problem:** since v0.2.0 dictation gives no text. The pill stops at 0:03 and the waveform is flat. v0.1.1 worked on the owner's M5.
**Suspects, in order:**
1. Microphone permission lost on reinstall (flat waveform means silence).
2. Lock contention between `Recorder.level()` and `stop()` (`saghi/recorder.py`).
3. Glass effect (`ui/macos_panel.py`), hotkey library on new macOS, or sounds.
4. Model path `engine._infer_array` on MPS, never verified since `c320334`.

**Done:**
- Added `saghi.log` + `crash.log` + freeze watchdog (`saghi/diagnostics.py`) and stage logs in `ui/dictation.py`.
- Added `dev/mic_check.py`, `dev/mac_session.sh`, this file, `CLAUDE.md`, and the cloud SessionStart hook.

**Next (on the Mac):**
- `mac_session.sh start`, then `mic`, then `model`, then `app` (try the hotkey), then `report`.
- Fix the real cause.
- Release v0.2.1.

A Mac session (`session_01UcqqWojo1WsTm9xegYeBaT`) started on this branch at the same time and is working on the diagnosis.
