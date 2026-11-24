# Third-party notices

Saghi-mac's own code is MIT licensed (see [LICENSE](LICENSE)). It uses and ships the following third-party software and model. Each keeps its own license.

## Speech model

| Component | License | Link |
|---|---|---|
| cohere-transcribe-arabic-07-2026 (CohereLabs), original model | Apache 2.0 | https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026 |
| cohere-transcribe-arabic-07-2026-Q6_K.gguf: a quantized (6-bit) conversion of that model made by handy-computer | Apache 2.0 | https://huggingface.co/handy-computer/cohere-transcribe-arabic-07-2026-gguf |

The app ships both: the original model as PyTorch weights ("Saghi Full", `model.safetensors` in the app's `model/` folder, with the model's own `LICENSE` and `README.md`; Saghi-mac does not modify it), and, since v0.3.0, the Q6_K GGUF file ("Saghi Fast"), a third-party quantized derivative of the CohereLabs model. Saghi-mac itself does not modify it: the file is bundled byte for byte (the build checks its sha256) in the app's `model-gguf/` folder, next to the `LICENSE` and `NOTICE` files of the GGUF repository. The release build fetches it from the GGUF repository at a pinned revision.

## Speech engine

| Component | License | Link |
|---|---|---|
| transcribe.cpp (`transcribe-cli`, built from source in the release workflow, includes ggml) | MIT | https://github.com/handy-computer/transcribe.cpp |

The app ships the compiled `transcribe-cli` in `Resources/bin/`, with its license text (`LICENSE-transcribe.cpp`) beside it.

## Python runtime

| Component | License | Link |
|---|---|---|
| CPython | PSF License | https://docs.python.org/3/license.html |
| python-build-standalone (prebuilt CPython builds) | MPL-2.0 | https://github.com/astral-sh/python-build-standalone |

## Python packages (from `saghi-mac/dev/requirements-arm64.txt`)

Licenses below are taken from each package's PyPI metadata. Wheels can bundle further native libraries under their own licenses; see each package for the full list.

| Package | License |
|---|---|
| PySide6 (Qt for Python) | LGPL-3.0 (also offered under GPL-2.0/GPL-3.0 and commercial terms). Qt: https://doc.qt.io/qt-6/lgpl.html |
| torch | BSD-3-Clause (plus bundled components under Apache-2.0, BSD-2-Clause, BSL-1.0, MIT) |
| transformers | Apache 2.0 |
| accelerate | Apache 2.0 |
| safetensors | Apache 2.0 |
| tokenizers | Apache 2.0 |
| huggingface_hub | Apache 2.0 |
| sentencepiece | Apache 2.0 |
| protobuf | BSD-3-Clause |
| numpy | BSD-3-Clause (plus bundled components under 0BSD, MIT, Zlib, CC0-1.0) |
| scipy | BSD-3-Clause |
| librosa | ISC |
| numba, llvmlite (installed as librosa dependencies) | BSD-2-Clause (llvmlite also Apache-2.0 with LLVM exception) |
| soundfile | BSD-3-Clause (its wheels bundle libsndfile, LGPL-2.1) |
| soxr (python-soxr) | LGPL-2.1-or-later |
| sounddevice | MIT (its wheels bundle PortAudio, MIT) |
| pynput | LGPL-3.0 |
| pyobjc-core and pyobjc-framework-* | MIT |
| six | MIT |
| fastapi | MIT |
| starlette | BSD-3-Clause |
| pydantic, pydantic_core | MIT |
| uvicorn | BSD-3-Clause |
| python-multipart | Apache 2.0 |
| httpx | BSD-3-Clause |
| keyring | MIT |
| jaraco.classes, jaraco.functools, jaraco.context | MIT |
| more-itertools | MIT |
| Other transitive dependencies (scikit-learn, joblib, decorator, pooch, lazy_loader, msgpack, anyio, h11, httpcore, certifi, click, cffi, ...) | see each package |

## LGPL note

PySide6 (Qt), pynput, soxr and libsndfile are LGPL libraries. Saghi-mac uses them unmodified as separate, replaceable Python packages: the app ships them as ordinary wheels that are installed into the app's own virtual environment (`~/Applications/Saghi.app/Contents/Resources/venv`), so you can replace any of them with another version using `pip`.

## Font: Amiri (app icon only)

The lettering of the app icon was drawn with **Amiri Bold**, Copyright 2010-2022 The Amiri Project Authors (https://github.com/aliftype/amiri), licensed under the SIL Open Font License 1.1. The font itself is not shipped with the app; the interface uses the macOS system font.

## Not affiliated

Saghi (صاغي) is a product of أسلس AI in collaboration with جندي: https://asls-ai.auraspectrum.sa/saghi/ . Saghi-mac is an independent, free, community Mac version and is not affiliated with or endorsed by أسلس AI.
