"""
The app's look, in the style of current macOS (macOS 26/27 "Liquid Glass"):
a translucent sidebar, content on calm solid backgrounds (macOS 27 dialled
the glass back where text has to be read), grouped rounded cards like
System Settings, and the user's own system accent colour for selection.

Only containers are styled here (window areas, cards, separators, labels,
the sidebar list, the drop zone). Buttons, pop-ups and text fields are left
to Qt's native macOS style on purpose: those are drawn by AppKit itself, so
they automatically look exactly like the macOS version the user runs.

Colours follow macOS light/dark mode: `stylesheet()` is re-applied whenever
the palette changes (see MainWindow.changeEvent).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette

logger = logging.getLogger("saghi.ui.theme")

# ---- interface typeface (settings.ui_font) ------------------------------------
#
# Amiri (SIL OFL, ui/assets/fonts/) is the default interface font. It is a
# book face with a small x-height and tall ascenders, so every size in the
# stylesheet grows by _AMIRI_BOOST px while it is active (see fs()).

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_AMIRI_BOOST = 2
_AMIRI_POINT_SIZE = 15.0
_amiri_family: Optional[str] = None
_system_font: Optional[QFont] = None
_ui_font = "system"


def load_bundled_fonts() -> Optional[str]:
    """Register the bundled Amiri files once; returns its family name (None if loading failed)."""
    global _amiri_family
    if _amiri_family is None:
        for name in ("Amiri-Regular.ttf", "Amiri-Bold.ttf"):
            fid = QFontDatabase.addApplicationFont(str(_FONT_DIR / name))
            families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if families:
                _amiri_family = families[0]
            else:
                logger.warning("Could not load bundled font %s", name)
    return _amiri_family


def apply_ui_font(app, choice: str) -> str:
    """Make `choice` ("amiri" | "system") the application font. Returns the choice actually applied."""
    global _system_font, _ui_font
    if _system_font is None:
        _system_font = QFont(app.font())
    family = load_bundled_fonts() if choice == "amiri" else None
    if family:
        font = QFont(family)
        font.setPointSizeF(_AMIRI_POINT_SIZE)
        app.setFont(font)
        _ui_font = "amiri"
    else:
        app.setFont(_system_font)
        _ui_font = "system"
    return _ui_font


def ui_font() -> str:
    return _ui_font


def fs(px: int) -> int:
    """A stylesheet/painting font size, enlarged for Amiri."""
    return px + _AMIRI_BOOST if _ui_font == "amiri" else px

# Saghi's own colours (from packaging/icon/make_icon.py) -- used for the
# logo-adjacent accents only, never for controls.
BRAND_NAVY = "#0F1B3D"
BRAND_BLUE = "#2F6BFF"
BRAND_MELON = "#FC6C85"

# Status dot colours (macOS system green / orange / gray).
STATUS_COLORS = {
    "ready": "#34C759",
    "loading": "#FF9F0A",
    "cold": "#8E8E93",
}

# Preset colours offered for the recording indicator (Settings page); the
# first is settings.py's default waveform_color.
WAVEFORM_PRESETS = ("#4A90D9", BRAND_MELON, "#30D158", "#BF5AF2", "#FF9F0A", "#64D2FF")

# macOS system blue, used as the accent where the platform gives us no
# real system accent (Linux test runs / screenshots).
_FALLBACK_ACCENT = "#0A84FF"


@dataclass(frozen=True)
class Tokens:
    dark: bool
    window: str  # content area background
    sidebar: str  # sidebar background when there is no real glass
    card: str
    card_border: str
    separator: str
    text: str
    text_secondary: str
    hover: str
    accent: str
    accent_text: str
    danger: str
    success: str


def is_dark() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def accent_color() -> str:
    app = QGuiApplication.instance()
    if sys.platform == "darwin" and app is not None:
        return app.palette().color(QPalette.ColorRole.Highlight).name()
    return _FALLBACK_ACCENT


def tokens() -> Tokens:
    accent = accent_color()
    if is_dark():
        return Tokens(
            dark=True,
            window="#1E1E20",
            sidebar="#27272A",
            card="#2B2B2E",
            card_border="rgba(255, 255, 255, 20)",
            separator="rgba(255, 255, 255, 18)",
            text="#F5F5F7",
            text_secondary="#98989D",
            hover="rgba(255, 255, 255, 14)",
            accent=accent,
            accent_text="#FFFFFF",
            danger="#FF453A",
            success="#30D158",
        )
    return Tokens(
        dark=False,
        window="#F5F5F7",
        sidebar="#E8E8ED",
        card="#FFFFFF",
        card_border="rgba(0, 0, 0, 18)",
        separator="rgba(0, 0, 0, 16)",
        text="#1D1D1F",
        text_secondary="#6E6E73",
        hover="rgba(0, 0, 0, 10)",
        accent=accent,
        accent_text="#FFFFFF",
        danger="#FF3B30",
        success="#248A3D",
    )


def qcolor(value: str) -> QColor:
    """Parse a token ('#RRGGBB' or 'rgba(r, g, b, a)') into a QColor."""
    value = value.strip()
    if value.startswith("rgba("):
        r, g, b, a = (int(float(x)) for x in value[5:-1].split(","))
        return QColor(r, g, b, a)
    return QColor(value)


def stylesheet(glass: bool = False) -> str:
    t = tokens()
    sidebar_bg = "transparent" if glass else t.sidebar
    return f"""
