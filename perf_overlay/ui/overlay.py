"""The always-on-top performance HUD."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFontMetrics, QLinearGradient, QMouseEvent,
    QPainter, QPen, QPolygon,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .. import platform_win
from ..config import Settings
from ..metrics import GROUP_COLORS, METRIC_BY_KEY, METRIC_SPECS, Metrics, MetricSpec
from . import theme
from .net_card import NetSpeedCard

Edge = Tuple[bool, bool, bool, bool]  # left, right, top, bottom


class OverlayWidget(QWidget):
    """Frameless, translucent, always-on-top metric panel."""

    settingsRequested = Signal()
    memoryBoostRequested = Signal()

    RESIZE_MARGIN = 9
    MIN_WIDTH = 154
    MIN_HEIGHT = 64
    # circular gauge (memory-only) — compact, matches system widget size
    CIRC_MIN = 48
    CIRC_NICE = 48          # default = smallest allowed
    CIRC_LEGACY = 60        # pre-shrink default; saved sizes <= this were
                            # never deliberately enlarged by the user

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.metrics = Metrics()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._dragging = False
        self._resize_edge: Optional[Edge] = None
        self._press_global = None
        self._press_geom = QRect()
        self._rows: List[dict] = []
        self._hover_settings = False
        self._settings_rect = QRect()
        self._header_rect = QRect()
        self._scale = {"cpu_power": 60.0, "gpu_power": 120.0, "fps": 60.0}
        self._unit_col_w = 0
        self._row_h = 30.0
        self._draw_bars = True
        self._inner = QRect()
        self._landscape = False
        self._text_color = QColor(theme.TEXT_PRIMARY)   # 数值文字（可配置）
        self._label_color = QColor(theme.TEXT_MUTED)    # 标签文字（可配置）

        # one-click memory boost animation state
        self._boost_active = False
        self._boost_t = 0.0          # seconds since trigger
        self._boost_freed = 0.0      # MB returned by the worker
        self._boost_done = False
        self._boost_start_pct = 0.0  # value when the boost began
        self._boost_end_pct = 0.0    # value we settle on after freeing
        self._hover_mem = False
        self._press_row_key = ""
        self._press_pos = QPoint()
        self._boost_timer = QTimer(self)
        self._boost_timer.setInterval(33)          # ~30 fps
        self._boost_timer.timeout.connect(self._boost_tick)
        self._was_circular = False

        # network-speed hover card (memory-only circular gauge)
        self._hover_disc = False
        self._net_card = NetSpeedCard()

        # content-driven shrink state
        self._shrinking = False
        self._cols = 1

        self.settings.subscribe(self._on_settings_changed)
        self.apply_settings()

    # ------------------------------------------------------------------
    # settings plumbing
    # ------------------------------------------------------------------
    def _on_settings_changed(self, _s: Settings) -> None:
        self.apply_settings()

    def apply_settings(self) -> None:
        s = self.settings
        self._text_color = theme.qcolor(
            s.get("appearance", "text_color", default=theme.DEFAULT_TEXT))
        self._label_color = theme.qcolor(
            s.get("appearance", "label_color", default=theme.DEFAULT_LABEL))
        self.setWindowOpacity(max(0.15, int(s.get("appearance", "window_opacity", default=100)) / 100.0))
        circ = self._is_circular()
        prev = getattr(self, "_was_circular", None)
        if circ:
            self.setMinimumSize(self.CIRC_MIN, self.CIRC_MIN)
            g = self.geometry()
            # snap down when entering the mode OR when the size is still a
            # leftover rectangle; a manual tweak within ~1.6x is kept
            limit = int(self.CIRC_NICE * 1.6)
            if prev is False or g.width() > limit or g.height() > limit:
                side = self.CIRC_NICE
                self.setGeometry(g.center().x() - side // 2,
                                 g.center().y() - side // 2, side, side)
        else:
            self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
            if prev is True:
                side_w, side_h = self.MIN_WIDTH + 40, self.MIN_HEIGHT + 60
                g = self.geometry()
                self.setGeometry(g.center().x() - side_w // 2,
                                 g.center().y() - side_h // 2, side_w, side_h)
        self._was_circular = circ
        self._hover_disc = False
        self._update_net_card()
        self._relayout()
        self.update()

    def set_metrics(self, m: Metrics) -> None:
        self.metrics = m
        self._update_scales(m)
        if self._net_card.isVisible():
            self._net_card.set_speeds(m.net_up, m.net_down)
            self._position_net_card()
        self.update()

    def _update_scales(self, m: Metrics) -> None:
        for key in ("cpu_power", "gpu_power", "fps"):
            v = getattr(m, key, None)
            if v is None:
                continue
            floor = 60.0 if key == "fps" else 20.0
            self._scale[key] = max(floor, max(v * 1.12, self._scale[key] * 0.985))

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _enabled_specs(self) -> List[MetricSpec]:
        return [METRIC_BY_KEY[k] for k in self.settings.enabled_keys() if k in METRIC_BY_KEY]

    def _is_circular(self) -> bool:
        """Only the memory metric is on -> circular gauge (PC-manager style)."""
        keys = [k for k in self.settings.enabled_keys() if k in METRIC_BY_KEY]
        return keys == ["mem_usage"]

    def _circle_rect(self) -> QRectF:
        """Bounding square of the gauge, centred in the widget."""
        w, h = self.rect().width(), self.rect().height()
        d = float(min(w, h) - 2)
        x = (w - d) / 2.0
        y = (h - d) / 2.0
        return QRectF(x, y, d, d)

    def _in_center_hit(self, pos) -> bool:
        """Only the middle of the disc is a boost click target (not the ring)."""
        circ = self._circle_rect()
        dx = pos.x() - circ.center().x()
        dy = pos.y() - circ.center().y()
        r = circ.width() / 2.0
        return (dx * dx + dy * dy) <= (r * 0.50) ** 2

    def _in_disc(self, pos) -> bool:
        """Anywhere on the memory disc (ring included) -> show the net card."""
        circ = self._circle_rect()
        dx = pos.x() - circ.center().x()
        dy = pos.y() - circ.center().y()
        r = circ.width() / 2.0
        return (dx * dx + dy * dy) <= (r * 0.96) ** 2

    # ------------------------------------------------------------------
    # network-speed hover card (circular gauge only)
    # ------------------------------------------------------------------
    def _update_net_card(self) -> None:
        card = self._net_card
        show = (self.isVisible() and self._is_circular()
                and self._hover_disc and not self._boost_active)
        if not show:
            if card.isVisible():
                card.hide()
            return
        card.set_speeds(self.metrics.net_up, self.metrics.net_down)
        self._position_net_card()
        if not card.isVisible():
            card.show()
        card.raise_()

    def _position_net_card(self) -> None:
        """Park the card flush against the gauge's left edge, vertically centred."""
        card = self._net_card
        g = self.geometry()
        gap = 0  # tangent to the ring
        x = g.left() - card.width() - gap
        y = g.top() + (g.height() - card.height()) // 2
        try:
            screen = self.screen()
            avail = screen.availableGeometry() if screen else None
            if avail is not None and avail.width() > 10:
                if x < avail.left() + 2:  # no room on the left -> flip right
                    x = g.right() + gap
                    if x + card.width() > avail.right() - 2:
                        x = avail.right() - card.width() - 2
                y = max(avail.top() + 2, min(y, avail.bottom() - card.height() - 2))
        except Exception:
            pass
        card.move(x, y)

    def _resize_margin(self) -> int:
        # keep a grabable rim around the compact circular widget
        return 6 if self._is_circular() else self.RESIZE_MARGIN

    def _ring_colors(self, value: Optional[float]):
        """Track / progress colours for the memory ring."""
        track = QColor(255, 255, 255, 34)
        if value is None:
            return track, QColor("#5D6577")
        pct = max(0.0, min(100.0, float(value)))
        if pct >= 90.0:
            col = QColor("#F87171")
        elif pct >= 75.0:
            col = QColor("#FBBF24")
        else:
            col = QColor("#34D399")
        return track, col

    def _relayout(self) -> None:
        s = self.settings
        font_size = int(s.get("appearance", "font_size", default=12))
        show_bars = bool(s.get("appearance", "show_bars", default=True))
        grid = str(s.get("appearance", "layout", default="stack")) == "grid"

        specs = self._enabled_specs()
        win_w = self.rect().width()
        win_h = self.rect().height()

        # ---- circular gauge mode: memory only ----
        if self._is_circular():
            self._landscape = False
            self._draw_bars = False
            self._unit_col_w = 0
            self._row_h = float(min(win_w, win_h))
            self._inner = QRect(self.rect())
            self._header_rect = QRect()
            circ = self._circle_rect()
            self._rows = [{
                "spec": specs[0] if specs else METRIC_BY_KEY["mem_usage"],
                "rect": QRect(round(circ.x()), round(circ.y()),
                              round(circ.width()), round(circ.height())),
            }]
            return

        landscape = win_w > win_h

        # strips: tighter padding so the wide edge is fully used
        pad_x = 8 if landscape else theme.PAD
        pad_y = theme.PAD if win_h >= 200 else max(4, theme.PAD // 4)
        inner = self.rect().adjusted(pad_x, pad_y, -pad_x, -pad_y)
        if inner.width() <= 4 or inner.height() <= 4:
            self._rows = []
            return

        # keep the last progress bar clear of the rounded bottom edge
        FOOT_GAP = 13 if win_h >= 200 else (3 if landscape else 5)
        bottom = inner.bottom() - FOOT_GAP

        # metrics-only panel: no title bar, no gear — settings live in the
        # right-click menu and the tray
        header_h = 0
        row_h_nat = (font_size + 17) if show_bars else (font_size + 9)
        gap = 5 if landscape else 5

        # Primary axis follows the long edge:
        #   wide strip  -> metrics flow left-to-right (wrap to next band)
        #   tall panel  -> metrics flow top-to-bottom (optional 2-col grid)
        self._landscape = landscape

        if not specs:
            cols = 1
            rows_count = 0
        elif landscape:
            # each metric gets its own cell; how many fit side by side
            min_cell = max(56, font_size * 4 + 12)
            cols = max(1, min(len(specs), (inner.width() + gap) // (min_cell + gap)))
            rows_count = (len(specs) + cols - 1) // cols
        else:
            cols = 2 if (grid and inner.width() >= 300) else 1
            rows_count = (len(specs) + cols - 1) // cols

        available = bottom - inner.top()
        gap_total = gap * max(0, rows_count - 1)
        room = max(0.0, available - header_h - gap_total)
        if rows_count <= 0:
            row_h = row_h_nat
        elif landscape:
            # strip form: metrics fill the whole remaining height (no dead band)
            row_h = max(17.0, room / rows_count)
        else:
            # tall form: grow a little if there is slack, otherwise natural size
            row_h = min(max(row_h_nat, room / rows_count), row_h_nat * 1.9)
            row_h = max(17.0, min(row_h, room / rows_count))

        content_h = rows_count * row_h + gap_total
        top0 = inner.top() + header_h
        leftover = available - header_h - content_h
        if not landscape and leftover > 1:
            top0 += leftover / 2  # centre the stack, don't pin it to the top

        self._rows = []
        col_w = (inner.width() - gap * (cols - 1)) / cols if cols else inner.width()
        for i, spec in enumerate(specs):
            col = i % cols
            row_index = i // cols
            x = inner.left() + col * (col_w + gap)
            top = top0 + row_index * (row_h + gap)
            self._rows.append({
                "spec": spec,
                "rect": QRect(round(x), round(top), round(col_w), round(row_h)),
            })

        # metrics may not fit with bars; remember whether bars are drawn
        self._row_h = row_h
        self._draw_bars = show_bars and row_h >= 22
        self._inner = inner
        self._header_rect = QRect()
        self._settings_rect = QRect()
        self._cols = cols
        self._shrink_to_content()

    # ------------------------------------------------------------------
    # content-driven sizing: keep the short side snug around the metrics
    # ------------------------------------------------------------------
    def _natural_stack_width(self) -> int:
        """Width a tall single-column panel needs: label + value + unit."""
        s = self.settings
        font_size = int(s.get("appearance", "font_size", default=12))
        temp_unit = str(s.get("sampling", "temp_unit", default="C"))
        specs = self._enabled_specs() or [METRIC_BY_KEY["mem_usage"]]
        fm_label = QFontMetrics(theme.ui_font(max(8, font_size - 2)))
        fm_value = QFontMetrics(theme.ui_font(max(9, font_size + 1),
                                              theme.QFont.Weight.DemiBold))
        fm_unit = QFontMetrics(theme.ui_font(max(8, font_size - 2)))
        label_w = max(fm_label.horizontalAdvance(sp.label) for sp in specs)
        value_w = fm_value.horizontalAdvance("888.8")   # widest realistic number
        unit_w = max(fm_unit.horizontalAdvance(theme.display_unit(sp, temp_unit))
                     for sp in specs)
        # PAD + tick/label indent + label<->value gap + unit gap + right margin
        return theme.PAD + 12 + label_w + 14 + value_w + 3 + unit_w + 4

    def _natural_strip_height(self) -> int:
        """Height the landscape strip needs at its natural row height."""
        s = self.settings
        font_size = int(s.get("appearance", "font_size", default=12))
        show_bars = bool(s.get("appearance", "show_bars", default=True))
        rows_count = max(1, len({r["rect"].top() for r in self._rows})) if self._rows else 1
        pad_y = max(4, theme.PAD // 4)
        row_h = (font_size + 17) if show_bars else (font_size + 9)
        return pad_y * 2 + rows_count * row_h + 5 * (rows_count - 1) + 3  # 3 = FOOT_GAP

    def _shrink_to_content(self) -> None:
        """Narrow the short side to hug the metrics; never grow it back."""
        if self._is_circular() or self._shrinking:
            return
        g = self.geometry()
        if self._landscape:
            nat = self._natural_strip_height()
            if g.height() - nat > 3:
                g.setHeight(max(self.minimumHeight(), nat))
                self._apply_shrink(g)
        elif self._cols <= 1:  # never fight the two-column grid layout
            nat = self._natural_stack_width()
            if g.width() - nat > 3:
                g.setWidth(max(self.minimumWidth(), nat))
                self._apply_shrink(g)

    def _apply_shrink(self, g: QRect) -> None:
        self._shrinking = True
        try:
            self.setGeometry(g)
        finally:
            self._shrinking = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        if self._net_card.isVisible():
            self._position_net_card()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        platform_win.apply_overlay_styles(self)
        platform_win.force_topmost(self)
        self._relayout()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover_disc = False
        self._update_net_card()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = self.settings
        radius = float(s.get("appearance", "corner_radius", default=14))
        bg = theme.qcolor(s.get("appearance", "bg_color", default=theme.DEFAULT_BG))
        alpha = int(s.get("appearance", "bg_opacity", default=82) * 2.55)
        accent = theme.qcolor(s.get("appearance", "accent_color", default=theme.DEFAULT_ACCENT))

        # ---- circular gauge (memory-only mode) --------------------
        if self._is_circular():
            self._paint_circular(p, accent, alpha)
            p.end()
            return

        rect = QRect(self.rect())
        # --- panel -------------------------------------------------
        top_c = theme.lighten(bg, 0.10)
        top_c.setAlpha(alpha)
        bot_c = theme.darken(bg, 0.16)
        bot_c.setAlpha(min(255, alpha + 12))
        p.save()
        p.setBrush(theme.vertical_gradient(top_c, bot_c))
        p.setPen(Qt.PenStyle.NoPen)  # no window/frame border
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
        p.restore()

        # --- rows -------------------------------------------------
        if not self._rows:
            self._paint_empty(p, accent)
        else:
            for row in self._rows:
                self._paint_row(p, row["spec"], row["rect"], accent)
            # memory-boost sweep sits on top of the memory row
            if self._boost_active:
                self._paint_boost(p, self._mem_row_rect())

        p.end()

    # ------------------------------------------------------------------
    # circular gauge (memory-only) + its own boost animation
    # ------------------------------------------------------------------
    def _paint_circular(self, p: QPainter, accent: QColor, alpha: int) -> None:
        """PC-manager style: big value in the middle, progress ring around it."""
        s = self.settings
        bg = theme.qcolor(s.get("appearance", "bg_color", default=theme.DEFAULT_BG))
        value = self.metrics.mem_usage
        circ = self._circle_rect()
        cx, cy = circ.center().x(), circ.center().y()
        r = circ.width() / 2.0

        # disc background
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        top_c = theme.lighten(bg, 0.12)
        top_c.setAlpha(alpha)
        bot_c = theme.darken(bg, 0.22)
        bot_c.setAlpha(min(255, alpha + 16))
        grad = QLinearGradient(circ.topLeft(), circ.bottomRight())
        grad.setColorAt(0.0, top_c)
        grad.setColorAt(1.0, bot_c)
        p.setBrush(grad)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(circ)
        p.restore()

        ring_w = max(3.5, r * 0.085)          # thin stroke like system widgets
        inset = ring_w / 2.0 + 1.0
        ring_rect = QRectF(cx - r + inset, cy - r + inset,
                           (r - inset) * 2, (r - inset) * 2)

        if not self._boost_active:
            track, col = self._ring_colors(value)
            # ring track
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(track)
            pen.setWidthF(ring_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring_rect)
            p.restore()

            # progress arc  (top = 12 o'clock, clockwise)
            pct = 0.0 if value is None else max(0.0, min(100.0, float(value)))
            if pct > 0.05:
                p.save()
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                pen = QPen(col)
                pen.setWidthF(ring_w)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                span = -int(round(360 * 16 * pct / 100.0))   # negative = clockwise
                p.drawArc(ring_rect, 90 * 16, span)
                p.restore()

        # ---- two elements only: value + unit ----
        display = value
        if self._boost_active:
            display = self._boost_display_pct(value)
        val_text = "--" if display is None else f"{display:.0f}"
        unit_text = "%" if display is not None else ""
        v_pt = int(max(16, r * 0.58))         # big bold number, compact disc
        u_pt = int(max(8, r * 0.22))
        v_font = theme.ui_font(v_pt, theme.QFont.Weight.DemiBold)
        u_font = theme.ui_font(u_pt)
        fm = QFontMetrics(v_font)
        ufm = QFontMetrics(u_font)

        # 用 tightBoundingRect 取实际墨迹宽度与基线，避免 horizontalAdvance
        # 的 side-bearing 让「数值+单位」整体偏左。
        vb = fm.tightBoundingRect(val_text)
        ub = ufm.tightBoundingRect(unit_text) if unit_text else None
        gap = max(2, int(round(r * 0.05)))
        w_val = max(1, vb.width())
        w_unit = max(1, ub.width()) if unit_text else 0
        total = w_val + (gap + w_unit if unit_text else 0)
        x0 = cx - total / 2.0

        # 基线：让墨迹垂直居中于圆心
        baseline_v = cy - (vb.top() + vb.bottom()) / 2.0
        p.setFont(v_font)
        p.setPen(QPen(self._val_color(METRIC_BY_KEY["mem_usage"], display)))
        p.drawText(QPointF(x0 - vb.left(), baseline_v), val_text)

        if unit_text and ub is not None:
            baseline_u = cy - (ub.top() + ub.bottom()) / 2.0
            p.setFont(u_font)
            p.setPen(QPen(self._label_color))
            p.drawText(QPointF(x0 + w_val + gap - ub.left(), baseline_u), unit_text)

        # (no settings gear in circular mode — right-click still opens the menu)
        self._settings_rect = QRect()

        # circular boost animation
        if self._boost_active:
            self._paint_boost_circular(p, circ)

    def _paint_boost_circular(self, p: QPainter, circ: QRectF) -> None:
        """Circular-only accelerate animation.

        The progress ring itself is the animation:
          A  0.0-1.2s  ring charges 0% -> current value with a glowing head
          B  1.2-2.1s  filled ring spins around the disc, colour flashes
          C  2.1-3.4s  ring falls to the post-free value + result chip
        """
        t = self._boost_t
        cx, cy = circ.center().x(), circ.center().y()
        r = circ.width() / 2.0
        green = QColor("#34D399")
        ring_w = max(3.5, r * 0.085)
        inset = ring_w / 2.0 + 1.0
        ring_rect = QRectF(cx - r + inset, cy - r + inset,
                           (r - inset) * 2, (r - inset) * 2)

        live = self.metrics.mem_usage
        pct = self._boost_display_pct(live)
        pct = max(0.0, min(100.0, pct))

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # ---- track (always) ----
        track = QColor(255, 255, 255, 34)
        pen = QPen(track)
        pen.setWidthF(ring_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(ring_rect)

        # ---- animated progress arc ----
        if t < 0.45:
            # phase A: charging sweep 0 -> start, bright head at the tip
            col = QColor(green)
            col.setAlpha(220)
            pen = QPen(col)
            pen.setWidthF(ring_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            if pct > 0.4:
                span = -int(round(360 * 16 * pct / 100.0))
                p.drawArc(ring_rect, 90 * 16, span)
            # glowing tip
            tip_deg = 90.0 - 360.0 * (pct / 100.0)
            tip_rad = math.radians(tip_deg)
            tx = cx + (r - inset) * math.cos(tip_rad)
            ty = cy - (r - inset) * math.sin(tip_rad)
            glow = QColor(green)
            glow.setAlpha(200)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QRectF(tx - ring_w * 0.9, ty - ring_w * 0.9,
                                 ring_w * 1.8, ring_w * 1.8))
        elif t < 1.0:
            # phase B: the filled ring spins around the disc
            spin = (t - 0.45) / 0.55                  # 0 -> 1
            rot = -360.0 * spin * 1.25                # just over one turn
            base = 90.0 + rot
            span_deg = -360.0 * (pct / 100.0)
            # halo
            halo = QColor(green)
            halo.setAlpha(40)
            pen = QPen(halo)
            pen.setWidthF(ring_w * 2.1)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawEllipse(ring_rect)
            # spinning arc with a fading tail
            tail = 26.0
            steps = 8
            for i in range(steps):
                frac = i / float(steps)
                col = QColor(green)
                if abs(span_deg) > tail:
                    col.setAlpha(int(235 * (1.0 - frac) ** 1.5) + 18)
                else:
                    col.setAlpha(210)
                col.setRgb(min(255, col.red() + int(40 * (1 - frac))),
                           min(255, col.green() + int(30 * (1 - frac))),
                           min(255, col.blue() + int(20 * (1 - frac))),
                           col.alpha())
                pen = QPen(col)
                pen.setWidthF(ring_w * (1.0 - frac * 0.35))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                seg = span_deg / steps
                start_deg = base + seg * i
                p.drawArc(ring_rect, int(round(start_deg * 16)),
                          int(round(seg * 16)))
        else:
            # phase C: ring tracks the live value; brief settle flash
            col = QColor(green)
            settle = max(0.0, 1.0 - (t - 1.0) / 0.55)
            col.setAlpha(int(150 + 105 * settle))
            pen = QPen(col)
            pen.setWidthF(ring_w * (1.0 + 0.35 * settle))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            if pct > 0.4:
                span = -int(round(360 * 16 * pct / 100.0))
                p.drawArc(ring_rect, 90 * 16, span)
            # ripple from the ring outward
            for i in range(2):
                prog = min(1.0, (t - 1.0) / 0.55 - i * 0.22)
                if prog <= 0.0 or prog >= 1.0:
                    continue
                rr = r * (1.0 + prog * 0.32)
                rc = QColor(green)
                rc.setAlpha(int(120 * (1.0 - prog)))
                pen = QPen(rc)
                pen.setWidthF(max(1.2, ring_w * 0.5 * (1.0 - prog)))
                p.setPen(pen)
                p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        # ---- status text under the number ----
        if self._boost_done and 0.85 <= t <= 1.65:
            fade = 1.0 if t < 1.35 else max(0.0, 1.0 - (t - 1.35) / 0.3)
            txt = f"释放 {self._boost_freed:.0f} MB" if self._boost_freed >= 1 else "已加速"
            font = theme.ui_font(int(max(8, r * 0.16)))
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(txt)
            th = fm.height()
            tx = cx - tw / 2.0
            ty = cy + r * 0.28
            chip = QColor(0, 0, 0, int(115 * fade))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(chip)
            p.drawRoundedRect(QRectF(tx - 7, ty - 2, tw + 14, th + 5), 5, 5)
            col = QColor(green)
            col.setAlpha(int(255 * fade))
            p.setFont(font)
            p.setPen(QPen(col))
            p.drawText(QRectF(tx, ty, tw, th),
                       Qt.AlignmentFlag.AlignCenter, txt)
        elif not self._boost_done and t < 1.0:
            txt = "加速中…"
            font = theme.ui_font(int(max(8, r * 0.16)))
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(txt)
            th = fm.height()
            col = QColor(255, 255, 255, int(200 * max(0.0, 1.0 - t / 1.0)))
            p.setFont(font)
            p.setPen(QPen(col))
            p.drawText(QRectF(cx - tw / 2.0, cy + r * 0.28, tw, th),
                       Qt.AlignmentFlag.AlignCenter, txt)

        p.restore()

    # ------------------------------------------------------------------
    # empty / rectangular boost + boost state machine
    # ------------------------------------------------------------------
    def _paint_empty(self, p: QPainter, accent: QColor) -> None:
        r = self.rect().adjusted(theme.PAD, theme.PAD + 12, -theme.PAD, -theme.PAD)
        p.setFont(theme.ui_font(11))
        p.setPen(QPen(self._label_color))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "没有启用任何指标\n右键 → 设置")
        _ = accent

    def _row_at(self, pos) -> Optional[dict]:
        for row in self._rows:
            if row["rect"].contains(pos):
                return row
        return None

    def _mem_row_rect(self) -> QRect:
        for row in self._rows:
            if row["spec"].key == "mem_usage":
                return row["rect"]
        return QRect()

    def start_memory_boost(self, freed_mb: float = 0.0) -> None:
        """Kick the boost animation; call set_boost_result() when the work ends."""
        if self._boost_active:
            return
        self._boost_active = True
        self._boost_done = False
        self._boost_freed = 0.0
        self._boost_t = 0.0
        cur = self.metrics.mem_usage
        self._boost_start_pct = float(cur) if cur is not None else 0.0
        self._boost_end_pct = self._boost_start_pct
        self._hover_disc = False
        self._update_net_card()
        self._boost_timer.start()
        self.update()

    def set_boost_result(self, freed_mb: float) -> None:
        self._boost_freed = max(0.0, float(freed_mb or 0.0))
        self._boost_done = True

    def _boost_display_pct(self, live_value: Optional[float]) -> float:
        """Value shown while boosting.

        Phase A (0-0.45s) charges the ring 0 -> live reading as a visual.
        Afterwards we ALWAYS follow the live sensor value — never a made-up
        drop — so the ring cannot freeze on a stale number.
        """
        t = self._boost_t
        start = self._boost_start_pct
        if live_value is None:
            return start
        if t < 0.45:
            return start * min(1.0, t / 0.45)
        return float(live_value)

    def _boost_tick(self) -> None:
        self._boost_t += self._boost_timer.interval() / 1000.0
        if self._boost_t >= 1.6:
            self._boost_timer.stop()
            self._boost_active = False
            self._boost_done = False
            self._boost_t = 0.0
            self._boost_freed = 0.0
            self._boost_end_pct = 0.0
            # mouse may still sit on the disc -> bring the card back
            self._hover_disc = self._in_disc(self.mapFromGlobal(QCursor.pos()))
        self.update()
        self._update_net_card()

    def _paint_boost(self, p: QPainter, rect: QRect) -> None:
        """Rectangular-mode accelerate animation (sweep + glow + result)."""
        if not self._boost_active:
            return
        t = self._boost_t
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        base = QColor("#34D399")
        if t < 0.85:
            prog = t / 0.85
            band_w = max(24.0, rect.width() * 0.36)
            cx = rect.left() - band_w + (rect.width() + band_w * 2) * prog
            grad_rect = QRectF(cx - band_w / 2, rect.top(), band_w, rect.height())
            grad = QLinearGradient(grad_rect.topLeft(), grad_rect.topRight())
            c0 = QColor(base); c0.setAlpha(0)
            c1 = QColor(base); c1.setAlpha(70)
            c2 = QColor("#FFFFFF"); c2.setAlpha(110)
            c3 = QColor(base); c3.setAlpha(70)
            c4 = QColor(base); c4.setAlpha(0)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(0.35, c1)
            grad.setColorAt(0.5, c2)
            grad.setColorAt(0.65, c3)
            grad.setColorAt(1.0, c4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(rect, 6, 6)
        else:
            if t < 1.7:
                phase = (t - 0.85) / 0.85
                glow = QColor(base)
                glow.setAlpha(int(60 * (1.0 - phase) + 18))
            else:
                glow = QColor(base)
                fade = max(0.0, 1.0 - (t - 1.7) / 1.5)
                glow.setAlpha(int(42 * fade))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(rect, 6, 6)

        if self._boost_done and 1.1 <= t <= 3.2:
            fade = 1.0 if t < 2.5 else max(0.0, 1.0 - (t - 2.5) / 0.7)
            txt = f"释放 {self._boost_freed:.0f} MB" if self._boost_freed >= 1 else "已加速"
            font = theme.ui_font(max(8, rect.height() // 3))
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(txt)
            th = fm.height()
            tx = rect.right() - tw - 8
            ty = rect.top() + max(0, (rect.height() - th) // 2)
            chip = QColor(0, 0, 0, int(120 * fade))
            p.setBrush(chip)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(tx - 6, ty - 2, tw + 12, th + 4), 5, 5)
            col = QColor(base)
            col.setAlpha(int(255 * fade))
            p.setFont(font)
            p.setPen(QPen(col))
            p.drawText(QRect(round(tx), round(ty), tw, th),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        elif not self._boost_done and t < 1.0:
            txt = "加速中…"
            font = theme.ui_font(max(8, rect.height() // 3))
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(txt)
            th = fm.height()
            tx = rect.right() - tw - 8
            ty = rect.top() + max(0, (rect.height() - th) // 2)
            col = QColor(255, 255, 255, int(210 * (1.0 - t)))
            p.setFont(font)
            p.setPen(QPen(col))
            p.drawText(QRect(round(tx), round(ty), tw, th),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        p.restore()

    def _paint_row(self, p: QPainter, spec: MetricSpec, rect: QRect, accent: QColor) -> None:
        # Narrow cells (horizontal strip) stack label over value; wide cells keep
        # the side-by-side table look. Threshold is tuned to the unit column.
        if rect.width() < 118:
            self._paint_cell(p, spec, rect, accent)
        else:
            self._paint_row_wide(p, spec, rect, accent)

    def _paint_bar(self, p: QPainter, spec: MetricSpec, rect: QRect, accent: QColor) -> None:
        s = self.settings
        temp_unit = str(s.get("sampling", "temp_unit", default="C"))
        value = getattr(self.metrics, spec.key, None)
        group_color = QColor(GROUP_COLORS.get(spec.group, accent.name()))
        bar_h = 4.0
        track = QColor(255, 255, 255, 26)
        p.setBrush(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, bar_h / 2, bar_h / 2)

        ratio = self._bar_ratio(spec, value)
        if ratio > 0.01:
            fill_w = max(bar_h, rect.width() * min(1.0, ratio))
            fill = QRect(rect.left(), rect.top(), round(fill_w), rect.height())
            c = QColor(group_color)
            if spec.kind in ("temp", "percent"):
                c = self._val_color(spec, value, temp_unit)
                # healthy reading -> keep the group colour on the bar
                if value is not None and c == self._text_color:
                    c = QColor(group_color)
            p.setBrush(c)
            p.drawRoundedRect(fill, bar_h / 2, bar_h / 2)

    def _paint_tick(self, p: QPainter, rect: QRect, group_color: QColor) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        tick = QColor(group_color)
        tick.setAlpha(205)
        p.setBrush(tick)
        p.drawRoundedRect(rect, 1.5, 1.5)

    def _fit_value_unit(self, num_text: str, unit_text: str, max_w: int,
                        value_font, unit_font, max_h: int = 0):
        """Shrink value/unit fonts until the pair fits max_w (and max_h)."""
        v_size = value_font.pointSize()
        u_size = unit_font.pointSize()
        best = (value_font, unit_font, 0, 0)
        while True:
            vf = theme.ui_font(max(6, v_size), theme.QFont.Weight.DemiBold)
            uf = theme.ui_font(max(5, u_size))
            nw = QFontMetrics(vf).horizontalAdvance(num_text)
            uw = (QFontMetrics(uf).horizontalAdvance(unit_text) + 3) if unit_text else 0
            best = (vf, uf, nw, uw)
            vh = QFontMetrics(vf).height()
            if nw + uw <= max_w and (max_h <= 0 or vh <= max_h):
                return best
            if v_size <= 6 and u_size <= 5:
                return best
            v_size = max(6, v_size - 1)
            u_size = max(5, u_size - 1)

    def _paint_cell(self, p: QPainter, spec: MetricSpec, rect: QRect, accent: QColor) -> None:
        """Compact metric cell for the horizontal axis.

        Label sits above the value; both fonts auto-shrink so nothing clips
        when the strip gets narrow or short.
        """
        s = self.settings
        temp_unit = str(s.get("sampling", "temp_unit", default="C"))
        value = getattr(self.metrics, spec.key, None)
        group_color = QColor(GROUP_COLORS.get(spec.group, accent.name()))

        font_size = int(s.get("appearance", "font_size", default=12))
        cell_h = rect.height()
        # smaller value type + snug label/value spacing keep the strip low;
        # fit_font / _fit_value_unit only shrink from here if the width is tight
        val_pt = int(max(8, min(font_size + 3, round(cell_h * 0.36))))
        lab_pt = int(max(7, min(font_size + 1, round(cell_h * 0.24))))
        base_label = theme.ui_font(lab_pt)
        base_value = theme.ui_font(val_pt, theme.QFont.Weight.DemiBold)
        base_unit = theme.ui_font(max(7, lab_pt - 1))

        bar_h = 5.0 if self._draw_bars else 0.0
        pad = 2
        text_h = max(8, cell_h - pad * 2 - bar_h - (2 if self._draw_bars else 0))
        text_rect = QRect(rect.left() + 8, rect.top() + pad,
                          max(4, rect.width() - 10), round(text_h))
        bar_rect = QRect(rect.left() + 4, round(rect.bottom() - bar_h - 2),
                         max(4, rect.width() - 8), max(3, round(bar_h)))

        self._paint_tick(p, QRect(rect.left(), text_rect.top(), 3, max(8, text_rect.height() - 2)),
                         group_color)

        num_text, unit_text = theme.format_value(spec, value, temp_unit)
        avail_w = text_rect.width()

        show_label = text_rect.height() >= 14
        if show_label:
            label_h = max(8, round(text_rect.height() * 0.28))
            value_h = max(9, text_rect.height() - label_h + 2)
        else:
            label_h = 0
            value_h = text_rect.height()

        label_font = theme.fit_font(base_label, spec.label, avail_w, max(8, label_h), min_size=5)
        v_font, u_font, num_w, unit_w = self._fit_value_unit(
            num_text, unit_text, avail_w, base_value, base_unit, max_h=value_h)

        if show_label:
            p.setFont(label_font)
            p.setPen(QPen(self._label_color))
            label_rect = QRect(text_rect.left(), text_rect.top(), avail_w, label_h)
            p.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       theme.elided(spec.label, label_font, avail_w))
            vy = text_rect.top() + label_h - 1
            vh = value_h
        else:
            vy = text_rect.top()
            vh = text_rect.height()

        # value + unit centred on the remaining band
        total_w = num_w + unit_w
        vx = text_rect.left() + max(0, (avail_w - total_w) // 2)
        p.setFont(v_font)
        p.setPen(QPen(self._val_color(spec, value, temp_unit)))
        p.drawText(QRect(vx, vy, num_w, vh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, num_text)
        if unit_text:
            p.setFont(u_font)
            p.setPen(QPen(self._label_color))
            p.drawText(QRect(vx + num_w + 2, vy, unit_w + 2, vh),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, unit_text)

        if self._draw_bars:
            self._paint_bar(p, spec, bar_rect, accent)

    def _paint_row_wide(self, p: QPainter, spec: MetricSpec, rect: QRect, accent: QColor) -> None:
        s = self.settings
        temp_unit = str(s.get("sampling", "temp_unit", default="C"))
        value = getattr(self.metrics, spec.key, None)
        group_color = QColor(GROUP_COLORS.get(spec.group, accent.name()))

        font_size = int(s.get("appearance", "font_size", default=12))
        base_label = theme.ui_font(max(8, font_size - 2))
        base_value = theme.ui_font(max(9, font_size + 1), theme.QFont.Weight.DemiBold)
        base_unit = theme.ui_font(max(8, font_size - 2))

        bar_h = 4.0
        text_h = rect.height() - (7 + bar_h) if self._draw_bars else rect.height()
        text_rect = QRect(rect.left(), rect.top(), rect.width(), round(text_h))
        bar_rect = QRect(rect.left() + 12, round(rect.bottom() - bar_h - 1),
                         max(4, rect.width() - 12), round(bar_h))

        self._paint_tick(p, QRect(rect.left(), text_rect.top() + (round(text_h) - max(8, round(text_h - 2))) // 2,
                                  3, max(8, round(text_h) - 2)), group_color)

        num_text, unit_text = theme.format_value(spec, value, temp_unit)
        right = rect.right() - 4
        label_zone_w = max(24, rect.width() - 12 - 36)
        label_font = theme.fit_font(base_label, spec.label, label_zone_w,
                                    text_rect.height(), min_size=6)
        v_font, u_font, num_w, unit_w = self._fit_value_unit(
            num_text, unit_text, max(24, rect.width() // 2), base_value, base_unit,
            max_h=text_rect.height())

        # label
        p.setFont(label_font)
        p.setPen(QPen(self._label_color))
        label_rect = QRect(rect.left() + 12, text_rect.top(),
                           max(20, rect.width() - 24 - num_w), text_rect.height())
        p.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   theme.elided(spec.label, label_font, label_rect.width()))

        # value + unit drawn snug together, right-aligned as one group
        if unit_text:
            x0 = right - num_w - unit_w - 3
        else:
            x0 = right - num_w
        p.setFont(v_font)
        p.setPen(QPen(self._val_color(spec, value, temp_unit)))
        p.drawText(QRect(x0, text_rect.top(), num_w, text_rect.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, num_text)
        if unit_text:
            p.setFont(u_font)
            p.setPen(QPen(self._label_color))
            p.drawText(QRect(x0 + num_w + 3, text_rect.top(), unit_w, text_rect.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, unit_text)

        if self._draw_bars:
            self._paint_bar(p, spec, bar_rect, accent)

    def _val_color(self, spec: MetricSpec, value: Optional[float], temp_unit: str = "C") -> QColor:
        """value colour with the user-configurable healthy-text colour applied."""
        return theme.value_color(spec, value, temp_unit, text=self._text_color)

    def _bar_ratio(self, spec: MetricSpec, value: Optional[float]) -> float:
        if value is None:
            return 0.0
        if spec.kind == "percent":
            return max(0.0, min(1.0, value / 100.0))
        if spec.kind == "temp":
            unit = str(self.settings.get("sampling", "temp_unit", default="C"))
            c = theme.to_display_temp(value, unit)
            span = 100.0 if unit.upper() != "F" else 212.0
            return max(0.0, min(1.0, (c or 0) / span))
        if spec.kind == "power":
            return max(0.0, min(1.0, value / self._scale.get(spec.key, 100.0)))
        return max(0.0, min(1.0, value / self._scale.get("fps", 120.0)))

    # ------------------------------------------------------------------
    # mouse: drag + edge resize
    # ------------------------------------------------------------------
    def _edge_at(self, pos) -> Edge:
        r = self.rect()
        m = self._resize_margin()
        return (
            pos.x() <= r.left() + m,
            pos.x() >= r.right() - m,
            pos.y() <= r.top() + m,
            pos.y() >= r.bottom() - m,
        )

    @staticmethod
    def _cursor_for_edge(edge: Edge):
        l, r, t, b = edge
        if (l and t) or (r and b):
            return QCursor(Qt.CursorShape.SizeFDiagCursor)
        if (r and t) or (l and b):
            return QCursor(Qt.CursorShape.SizeBDiagCursor)
        if l or r:
            return QCursor(Qt.CursorShape.SizeHorCursor)
        if t or b:
            return QCursor(Qt.CursorShape.SizeVerCursor)
        return QCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self._press_global = event.globalPosition().toPoint()
            self._press_geom = QRect(self.geometry())
            self._press_pos = pos
            edge = self._edge_at(pos)
            if any(edge):
                # rim grab -> resize only, never a boost click
                self._resize_edge = edge
                self._dragging = False
                self._press_row_key = ""
            else:
                self._resize_edge = None
                row = self._row_at(pos)
                key = row["spec"].key if row else ""
                if key == "mem_usage" and self._is_circular():
                    # circular: only the middle of the disc boosts;
                    # the ring band is the drag handle
                    self._press_row_key = "mem_usage" if self._in_center_hit(pos) else ""
                    self._dragging = not bool(self.settings.get("window", "locked", default=False))
                else:
                    self._press_row_key = key
                    self._dragging = not bool(self.settings.get("window", "locked", default=False))
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._resize_edge and self._press_global is not None:
            self._do_resize(event.globalPosition().toPoint())
        elif self._dragging and self._press_global is not None:
            delta = event.globalPosition().toPoint() - self._press_global
            self.move(self._press_geom.topLeft() + delta)
        else:
            self._hover_settings = self._settings_rect.contains(pos) if self._settings_rect.isValid() else False
            row = self._row_at(pos)
            if self._is_circular():
                self._hover_mem = (self._in_center_hit(pos) and not self._boost_active)
                hover_disc = self._in_disc(pos) and not self._boost_active
            else:
                self._hover_mem = bool(row and row["spec"].key == "mem_usage" and not self._boost_active)
                hover_disc = False
            self._hover_disc = hover_disc
            self._update_net_card()
            if self._hover_mem:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.setCursor(self._cursor_for_edge(self._edge_at(pos)))
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        moved = (pos - self._press_pos).manhattanLength() if self._press_pos is not None else 999
        if self._dragging or self._resize_edge:
            self._persist_geometry()
        # a press+release with no real movement is a click, even though _dragging
        # is armed on press — use that to trigger the memory boost
        if (event.button() == Qt.MouseButton.LeftButton
                and moved <= 5
                and self._press_row_key == "mem_usage"):
            if self._is_circular():
                if self._in_center_hit(pos):
                    self.memoryBoostRequested.emit()
            else:
                row = self._row_at(pos)
                if row is not None and row["spec"].key == "mem_usage":
                    self.memoryBoostRequested.emit()
        self._dragging = False
        self._resize_edge = None
        self._press_global = None
        self._press_row_key = ""
        self._press_pos = QPoint()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        event.accept()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover_settings = False
        self._hover_disc = False
        self._update_net_card()
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def _do_resize(self, global_pos) -> None:
        if self._press_geom.isEmpty():
            return
        delta = global_pos - self._press_global
        g = QRect(self._press_geom)
        l, r, t, b = self._resize_edge or (False, False, False, False)
        # clamp instead of rejecting: the edge stops at the minimum size
        min_w = self.minimumWidth() or self.MIN_WIDTH
        min_h = self.minimumHeight() or self.MIN_HEIGHT
        if l:
            g.setLeft(min(g.left() + delta.x(), g.right() - min_w + 1))
        if r:
            g.setRight(max(g.right() + delta.x(), g.left() + min_w - 1))
        if t:
            g.setTop(min(g.top() + delta.y(), g.bottom() - min_h + 1))
        if b:
            g.setBottom(max(g.bottom() + delta.y(), g.top() + min_h - 1))
        self.setGeometry(g)
        self._relayout()
        self.update()

    def _persist_geometry(self) -> None:
        g = self.geometry()
        self.settings.set("window", "x", g.x())
        self.settings.set("window", "y", g.y())
        self.settings.set("window", "width", g.width())
        self.settings.set("window", "height", g.height())

    # ------------------------------------------------------------------
    # context menu
    # ------------------------------------------------------------------
    def _show_menu(self, global_pos) -> None:
        s = self.settings
        menu = QMenu(self)

        act_settings = QAction("⚙  设置…", menu)
        act_settings.triggered.connect(self.settingsRequested.emit)
        menu.addAction(act_settings)

        menu.addSeparator()

        act_lock = QAction("锁定位置", menu)
        act_lock.setCheckable(True)
        act_lock.setChecked(bool(s.get("window", "locked", default=False)))
        act_lock.toggled.connect(lambda on: s.set("window", "locked", bool(on)))
        menu.addAction(act_lock)

        act_top = QAction("窗口置顶", menu)
        act_top.setCheckable(True)
        act_top.setChecked(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        act_top.toggled.connect(self._toggle_topmost)
        menu.addAction(act_top)

        act_click = QAction("鼠标穿透", menu)
        act_click.setCheckable(True)
        act_click.setChecked(bool(s.get("window", "click_through", default=False)))
        act_click.setToolTip("开启后浮窗不响应鼠标，可从托盘菜单恢复")
        act_click.toggled.connect(self._toggle_click_through)
        menu.addAction(act_click)

        menu.addSeparator()

        act_copy = QAction("复制当前数据", menu)
        act_copy.triggered.connect(self._copy_snapshot)
        menu.addAction(act_copy)

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_quit)

        menu.exec(global_pos)

    def _toggle_topmost(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()
        platform_win.apply_overlay_styles(self)
        if on:
            platform_win.force_topmost(self)

    def _toggle_click_through(self, on: bool) -> None:
        self.settings.set("window", "click_through", bool(on))
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, bool(on))
        self.show()
        platform_win.apply_overlay_styles(self)
        platform_win.set_click_through(self, bool(on))
        if not on:
            platform_win.force_topmost(self)

    def _copy_snapshot(self) -> None:
        unit = str(self.settings.get("sampling", "temp_unit", default="C"))
        lines = []
        for spec in self._enabled_specs():
            num, u = theme.format_value(spec, getattr(self.metrics, spec.key, None), unit)
            lines.append(f"{spec.label}: {num}{u}")
        QApplication.clipboard().setText("\n".join(lines))

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self.settings.unsubscribe(self._on_settings_changed)
        super().closeEvent(event)
