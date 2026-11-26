# Status

Newest first. Every session adds an entry before it ends.

## 2026-11-27 - freeze on stopping a recording + menu-bar icon back (owner's Mac, branch `fix/menubar-icon-and-stop-freeze`)
**Area:** `saghi/recorder.py`, `saghi/ui/app.py`, `saghi/ui/tray.py`, `saghi/login_item.py`, `packaging/launcher/`, `packaging/install-saghi.command`, `.github/workflows/build-app.yml`, `dev/test_recorder_stop_timeout.py`, `dev/test_new_options.py`.

- **Freeze:** the app hung for good after a dictation (2026-11-26 and 2026-11-27). The watchdog stacks show the UI thread in `Recorder._stop_stream` -> `stream.stop()` -> `Pa_StopStream` -> CoreAudio `AudioOutputUnitStop`, waiting on a CoreAudio mutex. Fix: the stream is stopped and closed on a helper thread; the UI thread waits at most 1.5 s and then carries on with the audio it already has. The stuck stream stays counted as open until its thread ends, so a device refresh never terminates PortAudio under it. New test `test_recorder_stop_timeout.py` (9 checks, fake streams).
- **Menu-bar icon:** removed with the notch UI (#6); the owner wants it back and no Dock icon. `SaghiTray` is created again in `build()`. Resting glyph is now `waveform` (was `mic`). The tray opens the window through the app's show function (brings the app forward). The update notice stays the dialog only (`connect_updates(notify=False)`). Dock icon stays hidden (Accessory policy, unchanged).
- **Why the icon was hidden, and the fix:** macOS 27 hides a status item whose process is not a real app. Saghi.app's executable was a bash script that `exec`s `venv/bin/python3.12`, so the process was a bare python3.12; macOS never listed Saghi under System Settings > Menu Bar > Allow in the Menu Bar and drew nothing (the tray geometry logged `QRect(0, 956, 23, 0)`). A bare QSystemTrayIcon from a terminal was hidden the same way. Fix: `packaging/launcher/saghi_launcher.c`, a native executable that runs Python inside Saghi.app's own process (PyConfig with the venv's interpreter path, `-m saghi.ui.app`). CI compiles it (`Saghi-Mac-Portable/Saghi-launcher`), the installer copies it to `Contents/MacOS/Saghi` (shell script stays as fallback), and the LaunchAgent now starts `Contents/MacOS/Saghi` instead of bare python (`login_item.native_launcher`). On the owner's Mac: icon visible at x=1060 (screenshot), also when started through launchd. A spawn-and-wait launcher was tried first and did not help. Not the menu-bar hider app.
- **Permissions:** each ad-hoc re-sign gives the app a new identity, so Accessibility (hotkey + auto-paste) and possibly Microphone must be granted again. Removing and re-adding the row did not take; what worked: `tccutil reset Accessibility io.github.alpha2xyz.saghi-mac`, relaunch, the owner switched Saghi on in the prompt, relaunch again -> no "not trusted" warning. Input Monitoring (the shortcut's listen-only event tap) and Microphone were stale the same way: `tccutil reset ListenEvent|Microphone io.github.alpha2xyz.saghi-mac`. Saghi never asked for Input Monitoring itself (it only worked because an older copy had it), so `app._request_input_monitoring()` now calls `CGRequestListenEventAccess()` on start-up when it is missing. After that a simulated Left Control hold started a recording. Do not re-sign the owner's app again without need.
- **Menu (owner's ask, same day):** status line, hotkey hint, «فتح إعدادات صاغي», «إنهاء». Removed: engine line, «إظهار التبويب العائم دائمًا», «تفعيل الإملاء», «التحقق من التحديثات…» (updates keep their own dialog). A click on the icon only opens the menu. The window used to open on every app activation (the icon click activates the app); now it opens only on the "reopen" Apple event (Saghi.app opened again), registered 0.5 s after start because AppKit replaces handlers set before launch finishes. Verified: second `open` logs "Saghi.app opened again".
- **Existing installs** get the native launcher only with a full install of the next release (light updates replace `app/saghi` only).
- **Tests:** test_new_options 116, test_recorder_stop_timeout 9, test_mic_devices 54, test_gui_smoke 120, test_status_ui 145, test_tap_toggle 31, test_hotkey_custom 142 pass. test_gui_apiserver fails the same way on `main` while the installed app holds the API port (not from this change).
- **Installed in place:** this diff patched onto the installed 0.3.2 bundle (which also carries the unmerged punctuation and quit commits from `claude/both-models`); backup in `~/.saghi-work/` outside the repo. Bundle re-signed ad-hoc (its Info.plist had been edited by the updater, leaving the signature invalid); re-signing may reset the Microphone/Accessibility permissions, so check the next dictation.
- **Merged** as PR #11 on 2026-11-27 after the owner's dictation test (log: 3.08 s captured, peak 0.26, transcribed in 0.6 s). Dry-run build on `main` (publish off) passed, including the new launcher step and the Mach-O check in the install smoke test. Nothing published; v0.3.2 is still the latest release.
- **Punctuation + quit fixes** from `claude/both-models` reached `main` via PR #12 (cherry-picked, merged 2026-11-27). `main` now equals the owner's installed app code; `claude/both-models` can be deleted. Next: release 0.3.3 (owner's decision) so other installs get the native launcher with a full install.

## 2026-11-25 - Update installed but never loaded: quit was cancelled (owner's Mac, branch `claude/both-models`)
**Area:** `saghi/ui/app.py` (`_quit`, `main`), `dev/test_update_restart.py`.

- Owner's report: the app offered an update; pressing update/restart (several times) and quitting changed nothing.
- Log + `sample` of the live process: the light update to 0.3.2 installed twice, "Quitting Saghi" was logged, but the main Qt loop kept running; the 20:33 process never ended, so the new code never loaded and every reopen just showed the old window.
- Cause: `_quit()` used `QApplication.quit()`, which in Qt 6 first asks every window (and macOS) to close; one refusal silently cancels it. Fix: `app.exit(0)`, plus a 5 s daemon timer after `exec()` returns that hard-exits, so the relaunch (which waits for the PID to end) always fires.
- Why an update was offered at all: the in-place punctuation install had no `_version.py` (source tree), so the app reported 0.0.0. The update then overwrote the punctuation fix with plain 0.3.2. **In-place installs must keep the installed `_version.py`** (rsync with `--exclude=_version.py`).
- Tests: new `test_update_restart` (fails before the fix with exit 99, passes after), `test_gui_apiserver` 6/6, `test_finish_dictation` 14/14.
- Installed in place again (repo code incl. punctuation fix, version file kept at 0.3.2; backup `Resources/app/saghi.before-quitfix`), old process stopped, Saghi reopened. Owner still to confirm the quit button now closes the app.

## 2026-11-24 - Auto full stop + space after each dictation (owner's Mac, branch `claude/both-models`)
**Area:** `saghi/cleanup.py` (`finish_dictation`), `saghi/ui/dictation.py`, `dev/test_finish_dictation.py`.

- Owner's report: no full stop at the end of a take, and the next take glues onto the previous one, so speaking sentence by sentence was slow.
- Fix: before pasting a live dictation, Saghi adds «.» when the take ends without punctuation («؟» when it opens with a question word such as هل/كيف/وش/ليش; "?" for English), then one trailing space. History keeps the text without the extra space. File jobs are unchanged.
- Tests: test_finish_dictation 14/14, test_cleanup 20/20.
- Owner's decision: the owner's name and other words NOT added to `vocabulary.txt` for now. The ط/ظ/ض/ص mix-ups come from the model; later option is the OpenRouter text polish.
- 2026-11-25: copied in place into the owner's installed Saghi.app (backup in `~/.saghi-work/backup-before-punct-2026-11-25/`), re-signed ad-hoc, restarted; app runs. Owner wants to batch more changes before the next release. Not yet tried by voice.

## 2026-11-24 - § key crash + «آآ» left in the text (owner's Mac, branch `claude/both-models`)
**Area:** `saghi/hotkey.py`, `saghi/cleanup.py`, `dev/test_cleanup.py`.

- **Crash:** choosing the key left of 1 (vk:10) as the shortcut quit Saghi; reopening worked. Three crash reports (SIGTRAP, `dispatch_assert_queue` in `TSMGetInputSourceProperty`): pynput's listener thread reads the keyboard layout when it starts, and recent macOS kills the app when that runs off the main thread. It hit when the listener restarted from `set_combo` (modifier-only -> key combo). Fix: `_listener_class` reads the layout on the caller's (main) thread and gives it to the listener. Hotkey tests pass; a start/switch/stop cycle runs clean. The crash itself is not reproducible on demand, so the owner must confirm by setting the § key again.
- **Fillers:** the GGUF model writes the short «آآ» (2 letters), which the 3+ rule missed, and leaves «فـ» before it. Cleanup now drops «آآ»/«آآه» and glues a lone «فـ»/«وـ» onto the next word. 20/20 cleanup tests.
- Also seen in history: «(تحذير حرق)» and «@@@فراغ» on near-silent clips (model hallucination, not handled yet).
- Ships as 0.3.2 (0.3.1 was already published on 2026-11-24 09:37 UTC).

## 2026-11-24 - v0.3.0 published and verified

- Released v0.3.0 (publish run on main). Assets: installer, light update zip, bundle parts (2.02 GB total), SHA256SUMS.
- The updater (`updater.check('0.2.1')`) sees v0.3.0.
- Fresh install on the owner's Mac from the release: app version 0.3.0, engine gguf, installed size 2.4 GB, no PyTorch model. A generated Arabic sentence transcribed correctly.
- Next: owner tries real dictation by voice; watch for first-run feedback.

## 2026-11-24 — v0.3.0: Saghi Fast ships in the release (branch `claude/release-0.3.0`, from `main` 3e71352)
**Area:** `.github/workflows/build-app.yml`, `packaging/` (build_bundle, installer), `dev/requirements-arm64.txt`, `saghi/engines.py`, `settings.py`, `ui/home_page.py`, notices, docs.

**Owner's decision (2026-11-24):** Fast only. The PyTorch model (3.8 GB) and the torch stack are no longer shipped.

**Built:** the workflow compiles transcribe.cpp (`adfc50210a3f026a655a3e19937d90ef2bf0728c`, deployment target 13.0, static, Metal embedded; fails if anything outside `/usr/lib` and `/System/Library` is linked), fetches the Q6_K `.gguf` from Hugging Face at a pinned revision and sha256 (Apache 2.0, LICENSE/NOTICE shipped beside it), and bundles both as `Resources/bin/transcribe-cli` and `Resources/model-gguf/`. New `publish` input (default off) so a build can be tested without releasing. Default engine is `gguf`; a saved `pytorch` setting runs GGUF when no PyTorch model folder exists (so 0.2.x installs keep working after the update); the Full row is hidden then. The requirements lost torch, transformers, librosa and friends (hash changes, so 0.2.x updates through the full installer, which also deletes the old `model/` and recreates the venv if torch is in it).

**Verified:** CI dry run (publish off) green: bundle built, installer smoke test, `find_cli`/`find_model` resolve, torch absent, API health ok. Download is 2.02 GB (two parts), was 3.6 GB. Locally: tests pass in a torch-less venv (460 MB; test_gui_smoke, status_ui, new_options, home_page, gguf_engine, notch, apiserver, segmentation, cleanup); the freshly built binary transcribed a spoken Arabic sentence on Metal in 5.6 s cold.

**Not done / next:** the CI runner has no Arabic voice, so no transcription check ran there (fixed the voice match, may still skip). Release (publish on), then verify the in-app updater and a fresh install on the owner's Mac. The owner's Mac still runs from the repo source and has the trial install: reinstall or restore the LaunchAgent before the updater check.

## 2026-11-24 — 0.3.1 prepared: two GGUF models + Dock fix (owner's Mac, branch `claude/both-models`)
**Area:** `saghi/ui/app.py`, `settings.py`, `gguf_engine.py`, `engines.py`, `ui/home_page.py`, `ui/strings.py`, packaging, workflow, docs.

**Owner's reports and decisions:**
- "No text" was no real key press reaching Saghi after the shortcut changed to ctrl+cmd; a synthetic ctrl+cmd hold recorded and transcribed fine.
- Dock icon did nothing: when Saghi is started from Saghi.app, re-opening it re-activates the running process instead of starting a second copy, so `single_instance` never fired. Fix: `_install_reopen_handler` shows the window on `applicationStateChanged(Active)`, but only while dictation is idle and NSApp is really active (Qt reported "active" once when the notch island appeared mid-dictation, which popped the window and would have stolen the paste).
- Owner wants the model choice back. A first try shipped PyTorch beside GGUF (bundle ~6.3 GB; its dry-run build failed); reverted. Owner chose instead: «صاغي السريع» = Q6_K (default), «صاغي الكامل» = Q8_0, both GGUF via transcribe-cli, no torch (the 2026-11-18 measurements show every quant matching or beating PyTorch WER). New setting `gguf_variant`; a saved engine "pytorch" without the PyTorch folder runs Full; choosing a model clears an old explicit `gguf_model` path.

**Verified:** dry-run workflow (publish off) passed, bundle 4.3 GB. On the owner's Mac the installed 0.3.0 bundle was updated in place (new `saghi/`, Q8_0 cloned into `model-gguf/`, version 0.3.1, re-signed ad-hoc; backup in `~/.saghi-work/backup-before-0.3.1/`): Home page shows both models; dictation on Q8_0 and on Q6_K transcribes; two dictations with Finder in front left Finder in front; re-opening Saghi.app shows the window. Tests: test_gguf_engine 59, test_home_page 49, test_new_options 114, test_gui_smoke 123, test_status_ui 161 pass.

**Next:** owner's yes, then merge and publish 0.3.1 (workflow with publish on). Branch `claude/dock-reopen-shows-window` is superseded by this one.

## 2026-11-23 — notch UI redesign (owner's Mac, branch `claude/notch-ui`, from `claude/gguf-engine-v2` + `claude/idle-unload`)
**Area:** `saghi/ui/notch.py` (new), `ui/dictation.py`, `hotkey.py`, `ui/sounds.py` + `ui/assets/sounds/`, `ui/mainwindow.py`, `ui/home_page.py` (new), `ui/settings_page.py`, `ui/app.py`, `history.py`.

**Built (owner's request: Saghi lives in the notch, one key, a simple Apple-style window):**
- Notch island replaces the floating pill: one black shape fused to the hardware notch (size read from NSScreen; 179x32 pt on the owner's MacBook Air), opens with a spring, shows level dots that scroll left (one slot per 0.2 s; silence stays flat, height = real mic level only), then a shimmering "processing" label; closes back into the notch. Click it to open the window. Macs without a notch: the island only appears while active.
- Hotkey: tap (< 0.35 s) starts and the next press stops; or hold to talk; Esc discards (setting `tap_to_toggle`, default on).
- Sounds: Saghi's own synthesized tones (`dev/make_sounds.py`): start, stop, discard, error.
- Window: Home (time saved / words / time spoken, how to use, model choice «صاغي السريع» GGUF / «صاغي الكامل» PyTorch with restart), Configuration, History. No tray icon, no Dock icon; the app starts quietly (window only on first launch).
- Rule change: the old pill's "nothing moves in silence" contract is replaced by "the dots scroll as a clock but never grow without sound" (test_indicator_render still covers the old pill class, which stays in the repo unused).

**Verified live on the owner's Mac (branch run from source):** island pinned over the notch; tap -> Arabic sentence transcribed and pasted; Esc discarded (nothing pasted); clicking the notch opened the window; all three pages render. Tests: test_notch 35, test_tap_toggle 31, test_hotkey_logic 46, test_status_ui 161, test_gui_smoke 110, test_new_options 125, test_home_page 33 pass.

**Round 2 (same day, owner's notes):** any 1-2 key shortcut via a recorder in Settings (single key like § is kept from typing via pynput darwin_intercept; verified live: § started/stopped dictation and no § was typed); macOS-style rounded buttons and fields; Settings split into Dictation / Text / App sub-pages in an animated sidebar sub-menu; pages fade in; Home stats count up; model rows show time per minute of speech (GGUF ~3 s, PyTorch ~12 s, from the 20.8 min file-job measurements); blue "local" chip and a green ready dot (cold now reads "ready").

**Not done / next:** owner reviews by using it; Dock-less window focus and the restart button not tried live; history page buttons still old-style; release needs the GGUF branch's bundling work (transcribe-cli + model asset). Running from source rewrote the login LaunchAgent's PYTHONPATH to the repo; restore it to the bundle's `Resources/app` (or reinstall) when the trial ends.

## 2026-11-18 (later) — GGUF one-week trial on the owner's Mac (branch `claude/gguf-engine-v2`)
**Installed for the trial:** the branch's changed files copied into the installed v0.2.1 bundle (`Resources/app/saghi/`), `transcribe-cli` (transcribe.cpp `adfc502`) into `Resources/bin/`, settings `"engine": "gguf"` with `gguf_model` pointing at the local Q6_K file; bundle re-signed ad-hoc. Backup of the old code and settings in `~/.saghi-work/backup-before-gguf-trial/` (outside the repo). `_version.py` left at 0.2.1.

**Verified live:** API health reports `stack: gguf, device: metal`; Arabic clips transcribe through the running app: first call 5.6 s (cold start: Metal and the model file paging in), then 0.25-0.5 s per 6-11 s clip. Whole app footprint 196 MB (was 9.3 GB with the PyTorch model loaded).

**Mic verified live:** after the re-sign the owner ran `tccutil reset Microphone io.github.alpha2xyz.saghi-mac` and allowed the prompt; dictation works (log: 8.71 s captured, peak 0.20, transcribed in 0.3 s). Owner's first impression: "very fast", about 90% accurate on their own dictation.

**Roll back:** quit Saghi, copy the backup `app/` over `Resources/app/`, restore `settings.json` (or set `"engine": "pytorch"`), re-sign ad-hoc.

**Next:** owner uses it for a week (to 2026-11-25). If no difference is noticed: release with Q6_K bundled as the only engine (option 1 in the owner's page).

## 2026-11-18 — GGUF engine (owner's Mac, branch `claude/gguf-engine-v2`, from `main`)
**Area:** `saghi/gguf_engine.py`, `saghi/engines.py`, `saghi/vocabulary.py`, the `engine` / `gguf_model` settings, `dev/test_gguf_engine.py`, `dev/bench_gguf.py`. Full results: `docs/GGUF_RESULTS.md`.

**Built:** an opt-in second engine: the same Cohere Arabic model as GGUF on transcribe.cpp (subprocess, no torch). Long audio is split exactly like the PyTorch path (cut points checked identical). `<hesitation>` stripped; chunks stopped by transcribe.cpp's repetition guard keep their text and are retried once as two halves. Default engine stays PyTorch (`"engine": "gguf"` in settings.json, `SAGHI_ENGINE=gguf` or `cli.py --engine gguf` to try it).

**Measured (Q8/Q6/Q5/Q4):** every quant matches or beats PyTorch WER (SADA 31.7% → 26.7-31.7%, MSA 4.3%, synthetic 8.3%); the 90 s clip is complete (242 words, no loop); real set within 0.8-2.5% of PyTorch's text; a 20.8 min file in 27-45 s (PyTorch 302 s) with no loop. Memory: file size + ~0.25 GB (Q4 ~1.8 GB) vs PyTorch 9-21 GB. Custom vocabulary: transcribe.cpp ignores `--vocabulary` for this model, so it is a post-correction; it changed no WER and made one wrong change (a brand name that is also a common word).

**Recommendation:** Q6_K (1.97 GB, ~2.2 GB in memory) as the GGUF default: on long real speech through the app's file-job path it tracks PyTorch like Q8_0 (4.2% on clean speech), Q4_K_M drifts twice as far (7.6%). File jobs with GGUF: 74 s vs PyTorch 242 s for 20.8 min (one process per segment; batching per read block would speed it up).

**Tests:** `test_gguf_engine.py` (40 checks + optional real run), test_cleanup, test_segmentation, test_new_options pass.

**Not done / next:** the GUI was not launched with GGUF (GUI smoke/status tests pass). transcribe-cli and the .gguf are not bundled in the app yet (needs the build workflow to compile transcribe.cpp and a release asset for the model; owner's yes). No UI switch yet. `claude/idle-unload` (local, unmerged) also edits `engine.py`/`app.py`; merge it before or after this and re-run the tests (the GGUF engine holds no model, so idle unload does not apply to it).

## 2026-11-17 — idle model unload (owner's Mac, branch `claude/idle-unload`)
**Found:** Saghi sat idle holding a 10 GB footprint (model loaded once, never freed), which pushed the owner's 32 GB Mac into 11.6 GB compressed memory and 3.7 GB swap.

**Fix:** `SaghiEngine` now unloads the model after 600 s with no transcription (`idle_unload_s`, env `SAGHI_IDLE_UNLOAD_S` overrides; one daemon timer, re-checks `last_used` under the engine lock, so long file jobs are not cut). The next dictation reloads it lazily. `EngineStatusBridge` goes back to "cold" after an unload. New test `dev/test_idle_unload.py` (17 checks); test_status_ui (161) and test_gui_smoke (112) pass.

**Measured with the real model on mps:** footprint 9.3 GB loaded -> 0.9 GB after the idle unload; reload took 3.4 s and transcribed again. **Next:** merge, release, install on the owner's Mac.

## 2026-11-02 (later) — model comparison + long-audio cut-off fix (same branch)
**Compared** Saghi's Cohere model with Whisper large-v3-turbo (mlx) and Nemotron 3.5 Transcribe on the same 6 Arabic clips (4 short read sentences with a known text, 2 real voice notes). Word error rate on the read sentences: Cohere 8%, Whisper 15%, Nemotron 17%. Cohere was also fastest (9.5 s for all six after a 3.4 s load) and clearest on the voice notes. Decision input: no need to add another model for quality.

**Bug found and fixed:** audio over ~35 s lost everything after the first clip. The processor returns one string per 35 s clip and `_infer_array` kept only `text[0]`. Now it joins them. Verified on a 65 s clip: full text returned. This hit long dictations too (same path via `transcribe_array`).

## 2026-11-02 — Mac session (owner's Mac, branch `claude/single-instance-slim`)
**Found:** two full Saghi copies running after boot (login LaunchAgent + a manual open 17 s later; no single-instance guard). Installed app is 6.2 GB: model 3.8 GB, venv 2.3 GB, of which PySide6 is 1.2 GB. The engine is Cohere transcribe-arabic, not Whisper.

**Fix 1, one copy only:** `saghi/ui/single_instance.py` (QLocalServer named from the data dir). A second launch asks the first to show its window and exits. Verified with two real `saghi.ui.app` processes: the second exited in 0.3 s and the first logged the show request. New test `dev/test_single_instance.py`.

**Fix 2, smaller:** `pyside6` -> `pyside6_essentials` in `dev/requirements-arm64.txt` (Saghi uses only QtCore/Gui/Widgets/Network; Addons is 885 MB). The installer removes the old full PySide6 from an existing venv before installing. Checked on an APFS clone of the installed venv: 2.3 GB -> 1.4 GB; test_single_instance, test_gui_smoke (112), test_status_ui (161), test_pill (158) pass; real model transcribed an Arabic sample through the GUI worker and the CLI. (test_gui_filejob's chunk-count check needs a 23 s WAV; the sample was shorter.)

**Not removed:** librosa/numba/scipy/sklearn (~320 MB). The model's own bundled code imports librosa at load time.

**Done:** merged (PR #5) and released as **v0.2.1** (download 3.96 GB -> 3.63 GB). **Verified live 2026-11-03 on the owner's Mac:** updated to 0.2.1 in-app; app 6.2 GB -> 5.4 GB (venv 1.4 GB, Essentials only); one process; launching the bundle again logged "already running" and exited; dictations transcribe (1.4-1.9 s). **Next:** optional: try an int8/fp16 model to cut the 3.8 GB (quality check first).

## 2026-10-29 — Mac session (owner's MacBook Air, branch `claude/mac-mic-only`)
**Solved:** "Saghi never works". Since the 2026-10-12 14:23 code copy into the unsigned `Saghi.app`, every mic (built-in included) gave bit-exact silence inside the app while the same mic gave real audio to a terminal Python: the macOS Microphone grant was stale. Plus the saved mic was the owner's iPhone (Continuity), slow and silent.

**Fix:** commit `0856b68`: the built-in mic is the default; iPhone/iPad mics are hidden and never auto-chosen; a silent non-built-in mic clears the setting so the next try uses the built-in mic; silence on the built-in mic shows a clear permission message. Installed into `Saghi.app`, bundle ad-hoc signed (`codesign --force --deep -s -`), then the owner ran `tccutil reset Microphone io.github.alpha2xyz.saghi-mac` and allowed the prompt. **Verified live:** peak 0.18, text out in 0.5 s.

**Lesson:** after copying code into the bundle, re-sign it ad-hoc and reset the Microphone grant, or audio is silent.

**Tests:** test_mic_devices (54), test_status_ui (161) and the other GUI tests pass. Not run: test_e2e_dictation (quiet room).

**Next:** merge to main (owner's yes); Continuity mics with a custom name (no "iPhone" in it) are not hidden by name yet; make the release build sign ad-hoc so this does not repeat.

## 2026-10-12 — Mac session (owner's MacBook Air, macOS 27.0.1, branch `claude/elegant-germain-dec7cb`, on top of `claude/friendly-planck-ygkqn4`)
**Solved:** the pre-existing `dev/test_sf_symbols.py` failure ("a symbol AppKit does not know is drawn by the fallback"). The test was wrong, not `saghi/ui/sf_symbols.py`, and not macOS 27.

**Cause:** section 3 set `sys.platform` back to the real `darwin` between its fake-AppKit blocks. On a Mac with pyobjc, the drawn reference `ref` (used by 3f-3j) was therefore rendered by the real AppKit (Apple's filled hourglass), while the value under test was the pure-Python drawing. 3k ("never native off macOS") and the check after it would have failed next for the same reason. It only ever passed on Linux / without pyobjc. Checked: the real native hourglass on macOS 27 renders correctly (29x44 glyph, centred, coloured).

**Fix (test only):** the whole test now runs as `linux`; `fake_macos()` still switches to `darwin` inside each fake block. Result: `ALL PASSED (177 checks)` with the installed app's Python.

**Separate small cleanup in `sf_symbols.py`:** `_cg_image` imports Quartz so pyobjc 12 returns a real `CGImageRef` instead of a raw `objc.PyObjCPointer` (deprecated, printed an `ObjCPointerWarning` on every native icon render). Same pixels; warning gone.

**Other tests (installed app's Python):** all `dev/test_*.py` pass, except: `test_e2e_dictation.py` not run (needs speakers + quiet room, see below); `test_dictation_flow.py` and `test_gui_filejob.py` skip without a model path (as before); `test_gui_apiserver.py` fails its last check ("port 17865 is released after quitting") while the installed Saghi app is running, because the app holds that fixed port. With Saghi quit it passes (6 checks).

**Next:** the open items of the entry below still stand (quiet-room e2e run, release v0.2.1, "opening mic" hint).

## 2026-10-12 — Mac session (owner's MacBook Air, branch `claude/friendly-planck-ygkqn4`)
**Solved:** "since v0.2.0 no text, pill stops at 0:03, flat waveform". Not a permission problem and not the model.

**What was checked (installed v0.2.0's Python and model, branch code):**
- `dev/mic_check.py`: built-in mic OK (peak 0.27). Per device: "MacBook Air Microphone" OK, "Samsung USB C Earphones" OK, **"iPhone Microphone" = bit-exact silence** (peak 0.0).
- `saghi.cli transcribe` on a `say -v Majed` WAV: correct Arabic in 0.72 s (model load 8.8 s, MPS). The model is fine.
- App from the terminal + `saghi.log`: Settings had `"microphone": "iPhone Microphone"`. Every hold from it came back as pure silence ("تعذر الوصول إلى الميكروفون"). The watchdog caught a **5 s UI freeze inside the mic open/stop** on the iPhone mic.
- Timing per mic (open): Samsung 0.10 s, default 0.08 s, **iPhone 1.2-10.1 s** ("Audio Hardware Not Running"), then silence.
- The chosen mic is the owner's iPhone (Continuity mic). It sends silence while the iPhone is plugged into the Mac by cable; it works once the cable is out (owner confirmed).
- Separate bug: the Samsung earphones plugged in after Saghi started never appeared. PortAudio reads the device list once at `import sounddevice`.
- v0.1.1 worked because it used the default mic (owner confirmed). Nothing in 0.2.0 writes the mic setting on its own; a scroll-wheel theory was tested and ruled out (the macOS style ignores wheel on combos).

**Root cause:** the chosen mic (iPhone Continuity) opened slowly on the UI thread, freezing the whole app (pill stuck mid-count, flat wave), then delivered silence, which the app reported as a permission problem. Plus: hot-plugged mics were invisible.

**Fixes (commits `bcbcc2d`, `5894ade`):**
- `recorder.refresh_devices()` re-reads the device list (~3 ms) before every recording and whenever the Settings mic list is shown or opened; never while a stream is open.
- The mic opens on a helper thread; the UI waits at most 0.25 s, so a slow mic can no longer freeze the app. Release/Esc during a slow open are handled.
- A silent chosen mic is named in the pill («iPhone Microphone» لا يرسل صوتًا); an unplugged saved mic stays listed as "(غير متصل)".

**Verified live by the owner:** Samsung shows up after re-plugging and dictates (log: peak 0.17, text out); the iPhone mic opens in 4.3 s / 2.6 s with no freeze, the silent case shows the named message, and the iPhone mic dictates once the iPhone's cable is out.

**Tests:** new `dev/test_mic_devices.py` (39 checks, fake PortAudio: hot-plug, refresh guard, Settings list, error text, slow mic). New `dev/test_e2e_dictation.py` (real hotkey state machine -> real Recorder on a real mic -> real model -> text, with the Mac voice through the speakers). It ran end to end on the owner's Mac (mic heard the voice, model loaded and returned text, history and clipboard filled) but the transcript was wrong because the speakers were at 6% in a loud room (room rms 0.02-0.05). The test now checks volume >= 50% and room noise first and prints "SKIPPED (ROOM/VOLUME)" (exit 0) when that is the problem, so red always means a Saghi fault. Recorder also counts a stream as open from before the (slow) open, so a Settings refresh can never reset PortAudio mid-open. **Not yet passed in a quiet room** -- owner asked to run it later. All other `dev/test_*.py` pass on the Mac except `test_sf_symbols.py`, which already fails on unchanged code (not related).

**Installed on the owner's Mac (14:23):** the branch code (`283ec02`) was copied into `~/Applications/Saghi.app/Contents/Resources/app/saghi/` (requirements unchanged, same sha256; model and venv untouched) and the old 0.2.0 code removed (recoverable from tag `v0.2.0`). `_version.py` still says 0.2.0 on purpose, so the in-app updater will still offer the real v0.2.1. Installed app started cleanly. The owner will test it over the next two days.

**Merged to main (2026-10-12):** PR #3, merge commit `f7675eb`. Tests re-run on main: mic, smoke and status tests pass; `check-repo.sh --public` OK. No release yet: v0.2.1 still needs the owner's yes.

**Next:**
- Owner's report from two days of real use.
- Run `dev/test_e2e_dictation.py` once with the volume at 50%+ in a quiet room.
- Release v0.2.1 (run the build workflow) so the installed app gets the fixes. Until then the installed 0.2.0 still freezes on a slow mic: choose Samsung or the default mic in Settings.
- Look at the pre-existing `test_sf_symbols.py` failure.
- Speech said while a slow mic is still opening (iPhone: 1-10 s) is lost; an option is a short "opening mic" hint in the pill.

## 2026-10-12 — cloud session (branch `claude/friendly-planck-ygkqn4`)
**Open problem:** since v0.2.0 dictation gives no text. The pill stops at 0:03 and the waveform is flat. v0.1.1 worked on the owner's Mac.
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
