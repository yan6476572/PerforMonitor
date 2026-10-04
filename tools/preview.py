#!/usr/bin/env python3
"""Render the HUD and the settings dialog to PNGs, using synthetic sensor data.

Usage:
    QT_QPA_PLATFORM=offscreen python tools/preview.py [outdir]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PERF_OVERLAY_CONFIG", str(ROOT / ".openclaw-preview.json"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from perf_overlay.config import Settings  # noqa: E402
from perf_overlay.metrics import Metrics  # noqa: E402
from perf_overlay.ui.overlay import OverlayWidget  # noqa: E402
from perf_overlay.ui.settings import SettingsDialog  # noqa: E402

SAMPLE = Metrics(
    cpu_temp=61.0, cpu_power=78.4, cpu_usage=42.0,
    gpu_temp=57.0, gpu_power=186.3, gpu_usage=87.0,
    mem_usage=51.0, fps=143.0,
)


def grab(widget, path: Path, w: int, h: int) -> None:
    widget.resize(w, h)
    widget.show()
    app.processEvents()
    for _ in range(6):
        app.processEvents()
    pix = widget.grab()
    pix.save(str(path))
    print(f"  -> {path.name}  {pix.width()}x{pix.height()}")


def main(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    settings = Settings.load()

    ov = OverlayWidget(settings)
    ov.set_metrics(SAMPLE)

    # 1) default stacked look
    settings.set("appearance", "layout", "stack", notify=False)
    settings.set("appearance", "bg_color", "#12141C", notify=False)
    settings.set("appearance", "accent_color", "#5B8CFF", notify=False)
    settings.set("window", "width", 268, notify=False)
    settings.set("window", "height", 382, notify=False)
    ov.apply_settings()
    grab(ov, outdir / "hud-default.png", 268, 382)

    # 2) taller / translucent purple variant
    settings.set("appearance", "bg_color", "#1B1230", notify=False)
    settings.set("appearance", "accent_color", "#C084FC", notify=False)
    settings.set("appearance", "bg_opacity", 68, notify=False)
    settings.set("appearance", "corner_radius", 20, notify=False)
    ov.apply_settings()
    grab(ov, outdir / "hud-purple.png", 268, 382)

    # 3) compact two-column grid
    settings.set("appearance", "layout", "grid", notify=False)
    settings.set("appearance", "bg_color", "#0E1116", notify=False)
    settings.set("appearance", "accent_color", "#34D399", notify=False)
    settings.set("appearance", "bg_opacity", 88, notify=False)
    settings.set("appearance", "corner_radius", 12, notify=False)
    ov.apply_settings()
    grab(ov, outdir / "hud-grid.png", 420, 220)

    # 4) restore default look for the settings screenshot
    settings.set("appearance", "layout", "stack", notify=False)
    settings.set("appearance", "bg_color", "#12141C", notify=False)
    settings.set("appearance", "accent_color", "#5B8CFF", notify=False)
    settings.set("appearance", "bg_opacity", 82, notify=False)
    settings.set("appearance", "corner_radius", 14, notify=False)
    ov.apply_settings()

    dlg = SettingsDialog(settings, None)
    dlg.resize(580, 760)
    dlg.show()
    app.processEvents()
    for _ in range(10):
        app.processEvents()
    pix = dlg.grab()
    pix.save(str(outdir / "settings.png"))
    print(f"  -> settings.png  {pix.width()}x{pix.height()}")

    # 5) a bare "minimal" config to show the toggle feature
    for key in ("cpu_power", "gpu_power", "mem_usage", "fps"):
        settings.set("metrics", key, False, notify=False)
    settings.set("appearance", "show_bars", False, notify=False)
    ov.apply_settings()
    grab(ov, outdir / "hud-minimal.png", 230, 190)

    QTimer.singleShot(60, app.quit)
    app.exec()


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    app = QApplication(sys.argv[:1])
    main(out)
