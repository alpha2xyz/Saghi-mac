# GGUF engine: results (2026-11-18)

Branch `claude/gguf-engine-v2`. Same model (Cohere Transcribe Arabic 07-2026), quantized to GGUF and run by
[transcribe.cpp](https://github.com/handy-computer/transcribe.cpp) (MIT, commit `adfc502`) instead of PyTorch.
GGUF files: `handy-computer/cohere-transcribe-arabic-07-2026-gguf`, revision `715cbe09ca9b60fc1e497db1e4478fa9fcf4ac21`.
Measured on the owner's Mac (Apple silicon, 32 GB), Metal backend.

**Recommendation: make `Q6_K` (1.97 GB, about 2.2 GB in memory) the default GGUF quant.** Every quant meets the WER targets, but
on long real speech through the app's file-job path Q6_K stays as close to PyTorch as Q8_0 (4.2% on clean speech), while Q4_K_M
drifts about twice as far (7.6%). Q4_K_M's better SADA score is 3 words on a 180-word set: noise. Q4_K_M (1.56 GB) remains an
option if size matters more than matching today's text.

## How it works
- `saghi/gguf_engine.py`: no torch. Calls `transcribe-cli` as a subprocess. With the weights memory-mapped, one call costs about 0.3 s
  including the model load, so there is no resident model.
- Long audio is split **exactly** like the PyTorch path does today: 35 s windows, cut at the quietest 0.1 s block in the last 5 s,
  no split up to 30 s. The test checks the cut points are identical to transformers' own splitter. All chunks of one call go to one
  `--batch` run (one model load).
- Why the split is required: transcribe.cpp does not chunk. A single 90 s clip starts repeating after about 45 s. Probe on the same clip:
  30 s and 35 s and 40 s slices are clean, 45 s already drops words.
- `<hesitation>` markers are stripped.
- transcribe.cpp has a repetition guard. On music, laughter or a stuck phrase it stops decoding and returns the text so far with an
  `output repetition` error. The engine keeps that text and retries the chunk once as two halves (cut at the quietest point near the
  middle); the halves win if they give more words. Any other error raises: the engine never returns empty text silently.
- Opt-in, default unchanged: setting `"engine": "gguf"` in `settings.json` (or `SAGHI_ENGINE=gguf`, or `cli.py --engine gguf`).
  Paths: `SAGHI_GGUF_CLI` / `SAGHI_GGUF_MODEL` / setting `gguf_model`, else `Saghi.app/Contents/Resources/bin/transcribe-cli` and
  `Resources/model-gguf/*.gguf` (not bundled yet).

## Accuracy (word error rate, lower is better)
Reference set: 18 clips with known text (SADA Saudi speech 180 words, Common Voice MSA 47 words, synthetic Saudi 48 words).
The sets are small: one word on SADA is 0.6 points, so differences between quants are mostly noise.

| Engine | File | SADA (Saudi) | MSA | Synthetic Saudi | 90 s clip |
|---|---|---|---|---|---|
| PyTorch (today) | 3.8 GB | 31.7% | 4.3% | 8.3% | 241 words |
| GGUF Q8_0 | 2.41 GB | 31.7% | 4.3% | 8.3% | 242 words, 0.8% from PyTorch |
| GGUF Q6_K | 1.97 GB | 31.7% | 4.3% | 8.3% | 242 words, 0.8% |
| GGUF Q5_K_M | 1.77 GB | 30.0% | 4.3% | 8.3% | 242 words, 0.8% |
| GGUF Q4_K_M | 1.56 GB | 26.7% | 4.3% | 8.3% | 242 words, 2.9% |

No quant loops on the 90 s clip (most repeated 4-word phrase: 3 times, same as PyTorch).

**Real set** (14 private clips: 9 voice notes, 5 meeting cuts, 5.3 min, 599 words). No reference text, so scored against
PyTorch Saghi's own output ("how far from today's Saghi"):

| Quant | Words different from PyTorch | Files identical |
|---|---|---|
| Q8_0 | 0.8% | 11 / 14 |
| Q6_K | 1.5% | 8 / 14 |
| Q5_K_M | 2.5% | 6 / 14 |
| Q4_K_M | 1.8% | 6 / 14 |

## Long recording (20.8 min)
Two public SADA episodes joined (Saudi speech with music intros), one `transcribe()` call, 39 chunks.

| Engine | Time | Words | Different from PyTorch | Max 4-word repeat |
|---|---|---|---|---|
| PyTorch | 302 s (+9.6 s load) | 1497 | - | 10 |
| Q8_0 | 45 s | 1487 | 4.2% | 3 |
| Q6_K | 33 s | 1494 | 3.4% | 6 |
| Q5_K_M | 29 s | 1515 | 5.0% | 8 |
| Q4_K_M | 27 s | 1496 | 5.5% | 5 |

The first run raised on the repetition guard; that is what led to the keep-and-retry rule above. After it, all quants finished
with no loop. Guard hits on this file: Q8_0 4 of 39 chunks, Q6_K / Q5_K_M / Q4_K_M 3 each. Most are the music intro; one is real
speech (21 words before the guard stopped it), recovered by the half-split retry. The reference and real sets had **zero** guard
hits (the first benchmark ran with the raise-on-error code and passed).

**The app's real long-file path** (`FileJob`: silence-based segments of 20 s or less, one call each; 418 segments) on the same
file, every engine through the same segments, so they compare segment by segment:

| Engine | Time | Segments identical to PyTorch | Episode 1 speech | Episode 2 speech | Music intros |
|---|---|---|---|---|---|
| PyTorch | 242 s | - | - | - | - |
| Q8_0 | not timed | 359 / 418 | 13.1% | 4.2% | 7-73% |
| Q6_K | not timed | 352 / 418 | 13.9% | 4.2% | 7-77% |
| Q4_K_M | 74 s | 323 / 418 | 18.8% | 7.6% | 10-73% |

(Percent = words different from PyTorch. Episode 1 is noisy drama, episode 2 clean talk; music intros are noise for every engine:
PyTorch's own whole-file and file-job texts differ by 39% on this file, mostly there.) File jobs take 74 s instead of 27 s because
each of the 418 segments is its own process (~0.15 s each); batching segments per 60 s read block would close most of that gap.

## Memory and speed
- **GGUF:** `footprint` sampled every second during the 20-minute run: at most **0.24 GB dirty** for every quant. The weights are a
  clean memory-mapped file on top. Real cost = file size + about 0.25 GB: **Q4 about 1.8 GB, Q8 about 2.7 GB**.
- **PyTorch:** footprint 21 GB during the 20-minute file, 14 GB peak on the short clips (earlier experiment), 9.3 GB when loaded
  and idle.
- **Speed, short clips** (18 clips, 142.6 s of audio, one process per clip): GGUF about 6 s in all (0.33 s per clip including load),
  PyTorch 13.8 s plus a 7.2 s model load.
- **Speed, real set** (5.3 min): GGUF 8.7 to 9.4 s, PyTorch 37.8 s.

## Custom vocabulary
- transcribe.cpp **ignores** `--vocabulary` and `--prompt` for this model (it logs "model 'cohere_asr' does not support vocabulary";
  `--prefix` is an error). So names cannot bias the decoder.
- Built instead: `saghi/vocabulary.py`, a whole-word post-correction from `vocabulary.txt` in the data dir (never in the repo).
- Measured with a 3-name file (the brand name, «منصة», a vendor name) on Q4: **WER unchanged on every group.** The model already
  writes these names correctly; the file only added a diacritic or changed ه to ة in 4 places. One change was **wrong**: in a meeting
  the same letters meant an ordinary Arabic word, and the rule rewrote it to the brand spelling.
- Conclusion: keep the mechanism for names that can never be ordinary words. Do not add a rule for a brand name that is also a
  common word.

## Reproduce
```
PYTHONPATH=. python3 dev/test_gguf_engine.py
PYTHONPATH=. python3 dev/bench_gguf.py --ref-dir REF --out OUT --model Q4_K_M=/path/q4.gguf [--real-dir ... --pytorch-real ...]
```
