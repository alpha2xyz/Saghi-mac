"""
Display labels for a shortcut string (see hotkey_spec.py), using the symbols
printed on a Mac keyboard: ⌃ ⌥ ⌘ ⇧, F1-F20, Space, arrows, letters ...

Pure Python, no Qt. The vk table is the physical ANSI/ISO layout (virtual key
codes), so it does not follow the user's input language.
"""

from __future__ import annotations

from .hotkey_spec import family_of, parse_hotkey, vk_of

_SYMBOLS = {"ctrl": "⌃", "alt": "⌥", "cmd": "⌘", "shift": "⇧"}

# Side-specific modifiers: English words, so the bidi order never flips them.
_SIDE = {"l": "Left", "r": "Right"}

_VK_LABELS = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V",
    10: "§", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T",
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9",
    26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "O", 32: "U", 33: "[",
    34: "I", 35: "P", 36: "Return", 37: "L", 38: "J", 39: "'", 40: "K", 41: ";",
    42: "\\", 43: ",", 44: "/", 45: "N", 46: "M", 47: ".", 48: "Tab", 49: "Space",
    50: "`", 51: "Delete", 53: "Esc",
    64: "F17", 65: "Num .", 67: "Num *", 69: "Num +", 71: "Clear", 75: "Num /",
    76: "Enter", 78: "Num -", 79: "F18", 80: "F19", 81: "Num =",
    82: "Num 0", 83: "Num 1", 84: "Num 2", 85: "Num 3", 86: "Num 4", 87: "Num 5",
    88: "Num 6", 89: "Num 7", 90: "F20", 91: "Num 8", 92: "Num 9",
    96: "F5", 97: "F6", 98: "F7", 99: "F3", 100: "F8", 101: "F9", 103: "F11",
    105: "F13", 106: "F16", 107: "F14", 109: "F10", 111: "F12", 113: "F15",
    114: "Help", 115: "Home", 116: "Page Up", 117: "Forward Delete", 118: "F4",
    119: "End", 120: "F2", 121: "Page Down", 122: "F1",
    123: "←", 124: "→", 125: "↓", 126: "↑",
}


def token_label(token: str) -> str:
    fam = family_of(token)
    if fam is not None:
        sym = _SYMBOLS[fam]
        if token == fam:
            return sym
        return f"{_SIDE[token[-1]]} {sym}"
    vk = vk_of(token)
    if vk is None:
        return token
    return _VK_LABELS.get(vk) or f"مفتاح {vk}"


def hotkey_label(hotkey: str) -> str:
    """'ctrl+alt' -> '⌃⌥', 'alt_r' -> 'Right ⌥', 'cmd+vk:49' -> '⌘ + Space'."""
    tokens = parse_hotkey(hotkey)
    if not tokens:
        return ""
    labels = [token_label(t) for t in tokens]
    if all(len(x) == 1 for x in labels):
        return "".join(labels)
    return " + ".join(labels)
