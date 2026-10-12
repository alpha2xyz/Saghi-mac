# Status

Newest first. Every session adds an entry before it ends.

## 2026-10-12 — Mac session (owner's MacBook Air M5, macOS 27.0.1, branch `claude/elegant-germain-dec7cb`, on top of `claude/friendly-planck-ygkqn4`)
**Solved:** the pre-existing `dev/test_sf_symbols.py` failure ("a symbol AppKit does not know is drawn by the fallback"). The test was wrong, not `saghi/ui/sf_symbols.py`, and not macOS 27.

**Cause:** section 3 set `sys.platform` back to the real `darwin` between its fake-AppKit blocks. On a Mac with pyobjc, the drawn reference `ref` (used by 3f-3j) was therefore rendered by the real AppKit (Apple's filled hourglass), while the value under test was the pure-Python drawing. 3k ("never native off macOS") and the check after it would have failed next for the same reason. It only ever passed on Linux / without pyobjc. Checked: the real native hourglass on macOS 27 renders correctly (29x44 glyph, centred, coloured).

**Fix (test only):** the whole test now runs as `linux`; `fake_macos()` still switches to `darwin` inside each fake block. Result: `ALL PASSED (177 checks)` with the installed app's Python.

**Separate small cleanup in `sf_symbols.py`:** `_cg_image` imports Quartz so pyobjc 12 returns a real `CGImageRef` instead of a raw `objc.PyObjCPointer` (deprecated, printed an `ObjCPointerWarning` on every native icon render). Same pixels; warning gone.

**Other tests (installed app's Python):** all `dev/test_*.py` pass, except: `test_e2e_dictation.py` not run (needs speakers + quiet room, see below); `test_dictation_flow.py` and `test_gui_filejob.py` skip without a model path (as before); `test_gui_apiserver.py` fails its last check ("port 17865 is released after quitting") while the installed Saghi app is running, because the app holds that fixed port. With Saghi quit it passes (6 checks).

**Next:** the open items of the entry below still stand (quiet-room e2e run, release v0.2.1, "opening mic" hint).

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

**Tests:** new `dev/test_mic_devices.py` (39 checks, fake PortAudio: hot-plug, refresh guard, Settings list, error text, slow mic). New `dev/test_e2e_dictation.py` (real hotkey state machine -> real Recorder on a real mic -> real model -> text, with the Mac voice through the speakers). It ran end to end on the M5 (mic heard the voice, model loaded and returned text, history and clipboard filled) but the transcript was wrong because the speakers were at 6% in a loud room (room rms 0.02-0.05). The test now checks volume >= 50% and room noise first and prints "SKIPPED (ROOM/VOLUME)" (exit 0) when that is the problem, so red always means a Saghi fault. Recorder also counts a stream as open from before the (slow) open, so a Settings refresh can never reset PortAudio mid-open. **Not yet passed in a quiet room** -- owner asked to run it later. All other `dev/test_*.py` pass on the Mac except `test_sf_symbols.py`, which already fails on unchanged code (not related).

**Installed on the owner's Mac (14:23):** the branch code (`283ec02`) was copied into `~/Applications/Saghi.app/Contents/Resources/app/saghi/` (requirements unchanged, same sha256; model and venv untouched) and the old 0.2.0 code removed (recoverable from tag `v0.2.0`). `_version.py` still says 0.2.0 on purpose, so the in-app updater will still offer the real v0.2.1. Installed app started cleanly. The owner will test it over the next two days.

**Next:**
- Owner's report from two days of real use.
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
