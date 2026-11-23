"""Draws the GitHub social preview (1280x640) into packaging/social/social-preview.png.

Run from saghi-mac/ with the app's Python:  python3 packaging/social/make_social_preview.py
Uses the notch screenshot in dev/screenshots/ and the app icon.
"""
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parents[2]
W, H = 1280, 640

from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
app = _QA(sys.argv)
img = QImage(W, H, QImage.Format.Format_ARGB32)
p = QPainter(img)
p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.TextAntialiasing)

g = QLinearGradient(0, 0, 0, H)
g.setColorAt(0, QColor("#1c2b5a"))
g.setColorAt(1, QColor("#0b0f1e"))
p.fillRect(0, 0, W, H, g)

# the real notch island (saghi/ui/notch.py), drawn mid-dictation with a fake clock
sys.path.insert(0, str(ROOT))
import math  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi.ui import notch  # noqa: E402

T = [0.0]
k = [0]


def level():
    k[0] += 1
    t = k[0] * 0.016
    return [], (0.012 + 0.03 * abs(math.sin(t * 4.3)) * (0.6 + 0.4 * math.sin(t * 1.7))) if t > 0.5 else 0.0


isl = notch.NotchIsland(clock=lambda: T[0])
isl.show_recording(level)
for _ in range(int(4.2 / 0.016)):
    T[0] += 0.016
    isl._tick()
SCALE = 2.6
shot = QImage(int(isl.width() * SCALE), int(isl.height() * SCALE), QImage.Format.Format_ARGB32)
shot.setDevicePixelRatio(SCALE)
shot.fill(QColor(0, 0, 0, 0))
isl.clearMask()
isl.render(shot)
p.drawImage(QRectF((W - isl.width() * SCALE) / 2, 0, isl.width() * SCALE, isl.height() * SCALE), shot)

icon = QImage(str(ROOT / "packaging/icon/saghi-icon-1024.png"))
p.drawImage(QRectF(W / 2 - 60, 262, 120, 120), icon)

def text(s, y, size, weight, color):
    f = QFont()
    f.setPixelSize(size)
    f.setWeight(weight)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(40, y, W - 80, size * 1.6), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, s)

text("صاغي للماك", 385, 64, QFont.Weight.Bold, "#ffffff")
text("كتابة صوتية عربية مجانية، بدون إنترنت، من النوتش", 480, 32, QFont.Weight.Medium, "#c9d4f2")
text("Free offline Arabic voice typing for Apple-silicon Macs", 545, 26, QFont.Weight.Normal, "#8e9bc4")
p.end()
out = ROOT / "packaging/social/social-preview.png"
img.save(str(out))
print(out)
