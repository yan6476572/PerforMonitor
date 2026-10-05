"""Colours, formatting and Qt style sheets."""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QLinearGradient, QPainter, QPainterPath

from ..metrics import MetricSpec

# ---------------------------------------------------------------- palette
DEFAULT_BG = "#12141C"
DEFAULT_ACCENT = "#5B8CFF"

#: inner padding of the HUD panel, in pixels
PAD = 16

DEFAULT_TEXT = "#EAF0FA"      # 主文字（数值）
DEFAULT_LABEL = "#8A93A8"     # 次级文字（标签）
TEXT_PRIMARY = QColor(DEFAULT_TEXT)
TEXT_MUTED = QColor(DEFAULT_LABEL)
TEXT_DIM = QColor("#5D6577")
TRACK = QColor(255, 255, 255, 26)
BORDER = QColor(255, 255, 255, 26)

GOOD = QColor("#34D399")
WARN = QColor("#FBBF24")
HOT = QColor("#F87171")

FONT_STACK = {
    "win32": ["Segoe UI Variable Display", "Segoe UI"],
    "darwin": ["SF Pro Display", "Helvetica Neue"],
}.get(__import__("sys").platform, ["Inter", "Noto Sans", "DejaVu Sans"])


def ui_font(point_size: int = 12, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    for family in FONT_STACK:
        f = QFont(family, point_size)
        if f.exactMatch():
            f.setWeight(weight)
            f.setPointSize(point_size)
            f.setStyleHint(QFont.StyleHint.SansSerif)
            return f
    f = QFont()
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPointSize(point_size)
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------- colour utils
def qcolor(value: str | QColor, alpha: Optional[int] = None) -> QColor:
    c = QColor(value) if not isinstance(value, QColor) else QColor(value)
    if not c.isValid():
        c = QColor(DEFAULT_BG)
    if alpha is not None:
        c.setAlpha(max(0, min(255, alpha)))
    return c


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def lighten(c: QColor, t: float) -> QColor:
    return mix(c, QColor(255, 255, 255, c.alpha()), t)


def darken(c: QColor, t: float) -> QColor:
    return mix(c, QColor(0, 0, 0, c.alpha()), t)


# ---------------------------------------------------------------- formatting
def to_display_temp(celsius: Optional[float], unit: str) -> Optional[float]:
    if celsius is None:
        return None
    return celsius * 9.0 / 5.0 + 32.0 if unit.upper() == "F" else celsius


def display_unit(spec: MetricSpec, temp_unit: str = "C") -> str:
    if spec.kind == "temp":
        return "°F" if temp_unit.upper() == "F" else "°C"
    return spec.unit


def fit_font(base: QFont, text: str, max_w: int, max_h: int = 0, min_size: int = 6) -> QFont:
    """Shrink *base* until *text* fits in max_w x max_h (max_h 0 = ignore height)."""
    f = QFont(base)
    size = f.pointSize()
    while size > min_size:
        f.setPointSize(size)
        fm = QFontMetrics(f)
        if fm.horizontalAdvance(text) <= max_w and (max_h <= 0 or fm.height() <= max_h):
            return f
        size -= 1
    f.setPointSize(min_size)
    return f


def elided(text: str, font: QFont, max_w: int) -> str:
    if max_w <= 0:
        return text
    return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, max_w)


def format_value(spec: MetricSpec, value: Optional[float], temp_unit: str = "C") -> Tuple[str, str]:
    """Returns (number_text, unit_text)."""
    if value is None:
        return "--", display_unit(spec, temp_unit)
    if spec.kind == "temp":
        v = to_display_temp(value, temp_unit)
        return f"{v:.0f}", display_unit(spec, temp_unit)
    if spec.kind == "power":
        return f"{value:.1f}", spec.unit
    return f"{value:.0f}", spec.unit


