# Privacy

Saghi-mac turns your speech into text on your own Mac. By default nothing leaves your Mac.

## What stays on your Mac

Everything below lives in `~/Library/Application Support/Saghi/`:

- **Audio.** Your voice is turned into text on your Mac by the bundled model. Recordings are not kept unless you turn on "save recordings" in Settings (off by default).
- **History.** The text of each dictation and file job is saved so you can find it again. Settings let you delete it after 7, 30 or 90 days, or clear it at any time.
- **Settings** and a **log file** (`saghi.log`) that helps fix problems. The log holds timings and sound levels, not your words.

The local API listens on `127.0.0.1` only, so other computers can't reach it.

## What can leave your Mac (only if you turn it on)

- **Rephrase with OpenRouter** (off by default). When on, the *text* of a dictation and your instructions are sent to [OpenRouter](https://openrouter.ai) and the model you chose. Audio is never sent. Your OpenRouter key is kept in the macOS Keychain.
- **Update checks.** "Check for updates" (or the daily automatic check, off by default) asks GitHub for the list of Saghi-mac releases. Nothing about you or your dictations is sent.

## Contact

Open an issue on this repository.