#contentArea {{
    background: {t.window};
}}
#sidebar {{
    background: {sidebar_bg};
    border: none;
}}
#sidebarDivider {{
    background: {t.separator};
}}
#appName {{
    font-size: {fs(15)}px;
    font-weight: 700;
    color: {t.text};
}}
#versionLabel {{
    font-size: {fs(11)}px;
    color: {t.text_secondary};
}}
QListWidget#nav {{
    background: transparent;
    border: none;
    outline: none;
    font-size: {fs(13)}px;
    color: {t.text};
}}
QListWidget#nav::item {{
    padding: 7px 10px;
    border-radius: 8px;
    margin: 1px 10px;
    color: {t.text};
}}
QListWidget#nav::item:hover:!selected {{
    background: {t.hover};
}}
QListWidget#nav::item:selected {{
    background: {t.accent};
    color: {t.accent_text};
}}
#statusChip {{
    font-size: {fs(12)}px;
    color: {t.text_secondary};
}}
#pageTitle {{
    font-size: {fs(22)}px;
    font-weight: 700;
    color: {t.text};
}}
#pageSubtitle {{
    font-size: {fs(12)}px;
    color: {t.text_secondary};
}}
#sectionTitle {{
    font-size: {fs(13)}px;
    font-weight: 600;
    color: {t.text_secondary};
    padding: 0px 4px;
}}
#card {{
    background: {t.card};
    border: 1px solid {t.card_border};
    border-radius: 12px;
}}
#separator {{
    background: {t.separator};
    border: none;
}}
#rowTitle {{
    font-size: {fs(13)}px;
    color: {t.text};
}}
#rowHint, #secondaryText {{
    font-size: {fs(11)}px;
    color: {t.text_secondary};
}}
#successText {{
    font-size: {fs(12)}px;
    color: {t.success};
}}
#errorText {{
    font-size: {fs(12)}px;
    color: {t.danger};
}}
#emptyTitle {{
    font-size: {fs(15)}px;
    font-weight: 600;
    color: {t.text};
}}
#dropCard {{
    background: {t.card};
    border: 1.5px dashed {t.card_border};
    border-radius: 14px;
}}
#dropCard[dragActive="true"] {{
    border: 1.5px dashed {t.accent};
}}
#dropTitle {{
    font-size: {fs(15)}px;
    font-weight: 600;
    color: {t.text};
}}
#fileName {{
    font-size: {fs(13)}px;
    font-weight: 600;
    color: {t.text};
}}
#cardText {{
    background: transparent;
    border: none;
    color: {t.text};
    font-size: {fs(14)}px;
}}
QScrollArea#pageScroll, QScrollArea#pageScroll > QWidget > QWidget#scrollBody {{
    background: transparent;
    border: none;
}}
QListWidget#historyList {{
    background: transparent;
    border: none;
    outline: none;
}}
QProgressBar {{
    border: none;
    border-radius: 3px;
    background: {t.separator};
    max-height: 6px;
    min-height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    border-radius: 3px;
    background: {t.accent};
}}
QSplitter::handle {{
    background: transparent;
}}
"""
