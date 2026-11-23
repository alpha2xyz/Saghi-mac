"""Synthesize Saghi's feedback tones into saghi/ui/assets/sounds/ (run once; output is committed).

Pure sine plucks, generated here so the app ships no third-party audio:
  start.wav    a soft high pluck  (~1046 Hz, C6)
  stop.wav     a soft lower pluck (~831 Hz, G#5)
  discard.wav  a short downward glide (660 -> 392 Hz)
  error.wav    two low taps
"""
import wave
from pathlib import Path

import numpy as np

SR = 48000
OUT = Path(__file__).resolve().parent.parent / "saghi" / "ui" / "assets" / "sounds"


def pluck(freq: float, dur: float = 0.2, attack: float = 0.006, decay: float = 0.028) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    env = np.where(t < attack, t / attack, np.exp(-(t - attack) / decay))
    tone = np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t / 0.012)
    return tone * env


def glide(f0: float, f1: float, dur: float = 0.24) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    f = f0 * (f1 / f0) ** (t / dur)
    phase = 2 * np.pi * np.cumsum(f) / SR
    env = np.minimum(t / 0.01, 1) * np.exp(-t / 0.07)
    return np.sin(phase) * env


def write(name: str, x: np.ndarray) -> None:
    x = x / np.abs(x).max() * 0.7
    data = (x * 32767).astype("<i2").tobytes()
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    write("start.wav", pluck(1046.5))
    write("stop.wav", pluck(830.6))
    write("discard.wav", glide(660, 392))
    gap = np.zeros(int(SR * 0.07))
    write("error.wav", np.concatenate([pluck(330, 0.12, decay=0.03), gap, pluck(262, 0.2, decay=0.04)]))
    print("wrote", sorted(p.name for p in OUT.iterdir()))
