"""
The app's icons, as Apple's own SF Symbols.

Why: a hand-drawn icon can only ever look *like* the system's, and the system's
look moves with every macOS release (Liquid Glass changed the weights again).
Asking macOS for the symbol by name means our sidebar, buttons and menu-bar item
always match whatever the Mac is running.

  pixmap(name, size, color)   a square QPixmap, glyph centred and fitted.
  icon(name, size, ...)       the same as a QIcon (mask=True for the menu bar).

NATIVE PATH (macOS + pyobjc, the app already ships it for paste.py):
NSImage.imageWithSystemSymbolName: -> NSImageSymbolConfiguration (point size +
weight) -> CGImage -> PNG bytes -> QImage. AppKit hands back the glyph as a
black shape on transparent; we use only its alpha as a mask and fill it with
the colour Qt asked for (theme colours are ours, not AppKit's, so the drawn
result never depends on which NSAppearance happened to be current). Going via
PNG bytes keeps every pointer/CF type on the AppKit side: nothing but plain
bytes crosses into Qt.

FALLBACK PATH (Linux test runs, pyobjc missing, a symbol the OS lacks, any
AppKit error): the same glyphs drawn with QPainter from SVG-style path data on
a 24-unit grid -- round caps and joins, ~1.8 stroke, the look of SF Symbols'
"medium" weight. The path data is parsed by a tiny SVG-path reader below so the
shapes stay copy-compatible with the design's SVGs and QtSvg is not needed.

Everything native is best effort: it never raises, and a name that fails once
is remembered so the failure costs nothing the second time.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import math
import re
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

from . import theme

logger = logging.getLogger("saghi.ui.sf_symbols")

GRID = 24.0  # the drawn fallbacks live on a 24x24 grid

# Stroke width on the 24 grid for each SF weight (medium is the app's default).
_STROKE = {"regular": 1.5, "medium": 1.8, "semibold": 2.1, "bold": 2.4}
# NSFontWeight values, used only if AppKit does not export the constants.
_NS_WEIGHT = {"regular": 0.0, "medium": 0.23, "semibold": 0.3, "bold": 0.4}
_NS_PNG = 4  # NSBitmapImageFileTypePNG
# Variable-colour layers that are "off" render at this opacity (as SF does).
_DIM = 0.3


# ---- drawn fallbacks: SVG path data -------------------------------------------------

_TOKEN = re.compile(r"[MmLlHhVvCcSsAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "A": 7, "Z": 0}


def _arc_to(path: QPainterPath, p0: QPointF, rx: float, ry: float, large: bool, sweep: bool, p1: QPointF) -> None:
    """SVG elliptical arc (endpoint form, no x-axis rotation) -> QPainterPath.arcTo.

    Centre parameterisation is SVG 1.1 implementation notes F.6.5. SVG angles run
    clockwise on screen (y down), Qt's counter-clockwise, hence the sign flips.
    Only circular arcs are used, where Qt's and SVG's angles agree exactly.
    """
    rx, ry = abs(rx), abs(ry)
    if p0 == p1:
        return
    if rx == 0 or ry == 0:
        path.lineTo(p1)
        return
    dx, dy = (p0.x() - p1.x()) / 2, (p0.y() - p1.y()) / 2
    lam = (dx / rx) ** 2 + (dy / ry) ** 2
    if lam > 1:  # radii too small to reach: scale up, as the SVG spec says
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num = (rx * ry) ** 2 - (rx * dy) ** 2 - (ry * dx) ** 2
    den = (rx * dy) ** 2 + (ry * dx) ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * dy / ry, -coef * ry * dx / rx
    cx, cy = cxp + (p0.x() + p1.x()) / 2, cyp + (p0.y() + p1.y()) / 2
    t1 = math.atan2((dy - cyp) / ry, (dx - cxp) / rx)
    t2 = math.atan2((-dy - cyp) / ry, (-dx - cxp) / rx)
    dt = t2 - t1
    if sweep and dt < 0:
        dt += 2 * math.pi
    elif not sweep and dt > 0:
        dt -= 2 * math.pi
    path.arcTo(QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry), -math.degrees(t1), -math.degrees(dt))


def _parse_path(d: str) -> QPainterPath:
    """The subset of SVG path data the icons need: M L H V C S A Z, absolute and relative."""
    tokens = _TOKEN.findall(d)
    path = QPainterPath()
    cur = start = QPointF(0, 0)
    ctrl: QPointF | None = None  # last cubic control point, for S
    cmd = ""
    i = 0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                path.closeSubpath()
                cur, ctrl = start, None
                continue
        n = _ARITY[cmd.upper()]
        a = [float(t) for t in tokens[i : i + n]]
        i += n
        rel = cmd.islower()
        ox, oy = (cur.x(), cur.y()) if rel else (0.0, 0.0)
        up = cmd.upper()
        new_ctrl: QPointF | None = None
        if up == "M":
            cur = start = QPointF(ox + a[0], oy + a[1])
            path.moveTo(cur)
            cmd = "l" if rel else "L"  # extra coordinate pairs after M are implicit lineto
        elif up == "L":
            cur = QPointF(ox + a[0], oy + a[1])
            path.lineTo(cur)
        elif up == "H":
            cur = QPointF(ox + a[0], cur.y())
            path.lineTo(cur)
        elif up == "V":
            cur = QPointF(cur.x(), oy + a[0])
            path.lineTo(cur)
        elif up in ("C", "S"):
            if up == "C":
                c1 = QPointF(ox + a[0], oy + a[1])
                a = a[2:]
            else:  # smooth: first control point mirrors the previous one
                c1 = QPointF(2 * cur.x() - ctrl.x(), 2 * cur.y() - ctrl.y()) if ctrl else QPointF(cur)
            new_ctrl = QPointF(ox + a[0], oy + a[1])
            cur = QPointF(ox + a[2], oy + a[3])
            path.cubicTo(c1, new_ctrl, cur)
        elif up == "A":
            end = QPointF(ox + a[5], oy + a[6])
            _arc_to(path, cur, a[0], a[1], bool(a[3]), bool(a[4]), end)
            cur = end
        ctrl = new_ctrl
    return path


@dataclass(frozen=True)
class _Shape:
    d: str
    kind: str = "stroke"  # "stroke" | "fill" (fill + outline) | "cut" (erase what is below)
    width: float | None = None  # absolute width on the grid; None = the weight's stroke
    level: int | None = None  # variable-colour layer (waveform bars)


@dataclass(frozen=True)
class _Glyph:
    shapes: tuple[_Shape, ...]
    levels: int = 0  # how many variable-colour layers there are


def _s(d: str, **kw) -> _Shape:
    return _Shape(d, **kw)


_MIC_CAPSULE = "M9 5.5a3 3 0 0 1 6 0V11a3 3 0 0 1-6 0z"  # rect x9 y2.5 w6 h11.5 rx3
_MIC_STAND = ("M5.5 11.2a6.5 6.5 0 0 0 13 0", "M12 17.8v3.2", "M8.8 21h6.4")
_WAVE_BARS = ("M3 10v4", "M6.6 7v10", "M10.2 3.5v17", "M13.8 6v12", "M17.4 8.5v7", "M21 10.5v3")  # half-heights 2,5,8.5,6,3.5,1.5
_TRIANGLE = "M10.3 3.9 2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"
_BANG = ("M12 9v4.6", "M12 17.1h.01")  # the stem, and the dot (a round-capped speck)


def _gear_path() -> str:
    """Eight flat-topped teeth around a ring, as path data (carried over from the old drawn gear)."""
    outer, inner, teeth = 10.0, 7.4, 8
    pts = []
    for i in range(teeth * 2):
        r = outer if i % 2 == 0 else inner
        for a in (i / (teeth * 2) * 2 * math.pi, (i + 1) / (teeth * 2) * 2 * math.pi):
            pts.append(f"{12 + r * math.cos(a):.3f} {12 + r * math.sin(a):.3f}")
    return "M" + " L".join(pts) + "z"


_GLYPHS: dict[str, _Glyph] = {
    "mic": _Glyph((_s(_MIC_CAPSULE), *(_s(d) for d in _MIC_STAND))),
    "mic.fill": _Glyph((_s(_MIC_CAPSULE, kind="fill"), *(_s(d) for d in _MIC_STAND))),
    "mic.slash": _Glyph(
        (
            _s(_MIC_CAPSULE),
            *(_s(d) for d in _MIC_STAND),
            _s("M4.2 4.2 19.8 19.8", kind="cut", width=4.6),  # the gap SF leaves around a slash
            _s("M4.2 4.2 19.8 19.8"),
        )
    ),
    "waveform": _Glyph(tuple(_s(d, level=i) for i, d in enumerate(_WAVE_BARS)), levels=len(_WAVE_BARS)),
    "hourglass": _Glyph(
        (
            _s("M6.5 2.8h11"),
            _s("M6.5 21.2h11"),
            _s("M7.5 3c0 5 9 5 9 9s-9 4-9 9"),
            _s("M16.5 3c0 5-9 5-9 9s9 4 9 9"),
        )
    ),
    "exclamationmark.triangle": _Glyph((_s(_TRIANGLE), *(_s(d) for d in _BANG))),
    # filled: the "!" is a real hole, not a second colour, so it works on any background
    "exclamationmark.triangle.fill": _Glyph((_s(_TRIANGLE, kind="fill"), *(_s(d, kind="cut", width=2.0) for d in _BANG))),
    "clock": _Glyph((_s("M12 2.9a9.1 9.1 0 1 1 0 18.2a9.1 9.1 0 1 1 0-18.2z"), _s("M12 6.8V12l3.6 2.2"))),
    "gearshape": _Glyph((_s(_gear_path()), _s("M12 9.1a2.9 2.9 0 1 1 0 5.8a2.9 2.9 0 1 1 0-5.8z"))),
    "square.and.arrow.down": _Glyph(
        (
            _s("M8.2 8.6H6.5A2.5 2.5 0 0 0 4 11.1v8.4A2.5 2.5 0 0 0 6.5 22h11a2.5 2.5 0 0 0 2.5-2.5v-8.4a2.5 2.5 0 0 0-2.5-2.5h-1.7"),
            _s("M12 2.8v12.4"),
            _s("M8.2 11.6 12 15.4l3.8-3.8"),
        )
    ),
    "doc.text": _Glyph(
        (
            _s("M7 2.8h7l5.2 5.2v11.2a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4.8a2 2 0 0 1 2-2z"),
            _s("M14 3.2V7a1 1 0 0 0 1 1h3.8"),  # the folded corner
            _s("M8.6 12.4h6.8"),
            _s("M8.6 15.6h6.8"),
            _s("M8.6 18.6h4"),
        )
    ),
    "arrow.down.circle": _Glyph(
        (
            _s("M12 2.9a9.1 9.1 0 1 1 0 18.2a9.1 9.1 0 1 1 0-18.2z"),
            _s("M12 7v9.4"),
            _s("M8.2 12.6 12 16.4l3.8-3.8"),
        )
    ),
    "checkmark": _Glyph((_s("M4.8 12.8l4.8 4.8L19.4 6.6"),)),
}

SYMBOLS: frozenset[str] = frozenset(_GLYPHS)


@functools.cache
def _glyph_paths(name: str) -> tuple[QPainterPath, ...]:
    return tuple(_parse_path(shape.d) for shape in _GLYPHS[name].shapes)


def _draw_fallback(name: str, color: QColor, weight: str, variable: float | None, px: int) -> QImage:
    """Draw a symbol with QPainter into a px-by-px transparent image (device pixels)."""
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    glyph = _GLYPHS[name]
    stroke = _STROKE.get(weight, _STROKE["medium"])
    lit = glyph.levels
    if variable is not None and glyph.levels:
        lit = math.ceil(round(min(1.0, max(0.0, variable)) * glyph.levels, 6))

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(px / GRID, px / GRID)
    for shape, path in zip(glyph.shapes, _glyph_paths(name)):
        pen = QPen(color, shape.width or stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setOpacity(_DIM if shape.level is not None and shape.level >= lit else 1.0)
        if shape.kind == "cut":
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            continue
        p.setPen(pen)
        p.setBrush(color if shape.kind == "fill" else Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.end()
    return img


# ---- native path (macOS SF Symbols via pyobjc) --------------------------------------

_native_ok: bool | None = None
_warned: set[str] = set()


def _probe_native() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import AppKit

        # macOS 11+; None back means AppKit has no such symbol image (never for "mic")
        return AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("mic", None) is not None
    except Exception:  # noqa: BLE001 -- ImportError, AttributeError (old OS), any pyobjc quirk
        logger.debug("SF Symbols not available natively", exc_info=True)
        return False


def native_available() -> bool:
    """True only where macOS will render SF Symbols for us (asked once, then cached)."""
    global _native_ok
    if _native_ok is None:
        _native_ok = _probe_native()
    return _native_ok


def _autorelease_pool():
    try:
        import objc

        return objc.autorelease_pool()
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()


def _ns_symbol(AppKit, name: str, weight: str, variable: float | None, point_size: float):
    """The NSImage for a symbol at a point size and weight, or None if the OS has no such symbol."""
    image = None
    if variable is not None:
        try:  # macOS 13+ (the app's minimum); AttributeError on anything older
            image = AppKit.NSImage.imageWithSystemSymbolName_variableValue_accessibilityDescription_(
                name, float(variable), None
            )
        except AttributeError:
            image = None
    if image is None:  # no variable value asked for, or the symbol takes none
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None
    ns_weight = getattr(AppKit, "NSFontWeight" + weight.capitalize(), _NS_WEIGHT.get(weight, _NS_WEIGHT["medium"]))
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(point_size, ns_weight)
    configured = image.imageWithSymbolConfiguration_(config)
    return image if configured is None else configured


def _cg_image(image):
    """A CGImage snapshot of an NSImage.

    pyobjc treats -CGImageForProposedRect:context:hints:'s rect as an `out`
    argument, which stays in the Python argument list and turns the result into
    an (image, rect) tuple; objc.NULL asks AppKit for its default rect. Older or
    newer bridges may hand back the bare image, so accept both.
    """
    try:
        import objc

        null = getattr(objc, "NULL", None)
    except Exception:  # noqa: BLE001
        null = None
    for rect_arg in (null, None):
        try:
            result = image.CGImageForProposedRect_context_hints_(rect_arg, None, None)
        except (TypeError, ValueError):
            continue
        cg = result[0] if isinstance(result, tuple) else result
        if cg is not None:
            return cg
    return None


def _png_bytes(AppKit, image) -> bytes | None:
    cg = _cg_image(image)
    if cg is not None:
        rep = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(cg)
    else:  # no CGImage: the TIFF representation is a plain bitmap too
        tiff = image.TIFFRepresentation()
        rep = AppKit.NSBitmapImageRep.imageRepWithData_(tiff) if tiff is not None else None
    if rep is None:
        return None
    data = rep.representationUsingType_properties_(getattr(AppKit, "NSBitmapImageFileTypePNG", _NS_PNG), {})
    return bytes(data) if data is not None else None


_ALPHA_ON = bytes(0 if i <= 8 else 1 for i in range(256))


def _crop_to_glyph(img: QImage) -> QImage | None:
    """Crop to the pixels that carry the glyph. None if it has no shape (blank, or fully opaque)."""
    alpha = img.convertToFormat(QImage.Format.Format_Alpha8)
    w, h, bpl = alpha.width(), alpha.height(), alpha.bytesPerLine()
    data = bytes(alpha.constBits())
    left, right, top, bottom, lowest = w, -1, h, -1, 255
    for y in range(h):
        row = data[y * bpl : y * bpl + w]
        lowest = min(lowest, min(row, default=255))
        marks = row.translate(_ALPHA_ON)
        first = w - len(marks.lstrip(b"\x00"))
        if first == w:
            continue
        top, bottom = min(top, y), y
        left, right = min(left, first), max(right, len(marks.rstrip(b"\x00")) - 1)
    if right < 0 or lowest >= 250:
        # blank, or an opaque box (a rendering with a background, not a glyph): not usable as a mask
        return None
    return img.copy(left, top, right - left + 1, bottom - top + 1).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )


def _render_native(name: str, weight: str, variable: float | None, point_size: float) -> QImage | None:
    import AppKit

    with _autorelease_pool():
        image = _ns_symbol(AppKit, name, weight, variable, point_size)
        png = _png_bytes(AppKit, image) if image is not None else None
    if not png:
        return None
    decoded = QImage.fromData(png, "PNG")
    return None if decoded.isNull() else _crop_to_glyph(decoded)


_masks: dict[tuple, QImage | None] = {}


def _native_mask(name: str, weight: str, variable: float | None, fit_px: int) -> QImage | None:
    """The system symbol as a glyph-cropped alpha mask whose longer side is close to fit_px pixels."""
    key = (name, weight, variable, fit_px)
    if key in _masks:
        return _masks[key]
    mask: QImage | None = None
    try:
        # SF scales strokes with the point size, so render at the size we need instead of
        # resampling: one measuring pass, and a second only if the first was off by > 4 %.
        point_size = float(fit_px)
        for _ in range(2):
            mask = _render_native(name, weight, variable, point_size)
            if mask is None:
                break
            k = fit_px / max(mask.width(), mask.height())
            if abs(k - 1) < 0.04:
                break
            point_size *= k
    except Exception as exc:  # noqa: BLE001 -- pyobjc / AppKit can fail in many ways; the drawn icon covers
        mask = None
        if name not in _warned:
            _warned.add(name)
            logger.warning("native SF Symbol %r failed (%s: %s); using the drawn icon", name, type(exc).__name__, exc)
    if mask is None:
        logger.debug("no native SF Symbol for %r", name)
    _masks[key] = mask
    return mask


def _native_image(name: str, color: QColor, weight: str, variable: float | None, px: int, dpr: float) -> QImage | None:
    fit = max(1, px - round(2 * dpr))  # ~1 logical px of air on every side
    mask = _native_mask(name, weight, variable, fit)
    if mask is None:
        return None
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    scale = min(fit / mask.width(), fit / mask.height())
    w, h = mask.width() * scale, mask.height() * scale
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage(QRectF((px - w) / 2, (px - h) / 2, w, h), mask)
    # colourise: keep the mask's alpha, replace its (black) colour
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(img.rect(), color)
    p.end()
    return img


# ---- public API -----------------------------------------------------------------------

_pixmaps: dict[tuple, QPixmap] = {}
_CACHE_LIMIT = 512


def _resolve_color(color) -> QColor:
    if isinstance(color, QColor):
        resolved = QColor(color)
    elif isinstance(color, str):
        resolved = theme.qcolor(color)  # also understands the tokens' rgba(...)
    else:
        resolved = QColor()
    return resolved if resolved.isValid() else theme.qcolor(theme.tokens().text)


def pixmap(
    name: str,
    size: int,
    color=None,
    *,
    weight: str = "medium",
    variable: float | None = None,
    dpr: float = 2.0,
) -> QPixmap:
    """A `size` x `size` (logical px) pixmap of the symbol, `size * dpr` pixels across.

    color: QColor | '#RRGGBB' / 'rgba(...)' string | None (the theme's text colour).
    weight: 'regular' | 'medium' | 'semibold' | 'bold'.
    variable: 0..1 SF "variable colour" value (waveform lights its bars up to it), None = plain.
    An unknown name gives an empty pixmap (and one logged warning), never an exception.
    """
    col = _resolve_color(color)
    weight = weight if weight in _STROKE else "medium"
    px = max(1, round(size * dpr))
    key = (name, px, col.rgba(), weight, None if variable is None else round(float(variable), 4), dpr)
    hit = _pixmaps.get(key)
    if hit is not None:
        return hit

    var = key[4]
    img = None
    if native_available():
        img = _native_image(name, col, weight, var, px, dpr)
    if img is None and name in SYMBOLS:
        img = _draw_fallback(name, col, weight, var, px)
    if img is None:
        if name not in _warned:
            _warned.add(name)
            logger.warning("unknown SF Symbol %r; showing nothing", name)
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)

    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    if len(_pixmaps) >= _CACHE_LIMIT:
        _pixmaps.clear()
    _pixmaps[key] = pm
    return pm


def icon(
    name: str,
    size: int = 16,
    color=None,
    *,
    weight: str = "medium",
    variable: float | None = None,
    mask: bool = False,
) -> QIcon:
    """The symbol as a QIcon with 1x and 2x pixmaps.

    mask=True: a black glyph flagged as a template image, so macOS tints it itself
    (light/dark menu bar, highlight when the item is pressed).
    """
    ic = QIcon()
    tint = QColor(Qt.GlobalColor.black) if mask else color
    for dpr in (1.0, 2.0):
        ic.addPixmap(pixmap(name, size, tint, weight=weight, variable=variable, dpr=dpr))
    if mask:
        ic.setIsMask(True)
    return ic


def _reset_caches() -> None:
    """Forget the native probe and every cache (tests swap AppKit in and out)."""
    global _native_ok
    _native_ok = None
    _warned.clear()
    _masks.clear()
    _pixmaps.clear()