def format_speed(bps: Optional[float]) -> str:
    """Network throughput (bytes/s) -> short text like ``16.0K/s`` / ``1.25M/s``."""
    if bps is None:
        return "--"
    k = max(0.0, float(bps)) / 1024.0
    if k >= 1024.0:
        return f"{k / 1024.0:.2f}M/s"
    return f"{k:.1f}K/s"


def value_color(spec: MetricSpec, value: Optional[float], temp_unit: str = "C",
                text: Optional[QColor] = None) -> QColor:
    """Colour for a metric value.  ``text`` overrides the normal (healthy) colour."""
    healthy = text if text is not None else TEXT_PRIMARY
    if value is None:
        return TEXT_DIM
    if spec.kind == "temp":
        c = to_display_temp(value, temp_unit)
        threshold = (75.0, 90.0) if temp_unit.upper() != "F" else (167.0, 194.0)
        if c >= threshold[1]:
            return HOT
        if c >= threshold[0]:
            return WARN
    if spec.kind == "percent" and value >= 92:
        return WARN
    return healthy


# ---------------------------------------------------------------- painting helpers
def draw_rounded_rect(painter: QPainter, rect, radius: float, fill: QColor,
                      border: Optional[QColor] = None, border_width: float = 1.0) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillPath(path, fill)
    if border is not None and border.alpha() > 0:
        pen = painter.pen()
        pen.setColor(border)
        pen.setWidthF(border_width)
        painter.setPen(pen)
        painter.drawPath(path)
    painter.restore()


def draw_shadow(painter: QPainter, rect, radius: float, color: QColor, layers: int = 7) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for i in range(layers, 0, -1):
        spread = i * 1.9
        alpha = int(color.alpha() * (1.0 - (i - 1) / (layers + 1.0)) ** 2)
        c = QColor(color)
        c.setAlpha(max(0, alpha))
        painter.fillPath(_rounded_path(rect.adjusted(-spread, -spread, spread, spread),
                                       radius + spread), c)
    painter.restore()


