"""The transparent, always-on-top window laid exactly over the target window.

The overlay never takes focus. In ``passive`` mode it is fully click-through,
so the window underneath keeps every click. In ``draw`` mode it captures the
mouse so viewers can draw; ``calibrate`` lets a viewer box in the shared screen.

Positions are converted to [0, 1] against the overlay's own size, so DPI
scaling and resolution differences between machines cancel out.
"""

from __future__ import annotations

import itertools
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .geometry import Rect, normalize
from .platforms import current as platform
from .protocol import MAX_POINTS_PER_MESSAGE
from .strokes import StrokeStore

LOCAL = "local"
FLUSH_MS = 30
ANIMATE_MS = 33
MIN_STEP_PX = 1.5


class Overlay(QWidget):
    outgoing = Signal(dict)  # protocol messages for the relay
    calibrated = Signal(float, float, float, float)  # selection as fractions of the overlay
    calibration_cancelled = Signal()

    def __init__(self, store: StrokeStore) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Only the control window decides when the app quits.
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setWindowTitle("Telecaster overlay")

        self.store = store
        self.mode = "passive"
        self.pen_color = "#ff3b30"
        self.pen_width = 6 / 1080
        self.pen_border = "#000000"
        self.pen_border_width = 0.0
        self.pen_ttl = 1.0
        self.exclude_from_capture = False

        self._sids = itertools.count(1)
        self._sid: str | None = None
        self._pending: list[list[float]] = []
        self._last_px: QPointF | None = None
        self._cal_start: QPointF | None = None
        self._cal_end: QPointF | None = None

        self._flush_timer = QTimer(self, interval=FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._anim_timer = QTimer(self, interval=ANIMATE_MS)
        self._anim_timer.timeout.connect(self._animate)

    # -- window management -------------------------------------------------

    def _apply_native(self) -> None:
        platform.prepare_overlay(self)
        platform.overlay_click_through(self, self.mode == "passive")
        platform.overlay_exclude_from_capture(self, self.exclude_from_capture)

    def place(self, rect: Rect) -> None:
        """Cover ``rect`` (in the platform's screen coordinates) exactly."""
        if not self.isVisible():
            # Before showing so Windows never shows a click-blocking frame;
            # after, because macOS only has a native window once shown.
            self._apply_native()
            self.show()
            self._apply_native()
        platform.place_overlay(self, rect)

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self.exclude_from_capture = enabled
        if self.isVisible():
            platform.overlay_exclude_from_capture(self, enabled)

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        if self.mode == "draw":
            self._finish_stroke()
        self.mode = mode
        self._cal_start = self._cal_end = None
        if self.isVisible():
            platform.overlay_click_through(self, mode == "passive")
        self.setCursor(Qt.ArrowCursor if mode == "passive" else Qt.CrossCursor)
        self.update()

    def refresh(self) -> None:
        if self.store.animating:
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        self.update()

    def _animate(self) -> None:
        self.store.prune(time.monotonic())
        if not self.store.animating:
            self._anim_timer.stop()
        self.update()

    # -- input ---------------------------------------------------------------

    def _norm(self, pos: QPointF) -> list[float]:
        x, y = normalize(pos.x(), pos.y(), self.width(), self.height())
        return [round(x, 5), round(y, 5)]

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if self.mode == "calibrate":
            if event.button() == Qt.RightButton:
                self.calibration_cancelled.emit()
            else:
                self._cal_start = self._cal_end = pos
            self.update()
            return
        if self.mode != "draw":
            return
        if event.button() == Qt.RightButton:
            pt = self._norm(pos)
            self.store.ping(LOCAL, self.pen_color, pt[0], pt[1], time.monotonic())
            self.outgoing.emit({"t": "ping", "p": pt, "color": self.pen_color})
            self.refresh()
        elif event.button() == Qt.LeftButton:
            self._finish_stroke()
            self._sid = str(next(self._sids))
            pt = self._norm(pos)
            self._last_px = pos
            self.store.begin(LOCAL, self._sid, self.pen_color, self.pen_width, self.pen_ttl,
                             [tuple(pt)], time.monotonic(), self.pen_border, self.pen_border_width)
            self.outgoing.emit({"t": "begin", "sid": self._sid, "color": self.pen_color,
                                "width": self.pen_width, "ttl": self.pen_ttl, "border": self.pen_border,
                                "border_width": self.pen_border_width, "p": [pt]})
            self._flush_timer.start()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self.mode == "calibrate" and self._cal_start is not None:
            self._cal_end = pos
            self.update()
            return
        if self.mode != "draw" or self._sid is None or self._last_px is None:
            return
        if math.dist((pos.x(), pos.y()), (self._last_px.x(), self._last_px.y())) < MIN_STEP_PX:
            return
        self._last_px = pos
        pt = self._norm(pos)
        self._pending.append(pt)
        self.store.extend(LOCAL, self._sid, [tuple(pt)])
        if len(self._pending) >= MAX_POINTS_PER_MESSAGE:
            self._flush()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.mode == "calibrate" and self._cal_start is not None:
            sel = QRectF(self._cal_start, event.position()).normalized()
            self._cal_start = self._cal_end = None
            if sel.width() >= 20 and sel.height() >= 20 and self.width() and self.height():
                self.calibrated.emit(sel.x() / self.width(), sel.y() / self.height(),
                                     sel.width() / self.width(), sel.height() / self.height())
            self.update()
            return
        if event.button() == Qt.LeftButton:
            self._finish_stroke()

    def _flush(self) -> None:
        if self._sid is not None and self._pending:
            self.outgoing.emit({"t": "pts", "sid": self._sid, "p": self._pending})
            self._pending = []

    def _finish_stroke(self) -> None:
        if self._sid is None:
            return
        self._flush()
        self._flush_timer.stop()
        self.outgoing.emit({"t": "end", "sid": self._sid})
        self.store.end(LOCAL, self._sid, time.monotonic())
        self._sid = None
        self._last_px = None
        self.refresh()

    # -- painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if self.mode == "draw":
            # Near-invisible fill: fully transparent pixels of a layered window
            # pass clicks through, so give every pixel a sliver of alpha.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 3))
            border = QPen(QColor(self.pen_color), 3, Qt.DashLine)
            painter.setPen(border)
            painter.drawRect(QRectF(1.5, 1.5, w - 3, h - 3))
        elif self.mode == "calibrate":
            painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
            painter.setPen(QColor("white"))
            painter.drawText(self.rect().adjusted(0, 20, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                             "Drag a box around the shared screen.  Right-click to cancel.")
            if self._cal_start is not None and self._cal_end is not None:
                sel = QRectF(self._cal_start, self._cal_end).normalized()
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.fillRect(sel, QColor(255, 255, 255, 30))
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                painter.setPen(QPen(QColor("#34c759"), 2))
                painter.drawRect(sel)
            return

        now = time.monotonic()
        for stroke in self.store.strokes.values():
            alpha = stroke.alpha(now)
            if alpha <= 0 or not stroke.points:
                continue
            fill_px = max(1.0, stroke.width * h)
            border_px = stroke.border_width * h
            x0, y0 = stroke.points[0]
            path = QPainterPath(QPointF(x0 * w, y0 * h))
            for x, y in stroke.points[1:]:
                path.lineTo(x * w, y * h)
            if len(stroke.points) == 1:
                path.lineTo(x0 * w + 0.01, y0 * h)  # a dot: round caps make it a circle
            if border_px < 0.5:
                color = QColor(stroke.color)
                color.setAlphaF(alpha)
                painter.setPen(QPen(color, fill_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)
            elif alpha >= 1:
                _paint_outlined(painter, path, stroke.color, fill_px, stroke.border, border_px)
            else:
                # Fading: paint opaque into a layer, then fade the layer, so the
                # border doesn't show through the half-transparent fill.
                reach = (fill_px / 2 + border_px + 2)
                box = path.boundingRect().adjusted(-reach, -reach, reach, reach).toAlignedRect()
                box = box.intersected(self.rect())
                if box.isEmpty():
                    continue
                layer = QImage(box.size(), QImage.Format_ARGB32_Premultiplied)
                layer.fill(0)
                lp = QPainter(layer)
                lp.setRenderHint(QPainter.Antialiasing)
                lp.translate(-box.x(), -box.y())
                _paint_outlined(lp, path, stroke.color, fill_px, stroke.border, border_px)
                lp.end()
                painter.setOpacity(alpha)
                painter.drawImage(box.topLeft(), layer)
                painter.setOpacity(1.0)

        painter.setBrush(Qt.NoBrush)
        for ping in self.store.pings:
            progress = ping.progress(now)
            if not 0 <= progress < 1:
                continue
            center = QPointF(ping.x * w, ping.y * h)
            color = QColor(ping.color)
            for ring in (0.0, 0.25):
                p = progress - ring
                if p <= 0:
                    continue
                color.setAlphaF(max(0.0, 1 - p))
                painter.setPen(QPen(color, max(2.0, h * 0.004)))
                radius = h * 0.05 * p
                painter.drawEllipse(center, radius, radius)


def _paint_outlined(painter: QPainter, path: QPainterPath, fill: str, fill_px: float,
                    border: str, border_px: float) -> None:
    """Draw ``path`` as a ``fill`` line with a ``border`` outline on each side."""
    painter.setPen(QPen(QColor(border), fill_px + 2 * border_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(path)
    painter.setPen(QPen(QColor(fill), fill_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(path)
