"""Self-test for the memory-gauge network-speed hover card.

Run from the project root:

    .venv/Scripts/python.exe tools/test_net_hover.py

Creates the overlay in memory-only (circular) mode, simulates hovering the
disc, and saves screenshots plus a composited "as on screen" image to
build/test_net_hover/.  Exits non-zero on any failed assertion.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ["PERF_OVERLAY_CONFIG"] = str(Path(tempfile.mkdtemp()) / "settings.json")

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from perf_overlay.config import Settings
from perf_overlay.metrics import Metrics
from perf_overlay.ui.overlay import OverlayWidget

OUT = Path(__file__).resolve().parent.parent / "build" / "test_net_hover"


def check(name: str, cond: bool) -> None:
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        raise SystemExit(f"test failed: {name}")


def main() -> int:
    app = QApplication([])
    settings = Settings.load()
    for key in settings.get("metrics"):
        settings.set("metrics", key, False, notify=False)
    settings.set("metrics", "mem_usage", True, notify=False)

    w = OverlayWidget(settings)
    w.setGeometry(200, 200, 64, 64)
    w.show()

    m = Metrics(mem_usage=57.0, net_up=16384.0, net_down=16384.0)
    w.set_metrics(m)
    app.processEvents()

    check("circular mode active", w._is_circular())
    check("card hidden before hover", not w._net_card.isVisible())

    # ---- hover inside the disc ---------------------------------------
    inside = QPoint(w.rect().center())
    w._hover_disc = w._in_disc(inside)
    w._update_net_card()
    app.processEvents()
    check("hover inside disc detected", w._hover_disc)
    check("card visible on hover", w._net_card.isVisible())
    check("card positioned left of overlay",
          w._net_card.x() + w._net_card.width() <= w.x())

    # composite screenshot as it would appear on screen
    card_pm = w._net_card.grab()
    w._net_card.repaint()
    card_pm = w._net_card.grab()
    ov_pm = w.grab()
    x0 = min(w.x(), w._net_card.x())
    y0 = min(w.y(), w._net_card.y())
    x1 = max(w.x() + w.width(), w._net_card.x() + w._net_card.width())
    y1 = max(w.y() + w.height(), w._net_card.y() + w._net_card.height())
    canvas = QPixmap(x1 - x0 + 20, y1 - y0 + 20)
    canvas.fill(QColor(40, 44, 60))
    p = QPainter(canvas)
    p.drawPixmap(w._net_card.x() - x0 + 10, w._net_card.y() - y0 + 10, card_pm)
    p.drawPixmap(w.x() - x0 + 10, w.y() - y0 + 10, ov_pm)
    p.end()
    OUT.mkdir(parents=True, exist_ok=True)
    canvas.save(str(OUT / "hover.png"))
    card_pm.save(str(OUT / "card.png"))
    ov_pm.save(str(OUT / "gauge.png"))

    # ---- leave the disc ----------------------------------------------
    w._hover_disc = False
    w._update_net_card()
    app.processEvents()
    check("card hidden after leave", not w._net_card.isVisible())

    # ---- hover outside the disc (ring corner) ------------------------
    corner = QPoint(2, 2)
    if w._in_disc(corner):  # 64px disc: corner must be outside
        check("corner outside disc", False)
    else:
        w._hover_disc = False
        check("corner does not trigger card", not w._net_card.isVisible())

    # ---- non-circular mode never shows the card ----------------------
    settings.set("metrics", "cpu_usage", True)
    app.processEvents()
    check("rectangular mode entered", not w._is_circular())
    w._hover_disc = True
    w._update_net_card()
    app.processEvents()
    check("card stays hidden in rectangular mode", not w._net_card.isVisible())

    # ---- speed formatting --------------------------------------------
    from perf_overlay.ui.theme import format_speed
    check("format 16384 B/s -> 16.0K/s", format_speed(16384.0) == "16.0K/s")
    check("format 1.5 MB/s -> 1.46M/s", format_speed(1024 * 1500) == "1.46M/s")
    check("format None -> --", format_speed(None) == "--")
    check("format negative clamps to 0.0K/s", format_speed(-5) == "0.0K/s")

    # ---- real sensor provider fills net fields -----------------------
    from perf_overlay.sensors.system import SystemProvider
    prov = SystemProvider()
    snap = Metrics()
    prov.poll(snap)
    prov.poll(snap)
    prov.stop()
    check("provider reports net_up", snap.net_up is not None)
    check("provider reports net_down", snap.net_down is not None)
    print(f"       live net: up={snap.net_up:.0f} B/s  down={snap.net_down:.0f} B/s")

    print(f"screenshots -> {OUT}")
    QTimer.singleShot(0, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
