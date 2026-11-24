#!/usr/bin/env python3
"""
Tests for the GGUF engine (saghi/gguf_engine.py), the engine factory
(saghi/engines.py), the engine settings and the vocabulary post-correction.
No model and no transcribe.cpp needed: a fake `transcribe-cli` (a small
Python script) stands in for the real binary.

    PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) python3 dev/test_gguf_engine.py

Optional real run (the owner's Mac): set SAGHI_GGUF_CLI, SAGHI_GGUF_MODEL and
SAGHI_TEST_WAV to also transcribe one real file through the real binary.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SAGHI_DATA_DIR", tempfile.mkdtemp(prefix="saghi-test-"))

import numpy as np  # noqa: E402

from saghi import settings  # noqa: E402
from saghi.engines import engine_choice, make_engine  # noqa: E402
from saghi.gguf_engine import GgufEngine, GgufEngineError, split_like_processor, strip_hesitation  # noqa: E402
from saghi.vocabulary import Vocabulary  # noqa: E402

SR = 16000
checks = 0


def ok(cond, msg):
    global checks
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    checks += 1


def noise(sec, amp=0.3, seed=0):
    return (np.random.default_rng(seed).standard_normal(int(sec * SR)) * amp).astype(np.float32)


# ---- 1. chunking: same rule as the transformers feature extractor ----
ok(split_like_processor(np.zeros(0, np.float32)) == [], "empty -> no chunks")
ok(split_like_processor(noise(10)) == [(0, 10 * SR)], "10 s -> one chunk")
ok(split_like_processor(noise(30)) == [(0, 30 * SR)], "30 s (fast path) -> one chunk")
ok(split_like_processor(noise(33)) == [(0, 33 * SR)], "33 s <= 35 s window -> one chunk")
a = noise(90)
quiet_at = 33 * SR
a[quiet_at : quiet_at + 3200] = 0.0  # a pause inside the last 5 s of window 1
spans = split_like_processor(a)
ok(len(spans) == 3, f"90 s -> 3 chunks, got {len(spans)}")
ok(spans[0][0] == 0 and spans[-1][1] == len(a), "chunks cover start to end")
ok(all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1)), "chunks are contiguous")
ok(all(b - s <= 35 * SR for s, b in spans), "no chunk longer than 35 s")
ok(quiet_at <= spans[0][1] <= quiet_at + 3200, f"first cut lands in the pause ({spans[0][1] / SR:.2f}s)")
try:
    import torch
    from transformers.models.cohere_asr.feature_extraction_cohere_asr import CohereAsrFeatureExtractor

    fe = CohereAsrFeatureExtractor.__new__(CohereAsrFeatureExtractor)
    fe.max_audio_clip_s, fe.overlap_chunk_second, fe.min_energy_window_samples, fe.sampling_rate = 35.0, 5.0, 1600, SR
    ref = fe._split_audio_chunks_energy(torch.from_numpy(a))
    ok([len(c) for c in ref] == [b - s for s, b in spans], "identical cut points to transformers")
    print("  (compared with transformers' own splitter)")
except Exception:
    pass

# ---- 2. hesitation markers ----
ok(strip_hesitation("هلا <hesitation> والله") == "هلا والله", "hesitation stripped mid-text")
ok(strip_hesitation("<hesitation> هلا<hesitation>") == "هلا", "hesitation stripped at edges")

# ---- 3. vocabulary post-correction ----
v = Vocabulary.parse("# names\nمنصة\nكتاب => كِتاب\nسلمى\n")
ok(v.apply("على المنصه الحين") == "على المنصة الحين", "prefix ال kept, spelling fixed")
ok(v.apply("قرأت كتاب") == "قرأت كِتاب", "explicit rule")
ok(v.apply("ومنصه") == "ومنصة", "prefix و kept")
ok(v.apply("مكتبة") == "مكتبة", "no partial-word match")
ok(Vocabulary.parse("").apply("نص") == "نص", "empty file -> unchanged")
ok(Vocabulary.load("/nonexistent/vocab.txt").rules == {}, "missing file -> no rules")

# ---- 4. settings + factory ----
s = settings._coerce({"engine": "bogus", "gguf_model": 5})
ok(s.engine == "gguf" and s.gguf_model == "", "bad engine values fall back to defaults")
ok(settings.Settings().engine == "gguf", "default engine is gguf")
ok(settings._coerce({"engine": "gguf"}).engine == "gguf", "gguf accepted")
os.environ.pop("SAGHI_ENGINE", None)
ok(engine_choice() == "gguf", "no env, default settings -> gguf")
ok(engine_choice("gguf") == "gguf", "explicit choice wins")
os.environ["SAGHI_ENGINE"] = "gguf"
ok(engine_choice() == "gguf", "SAGHI_ENGINE=gguf honoured")
ok(isinstance(make_engine("/nowhere"), GgufEngine), "factory builds GgufEngine")
os.environ.pop("SAGHI_ENGINE")
ok(isinstance(make_engine("/nowhere"), GgufEngine), "factory builds GgufEngine by default")
os.environ["SAGHI_ENGINE"] = "pytorch"
ok(type(make_engine("/nowhere")).__name__ == "SaghiEngine", "SAGHI_ENGINE=pytorch still builds SaghiEngine")
os.environ.pop("SAGHI_ENGINE")

# ---- 4b. variants: Fast = Q6_K, Full = Q8_0 ----
from saghi import engines, gguf_engine  # noqa: E402

ok(settings.Settings().gguf_variant == "fast", "default gguf_variant is fast")
ok(settings._coerce({"gguf_variant": "full"}).gguf_variant == "full", "full accepted")
ok(settings._coerce({"gguf_variant": "bogus"}).gguf_variant == "fast", "bad gguf_variant -> fast")
ok(settings._coerce({"gguf_variant": 3}).gguf_variant == "fast", "non-string gguf_variant -> fast")

_real_res = gguf_engine._APP_RES
_real_pa = engines.pytorch_available
os.environ.pop("SAGHI_GGUF_MODEL", None)
with tempfile.TemporaryDirectory() as td:
    res = Path(td)
    (res / "model-gguf").mkdir()
    gguf_engine._APP_RES = res
    ok(gguf_engine.find_model() is None, "no bundled file -> None")
    ok(not gguf_engine.variant_available("fast") and not gguf_engine.variant_available("full"), "nothing available")
    q6 = res / "model-gguf" / "m-Q6_K.gguf"
    q8 = res / "model-gguf" / "m-Q8_0.gguf"
    q6.write_bytes(b"x")
    ok(gguf_engine.find_model(variant="full") == q6, "Q8 missing -> falls back to the Q6 file")
    ok(gguf_engine.variant_available("fast") and not gguf_engine.variant_available("full"), "only fast available")
    q8.write_bytes(b"x")
    ok(gguf_engine.find_model(variant="fast") == q6, "fast -> Q6_K")
    ok(gguf_engine.find_model(variant="full") == q8, "full -> Q8_0")
    ok(gguf_engine.find_model() == q6, "no variant -> fast (Q6_K)")
    ok(gguf_engine.variant_available("full"), "full available with the Q8 file")
    ok(gguf_engine.find_model("/x/own.gguf", "full") == Path("/x/own.gguf"), "explicit path wins over variant")
    q6.unlink()
    ok(gguf_engine.find_model(variant="fast") == q8, "Q6 missing -> falls back to the Q8 file")

    # make_engine passes the variant from settings
    q6.write_bytes(b"x")
    engines.pytorch_available = lambda: False
    settings.save(settings.Settings(engine="gguf", gguf_variant="full"))
    ok(make_engine("/nowhere")._model_arg == str(q8), "factory: gguf_variant full -> Q8_0")
    settings.save(settings.Settings(engine="gguf", gguf_variant="fast"))
    ok(make_engine("/nowhere")._model_arg == str(q6), "factory: gguf_variant fast -> Q6_K")
    settings.save(settings.Settings(engine="pytorch", gguf_variant="fast"))
    ok(make_engine("/nowhere")._model_arg == str(q8), "factory: saved pytorch, no folder -> Full (Q8_0)")
    ok(settings.load().engine == "pytorch", "migration does not rewrite the settings file")
    settings.save(settings.Settings(engine="gguf", gguf_model="/x/own.gguf", gguf_variant="full"))
    ok(make_engine("/nowhere")._model_arg == "/x/own.gguf", "factory: explicit gguf_model wins")
    settings.save(settings.Settings())
gguf_engine._APP_RES = _real_res
engines.pytorch_available = _real_pa

# ---- 5. engine against a fake transcribe-cli ----
td = Path(tempfile.mkdtemp(prefix="saghi-fakecli-"))
log = td / "calls.jsonl"
fake = td / "transcribe-cli"
fake.write_text(f"""#!{sys.executable}
import json, sys, soundfile as sf
args = sys.argv[1:]
if "FAIL" in open(args[args.index("-m") + 1]).read():
    sys.stderr.write("boom"); sys.exit(3)
