"""
Transcript cleanup: filler-word / hesitation removal, matching the
behavior described in packaging/README_AR.md:

  "إزالة أصوات التردد والحشو مثل «اااا» و«آه» و`um` و`uhm`، وإخفاء وسوم
  النموذج مثل `<hesitation>`."
  ("Remove hesitation/filler sounds like «اااا» and «آه» and `um`/`uhm`,
  and hide model tags like `<hesitation>`.")

Three levels, each a superset of the previous:

  none   -- strip model markup tags only. Tags like <hesitation> are never
            user content, so they're removed even at the lowest level.
  light  -- (default) tags + standalone filler words/hesitations: Arabic
            elongated hesitation vowels («اااا»), the standalone words
            «آه»/«اه», and English um/uhm/uh/erm-family words.
  medium -- light + collapse immediately-repeated words («يعني يعني» ->
            «يعني») + tidy double spaces and spaces before punctuation.

Conservative by design: every removal is a *standalone token* match
(surrounded by whitespace/string edges), never a substring match, so real
words that merely contain a filler-like substring (e.g. «مياه» contains
«اه») are never touched. English words are never touched at any level --
preserving code-switched English is this app's core promise.
"""

from __future__ import annotations

import re

# Model markup tags: <hesitation>, <...> control tags of any kind. Always
# stripped, at every level, including "none". Matched non-greedily so a
# malformed/unclosed "<" doesn't eat the rest of the string.
_TAG_RE = re.compile(r"\s*<[^<>]*>\s*")

# Standalone Arabic filler words (exact token match only).
_FILLER_AR = {"آه", "اه", "أه"}

# Standalone English filler words (matched case-insensitively).
_FILLER_EN = {"um", "umm", "uh", "uhh", "uhm", "erm", "err"}

# Arabic elongated hesitation vowels: a token made up entirely of 3+
# repeated alef/heh-type characters, e.g. «اااا», «اااه». Not a substring
# match -- the whole token (after stripping punctuation) must consist only
# of these characters.
# Also 2+ alef-maddas («آآ», «آآه»): the GGUF model writes this short
# form, and it is never a real word.
_ELONGATED_RE = re.compile(r"^(?:آ{2,}ه*|[اآأإه]{3,})$")  # ا آ أ إ ه

# A one-letter prefix stretched with tatweel and left alone («فـ ما أدري»,
# the "so..." before a hesitation): glue it back onto the next word.
_STRETCHED_PREFIX_RE = re.compile(r"(?<!\S)([فوبلك])ـ+\s+(?=\S)")

# Punctuation stripped off a token's edges before comparing it against the
# filler sets above (so "um," or "،اه" still match). Stripped punctuation
# is discarded along with the token when it turns out to be filler.
_PUNCT_CHARS = "،؛؟!.,:;\"'()[]{}-–—…”“’‘"

# Duplicate-word collapse (medium level): a word immediately followed by
# one or more repeats of itself (whitespace-separated) collapses to a
# single occurrence. Word = any run of non-whitespace characters.
_DUP_WORD_RE = re.compile(r"(?<!\S)(\S+)(?:\s+\1(?!\S))+")

# Space(s) before a punctuation mark (medium level tidy-up only).
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([،؛؟!.,:;])")

# 2+ run of plain spaces/tabs collapsed to one (medium level tidy-up only).
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_tags(text: str) -> str:
    # Replace each tag (plus any immediately-adjacent whitespace) with a
    # single space, so removal never glues two neighboring words together
    # and never leaves a double space either. Pre-existing spacing
    # elsewhere in the string is left untouched -- that's medium's job.
    return _TAG_RE.sub(" ", text).strip()


def _is_filler_token(token: str) -> bool:
    core = token.strip(_PUNCT_CHARS)
    if not core:
        return False
    if core.lower() in _FILLER_EN:
        return True
    if core in _FILLER_AR:
        return True
    if _ELONGATED_RE.match(core):
        return True
    return False


def _strip_filler_words(text: str) -> str:
    """
    Remove standalone filler tokens. Token-aware (not a blanket regex
    substitution) so that removing a token also removes exactly the one
    whitespace run that separated it from the next token -- avoiding the
    double-space artifact a naive "delete the word" pass would leave
    behind, without touching any *other* pre-existing spacing in the
    string (that tidy-up is medium-only, see clean_transcript).
    """
    parts = re.split(r"(\s+)", text)  # tokens at even indices, whitespace at odd indices
    kept: list[str] = []
    i = 0
    n = len(parts)
    while i < n:
        token = parts[i]
        if i % 2 == 0 and token and _is_filler_token(token):
            i += 2  # drop the token and the whitespace run right after it
            continue
        kept.append(token)
        i += 1
    return "".join(kept).strip()


def _collapse_duplicate_words(text: str) -> str:
    return _DUP_WORD_RE.sub(r"\1", text)


def _tidy_spacing(text: str) -> str:
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def clean_transcript(text: str, level: str = "light") -> str:
    """
    Clean a raw transcript per `level`: "none", "light" (default), or
    "medium". See module docstring for what each level does.
    """
    if level not in ("none", "light", "medium"):
        raise ValueError(f"Unknown cleanup level: {level!r} (expected none/light/medium)")

    text = _strip_tags(text)

    if level == "none":
        return text

    text = _strip_filler_words(text)
    text = _STRETCHED_PREFIX_RE.sub(r"\1", text)

    if level == "medium":
        text = _collapse_duplicate_words(text)
        text = _tidy_spacing(text)

    return text


# ---- live dictation: finish each take so takes join cleanly ----------------

_END_PUNCT = "،؛؟!.,:;?…"
_CLOSERS = "\"')]}»”’"
_AR_QUESTION_WORDS = {
    "هل", "كيف", "ليش", "ليه", "وش", "ايش", "إيش", "شو", "متى", "وين",
    "فين", "كم", "مين", "لماذا", "ماذا", "أين", "كيفك", "اش",
}
_EN_QUESTION_WORDS = {
    "what", "why", "how", "when", "where", "who", "which", "is", "are",
    "do", "does", "did", "can", "could", "should", "would", "will",
}
_ARABIC_LETTER_RE = re.compile(r"[؀-ۿ]")


def finish_dictation(text: str) -> str:
    """
    Make one dictated take ready to paste after the previous one: add a
    full stop when the take ends without punctuation («؟» / "?" when it
    opens with a question word), then one trailing space so the next take
    does not glue onto this one. Empty input stays empty.
    """
    body = text.strip()
    if not body:
        return ""
    core = body.rstrip(_CLOSERS)
    if not core or core[-1] not in _END_PUNCT:
        first = body.split()[0].strip(_PUNCT_CHARS)
        arabic = bool(_ARABIC_LETTER_RE.search(body))
        if arabic:
            mark = "؟" if first in _AR_QUESTION_WORDS else "."
        else:
            mark = "?" if first.lower() in _EN_QUESTION_WORDS else "."
        body += mark
    return body + " "