def _rounded_path(rect, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def vertical_gradient(top: QColor, bottom: QColor):
    g = QLinearGradient(0, 0, 0, 1)
    g.setColorAt(0.0, top)
    g.setColorAt(1.0, bottom)
    return g


# ---------------------------------------------------------------- settings QSS
SETTINGS_QSS = """
* { font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; }
QDialog#SettingsDialog {
    background-color: #171A23;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 16px;
}
QLabel#DialogTitle { color: #EAF0FA; font-size: 22px; font-weight: 600; }
QLabel#DialogSubtitle { color: #6B7387; font-size: 15px; letter-spacing: 1px; }
QLabel#SectionTitle {
    color: #7FA6FF; font-size: 15px; font-weight: 700; letter-spacing: 1.4px;
    padding-top: 10px;
}
QLabel#FieldLabel { color: #A7B0C3; font-size: 17px; background: transparent; }
QLabel#ValueLabel { color: #EAF0FA; font-size: 17px; font-weight: 600; background: transparent; }
QLabel#Hint { color: #5D6577; font-size: 15px; background: transparent; }

QCheckBox {
    color: #C7CEDC; font-size: 17px; spacing: 8px; background: transparent;
}
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; }
QCheckBox::indicator:unchecked {
    background-color: #232734; border: 1px solid rgba(255,255,255,0.13);
}
QCheckBox::indicator:checked {
    background-color: %(accent)s; border: 1px solid %(accent)s;
    image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMiIgaGVpZ2h0PSIxMiIgdmlld0JveD0iMCAwIDEyIDEyIj48cGF0aCBkPSJNMi41IDYuMmwzIDMgNC01IiBzdHJva2U9IiNmZmYiIHN0cm9rZS13aWR0aD0iMS44IiBmaWxsPSJub25lIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4=);
}
QCheckBox::indicator:disabled { background-color: #1C1F29; }

QSlider::groove:horizontal {
    height: 4px; border-radius: 2px; background: #262B39;
}
QSlider::sub-page:horizontal {
    border-radius: 2px; background: %(accent)s;
}
QSlider::handle:horizontal {
    width: 15px; height: 15px; margin: -6px 0;
    border-radius: 7px; background: #FFFFFF;
    border: 3px solid %(accent)s;
}
QSlider::handle:horizontal:hover { background: #FFFFFF; border-color: %(accentLight)s; }

QComboBox {
    background-color: #21252F; color: #D5DBE7; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 7px; padding: 5px 26px 5px 10px; font-size: 17px; min-width: 96px;
}
QComboBox:hover { border-color: rgba(255,255,255,0.22); }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0px; height: 0px; }
QComboBox QAbstractItemView {
    background-color: #21252F; color: #D5DBE7;
    selection-background-color: %(accent)s; selection-color: #FFFFFF;
    border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
    outline: none; padding: 4px;
}

QSpinBox, QLineEdit {
    background-color: #21252F; color: #D5DBE7;
    border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
    padding: 5px 9px; font-size: 17px; selection-background-color: %(accent)s;
}
QSpinBox:focus, QLineEdit:focus { border-color: %(accent)s; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: none; }

QPushButton#ColorSwatch {
    border: 1px solid rgba(255,255,255,0.16); border-radius: 8px;
    min-width: 58px; max-width: 58px; min-height: 24px; max-height: 24px;
}
QPushButton#ColorSwatch:hover { border-color: rgba(255,255,255,0.38); }

QPushButton#Ghost {
    background: transparent; color: #8A93A8; border: 1px solid rgba(255,255,255,0.12);
    border-radius: 9px; padding: 8px 18px; font-size: 17px;
}
QPushButton#Ghost:hover { color: #D5DBE7; border-color: rgba(255,255,255,0.26); }

QPushButton#Primary {
    background-color: %(accent)s; color: #0B0D13; border: none;
    border-radius: 9px; padding: 8px 22px; font-size: 17px; font-weight: 700;
}
QPushButton#Primary:hover { background-color: %(accentLight)s; }
QPushButton#Primary:pressed { background-color: %(accentDark)s; }

QPushButton#Flat {
    background: transparent; color: #A7B0C3; border: none;
    border-radius: 8px; padding: 7px 14px; font-size: 17px;
}
QPushButton#Flat:hover { background-color: rgba(255,255,255,0.07); color: #EAF0FA; }

QScrollArea { border: none; background: transparent; }
QWidget#ScrollBody { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.16); border-radius: 4px; min-height: 28px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QFrame#Divider { background: rgba(255,255,255,0.07); max-height: 1px; border: none; }
"""


def settings_qss(accent: str = DEFAULT_ACCENT) -> str:
    a = QColor(accent)
    if not a.isValid():
        a = QColor(DEFAULT_ACCENT)
    return SETTINGS_QSS % {
        "accent": a.name(),
        "accentLight": lighten(a, 0.22).name(),
        "accentDark": darken(a, 0.22).name(),
    }


MENU_QSS = """
QMenu {
    background-color: #171A23;
    color: #EAF0FA;
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 10px;
    padding: 6px;
    font-size: 16px;
}
QMenu::item {
    padding: 8px 34px 8px 14px;
    border-radius: 7px;
    background: transparent;
}
QMenu::item:selected { background-color: %(accent)s; color: #0B0D13; }
QMenu::item:disabled { color: #5D6577; }
QMenu::separator { height: 1px; background: rgba(255,255,255,0.09); margin: 5px 10px; }
QMenu::indicator { width: 17px; height: 17px; margin-left: 4px; }
"""


def menu_qss(accent: str = DEFAULT_ACCENT) -> str:
    a = QColor(accent)
    if not a.isValid():
        a = QColor(DEFAULT_ACCENT)
    return MENU_QSS % {"accent": a.name()}
