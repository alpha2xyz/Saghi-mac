# Status

Newest first. Every session adds an entry before it ends.

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
