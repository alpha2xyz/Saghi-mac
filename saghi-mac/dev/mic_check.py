"""
Microphone check, without the app's UI: records 3 seconds from the default
input and prints the level. Run on the Mac from the saghi-mac folder:

    python3 dev/mic_check.py

Peak 0.00000 = macOS is handing the app silence, almost always because the
Microphone permission is missing for this Python/Terminal (System Settings ->
Privacy & Security -> Microphone).
"""

import sys

import numpy as np
import sounddevice as sd

SECONDS = 3
RATE = 16000

print("Default input device:", sd.query_devices(kind="input")["name"])
print(f"Recording {SECONDS}s -- say something...")
audio = sd.rec(int(SECONDS * RATE), samplerate=RATE, channels=1, dtype="float32")
sd.wait()
audio = audio[:, 0]
peak = float(np.max(np.abs(audio)))
rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
print(f"peak={peak:.5f} rms={rms:.5f}")
if peak <= 1e-6:
    print("SILENT: no audio reached the app -> check the Microphone permission.")
    sys.exit(1)
print("OK: the microphone works.")
