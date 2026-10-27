"""Regenerate src/telecaster/assets/icon.png (run: python packaging/make_icon.py)."""

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

SIZE = 1024
OUT = Path(__file__).resolve().parents[1] / "src" / "telecaster" / "assets" / "icon.png"

app = QGuiApplication(sys.argv)
image = QImage(SIZE, SIZE, QImage.Format_ARGB32)
image.fill(Qt.transparent)
p = QPainter(image)
p.setRenderHint(QPainter.Antialiasing)

# Rounded "window" tile with a title bar.
margin = SIZE * 0.08
tile = QRectF(margin, margin, SIZE - 2 * margin, SIZE - 2 * margin)
p.setPen(Qt.NoPen)
p.setBrush(QColor("#1f2430"))
p.drawRoundedRect(tile, SIZE * 0.18, SIZE * 0.18)
p.setBrush(QColor("#2d3446"))
bar = QPainterPath()
bar.addRoundedRect(tile, SIZE * 0.18, SIZE * 0.18)
clip = QPainterPath()
clip.addRect(QRectF(tile.left(), tile.top(), tile.width(), SIZE * 0.17))
p.drawPath(bar.intersected(clip))
for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
    p.setBrush(QColor(color))
    p.drawEllipse(QPointF(tile.left() + SIZE * (0.11 + i * 0.07), tile.top() + SIZE * 0.085), SIZE * 0.025, SIZE * 0.025)

# A hand-drawn circle annotation and a pointer arrow.
pen = QPen(QColor("#ff3b30"), SIZE * 0.045, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
p.setPen(pen)
p.setBrush(Qt.NoBrush)
s = SIZE
loop = QPainterPath(QPointF(s * 0.60, s * 0.36))
loop.cubicTo(s * 0.48, s * 0.30, s * 0.26, s * 0.36, s * 0.26, s * 0.50)
loop.cubicTo(s * 0.26, s * 0.64, s * 0.50, s * 0.68, s * 0.62, s * 0.60)
loop.cubicTo(s * 0.72, s * 0.53, s * 0.70, s * 0.40, s * 0.55, s * 0.37)
p.drawPath(loop)
tip = QPointF(s * 0.70, s * 0.70)
p.drawLine(QPointF(s * 0.84, s * 0.84), tip)
p.drawLine(tip, QPointF(s * 0.79, s * 0.70))
p.drawLine(tip, QPointF(s * 0.70, s * 0.79))
p.end()

OUT.parent.mkdir(parents=True, exist_ok=True)
image.save(str(OUT))
print(f"wrote {OUT}")