files = [l.strip() for l in open(args[args.index("--batch") + 1]) if l.strip()]
open({str(log)!r}, "a").write(json.dumps({{"args": args, "n": len(files)}}) + "\\n")
print(json.dumps({{"type": "batch_header", "load_ms": 1}}))
rep = "REP" in open(args[args.index("-m") + 1]).read()
for i, f in enumerate(files):
    d = sf.info(f).frames / 16000
    if rep and "/chunk" in f:
        print(json.dumps({{"file": f, "text": "قص", "error": "output repetition: decode stopped"}}))
    elif rep:
        print(json.dumps({{"file": f, "text": f"نصف{{i}} كامل"}}))
    else:
        print(json.dumps({{"file": f, "text": f"جزء{{i}} <hesitation> {{d:.0f}}ث"}}))
""")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
model = td / "m.gguf"
model.write_text("ok")
vocab = td / "vocab.txt"
vocab.write_text("جزء0 => الأول\n")

eng = GgufEngine(model_path=model, cli_path=fake, vocabulary_path=vocab)
ok(not eng.is_loaded, "not loaded before first use")
r = eng.transcribe_array(noise(10), SR, cleanup_level="none")
ok(eng.is_loaded and eng.stack_path == "gguf", "loaded lazily, stack gguf")
ok(r.raw_text == "الأول 10ث", f"short clip text + hesitation + vocab: {r.raw_text!r}")
ok(abs(r.duration_s - 10) < 0.01, "duration reported")
r = eng.transcribe_array(np.concatenate([noise(90), np.zeros(0, np.float32)]), SR, cleanup_level="none")
calls = [json.loads(l) for l in log.read_text().splitlines()]
ok(calls[-1]["n"] == 3, "90 s -> one batch call with 3 chunks")
ok(r.raw_text.startswith("الأول") and "جزء1" in r.raw_text and "جزء2" in r.raw_text, f"chunks joined in order: {r.raw_text!r}")
ok("<hesitation>" not in r.raw_text, "no hesitation left")
ok("-l" in calls[-1]["args"] and calls[-1]["args"][calls[-1]["args"].index("-l") + 1] == "ar", "language passed")
stereo44 = np.stack([noise(5, seed=1), noise(5, seed=2)], axis=1)
r = eng.transcribe_array(stereo44, 44100, cleanup_level="none")
ok(abs(r.duration_s - 5 * SR / 44100) < 0.05, "stereo 44.1 kHz resampled to 16 kHz mono")
wav = td / "in.wav"
import soundfile as sf  # noqa: E402

sf.write(str(wav), noise(4), SR)
ok(eng.transcribe(wav).text.startswith("الأول"), "file path API works")

bad = GgufEngine(model_path=td / "missing.gguf", cli_path=fake)
try:
    bad.load()
    ok(False, "missing model must raise")
except GgufEngineError:
    ok(True, "")
failing_model = td / "fail.gguf"
failing_model.write_text("FAIL")
try:
    GgufEngine(model_path=failing_model, cli_path=fake).transcribe_array(noise(2), SR)
    ok(False, "non-zero exit must raise, never return empty text")
except GgufEngineError as exc:
    ok("exited 3" in str(exc), "non-zero exit raises with the code")

rep_model = td / "rep.gguf"
rep_model.write_text("REP")
r = GgufEngine(model_path=rep_model, cli_path=fake).transcribe_array(noise(20), SR, cleanup_level="none")
ok(r.raw_text == "نصف0 كامل نصف1 كامل", f"repetition-guard chunk retried as halves: {r.raw_text!r}")
r = GgufEngine(model_path=rep_model, cli_path=fake).transcribe_array(noise(5), SR, cleanup_level="none")
ok(r.raw_text == "قص", "short repetition chunk keeps its partial text, no exception")

# ---- 6. optional: real binary + real model ----
real_wav = os.environ.get("SAGHI_TEST_WAV")
if os.environ.get("SAGHI_GGUF_CLI") and os.environ.get("SAGHI_GGUF_MODEL") and real_wav:
    real = GgufEngine(vocabulary_path=td / "none.txt")
    out = real.transcribe(real_wav)
    ok(len(out.text.split()) > 0, "real model returned text")
    print(f"  real: {out.inference_s:.2f}s for {out.duration_s:.1f}s audio -> {len(out.text.split())} words")

print(f"ALL PASSED ({checks} checks)")
