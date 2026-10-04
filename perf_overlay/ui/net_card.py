"""Hover card shown next to the memory-only circular gauge.

A small rounded pill listing current upload / download speed, styled after
PC-manager widgets: blue up arrow, green down arrow, white values.  The card
is its own frameless, click-through top-level window so it can float just
outside the gauge without changing the overlay's geometry.
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
BG_COLOR = QColor(16, 19, 28, 240)
BORDER_COLOR = QColor(255, 255, 255, 30)


class NetSpeedCard(QWidget):
    """Click-through pill that shows ↑/↓ throughput beside the gauge."""

    ARROW_CX = 12.0
    TEXT_X = 21.0
    PAD_RIGHT = 9.0

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
        self._up: Optional[float] = None
        self._down: Optional[float] = None
        self._font = theme.ui_font(12, theme.QFont.Weight.DemiBold)
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
        row_h = fm.height() + 2
        self.setFixedSize(int(self.TEXT_X + text_w + self.PAD_RIGHT),
                          int(row_h * 2 + 8))

    # -- painting ------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        p.setPen(QPen(BORDER_COLOR, 1))
        p.setBrush(BG_COLOR)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)

        fm = QFontMetrics(self._font)
        row_h = fm.height() + 2
        top = (self.height() - row_h * 2) / 2.0
        for i, (value, color) in enumerate(((self._up, UP_COLOR),
                                            (self._down, DOWN_COLOR))):
            cy = top + i * row_h + row_h / 2.0
            self._paint_arrow(p, self.ARROW_CX, cy, color, up=(i == 0))
            p.setFont(self._font)
            p.setPen(QPen(TEXT_COLOR))
            p.drawText(QRectF(self.TEXT_X, top + i * row_h,
                              self.width() - self.TEXT_X, row_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       theme.format_speed(value))
        p.end()

    @staticmethod
    def _paint_arrow(p: QPainter, cx: float, cy: float, color: QColor,
                     up: bool) -> None:
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        stem = 5.0
        head = 3.4
        head_back = 3.6
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
