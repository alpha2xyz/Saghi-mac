#!/usr/bin/env python3
"""
Benchmark the GGUF engine (saghi/gguf_engine.py) per quant, through the
engine itself (chunking, hesitation strip, cleanup "light" like the app).

    PYTHONPATH=. python3 dev/bench_gguf.py --ref-dir REF --out OUT \
        --model Q8_0=/path/q8.gguf --model Q4_K_M=/path/q4.gguf \
        [--real-dir REAL --pytorch-real PT_REAL] [--pytorch-ref PT_REF] \
        [--vocab FILE]

REF has clips/*.wav, long/*.wav and refs.json ({"name": {"ref": "..."}}),
names grouped by prefix (sada-, cv-, fish-). REAL is a folder of WAVs
without references; PT_REAL holds the PyTorch engine's <name>.txt for them,
used as a pseudo-reference ("how far from today's Saghi"). PT_REF holds the
PyTorch outputs for REF, scored the same way as the baseline row.
Writes OUT/<label>/<set>/<name>.txt and prints one JSON summary. Transcripts
stay in OUT (keep it outside the repo: the real set is private).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saghi.gguf_engine import GgufEngine, find_cli  # noqa: E402

GROUPS = {"sada": "sada", "cv": "msa", "fish": "fish"}


def norm(s: str) -> list[str]:
    s = re.sub(r"[ً-ْـٰ]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        s = s.replace(a, b)
    return s.split()


def edits(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (x != y))
    return d[-1]


def score(refs: dict, hyp_dir: Path) -> dict:
    tot: dict = {}
    for name, v in refs.items():
        f = hyp_dir / f"{name}.txt"
        if not f.exists():
            continue
        r, h = norm(v["ref"]), norm(f.read_text(encoding="utf-8"))
        g = GROUPS.get(name.split("-")[0], "other")
        t = tot.setdefault(g, [0, 0])
        t[0] += edits(r, h)
        t[1] += len(r)
    return {g: {"wer": round(e / n, 4), "words": n} for g, (e, n) in tot.items()}


def max_ngram_repeat(text: str, n: int = 4) -> int:
    w = norm(text)
    grams = Counter(tuple(w[i : i + n]) for i in range(len(w) - n + 1))
    return max(grams.values()) if grams else 0


def run_set(eng: GgufEngine, files: list[Path], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    times, audio = 0.0, 0.0
    for f in files:
        r = eng.transcribe(f, language="ar", cleanup_level="light")
        (out / f"{f.stem}.txt").write_text(r.text.strip() + "\n", encoding="utf-8")
        times += r.inference_s
        audio += r.duration_s
    return {"files": len(files), "audio_s": round(audio, 1), "infer_s": round(times, 2)}


def peak_memory(cli: Path, model: Path, wav: Path) -> dict:
    """Peak footprint / RSS of one transcribe-cli run, from /usr/bin/time -l."""
    p = subprocess.run(["/usr/bin/time", "-l", str(cli), "-m", str(model), "-l", "ar", "-q", str(wav)],
                       capture_output=True, text=True)
    m_rss = re.search(r"(\d+)\s+maximum resident set size", p.stderr)
    m_fp = re.search(r"(\d+)\s+peak memory footprint", p.stderr)
    return {"peak_rss_gb": round(int(m_rss.group(1)) / 1e9, 2) if m_rss else None,
            "peak_footprint_gb": round(int(m_fp.group(1)) / 1e9, 2) if m_fp else None}


def pseudo_wer(ref_dir: Path, hyp_dir: Path) -> dict:
    e = n = same = files = 0
    for f in sorted(ref_dir.glob("*.txt")):
        h = hyp_dir / f.name
        if not h.exists():
            continue
        r, hy = norm(f.read_text(encoding="utf-8")), norm(h.read_text(encoding="utf-8"))
        e += edits(r, hy)
        n += len(r)
        files += 1
        same += r == hy
    return {"files": files, "words": n, "diff_vs_pytorch": round(e / n, 4) if n else None, "identical_files": same}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", action="append", required=True, help="LABEL=path.gguf")
    ap.add_argument("--real-dir", type=Path)
    ap.add_argument("--pytorch-real", type=Path)
    ap.add_argument("--pytorch-ref", type=Path)
    ap.add_argument("--vocab", type=Path, help="vocabulary file; default: none")
    a = ap.parse_args()

    refs = json.loads((a.ref_dir / "refs.json").read_text(encoding="utf-8"))
    clips = sorted((a.ref_dir / "clips").glob("*.wav"))
    longs = sorted((a.ref_dir / "long").glob("*.wav"))
    real = sorted(a.real_dir.glob("*.wav")) if a.real_dir else []
    summary: dict = {}
    if a.pytorch_ref:
        summary["pytorch"] = {"ref": score(refs, a.pytorch_ref)}

    for spec in a.model:
        label, path = spec.split("=", 1)
        model = Path(path)
        eng = GgufEngine(model_path=model, vocabulary_path=a.vocab or a.out / "_no_vocab.txt")
        row: dict = {"file_gb": round(model.stat().st_size / 1e9, 2)}
        t = time.time()
        row["ref_run"] = run_set(eng, clips, a.out / label / "ref")
        row["ref"] = score(refs, a.out / label / "ref")
        for lw in longs:
            r = eng.transcribe(lw, language="ar", cleanup_level="light")
            (a.out / label / f"long-{lw.stem}.txt").write_text(r.text + "\n", encoding="utf-8")
            row[f"long:{lw.stem}"] = {"words": len(r.text.split()), "max_4gram_repeat": max_ngram_repeat(r.text),
                                      "audio_s": round(r.duration_s, 1), "infer_s": round(r.inference_s, 2)}
        if real:
            row["real_run"] = run_set(eng, real, a.out / label / "real")
            if a.pytorch_real:
                row["real_vs_pytorch"] = pseudo_wer(a.pytorch_real, a.out / label / "real")
        if longs:
            row["memory"] = peak_memory(find_cli(), model, longs[0])
        row["wall_s"] = round(time.time() - t, 1)
        summary[label] = row
        print(label, json.dumps(row, ensure_ascii=False), flush=True)

    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
