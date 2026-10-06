"""Hover card shown next to the memory-only circular gauge.

Two tight rows — blue up arrow + upload speed, green down arrow + download
speed — drawn directly on screen with no background box.  The card is its
own frameless, click-through top-level window so it can float just outside
the gauge without changing the overlay's geometry.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import theme

UP_COLOR = QColor("#7C8CFF")     # upload arrow (blue)
DOWN_COLOR = QColor("#34D399")   # download arrow (green)
TEXT_COLOR = QColor("#EAF0FA")


class NetSpeedCard(QWidget):
    """Click-through pill that shows ↑/↓ throughput beside the gauge."""

    ARROW_CX = 9.0
    TEXT_X = 17.0
    PAD_RIGHT = 4.0

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # a distinct title so the app-display-name fallback can't make this
        # hidden window collide with the HUD in FindWindow-by-title lookups
        self.setWindowTitle("_PerfOverlayNetCard")
        self._up: Optional[float] = None
        self._down: Optional[float] = None
        self._font = theme.ui_font(10, theme.QFont.Weight.DemiBold)
        self._resize_for_text()

    # -- data ----------------------------------------------------------
    def set_speeds(self, up: Optional[float], down: Optional[float]) -> None:
        self._up = up
        self._down = down
        self._resize_for_text()
        self.update()

    def _resize_for_text(self) -> None:
        fm = QFontMetrics(self._font)
        text_w = max(fm.horizontalAdvance(theme.format_speed(self._up)),
                     fm.horizontalAdvance(theme.format_speed(self._down)))
        # rows butt together with zero spacing; the widget is exactly two rows
        self.setFixedSize(int(self.TEXT_X + text_w + self.PAD_RIGHT),
                          fm.height() * 2)

    # -- painting ------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        fm = QFontMetrics(self._font)
        row_h = fm.height()
        for i, (value, color) in enumerate(((self._up, UP_COLOR),
                                            (self._down, DOWN_COLOR))):
            cy = i * row_h + row_h / 2.0
            self._paint_arrow(p, self.ARROW_CX, cy, color, up=(i == 0))
            p.setFont(self._font)
            p.setPen(QPen(TEXT_COLOR))
            p.drawText(QRectF(self.TEXT_X, i * row_h,
                              self.width() - self.TEXT_X, row_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       theme.format_speed(value))
        p.end()

    @staticmethod
    def _paint_arrow(p: QPainter, cx: float, cy: float, color: QColor,
                     up: bool) -> None:
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        stem = 4.2
        head = 2.9
        head_back = 3.1
        if up:
            p.drawLine(QPointF(cx, cy + stem), QPointF(cx, cy - stem))
            p.drawPolyline(QPolygonF([
                QPointF(cx - head, cy - stem + head_back),
                QPointF(cx, cy - stem),
                QPointF(cx + head, cy - stem + head_back),
            ]))
        else:
            p.drawLine(QPointF(cx, cy - stem), QPointF(cx, cy + stem))
            p.drawPolyline(QPolygonF([
                QPointF(cx - head, cy + stem - head_back),
                QPointF(cx, cy + stem),
                QPointF(cx + head, cy + stem - head_back),
            ]))
