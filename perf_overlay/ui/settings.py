"""The settings dialog — dark card UI with live preview."""

from __future__ import annotations

import copy
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import Settings
from ..metrics import GROUP_COLORS, METRIC_SPECS
from . import theme


# ------------------------------------------------------------------ widgets
class Dropdown(QComboBox):
    """Combo box with a hand-painted chevron (QSS arrow rendering is unreliable)."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#8A93A8"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        x = self.width() - 15
        y = self.height() / 2.0
        p.drawPolyline(QPolygon([
            QPoint(int(x - 4), int(y - 2)),
            QPoint(int(x), int(y + 2)),
            QPoint(int(x + 4), int(y - 2)),
        ]))
        p.end()


class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ColorSwatch")
        self._color = QColor(value)
        self.setToolTip("点击打开取色器")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._apply_style()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"QPushButton#ColorSwatch {{ background-color: {self._color.name()};"
            f" border: 1px solid rgba(255,255,255,0.20); border-radius: 8px;"
            f" min-width: 58px; max-width: 58px; min-height: 24px; max-height: 24px; }}"
        )

    def _pick(self) -> None:
        c = QColorDialog.getColor(self._color, self, "选择颜色")
        if c.isValid():
            self._color = c
            self._apply_style()
            self.colorChanged.emit(c.name())

    def set_color(self, value: str) -> None:
        self._color = QColor(value)
        self._apply_style()


class SliderRow(QWidget):
    valueChanged = Signal(int)

    def __init__(self, label: str, value: int, lo: int, hi: int,
                 suffix: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self._suffix = suffix
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        lab.setFixedWidth(76)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.readout = QLabel(f"{value}{suffix}")
        self.readout.setObjectName("ValueLabel")
        self.readout.setFixedWidth(44)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(lab)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.readout)
        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, v: int) -> None:
        self.readout.setText(f"{v}{self._suffix}")
        self.valueChanged.emit(v)

    def set_value(self, v: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.readout.setText(f"{v}{self._suffix}")


def _divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFrameShape(QFrame.Shape.NoFrame)
    f.setFixedHeight(1)
    return f


# ------------------------------------------------------------------ dialog
class SettingsDialog(QDialog):
    """Live-editing settings card. Edits write straight into ``Settings``."""

    closed = Signal(bool)  # True if saved

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._snapshot = copy.deepcopy(settings.data)
        self._saved = False
        self._dragging = False
        self._drag_offset = None

        self.setObjectName("SettingsDialog")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumSize(540, 660)
        self.resize(580, 760)

        self.setStyleSheet(theme.settings_qss(settings.get("appearance", "accent_color",
                                                           default=theme.DEFAULT_ACCENT)))
        self._build()

    # -- construction -------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        shell = QWidget()
        shell.setObjectName("SettingsDialog")
        shell.setStyleSheet(
            "QWidget#SettingsDialog { background-color: #171A23;"
            " border: 1px solid rgba(255,255,255,0.09); border-radius: 16px; }"
        )
        root.addWidget(shell)

        lay = QVBoxLayout(shell)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)

        # ---- title bar
        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("设置")
        title.setObjectName("DialogTitle")
        sub = QLabel("PerformanceMonitor")
        sub.setObjectName("DialogSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        title_row.addLayout(title_col)
        title_row.addStretch(1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("Flat")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn)
        lay.addLayout(title_row)
        lay.addWidget(_divider())

        # ---- scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("ScrollBody")
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 6, 4, 6)
        self.body_lay.setSpacing(6)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        self._path_edits = []
        self._build_metrics_section()
        self._build_appearance_section()
        self._build_sampling_section()
        self._build_window_section()
        self.body_lay.addStretch(1)

        # ---- footer
        lay.addWidget(_divider())
        foot = QHBoxLayout()
        foot.setSpacing(10)
        reset_btn = QPushButton("恢复默认")
        reset_btn.setObjectName("Ghost")
        reset_btn.clicked.connect(self._reset)
        foot.addWidget(reset_btn)
        foot.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("Flat")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存并关闭")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self._save)
        apply_btn = QPushButton("保存")
        apply_btn.setObjectName("Ghost")
        apply_btn.clicked.connect(self._apply_only)
        foot.addWidget(cancel_btn)
        foot.addWidget(apply_btn)
        foot.addWidget(save_btn)
        lay.addLayout(foot)

    # -- sections -----------------------------------------------------
    def _section(self, title: str) -> QVBoxLayout:
        lab = QLabel(title)
        lab.setObjectName("SectionTitle")
        self.body_lay.addWidget(lab)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 2, 0, 6)
        v.setSpacing(7)
        self.body_lay.addWidget(box)
        return v

    @staticmethod
    def _field(label: str, width: int = 100) -> QLabel:
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        lab.setFixedWidth(width)
        return lab

    def _build_metrics_section(self) -> None:
        v = self._section("显示项目")
        self.metric_checks = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        for i, spec in enumerate(METRIC_SPECS):
            cb = QCheckBox(spec.label)
            cb.setChecked(self.settings.metric_enabled(spec.key))
            cb.toggled.connect(lambda on, k=spec.key: self.settings.set("metrics", k, bool(on)))
            self.metric_checks[spec.key] = cb
            grid.addWidget(cb, i // 2, i % 2)
        grid.setColumnMinimumWidth(0, 226)
        grid.setColumnMinimumWidth(1, 226)
        grid.setColumnStretch(2, 1)
        v.addLayout(grid)
        hint = QLabel("取消勾选即可在浮窗中隐藏对应指标。")
        hint.setObjectName("Hint")
        v.addWidget(hint)

    def _build_appearance_section(self) -> None:
        v = self._section("外观")
        s = self.settings

        # colours — two aligned columns
        row = QGridLayout()
        row.setHorizontalSpacing(12)
        row.setVerticalSpacing(7)
        row.setColumnMinimumWidth(0, 100)
        row.setColumnMinimumWidth(2, 100)
        row.addWidget(self._field("背景颜色"), 0, 0)
        self.bg_btn = ColorButton(s.get("appearance", "bg_color", default=theme.DEFAULT_BG))
        self.bg_btn.colorChanged.connect(lambda c: s.set("appearance", "bg_color", c))
        row.addWidget(self.bg_btn, 0, 1, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(self._field("强调色"), 0, 2)
        self.accent_btn = ColorButton(s.get("appearance", "accent_color", default=theme.DEFAULT_ACCENT))
        self.accent_btn.colorChanged.connect(self._on_accent)
        row.addWidget(self.accent_btn, 0, 3, Qt.AlignmentFlag.AlignLeft)
        row.setColumnStretch(4, 1)
        v.addLayout(row)

        # 字体颜色 — 第二行
        row2 = QGridLayout()
        row2.setHorizontalSpacing(12)
        row2.setVerticalSpacing(7)
        row2.setColumnMinimumWidth(0, 100)
        row2.setColumnMinimumWidth(2, 100)
        row2.addWidget(self._field("数值颜色"), 0, 0)
        self.text_btn = ColorButton(s.get("appearance", "text_color", default=theme.DEFAULT_TEXT))
        self.text_btn.colorChanged.connect(lambda c: s.set("appearance", "text_color", c))
        row2.addWidget(self.text_btn, 0, 1, Qt.AlignmentFlag.AlignLeft)
        row2.addWidget(self._field("标签颜色"), 0, 2)
        self.label_btn = ColorButton(s.get("appearance", "label_color", default=theme.DEFAULT_LABEL))
        self.label_btn.colorChanged.connect(lambda c: s.set("appearance", "label_color", c))
        row2.addWidget(self.label_btn, 0, 3, Qt.AlignmentFlag.AlignLeft)
        row2.setColumnStretch(4, 1)
        v.addLayout(row2)

        self.bg_opacity = SliderRow("背景透明度", int(s.get("appearance", "bg_opacity", default=82)), 0, 100, "%")
        self.bg_opacity.valueChanged.connect(lambda val: s.set("appearance", "bg_opacity", int(val)))
        v.addWidget(self.bg_opacity)

        self.win_opacity = SliderRow("整体透明度", int(s.get("appearance", "window_opacity", default=100)), 15, 100, "%")
        self.win_opacity.valueChanged.connect(lambda val: s.set("appearance", "window_opacity", int(val)))
        v.addWidget(self.win_opacity)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        row2.addWidget(self._field("布局"))
        self.radio_h = QRadioButton("横排显示")
        self.radio_v = QRadioButton("纵排显示")
        if self._layout_mode_h(s):
            self.radio_h.setChecked(True)
        else:
            self.radio_v.setChecked(True)
        self.radio_h.toggled.connect(self._on_layout_mode)
        row2.addWidget(self.radio_h)
        row2.addSpacing(8)
        row2.addWidget(self.radio_v)
        row2.addSpacing(18)
        self.bars_cb = QCheckBox("显示进度条")
        self.bars_cb.setChecked(bool(s.get("appearance", "show_bars", default=True)))
        self.bars_cb.toggled.connect(lambda on: s.set("appearance", "show_bars", bool(on)))
        row2.addWidget(self.bars_cb)
        row2.addStretch(1)
        v.addLayout(row2)

    @staticmethod
    def _layout_mode_h(s: Settings) -> bool:
        return str(s.get("appearance", "layout", default="v")) in ("h", "strip", "hstrip")

    def _on_layout_mode(self) -> None:
        self.settings.set("appearance", "layout", "h" if self.radio_h.isChecked() else "v")

    def _build_sampling_section(self) -> None:
        v = self._section("采样")
        s = self.settings

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._field("刷新间隔"))
        self.interval = QSpinBox()
        self.interval.setRange(100, 5000)
        self.interval.setSingleStep(100)
        self.interval.setSuffix(" ms")
        self.interval.setValue(int(s.get("sampling", "interval_ms", default=1000)))
        self.interval.valueChanged.connect(lambda val: s.set("sampling", "interval_ms", int(val)))
        row.addWidget(self.interval)
        row.addStretch(1)
        v.addLayout(row)

        hint = QLabel("其他传感器按此间隔刷新（100–5000 ms，默认 1 秒）；"
                      "网速固定 500 ms 刷新，不受此值影响。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)

    def _build_window_section(self) -> None:
        v = self._section("窗口行为")
        s = self.settings
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)

        self.lock_cb = QCheckBox("锁定位置（禁止拖动）")
        self.lock_cb.setChecked(bool(s.get("window", "locked", default=False)))
        self.lock_cb.toggled.connect(lambda on: s.set("window", "locked", bool(on)))
        grid.addWidget(self.lock_cb, 0, 0)

        self.click_cb = QCheckBox("鼠标穿透")
        self.click_cb.setChecked(bool(s.get("window", "click_through", default=False)))
        self.click_cb.setToolTip("开启后浮窗不响应鼠标；可从系统托盘图标恢复")
        self.click_cb.toggled.connect(lambda on: s.set("window", "click_through", bool(on)))
        grid.addWidget(self.click_cb, 0, 1)

        self.top_cb = QCheckBox("窗口置顶")
        self.top_cb.setChecked(True)
        self.top_cb.setEnabled(False)
        self.top_cb.setToolTip("浮窗始终保持在其他窗口之上")
        grid.addWidget(self.top_cb, 1, 0)

        self.autostart_cb = QCheckBox("开机自启动")
        self.autostart_cb.setToolTip("登录 Windows 时自动启动（任务计划程序，最高权限）")
        self._refresh_autostart_state()
        self.autostart_cb.toggled.connect(self._on_autostart_toggled)
        grid.addWidget(self.autostart_cb, 1, 1)

        grid.setColumnMinimumWidth(0, 226)
        grid.setColumnMinimumWidth(1, 226)
        grid.setColumnStretch(2, 1)

        hint = QLabel("提示：浮窗尺寸随布局（横排 / 纵排）和指标数量自动调整，"
                      "右键浮窗打开设置。全屏独占游戏请使用「无边框窗口化」模式才能看到浮窗。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 0, 1, 2)
        v.addLayout(grid)

    def _refresh_autostart_state(self) -> None:
        # blockSignals：避免 setChecked 触发 toggled 误装/误卸任务
        try:
            from .. import autostart
            on = bool(autostart.status().get("enabled"))
        except Exception:
            on = False
        self.autostart_cb.blockSignals(True)
        self.autostart_cb.setChecked(on)
        self.autostart_cb.blockSignals(False)

    def _on_autostart_toggled(self, on: bool) -> None:
        try:
            from .. import autostart
            r = autostart.set_enabled(bool(on))
            if not r.get("ok"):
                self.autostart_cb.blockSignals(True)
                self.autostart_cb.setChecked(not on)
                self.autostart_cb.blockSignals(False)
                self.autostart_cb.setToolTip(f"设置失败：{r.get('error')}")
        except Exception as exc:
            self.autostart_cb.setToolTip(f"设置失败：{exc}")

    # -- handlers -----------------------------------------------------
    def _on_accent(self, color: str) -> None:
        self.settings.set("appearance", "accent_color", color)
        self.setStyleSheet(theme.settings_qss(color))

    # -- buttons ------------------------------------------------------
    def _sync_from_settings(self) -> None:
        """Pull current values back into every control (used by 恢复默认)."""
        s = self.settings
        for key, cb in self.metric_checks.items():
            cb.blockSignals(True)
            cb.setChecked(s.metric_enabled(key))
            cb.blockSignals(False)

        self.bg_btn.set_color(s.get("appearance", "bg_color", default=theme.DEFAULT_BG))
        self.accent_btn.set_color(s.get("appearance", "accent_color", default=theme.DEFAULT_ACCENT))
        self.text_btn.set_color(s.get("appearance", "text_color", default=theme.DEFAULT_TEXT))
        self.label_btn.set_color(s.get("appearance", "label_color", default=theme.DEFAULT_LABEL))
        self.bg_opacity.set_value(int(s.get("appearance", "bg_opacity", default=82)))
        self.win_opacity.set_value(int(s.get("appearance", "window_opacity", default=100)))
        h = self._layout_mode_h(s)
        self.radio_h.setChecked(h)
        self.radio_v.setChecked(not h)
        self.bars_cb.setChecked(bool(s.get("appearance", "show_bars", default=True)))

        self.lock_cb.setChecked(bool(s.get("window", "locked", default=False)))
        self.click_cb.setChecked(bool(s.get("window", "click_through", default=False)))
        self.setStyleSheet(theme.settings_qss(s.get("appearance", "accent_color",
                                                   default=theme.DEFAULT_ACCENT)))

    def _apply_only(self) -> None:
        self.settings.save()
        self._snapshot = copy.deepcopy(self.settings.data)

    def _save(self) -> None:
        self._apply_only()
        self._saved = True
        self.accept()

    def _reset(self) -> None:
        self.settings.reset()
        self._snapshot = copy.deepcopy(self.settings.data)
        self._sync_from_settings()
        self.settings.save()

    def reject(self) -> None:  # noqa: D102
        # revert live edits
        self.settings.data = copy.deepcopy(self._snapshot)
        self.settings.notify()
        super().reject()
        self.closed.emit(False)

    def accept(self) -> None:  # noqa: D102
        super().accept()
        self.closed.emit(True)

    # -- frameless dragging + rounded painting ------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(255, 255, 255, 22)))
        p.setBrush(QColor("#171A23"))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)
        p.end()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
