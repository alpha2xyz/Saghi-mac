# Draws the Saghi-mac app icon (saghi-icon-1024.png).
# Font: Lalezar (SIL Open Font License), from https://github.com/google/fonts/tree/main/ofl/lalezar
# Put Lalezar.ttf next to this script, then run it with the app's Python (PySide6).
# Make the macOS icon: sips each size into AppIcon.iconset/, then `iconutil -c icns AppIcon.iconset`.
import sys, os
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (QGuiApplication, QImage, QPainter, QColor, QFont, QFontDatabase,
                           QFontMetricsF, QPainterPath, QPen, QBrush)
app = QGuiApplication(sys.argv)
S = 1024; BLUE = QColor("#2F6BFF"); MELON = QColor("#FC6C85"); NAME = "صاغي"
for key, file, weight in [("lalezar", "Lalezar.ttf", QFont.Normal)]:
    fam = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(os.path.abspath(file)))[0]
    f = QFont(fam); f.setWeight(weight); size = 520
    while True:
        f.setPixelSize(size)
        if QFontMetricsF(f).horizontalAdvance(NAME) <= S * 0.60 or size < 50: break
        size -= 4
    img = QImage(S, S, QImage.Format_ARGB32); img.fill(Qt.transparent)
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing)
    path = QPainterPath(); path.addRoundedRect(QRectF(40, 40, S-80, S-80), 200, 200)
    p.fillPath(path, QColor("#0F1B3D"))
    p.setFont(f); p.setPen(QPen(QBrush(MELON), 1)); p.drawText(QRectF(0, -50, S, S), Qt.AlignCenter, NAME)
    p.setPen(Qt.NoPen); p.setBrush(BLUE)
    for i, h in enumerate([40, 90, 140, 90, 40]):
        x = S/2 - 140 + i*70; p.drawRoundedRect(QRectF(x-18, 800 - h/2, 36, h), 18, 18)
    p.end(); img.save(f"saghi-icon-1024.png")
print("done")
