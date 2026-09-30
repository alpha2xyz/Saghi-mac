"""
Settings persistence: `settings.json` in the data dir (see paths.py),
atomic write (tmp file + os.replace, same pattern filejobs.py already
uses for manifest.json/staged outputs).

Deliberately has ZERO Qt dependency -- this module is plain Python so it
can be imported and unit-tested without a QApplication, and so any future
non-GUI consumer (e.g. a Phase 5 hotkey/recorder daemon) can read/write the
same settings file without pulling in PySide6.

Fields (per README_AR.md's «الإعدادات»
page):

    hotkey          str   default "ctrl+cmd" -- global press-and-hold hotkey
    microphone      str|None  device name, None = system default
    cleanup_level   str   "none" | "light" | "medium", default "light"
    autopaste       bool  default True
    save_recordings bool  default False
    waveform_style  str   "bars" | "line" | "dots" | "pulse", default "bars"
    waveform_color  str   hex color, default "#4A90D9"
    language        str   "ar" | "en", default "ar"
    launch_at_login bool  default False

    Phase 6 additions (README_AR.md's «مساعد OpenRouter الاختياري»). The
    API KEY itself is NOT one of these fields -- it's a secret, stored in
    the macOS Keychain via saghi/openrouter.py, never in settings.json. The
    model id and instructions below are not secrets, so they live here like
    every other setting.

    openrouter_enabled       bool  default False -- «إعادة الصياغة بعد التفريغ»
    openrouter_model         str   default "" -- free-form model id, e.g.
                                    "google/gemini-3.1-flash-lite"; any id
                                    valid on the user's OpenRouter account,
                                    never validated against a fixed list
    openrouter_instructions  str   default "" -- free-form rephrase
                                    instructions the user writes themselves

    Later additions:

    sound_feedback          bool  default True -- short system sound when a
                                    dictation starts, finishes, or fails
    history_retention_days  int   0 | 7 | 30 | 90, default 0 (keep forever);
                                    older history rows are pruned on start-up
                                    and whenever this changes
    auto_check_updates      bool  default False -- ask GitHub once a day
                                    whether a newer release exists (only a
                                    notice; nothing is installed without
                                    the user clicking "update"). Off by
                                    default so the app stays offline unless
                                    the user asks otherwise.
    last_update_check       str   UTC ISO-8601 of the last automatic check,
                                    "" = never
    glass_effect            bool  default True -- translucent macOS sidebar
                                    (ui/macos_glass.py); applies at the next
                                    launch
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Optional

from . import paths

logger = logging.getLogger("saghi.settings")

_FILENAME = "settings.json"

VALID_CLEANUP_LEVELS = ("none", "light", "medium")
VALID_WAVEFORM_STYLES = ("bars", "line", "dots", "pulse")
VALID_LANGUAGES = ("ar", "en")
# Mirrors hotkey.COMBOS' keys (duplicated so this module keeps zero
# pynput dependency, see hotkey.py's COMBOS comment).
VALID_HOTKEYS = ("ctrl+cmd", "alt+cmd", "shift+cmd", "ctrl+alt", "ctrl+shift")
VALID_RETENTION_DAYS = (0, 7, 30, 90)


@dataclass
class Settings:
    hotkey: str = "ctrl+cmd"
    microphone: Optional[str] = None
    cleanup_level: str = "light"
    autopaste: bool = True
    save_recordings: bool = False
    waveform_style: str = "bars"
    waveform_color: str = "#4A90D9"
    language: str = "ar"
    launch_at_login: bool = False
    openrouter_enabled: bool = False
    openrouter_model: str = ""
    openrouter_instructions: str = ""
    sound_feedback: bool = True
    history_retention_days: int = 0
    auto_check_updates: bool = False
    last_update_check: str = ""
    glass_effect: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def settings_path() -> Path:
    return paths.data_dir() / _FILENAME


def _coerce(data: dict) -> Settings:
    """
    Build a Settings from a raw dict, falling back to the default value for
    any field that's missing or has an invalid enum value -- never raises
    on a partial/corrupt file. Unknown extra keys in the file are ignored.
    """
    defaults = Settings()
    kwargs = {}
    for f in fields(Settings):
        kwargs[f.name] = data.get(f.name, getattr(defaults, f.name))
    s = Settings(**kwargs)

    if s.cleanup_level not in VALID_CLEANUP_LEVELS:
        s.cleanup_level = defaults.cleanup_level
    if s.waveform_style not in VALID_WAVEFORM_STYLES:
        s.waveform_style = defaults.waveform_style
    if s.language not in VALID_LANGUAGES:
        s.language = defaults.language
    if s.hotkey not in VALID_HOTKEYS:
        s.hotkey = defaults.hotkey
    if s.microphone is not None and not isinstance(s.microphone, str):
        s.microphone = defaults.microphone
    if s.waveform_color is not None and not isinstance(s.waveform_color, str):
        s.waveform_color = defaults.waveform_color
    if not isinstance(s.openrouter_model, str):
        s.openrouter_model = defaults.openrouter_model
    if not isinstance(s.openrouter_instructions, str):
        s.openrouter_instructions = defaults.openrouter_instructions
    if not isinstance(s.last_update_check, str):
        s.last_update_check = defaults.last_update_check
    retention = s.history_retention_days
    if isinstance(retention, bool) or not isinstance(retention, (int, float)) or retention not in VALID_RETENTION_DAYS:
        s.history_retention_days = defaults.history_retention_days
    else:
        s.history_retention_days = int(retention)
    for bool_field in (
        "autopaste",
        "save_recordings",
        "launch_at_login",
        "openrouter_enabled",
        "sound_feedback",
        "auto_check_updates",
        "glass_effect",
    ):
        if not isinstance(getattr(s, bool_field), bool):
            setattr(s, bool_field, getattr(defaults, bool_field))

    return s


def load() -> Settings:
    """Load settings.json. Missing file or any parse/validation problem -> defaults (file is NOT written)."""
    path = settings_path()
    if not path.exists():
        return Settings()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("settings.json root is not a JSON object")
        return _coerce(data)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("settings.json missing/corrupt (%s) -- using defaults", exc)
        return Settings()


def save(settings: Settings) -> None:
    """Atomically write settings.json (tmp file + os.replace)."""
    paths.ensure_dirs()
    path = settings_path()
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class SettingsManager:
    """
    Stateful wrapper used by the GUI: holds the current Settings in memory
    (loaded once at construction), persists to disk on every change, and
    notifies registered listener callbacks -- the "simple change-
    notification hook" the Phase 4 task asks for, so UI widgets (and any
    later phase) can react without polling settings.json themselves.
    """

    def __init__(self) -> None:
        self._settings = load()
        self._listeners: list[Callable[[Settings], None]] = []

    @property
    def current(self) -> Settings:
        return self._settings

    def on_change(self, callback: Callable[[Settings], None]) -> None:
        self._listeners.append(callback)

    def update(self, **kwargs) -> Settings:
        """Set one or more fields, save immediately, and notify listeners."""
        for key, value in kwargs.items():
            if not hasattr(self._settings, key):
                raise AttributeError(f"Unknown setting: {key!r}")
            setattr(self._settings, key, value)
        save(self._settings)
        for cb in list(self._listeners):
            try:
                cb(self._settings)
            except Exception:
                logger.exception("Settings change-listener raised")
        return self._settings

    def reload(self) -> Settings:
        """Re-read settings.json from disk, replacing the in-memory copy (does not notify listeners)."""
        self._settings = load()
        return self._settings
