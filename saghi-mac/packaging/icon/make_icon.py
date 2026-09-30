# Draws the Saghi-mac app icon in the macOS 26/27 style: the word صاغي in Amiri
# Bold, system blue (#0A84FF), on a soft light-glass squircle whose colours are the
# app's design palette (periwinkle #a9c1ff, pink #ffc3ce, sky #9fd4ff).
#
# Font: Amiri Bold (SIL Open Font License 1.1), already shipped with the app in
# saghi/ui/assets/fonts/. Run from this folder with the app's Python (PySide6):
#
#     QT_QPA_PLATFORM=offscreen python3 make_icon.py
#
# Writes saghi-icon-1024.png here, ../AppIcon.icns (every size, no extra tools) and
# ../../saghi/ui/assets/saghi.png (512 px, the in-app / Dock icon).
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient)

HERE = Path(__file__).resolve().parent
FONT_FILE = HERE.parent.parent / "saghi" / "ui" / "assets" / "fonts" / "Amiri-Bold.ttf"
S = 1024
BODY = QRectF(100, 100, 824, 824)  # Apple's macOS icon grid: 824 pt body on a 1024 canvas
NAME = "صاغي"
BLUE = QColor("#0A84FF")


def squircle(rect: QRectF, n: float = 4.2, steps: int = 720) -> QPainterPath:
    """Superellipse |x|^n + |y|^n = 1: the continuous-corner shape of macOS icons."""
    cx, cy, a, b = rect.center().x(), rect.center().y(), rect.width() / 2, rect.height() / 2
    path = QPainterPath()
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(c) ** (2 / n), c)
        y = cy + b * math.copysign(abs(s) ** (2 / n), s)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    return path


def radial(p: QPainter, shape: QPainterPath, fx: float, fy: float, radius: float, color: str) -> None:
    g = QRadialGradient(QPointF(BODY.left() + BODY.width() * fx, BODY.top() + BODY.height() * fy), BODY.width() * radius)
    c0 = QColor(color)
    c1 = QColor(color)
    c1.setAlpha(0)
    g.setColorAt(0.0, c0)
    g.setColorAt(1.0, c1)
    p.fillPath(shape, QBrush(g))


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    fid = QFontDatabase.addApplicationFont(str(FONT_FILE))
    if fid < 0:
        print(f"could not load {FONT_FILE}", file=sys.stderr)
        return 1
    family = QFontDatabase.applicationFontFamilies(fid)[0]

    img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    shape = squircle(BODY)

    # Soft drop shadow (legacy .icns icons carry their own, like Big Sur's grid).
    for i in range(12, 0, -1):
        grow = i * 2.0
        sh = squircle(BODY.adjusted(-grow * 0.5, -grow * 0.2 + 12, grow * 0.5, grow * 0.8 + 12))
        p.fillPath(sh, QColor(15, 27, 61, 4))

    # Glass body: the design's light palette.
    p.fillPath(shape, QColor("#EEF3FC"))
    radial(p, shape, 0.18, 0.14, 0.58, "#a9c1ff")
    radial(p, shape, 0.92, 0.28, 0.55, "#ffc3ce")
    radial(p, shape, 0.50, 1.12, 0.62, "#9fd4ff")

    # Specular sheen on the top half and a faint shade at the bottom.
    p.save()
    p.setClipPath(shape)
    top = QLinearGradient(0, BODY.top(), 0, BODY.center().y())
    top.setColorAt(0.0, QColor(255, 255, 255, 140))
    top.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(BODY.left(), BODY.top(), BODY.width(), BODY.height() / 2), QBrush(top))
    bottom = QLinearGradient(0, BODY.bottom() - BODY.height() * 0.25, 0, BODY.bottom())
    bottom.setColorAt(0.0, QColor(15, 27, 61, 0))
    bottom.setColorAt(1.0, QColor(15, 27, 61, 22))
    p.fillRect(QRectF(BODY.left(), BODY.bottom() - BODY.height() * 0.25, BODY.width(), BODY.height() * 0.25), QBrush(bottom))
    p.restore()

    # Glass rim: a bright hairline all round, fading in a lit band along the top edge.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 200), 4))
    p.drawPath(squircle(BODY.adjusted(2, 2, -2, -2)))
    lit = QLinearGradient(0, BODY.top(), 0, BODY.top() + BODY.height() * 0.45)
    lit.setColorAt(0.0, QColor(255, 255, 255, 230))
    lit.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(QPen(QBrush(lit), 10))
    p.drawPath(squircle(BODY.adjusted(6, 6, -6, -6)))

    # The word, fitted to ~62% of the body width and centred on its ink box.
    font = QFont(family)
    font.setBold(True)
    size = 520
    while True:
        font.setPixelSize(size)
        text = QPainterPath()
        text.addText(0, 0, font, NAME)
        box = text.boundingRect()
        if box.width() <= BODY.width() * 0.62 or size < 60:
            break
        size -= 4
    text.translate(BODY.center().x() - box.center().x(), BODY.center().y() - box.center().y() - BODY.height() * 0.015)

    # Soft blue glow under the letters (the design's text-shadow).
    for width, alpha in ((40, 6), (26, 9), (14, 12)):
        glow = QPen(QColor(10, 132, 255, alpha), width)
        glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(text.translated(0, 16), glow)
    p.fillPath(text, BLUE)
    p.end()

    out_png = HERE / "saghi-icon-1024.png"
    img.save(str(out_png))
    img.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
        str(HERE.parent.parent / "saghi" / "ui" / "assets" / "saghi.png")
    )

    write_icns(img, HERE.parent / "AppIcon.icns")
    print("done")
    return 0


# Every PNG-based entry macOS looks for in an .icns: (type, pixel size).
# icp4/icp5 are the 16/32 px 1x slots (Finder lists, menus); ic11-ic14 are
# the @2x variants of 16/32/128/256.
_ICNS_ENTRIES = (
    (b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256),
    (b"ic09", 512), (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512),
)


def write_icns(img: QImage, path: Path) -> None:
    """Write an .icns (big-endian 'icns' container of PNG entries) without extra tools."""
    import struct

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    chunks = []
    for kind, px in _ICNS_ENTRIES:
        scaled = img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buf, "PNG")
        buf.close()
        png = bytes(data)
        chunks.append(kind + struct.pack(">I", len(png) + 8) + png)
    body = b"".join(chunks)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.exit(main())
