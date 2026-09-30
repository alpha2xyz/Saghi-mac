#!/usr/bin/env python3
"""
Tests for saghi/ui/sf_symbols.py and the icon section of ui/widgets.py --
offscreen Qt, no macOS needed:

  1. The drawn fallbacks: every symbol renders at the right pixel size and
     device-pixel ratio, in exactly the requested colour, honouring weight and
     the waveform's variable colour; unknown names give an empty pixmap and a
     single warning; icon(mask=True) is a template image.
  2. The SVG-path reader the fallbacks are built on (arcs, relative and smooth
     commands) against known geometry.
  3. The NATIVE path, exercised with a fake AppKit (and objc) module that
     returns a canned PNG: colourising, cropping, centring, weight and variable
     value plumbing, every pyobjc return shape, caching, and that any failure
     falls back to the drawn icon instead of raising.
  4. widgets.icon / icon_pixmap still serve the old names (history, filejob,
     settings, drop, empty) and pass SF names through.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_sf_symbols.py

Plain assert-based, no pytest -- same style as the other dev/ tests.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from saghi.ui import sf_symbols, theme, widgets  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


def argb(pm_or_img) -> QImage:
    img = pm_or_img.toImage() if hasattr(pm_or_img, "toImage") else pm_or_img
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def opaque_pixels(img: QImage, min_alpha: int = 200) -> list[QColor]:
    return [
        c
        for y in range(img.height())
        for x in range(img.width())
        if (c := img.pixelColor(x, y)).alpha() >= min_alpha
    ]


def bbox(img: QImage) -> tuple[int, int, int, int] | None:
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 0:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def total_alpha(img: QImage) -> int:
    return sum(img.pixelColor(x, y).alpha() for y in range(img.height()) for x in range(img.width()))


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= logging.WARNING]


log = LogCapture()
logging.getLogger("saghi.ui.sf_symbols").addHandler(log)
logging.getLogger("saghi.ui.sf_symbols").setLevel(logging.DEBUG)

REQUIRED = {
    "mic", "mic.fill", "mic.slash", "waveform", "hourglass", "exclamationmark.triangle",
    "exclamationmark.triangle.fill", "clock", "gearshape", "square.and.arrow.down",
    "doc.text", "arrow.down.circle", "checkmark",
}  # fmt: skip

# ---- 1. drawn fallbacks -------------------------------------------------------------

print("--- 1. drawn fallbacks ---")

sf_symbols._reset_caches()
check(not sf_symbols.native_available(), "native_available() is False without macOS/pyobjc")
check(isinstance(sf_symbols.SYMBOLS, frozenset), "SYMBOLS is a frozenset")
check(sf_symbols.SYMBOLS == REQUIRED, "SYMBOLS is exactly the 13 required names")

RED = QColor("#E0301E")
for name in sorted(sf_symbols.SYMBOLS):
    for size, dpr in ((16, 2.0), (24, 1.0), (40, 3.0)):
        pm = sf_symbols.pixmap(name, size, RED, dpr=dpr)
        px = round(size * dpr)
        assert not pm.isNull() and pm.width() == px and pm.height() == px, (name, size, dpr, pm.size())
        assert pm.devicePixelRatio() == dpr, (name, dpr, pm.devicePixelRatio())
    pm = sf_symbols.pixmap(name, 24, RED)  # default dpr is 2
    img = argb(pm)
    solid = opaque_pixels(img)
    check(pm.width() == 48 and pm.devicePixelRatio() == 2.0, f"{name}: 24 logical px at dpr 2 -> 48 px, ratio 2")
    check(len(solid) > 20, f"{name}: has opaque pixels")
    off = [c for c in solid if max(abs(c.red() - RED.red()), abs(c.green() - RED.green()), abs(c.blue() - RED.blue())) > 3]
    check(not off, f"{name}: every opaque pixel is the requested colour")
    box = bbox(img)
    check(box[0] >= 0 and box[1] >= 0 and box[2] <= 47 and box[3] <= 47, f"{name}: glyph stays inside the canvas")
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    check(abs(cx - 23.5) <= 5 and abs(cy - 23.5) <= 5, f"{name}: glyph is roughly centred")

# colour argument forms
tokens = theme.tokens()
default_px = opaque_pixels(argb(sf_symbols.pixmap("mic", 24)))
want = QColor(tokens.text)
check(
    all(abs(c.red() - want.red()) <= 3 and abs(c.blue() - want.blue()) <= 3 for c in default_px),
    "color=None uses the theme's text colour",
)
str_px = opaque_pixels(argb(sf_symbols.pixmap("mic", 24, "#00FF00")))
check(bool(str_px) and all(c.green() >= 252 and c.red() <= 3 for c in str_px), "color given as a '#RRGGBB' string")
rgba_px = argb(sf_symbols.pixmap("mic.fill", 24, "rgba(255, 0, 0, 128)"))
mid = rgba_px.pixelColor(24, 20)
check(90 <= mid.alpha() <= 165 and mid.red() > 240, "color given as the tokens' rgba(...) string keeps its alpha")
bad = opaque_pixels(argb(sf_symbols.pixmap("mic", 24, "not-a-colour")))
check(bool(bad) and abs(bad[0].red() - want.red()) <= 3, "an unparseable colour falls back to the text colour")

# cached: same args, same pixmap; different colour, different pixels
a = sf_symbols.pixmap("clock", 20, RED)
b = sf_symbols.pixmap("clock", 20, RED)
check(a.cacheKey() == b.cacheKey(), "identical requests are served from the cache")

# filled triangle: the "!" is a real hole (transparent), not painted in another colour
tri = argb(sf_symbols.pixmap("exclamationmark.triangle.fill", 48, RED, dpr=1.0))
check(tri.pixelColor(24, 30).alpha() >= 250, "triangle.fill is solid beside the mark")
check(tri.pixelColor(24, 22).alpha() == 0, "triangle.fill: the exclamation stem is cut out (transparent)")
tri_line = argb(sf_symbols.pixmap("exclamationmark.triangle", 48, RED, dpr=1.0))
check(tri_line.pixelColor(24, 30).alpha() == 0, "triangle (outline) is hollow inside")
check(total_alpha(tri) > total_alpha(tri_line) * 1.5, "triangle.fill has much more ink than the outline")

# mic.fill vs mic
mic = argb(sf_symbols.pixmap("mic", 48, RED, dpr=1.0))
mic_fill = argb(sf_symbols.pixmap("mic.fill", 48, RED, dpr=1.0))
check(mic.pixelColor(24, 14).alpha() == 0 and mic_fill.pixelColor(24, 14).alpha() >= 250, "mic is hollow, mic.fill is solid")
slash = argb(sf_symbols.pixmap("mic.slash", 48, RED, dpr=1.0))
check(total_alpha(slash) != total_alpha(mic), "mic.slash differs from mic")
check(slash.pixelColor(24, 24).alpha() >= 250, "mic.slash: the slash crosses the centre")

# weight
inks = {}
for w in ("regular", "medium", "semibold", "bold"):
    pm = sf_symbols.pixmap("checkmark", 24, RED, weight=w)
    assert not pm.isNull()
    inks[w] = total_alpha(argb(pm))
check(inks["regular"] < inks["medium"] < inks["semibold"] < inks["bold"], "heavier weights put down more ink")
odd = sf_symbols.pixmap("checkmark", 24, RED, weight="ultra-fat")
check(not odd.isNull() and total_alpha(argb(odd)) == inks["medium"], "an unknown weight behaves as medium, no exception")

# variable colour (waveform)
plain = argb(sf_symbols.pixmap("waveform", 32, RED, dpr=1.0))
v02 = argb(sf_symbols.pixmap("waveform", 32, RED, variable=0.2, dpr=1.0))
v10 = argb(sf_symbols.pixmap("waveform", 32, RED, variable=1.0, dpr=1.0))
v00 = argb(sf_symbols.pixmap("waveform", 32, RED, variable=0.0, dpr=1.0))
check(v02 != v10, "waveform variable 0.2 differs from 1.0")
check(v10 == plain, "waveform variable 1.0 equals the plain symbol (every bar lit)")
check(total_alpha(v00) < total_alpha(v02) < total_alpha(v10), "more variable value, more lit bars")
check(max(v00.pixelColor(x, y).alpha() for y in range(32) for x in range(32)) <= 0.31 * 255 + 2, "unlit bars sit at 30 % alpha")
# 6 bars, 0.2 -> ceil(1.2) = 2 lit: the leftmost bars solid, the rightmost dim
row = [v02.pixelColor(x, 16).alpha() for x in range(32)]
check(max(row[:9]) >= 250 and max(row[22:]) <= 0.31 * 255 + 2, "0.2 lights the first two bars and dims the last")
nonvar = argb(sf_symbols.pixmap("mic", 32, RED, variable=0.2, dpr=1.0))
check(nonvar == argb(sf_symbols.pixmap("mic", 32, RED, dpr=1.0)), "variable is ignored by symbols without variable layers")

# unknown names: empty pixmap, no exception, one warning
sf_symbols._reset_caches()
log.records.clear()
u1 = sf_symbols.pixmap("no.such.symbol", 20, RED)
u2 = sf_symbols.pixmap("no.such.symbol", 20, RED, weight="bold")
u3 = sf_symbols.pixmap("no.such.symbol", 30, RED)
check(not u1.isNull() and u1.width() == 40 and u1.devicePixelRatio() == 2.0, "unknown name: non-null pixmap of the right size")
check(total_alpha(argb(u1)) == 0 and total_alpha(argb(u3)) == 0, "unknown name: fully transparent")
check(len(log.warnings()) == 1 and "no.such.symbol" in log.warnings()[0], "unknown name: logged one warning, once")
check(not sf_symbols.icon("no.such.symbol", 16).isNull(), "unknown name: icon() does not raise either")

# icon()
ic = sf_symbols.icon("mic", 16, RED, weight="semibold", variable=None)
check(isinstance(ic, QIcon) and not ic.isNull() and not ic.isMask(), "icon() is a normal QIcon")
pm = ic.pixmap(16, 16)
check(not pm.isNull(), "icon() yields a pixmap")
m = sf_symbols.icon("mic.fill", 18, mask=True)
check(m.isMask(), "icon(mask=True) is a template image (isMask)")
mpx = opaque_pixels(argb(m.pixmap(18, 18)))
check(bool(mpx) and all(c.red() <= 3 and c.green() <= 3 and c.blue() <= 3 for c in mpx), "mask icon glyph is black")
check({sz.width() for sz in m.availableSizes()} == {18, 36}, "icon() carries a 1x (18 px) and a 2x (36 px) pixmap")
check(not sf_symbols.icon("waveform", 16, variable=0.5).isNull(), "icon() accepts variable")

# ---- 2. the SVG path reader ---------------------------------------------------------

print("--- 2. SVG path reader ---")


def rect_of(d: str):
    r = sf_symbols._parse_path(d).boundingRect()
    return (round(r.left(), 2), round(r.top(), 2), round(r.right(), 2), round(r.bottom(), 2))


check(rect_of("M0 0 h5 v5") == rect_of("M0 0 L5 0 L5 5") == (0.0, 0.0, 5.0, 5.0), "relative h/v equal absolute L")
check(rect_of("M1 1 2 3 4 1") == (1.0, 1.0, 4.0, 3.0), "extra coordinate pairs after M are implicit lineto")
# semicircle from (5.5, 11.2) to (18.5, 11.2), bulging down (sweep=0 in SVG terms -> counter-clockwise on screen)
mic_arc = rect_of("M5.5 11.2a6.5 6.5 0 0 0 13 0")
check(mic_arc == (5.5, 11.2, 18.5, 17.7), f"mic stand arc is a lower semicircle, got {mic_arc}")
# same arc, other sweep: bulges up
up = rect_of("M5.5 11.2a6.5 6.5 0 0 1 13 0")
check(up == (5.5, 4.7, 18.5, 11.2), f"sweep flag flips the arc to the top, got {up}")
capsule = rect_of("M9 5.5a3 3 0 0 1 6 0V11a3 3 0 0 1-6 0z")
check(capsule == (9.0, 2.5, 15.0, 14.0), f"mic capsule = rect x9 y2.5 w6 h11.5, got {capsule}")
tri_rect = rect_of("M10.3 3.9 2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z")
# the r=2 corner arcs bulge past the sharp vertices: apex 3.9 - (2 - sqrt(4 - 1.7^2)) = 2.95, sides symmetric about x=12
check(tri_rect == (2.33, 2.95, 21.67, 20.6), f"triangle extents (rounded corners), got {tri_rect}")
check(abs((tri_rect[0] + tri_rect[2]) / 2 - 12) < 0.01, "triangle is symmetric about x=12")
hour = sf_symbols._parse_path("M7.5 3c0 5 9 5 9 9s-9 4-9 9")
end = hour.currentPosition()
check((round(end.x(), 2), round(end.y(), 2)) == (7.5, 21.0), "smooth cubic (s) chains and ends where the SVG says")
big = rect_of("M0 0 a1 1 0 0 1 10 0")  # radii too small: the SVG spec scales them up
check(big == (0.0, -5.0, 10.0, 0.0), f"undersized arc radii are scaled to fit, got {big}")

# ---- 3. native path, with a fake AppKit ---------------------------------------------

print("--- 3. native path (fake AppKit) ---")


def png_of(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def glyph_png(opaque: bool = False) -> bytes:
    """A 80x60 canvas with a black 40x20 bar (offset from centre) -- 2:1, so aspect and centring are visible."""
    img = QImage(80, 60, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 255) if opaque else Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(30, 12, 40, 20, QColor(0, 0, 0))  # x 30..69, y 12..31
    p.end()
    return png_of(img)


class Calls:
    def __init__(self) -> None:
        self.plain: list[str] = []
        self.variable: list[tuple[str, float]] = []
        self.config: list[tuple[float, float]] = []
        self.rect_args: list[object] = []


def make_fake_appkit(
    *,
    png: bytes,
    calls: Calls,
    known: set[str] | None = None,
    with_variable: bool = True,
    cg_shape: str = "tuple",  # "tuple" | "bare" | "none" (forces the TIFF route)
    fail_config: bool = False,
    fail_probe: bool = False,
    null=None,
) -> types.ModuleType:
    mod = types.ModuleType("AppKit")
    mod.NSFontWeightRegular, mod.NSFontWeightMedium = 0.0, 0.23
    mod.NSFontWeightSemibold, mod.NSFontWeightBold = 0.3, 0.4
    mod.NSBitmapImageFileTypePNG = 4

    class Rep:
        def __init__(self, source) -> None:
            self.source = source

        def representationUsingType_properties_(self, kind, props):  # noqa: N802
            assert kind == 4 and props == {}, (kind, props)
            return png  # stands in for NSData; bytes() of it is the PNG

    class BitmapRep:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithCGImage_(self, cg):  # noqa: N802
            assert cg == "CG", cg
            return Rep(cg)

        @classmethod
        def imageRepWithData_(cls, data):  # noqa: N802
            assert data == "TIFF", data
            return Rep(data)

    class Image:
        def __init__(self, name: str) -> None:
            self.name = name

        def imageWithSymbolConfiguration_(self, cfg):  # noqa: N802
            return self

        def CGImageForProposedRect_context_hints_(self, rect, ctx, hints):  # noqa: N802
            calls.rect_args.append(rect)
            if null is not None and rect is not null:
                raise TypeError("pretend this bridge insists on objc.NULL")
            if cg_shape == "none":
                return None
            return ("CG", rect) if cg_shape == "tuple" else "CG"

        def TIFFRepresentation(self):  # noqa: N802
            return "TIFF"

    class NSImage:
        @staticmethod
        def imageWithSystemSymbolName_accessibilityDescription_(name, desc):  # noqa: N802
            if fail_probe:
                raise RuntimeError("AppKit exploded")
            calls.plain.append(name)
            return Image(name) if known is None or name in known else None

        if with_variable:

            @staticmethod
            def imageWithSystemSymbolName_variableValue_accessibilityDescription_(name, value, desc):  # noqa: N802
                calls.variable.append((name, value))
                return Image(name) if known is None or name in known else None

    class SymbolConfig:
        @staticmethod
        def configurationWithPointSize_weight_(pt, weight):  # noqa: N802
            if fail_config:
                raise RuntimeError("no symbol configuration for you")
            calls.config.append((pt, weight))
            return ("cfg", pt, weight)

    mod.NSImage = NSImage
    mod.NSImageSymbolConfiguration = SymbolConfig
    mod.NSBitmapImageRep = BitmapRep
    return mod


@contextlib.contextmanager
def fake_macos(appkit: types.ModuleType, objc_null: object | None = None):
    saved_platform, saved_modules = sys.platform, {k: sys.modules.get(k) for k in ("AppKit", "objc")}
    sys.platform = "darwin"
    sys.modules["AppKit"] = appkit
    if objc_null is not None:
        fake_objc = types.ModuleType("objc")
        fake_objc.NULL = objc_null
        fake_objc.autorelease_pool = contextlib.nullcontext
        sys.modules["objc"] = fake_objc
    else:
        sys.modules["objc"] = None  # type: ignore[assignment] -- makes `import objc` raise ImportError
    sf_symbols._reset_caches()
    try:
        yield
    finally:
        sys.platform = saved_platform
        for k, v in saved_modules.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        sf_symbols._reset_caches()


GREEN = QColor("#12A150")
CANNED = glyph_png()

# 3a. happy path, tuple return, objc.NULL for the rect
NULL = object()
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, null=NULL), objc_null=NULL):
    check(sf_symbols.native_available(), "native_available() is True once AppKit answers for a symbol")
    pm = sf_symbols.pixmap("mic", 24, GREEN)  # 48 px canvas, 44 px fit
    img = argb(pm)
    check(not pm.isNull() and pm.width() == 48 and pm.devicePixelRatio() == 2.0, "native: 48 px canvas at dpr 2")
    box = bbox(img)
    w, h = box[2] - box[0] + 1, box[3] - box[1] + 1
    check(43 <= w <= 45, f"native: glyph fitted to the canvas minus ~1px padding (width {w})")
    check(abs(h / w - 0.5) < 0.06, f"native: aspect ratio kept (2:1, got {w}x{h})")
    check(abs((box[0] + box[2]) / 2 - 23.5) <= 1 and abs((box[1] + box[3]) / 2 - 23.5) <= 1, "native: glyph centred (the canned art is off-centre)")
    solid = opaque_pixels(img)
    check(bool(solid) and all(abs(c.red() - 0x12) <= 2 and abs(c.green() - 0xA1) <= 2 and abs(c.blue() - 0x50) <= 2 for c in solid), "native: glyph colourised to the requested colour (black mask -> green)")
    check(img.pixelColor(2, 2).alpha() == 0, "native: background stays transparent")
    check(calls.plain[0] == "mic" and calls.variable == [], "native: plain selector for a plain symbol")
    check(all(r is NULL for r in calls.rect_args), "native: objc.NULL is passed for the out rect")
    check(calls.config[0] == (44.0, 0.23), f"native: first pass at point size = fit px, medium weight, got {calls.config[0]}")
    check(len(calls.config) == 2 and abs(calls.config[1][0] - 44.0 * 44 / 40) < 0.5, "native: re-rendered once at the corrected point size")

    before = len(calls.plain)
    other = argb(sf_symbols.pixmap("mic", 24, "#FF0000"))
    check(len(calls.plain) == before, "native: a second colour reuses the rendered mask (AppKit not asked again)")
    check(opaque_pixels(other)[0].red() >= 253, "native: ...and is coloured red")
    again = sf_symbols.pixmap("mic", 24, "#FF0000")
    check(again.cacheKey() == sf_symbols.pixmap("mic", 24, "#FF0000").cacheKey(), "native: repeat request is cached")

    sf_symbols.pixmap("mic", 24, GREEN, weight="bold")
    check(calls.config[-1][1] == 0.4, "native: weight 'bold' maps to NSFontWeightBold")
    sf_symbols.pixmap("checkmark", 24, GREEN, weight="semibold")
    check(calls.config[-1][1] == 0.3, "native: weight 'semibold' maps to NSFontWeightSemibold")
    sf_symbols.pixmap("checkmark", 24, GREEN, weight="regular")
    check(calls.config[-1][1] == 0.0, "native: weight 'regular' maps to NSFontWeightRegular")

    sf_symbols.pixmap("waveform", 24, GREEN, variable=0.5)
    check(calls.variable and set(calls.variable) == {("waveform", 0.5)}, "native: variable value goes through the variableValue selector")
    m = sf_symbols.icon("mic", 16, mask=True)
    check(m.isMask() and not m.pixmap(16, 16).isNull(), "native: mask icon works")

# 3b. bare-image return (no tuple), no objc module at all
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, cg_shape="bare")):
    pm = sf_symbols.pixmap("clock", 20, GREEN)
    check(sf_symbols.native_available() and bbox(argb(pm)) is not None, "native: a bare CGImage return (not a tuple) works, and objc is optional")
    check(calls.rect_args and calls.rect_args[0] is None, "native: without objc the out rect is None")

# 3c. CGImage unavailable -> TIFF route
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, cg_shape="none")):
    pm = sf_symbols.pixmap("clock", 20, GREEN)
    check(bool(opaque_pixels(argb(pm), 250)), "native: falls back to the TIFF representation when there is no CGImage")

# 3d. bridge that rejects NULL but takes None
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, null=None)):
    check(bbox(argb(sf_symbols.pixmap("doc.text", 20, GREEN))) is not None, "native: works with a plain None rect too")

# 3e. macOS without the variable-value selector: plain symbol instead
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, with_variable=False)):
    pm = sf_symbols.pixmap("waveform", 24, GREEN, variable=0.4)
    check(bbox(argb(pm)) is not None and calls.plain[-1] == "waveform", "native: missing variableValue selector -> plain symbol, no exception")

# 3e2. any SF Symbol name the OS knows goes through, listed in SYMBOLS or not
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, known={"mic", "star.fill"})):
    star = argb(sf_symbols.pixmap("star.fill", 24, GREEN))
    check(bool(opaque_pixels(star)), "native: an SF name outside SYMBOLS is rendered natively")
    gone = argb(sf_symbols.pixmap("not.a.symbol", 24, GREEN))
    check(total_alpha(gone) == 0, "native: an unknown name the OS rejects and we cannot draw is empty")

# 3f. symbol the OS doesn't have -> drawn fallback, quietly
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, known={"mic"})):
    log.records.clear()
    drawn = argb(sf_symbols.pixmap("hourglass", 24, GREEN))
    warned_for_missing = log.warnings()
sf_symbols._reset_caches()
ref = argb(sf_symbols.pixmap("hourglass", 24, GREEN))
check(drawn == ref, "native: a symbol AppKit does not know is drawn by the fallback (identical to the pure-Python result)")
check(not warned_for_missing, "native: a symbol simply missing from the OS is not a warning")

# 3g. an opaque rendering is not a usable mask -> fallback
calls = Calls()
with fake_macos(make_fake_appkit(png=glyph_png(opaque=True), calls=calls)):
    got = argb(sf_symbols.pixmap("hourglass", 24, GREEN))
check(got == ref, "native: a fully opaque render (a box, not a glyph) is rejected -> drawn fallback")

# 3h. garbage PNG bytes -> fallback
calls = Calls()
with fake_macos(make_fake_appkit(png=b"definitely not a png", calls=calls)):
    got = argb(sf_symbols.pixmap("hourglass", 24, GREEN))
check(got == ref, "native: undecodable image data -> drawn fallback")

# 3i. AppKit blows up in the probe -> native_available() False, fallback
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, fail_probe=True)):
    check(not sf_symbols.native_available(), "native: an AppKit that raises during the probe counts as unavailable")
    got = argb(sf_symbols.pixmap("hourglass", 24, GREEN))
check(got == ref, "native: ...and everything is drawn")

# 3j. AppKit blows up mid-render -> fallback + one warning, never an exception
calls = Calls()
with fake_macos(make_fake_appkit(png=CANNED, calls=calls, fail_config=True)):
    log.records.clear()
    check(sf_symbols.native_available(), "native: the probe passes...")
    got = argb(sf_symbols.pixmap("hourglass", 24, GREEN))
    got2 = argb(sf_symbols.pixmap("hourglass", 30, GREEN))
    check(got == ref and bbox(got2) is not None, "native: ...but a render failure falls back to the drawn icon (no exception)")
    check(len(log.warnings()) == 1 and "hourglass" in log.warnings()[0], "native: the failure is logged once per symbol")
    check(not sf_symbols.icon("hourglass", 16, mask=True).isNull(), "native: icon() survives it too")

# 3k. wrong platform: even with an AppKit module around, non-darwin never goes native
calls = Calls()
saved = sys.modules.get("AppKit")
sys.modules["AppKit"] = make_fake_appkit(png=CANNED, calls=calls)
sf_symbols._reset_caches()
check(not sf_symbols.native_available() and not calls.plain, "native: never attempted off macOS")
if saved is None:
    sys.modules.pop("AppKit", None)
else:
    sys.modules["AppKit"] = saved
sf_symbols._reset_caches()
check(not sf_symbols.native_available(), "native_available() is False again after the fakes are removed")

# ---- 4. widgets keeps the old names -------------------------------------------------

print("--- 4. widgets.icon / icon_pixmap ---")

for old, new in (("history", "clock"), ("filejob", "waveform"), ("settings", "gearshape"), ("drop", "square.and.arrow.down"), ("empty", "doc.text")):
    pm = widgets.icon_pixmap(old, 24, QColor("#336699"))
    ref_pm = sf_symbols.pixmap(new, 24, QColor("#336699"))
    check(not pm.isNull() and pm.width() == 48 and pm.devicePixelRatio() == 2.0, f"widgets.icon_pixmap({old!r}) -> 48 px at dpr 2")
    check(argb(pm) == argb(ref_pm), f"{old!r} is the {new!r} symbol")
    check(not widgets.icon(old).isNull() and not widgets.icon(old, 22).pixmap(22, 22).isNull(), f"widgets.icon({old!r}) works")

for name in sorted(sf_symbols.SYMBOLS):
    check(not widgets.icon_pixmap(name, 20).isNull(), f"widgets.icon_pixmap passes SF name {name!r} through")

pm_dpr = widgets.icon_pixmap("history", 30, None, device_pixel_ratio=3.0)
check(pm_dpr.width() == 90 and pm_dpr.devicePixelRatio() == 3.0, "device_pixel_ratio is honoured (positionally too)")
normal = argb(widgets.icon("history", 24).pixmap(24, 24, QIcon.Mode.Normal))
selected = argb(widgets.icon("history", 24).pixmap(24, 24, QIcon.Mode.Selected))
n_c, s_c = opaque_pixels(normal)[0], opaque_pixels(selected)[0]
check(abs(n_c.red() - QColor(tokens.text).red()) <= 3, "widgets.icon Normal mode is tinted with the text colour")
check(abs(s_c.red() - 255) <= 3 and abs(s_c.blue() - 255) <= 3, "widgets.icon Selected mode is tinted with accent_text (white)")
check(not hasattr(widgets, "_ICONS") and not hasattr(widgets, "_draw_clock"), "the old drawers moved out of widgets.py")

print(f"\nALL PASSED ({_checks} checks)")
