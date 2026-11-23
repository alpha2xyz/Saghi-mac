"""
Custom vocabulary as a post-correction of the transcript.

transcribe.cpp ignores `--vocabulary` / `--prompt` for the cohere_asr model
(it logs "does not support vocabulary"), so names cannot bias the decoder.
Instead, a small rule file rewrites whole words after transcription.

File: `vocabulary.txt` in the data dir (or SAGHI_VOCAB_FILE). Never shipped
in the repo: it holds the user's own names. One rule per line:

    wrong => right        rewrite the word `wrong` to `right`
    right                 rewrite any word that spells `right` without
                          diacritics/alef/ta-marbuta differences to `right`
    # comment

Matching is whole-word and ignores diacritics and the usual spelling
variants (أ/إ/آ -> ا, ى -> ي, ة -> ه). A leading «ال» or «و» on the spoken
word is kept («المنصه» with rule «منصة» -> «المنصة»).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

_DIACRITICS = re.compile(r"[ً-ْـٰ]")
_WORD = re.compile(r"[\w؀-ۿ]+")
_PREFIXES = ("وال", "ال", "و")


def normalize(word: str) -> str:
    w = _DIACRITICS.sub("", word)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        w = w.replace(a, b)
    return w


def default_path() -> Path:
    env = os.environ.get("SAGHI_VOCAB_FILE")
    if env:
        return Path(env).expanduser()
    from . import paths

    return paths.data_dir() / "vocabulary.txt"


@dataclass
class Vocabulary:
    rules: dict = field(default_factory=dict)  # normalized spoken form -> replacement

    @classmethod
    def parse(cls, text: str) -> "Vocabulary":
        rules = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=>" in line:
                wrong, right = (s.strip() for s in line.split("=>", 1))
            else:
                wrong = right = line
            if wrong and right:
                rules[normalize(wrong)] = right
        return cls(rules)

    @classmethod
    def load(cls, path: Optional[Union[str, Path]] = None) -> "Vocabulary":
        p = Path(path).expanduser() if path else default_path()
        try:
            return cls.parse(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return cls()

    def _fix(self, m: re.Match) -> str:
        word = m.group(0)
        n = normalize(word)
        if n in self.rules:
            return self.rules[n]
        for pre in _PREFIXES:
            if n.startswith(pre) and n[len(pre):] in self.rules:
                rep = self.rules[n[len(pre):]]
                return word[: len(pre)] + (rep[2:] if pre.endswith("ال") and rep.startswith("ال") else rep)
        return word

    def apply(self, text: str) -> str:
        if not self.rules or not text:
            return text
        return _WORD.sub(self._fix, text)
