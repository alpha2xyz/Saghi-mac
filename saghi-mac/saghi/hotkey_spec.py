"""
The dictation shortcut as a plain string: parsing and validation.

Pure Python (no pynput, no Qt) so settings.py, the recorder widget, the label
code and hotkey.py can all share one definition of "a valid shortcut".

Format: 1 or 2 tokens joined by "+". A token is one of

  * a modifier family   "ctrl" | "alt" | "cmd" | "shift"   (either side)
  * a side-specific one "ctrl_l" | "ctrl_r" | "alt_l" | "alt_r" |
                        "cmd_l" | "cmd_r" | "shift_l" | "shift_r"
  * a key               "vk:<n>"  -- a macOS virtual key code. Virtual key
                        codes name the physical key, not the character, so a
                        shortcut keeps working when the keyboard layout changes.

Rules: at most one non-modifier key; no family twice (so "ctrl+ctrl_l" is
invalid); Esc (vk 53) is reserved for "discard"; modifier / fn / caps-lock key
codes must be written as modifier tokens, not "vk:<n>".
"""

from __future__ import annotations

from typing import Optional, Tuple

DEFAULT_HOTKEY = "ctrl+cmd"

ESC_VK = 53

FAMILIES = ("ctrl", "alt", "cmd", "shift")

# macOS virtual key code -> side-specific modifier token.
MODIFIER_VK_TO_TOKEN = {
    55: "cmd_l",
    54: "cmd_r",
    56: "shift_l",
    60: "shift_r",
    58: "alt_l",
    61: "alt_r",
    59: "ctrl_l",
    62: "ctrl_r",
}
SIDED_TOKENS = frozenset(MODIFIER_VK_TO_TOKEN.values())

# Key codes that can never be a "vk:<n>" key: the modifiers themselves, caps
# lock (57, it only reports toggles) and fn (63).
_NOT_A_KEY_VKS = frozenset(MODIFIER_VK_TO_TOKEN) | {57, 63}

_MAX_VK = 255


def family_of(token: str) -> Optional[str]:
    """'alt_r' -> 'alt', 'ctrl' -> 'ctrl', 'vk:10' -> None."""
    if token in FAMILIES:
        return token
    if token in SIDED_TOKENS:
        return token.split("_")[0]
    return None


def is_modifier_token(token: str) -> bool:
    return family_of(token) is not None


def vk_of(token: str) -> Optional[int]:
    """'vk:49' -> 49; anything else -> None."""
    if token.startswith("vk:"):
        try:
            return int(token[3:])
        except ValueError:
            return None
    return None


def vk_token(vk: int) -> str:
    return f"vk:{int(vk)}"


def _valid_token(token: str) -> bool:
    if family_of(token) is not None:
        return True
    if not token.startswith("vk:"):
        return False
    digits = token[3:]
    if not digits.isdigit() or not digits.isascii():
        return False
    vk = int(digits)
    return 0 <= vk <= _MAX_VK and vk != ESC_VK and vk not in _NOT_A_KEY_VKS and token == vk_token(vk)


def parse_hotkey(value) -> Optional[Tuple[str, ...]]:
    """The tokens of a valid shortcut string, or None when it is not valid."""
    if not isinstance(value, str):
        return None
    tokens = tuple(part.strip().lower() for part in value.split("+"))
    if not 1 <= len(tokens) <= 2:
        return None
    if not all(_valid_token(t) for t in tokens):
        return None
    if len(tokens) == 2:
        a, b = tokens
        fa, fb = family_of(a), family_of(b)
        if fa is None and fb is None:
            return None  # two ordinary keys: not supported
        if fa is not None and fa == fb:
            return None  # the same modifier family twice
    return tokens


def is_valid_hotkey(value) -> bool:
    return parse_hotkey(value) is not None


def normalize_hotkey(value, default: str = DEFAULT_HOTKEY) -> str:
    """The canonical string for a valid shortcut, else `default`."""
    tokens = parse_hotkey(value)
    return "+".join(tokens) if tokens else default


def has_plain_key(hotkey: str) -> bool:
    """True when the shortcut contains a non-modifier key."""
    tokens = parse_hotkey(hotkey)
    return bool(tokens) and any(not is_modifier_token(t) for t in tokens)


def is_bare_typing_key(hotkey: str) -> bool:
    """A lone letter/digit/space/punctuation key: using it as the shortcut
    stops it from typing, so the settings page warns about it."""
    tokens = parse_hotkey(hotkey)
    if not tokens or len(tokens) != 1:
        return False
    vk = vk_of(tokens[0])
    return vk is not None and vk in TYPING_VKS


# Keys that produce a character (letters, digits, punctuation, space, keypad
# digits): ANSI/ISO main block plus the keypad.
TYPING_VKS = frozenset(
    list(range(0, 51))  # main block incl. space (49) and ` (50); tab/return excluded below
    + [65, 67, 69, 75, 78, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92]
) - {36, 48}
