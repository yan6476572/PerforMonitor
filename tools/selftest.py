#!/usr/bin/env python3
"""Functional self-test for PerfOverlay's core interactions.

Run:  QT_QPA_PLATFORM=offscreen python tools/selftest.py
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CFG = ROOT / ".openclaw-selftest.json"
if CFG.exists():
    CFG.unlink()
os.environ["PERF_OVERLAY_CONFIG"] = str(CFG)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from perf_overlay.config import Settings  # noqa: E402
from perf_overlay.metrics import Metrics, METRIC_SPECS  # noqa: E402
from perf_overlay.ui.overlay import OverlayWidget  # noqa: E402
from perf_overlay.ui.settings import SettingsDialog  # noqa: E402
from perf_overlay.ui.theme import format_value  # noqa: E402

app = QApplication(sys.argv[:1])

RESULTS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def sample() -> Metrics:
    return Metrics(cpu_temp=61.0, cpu_power=78.4, cpu_usage=42.0, gpu_temp=57.0,
                   gpu_power=186.3, gpu_usage=87.0, mem_usage=51.0, fps=143.0)


def make(settings: Settings) -> OverlayWidget:
    ov = OverlayWidget(settings)
    ov.setGeometry(200, 200, 268, 382)
    ov.show()
    app.processEvents()
    ov.set_metrics(sample())
    app.processEvents()
    return ov


# --------------------------------------------------------------------------
def main() -> int:
    settings = Settings.load()

    # ---- 1. all eight metrics render as rows
    ov = make(settings)
    check("8 metrics -> 8 rows", len(ov._rows) == 8, f"rows={len(ov._rows)}")

    # ---- 2. drag moves the window
    start = ov.geometry()
    c = ov.rect().center()
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    QTest.mouseMove(ov, c + QPoint(50, 30), delay=5)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c + QPoint(50, 30))
    app.processEvents()
    g = ov.geometry()
    check("drag moves window", g.x() == start.x() + 50 and g.y() == start.y() + 30,
          f"({start.x()},{start.y()}) -> ({g.x()},{g.y()})")

    # ---- 3. right-edge drag resizes width
    before = ov.geometry()
    p = QPoint(before.width() - 3, before.height() / 2)
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    QTest.mouseMove(ov, p + QPoint(60, 0), delay=5)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(60, 0))
    app.processEvents()
    after = ov.geometry()
    check("right-edge drag resizes width", after.width() == before.width() + 60,
          f"{before.width()} -> {after.width()}")

    # ---- 4. bottom-edge drag resizes height
    before = ov.geometry()
    p = QPoint(before.width() / 2, before.height() - 3)
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    QTest.mouseMove(ov, p + QPoint(0, 45), delay=5)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(0, 45))
    app.processEvents()
    after = ov.geometry()
    check("bottom-edge drag resizes height", after.height() == before.height() + 45,
          f"{before.height()} -> {after.height()}")

    # ---- 5. corner drag resizes both
    before = ov.geometry()
    p = QPoint(before.width() - 3, before.height() - 3)
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    QTest.mouseMove(ov, p + QPoint(-30, -20), delay=5)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(-30, -20))
    app.processEvents()
    after = ov.geometry()
    check("corner drag resizes both", after.width() == before.width() - 30 and after.height() == before.height() - 20,
          f"{before.width()}x{before.height()} -> {after.width()}x{after.height()}")

    # ---- 6. resize clamps at minimum size
    before = ov.geometry()
    p = QPoint(before.width() - 3, before.height() / 2)
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    QTest.mouseMove(ov, p + QPoint(-5000, 0), delay=5)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(-5000, 0))
    app.processEvents()
    after = ov.geometry()
    check("resize clamps to MIN_WIDTH", after.width() == OverlayWidget.MIN_WIDTH,
          f"width={after.width()}")

    # ---- 7. locked position ignores drag
    ov.setGeometry(300, 300, 268, 382)
    app.processEvents()
    settings.set("window", "locked", True)
    app.processEvents()
    before = ov.geometry()
    c = ov.rect().center()
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    QTest.mouseMove(ov, c + QPoint(80, 80), delay=5)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c + QPoint(80, 80))
    app.processEvents()
    after = ov.geometry()
    check("locked position ignores drag", after.topLeft() == before.topLeft(),
          f"moved={after.topLeft() != before.topLeft()}")
    settings.set("window", "locked", False)

    # ---- 8. per-metric toggles
    for k in ("cpu_power", "gpu_power", "mem_usage", "fps"):
        settings.set("metrics", k, False)
    app.processEvents()
    check("metric toggles hide rows", len(ov._rows) == 4, f"rows={len(ov._rows)}")
    for k in ("cpu_power", "gpu_power", "mem_usage", "fps"):
        settings.set("metrics", k, True)
    app.processEvents()
    check("re-enabling restores rows", len(ov._rows) == 8, f"rows={len(ov._rows)}")

    # ---- 9. row rects stay inside the panel and don't overlap
    ov.setGeometry(200, 200, 268, 382)
    app.processEvents()
    rects = [r["rect"] for r in ov._rows]
    inside = all(ov.rect().contains(r) for r in rects)
    overlap = any(rects[i].intersects(rects[i + 1]) for i in range(len(rects) - 1))
    check("rows inside panel", inside, f"rects_ok={inside}")
    check("rows do not overlap", not overlap)

    # ---- 9b. portrait stacks top-to-bottom; landscape flows left-to-right
    tops = sorted(r["rect"].top() for r in ov._rows)
    xs = sorted(r["rect"].left() for r in ov._rows)
    check("portrait flow is vertical", len(set(tops)) > 1 and tops != sorted(xs),
          f"tops={tops[:3]}...")

    ov.setGeometry(200, 200, 760, 120)  # wide strip
    app.processEvents()
    landscape = [r["rect"] for r in ov._rows]
    check("landscape flag set", bool(ov._landscape), f"w=760 h=120")
    same_band = len({r.top() for r in landscape}) == 1
    increasing_x = all(landscape[i].left() < landscape[i + 1].left() for i in range(len(landscape) - 1))
    check("landscape flow is horizontal", same_band and increasing_x,
          f"bands={len({r.top() for r in landscape})} xs={[r.left() for r in landscape[:4]]}")
    narrow = all(r.width() < 118 for r in landscape)
    check("landscape cells are compact", narrow, f"w0={landscape[0].width() if landscape else 0}")

    ov.setGeometry(200, 200, 268, 382)  # back to portrait for later checks
    app.processEvents()

    # ---- 10. unit column is shared (values line up)
    spec = METRIC_SPECS[2]  # cpu_usage -> "%"
    check("unit column width computed", ov._unit_col_w > 0, f"w={ov._unit_col_w}")

    # ---- 11. value formatting
    check("format percent", format_value(METRIC_SPECS[2], 42.0) == ("42", "%"),
          str(format_value(METRIC_SPECS[2], 42.0)))
    check("format power", format_value(METRIC_SPECS[1], 78.4) == ("78.4", "W"),
          str(format_value(METRIC_SPECS[1], 78.4)))
    check("format temp C", format_value(METRIC_SPECS[0], 61.0, "C") == ("61", "°C"),
          str(format_value(METRIC_SPECS[0], 61.0, "C")))
    check("format temp F", format_value(METRIC_SPECS[0], 61.0, "F") == ("142", "°F"),
          str(format_value(METRIC_SPECS[0], 61.0, "F")))
    check("format fps", format_value(METRIC_SPECS[7], 143.0) == ("143", "FPS"),
          str(format_value(METRIC_SPECS[7], 143.0)))
    check("format missing", format_value(METRIC_SPECS[0], None, "C") == ("--", "°C"),
          str(format_value(METRIC_SPECS[0], None, "C")))

    # ---- 12. missing sensors degrade to "--" instead of crashing
    ov.set_metrics(Metrics())
    app.processEvents()
    check("empty metrics paint safely", True)

    # ---- 12b. one-click memory boost
    mem = next((r for r in ov._rows if r["spec"].key == "mem_usage"), None)
    check("memory row present", mem is not None)
    if mem is not None:
        fired = []
        ov.memoryBoostRequested.connect(lambda: fired.append(1))
        c = mem["rect"].center()
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
        app.processEvents()
        check("mem click boosts", len(fired) == 1, f"fired={len(fired)}")
        fired.clear()
        QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
        QTest.mouseMove(ov, c + QPoint(48, 24), delay=5)
        QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           c + QPoint(48, 24))
        app.processEvents()
        check("drag does not boost", len(fired) == 0, f"fired={len(fired)}")
        ov.start_memory_boost()
        check("boost animation starts", bool(ov._boost_active))
        ov.set_boost_result(256.0)
        check("boost result accepted", ov._boost_freed == 256.0)
        ov._boost_timer.stop()
        ov._boost_active = False

    # ---- 12c. circular gauge when only memory is enabled
    for k in ("cpu_temp", "cpu_power", "cpu_usage", "gpu_temp", "gpu_power",
              "gpu_usage", "fps"):
        settings.set("metrics", k, False)
    settings.set("metrics", "mem_usage", True)
    app.processEvents()
    check("memory-only -> circular", bool(ov._is_circular()))
    check("circular has no header", not ov._header_rect.isValid())
    check("circular single target", len(ov._rows) == 1, f"rows={len(ov._rows)}")
    ov.start_memory_boost()
    ov.set_boost_result(64.0)
    check("circular boost works", ov._boost_freed == 64.0)
    ov._boost_timer.stop()
    ov._boost_active = False
    check("circular has no gear", not ov._settings_rect.isValid())
    check("circular min size compact", ov.minimumWidth() <= 100, f"min={ov.minimumWidth()}")
    circ = ov._circle_rect()
    cc = QPoint(int(circ.center().x()), int(circ.center().y()))
    edge = QPoint(ov.rect().width() - 3, cc.y())
    band = QPoint(cc.x() + int(circ.width() * 0.38), cc.y())
    check("centre hit accepts", ov._in_center_hit(cc))
    check("edge hit rejects", not ov._in_center_hit(edge))
    check("ring-band rejects", not ov._in_center_hit(band))
    fired = []
    ov.memoryBoostRequested.connect(lambda: fired.append(1))
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    app.processEvents()
    check("edge click does not boost", len(fired) == 0, f"fired={len(fired)}")
    for k in ("cpu_temp", "cpu_power", "cpu_usage", "gpu_temp", "gpu_power",
              "gpu_usage", "fps"):
        settings.set("metrics", k, True)
    app.processEvents()
    check("restored -> rectangular", not ov._is_circular())

    # ---- 13. settings persistence round-trip
    settings.set("appearance", "bg_color", "#203040")
    settings.set("appearance", "bg_opacity", 57)
    settings.set("appearance", "corner_radius", 9)
    settings.set("sampling", "interval_ms", 750)
    settings.set("window", "x", 424)
    path = settings.save()
    reloaded = Settings.load()
    ok = (reloaded.get("appearance", "bg_color") == "#203040"
          and reloaded.get("appearance", "bg_opacity") == 57
          and reloaded.get("appearance", "corner_radius") == 9
          and reloaded.get("sampling", "interval_ms") == 750
          and reloaded.get("window", "x") == 424)
    check("settings persist & reload", ok, str(path))

    # ---- 14. opacity settings actually apply
    ov.set_metrics(sample())
    settings.set("appearance", "window_opacity", 40)
    app.processEvents()
    check("window opacity applies", abs(ov.windowOpacity() - 0.40) < 0.02, f"{ov.windowOpacity():.2f}")
    settings.set("appearance", "window_opacity", 100)

    # ---- 15. always-on-top flag
    check("always-on-top flag set", bool(ov.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))

    # ---- 16. click-through flag
    ov._toggle_click_through(True)
    app.processEvents()
    check("click-through enables", bool(ov.windowFlags() & Qt.WindowType.WindowTransparentForInput))
    ov._toggle_click_through(False)
    app.processEvents()
    check("click-through disables", not bool(ov.windowFlags() & Qt.WindowType.WindowTransparentForInput))

    # ---- 17. geometry persisted on release
    settings.set("window", "locked", False)
    ov.setGeometry(111, 122, 300, 400)
    app.processEvents()
    ov._persist_geometry()
    check("geometry persisted", settings.get("window", "x") == 111
          and settings.get("window", "width") == 300)

    ov.close()

    # ---- 18. settings dialog builds and "恢复默认" re-syncs the widgets
    settings.set("appearance", "bg_color", "#334455")
    settings.set("appearance", "font_size", 18)
    settings.set("metrics", "fps", False)
    settings.set("sampling", "interval_ms", 1200)
    app.processEvents()
    dlg = SettingsDialog(settings, None)
    dlg.show()
    app.processEvents()
    check("settings dialog builds", dlg is not None and dlg.isVisible())
    check("dialog reflects changed settings",
          dlg.bg_btn._color.name().lower() == "#334455"
          and dlg.font_size.slider.value() == 18
          and not dlg.metric_checks["fps"].isChecked()
          and dlg.interval.value() == 1200)

    dlg._reset()
    app.processEvents()
    ok = (dlg.bg_btn._color.name().lower() == "#12141c"
          and dlg.font_size.slider.value() == 12
          and dlg.metric_checks["fps"].isChecked()
          and dlg.interval.value() == 500
          and dlg.layout_combo.currentData() == "stack"
          and dlg.bg_opacity.slider.value() == 82
          and dlg.win_opacity.slider.value() == 100
          and dlg.click_cb.isChecked() is False
          and dlg.lock_cb.isChecked() is False)
    check("恢复默认 re-syncs every widget", ok,
          f"bg={dlg.bg_btn._color.name()} font={dlg.font_size.slider.value()} "
          f"fps={dlg.metric_checks['fps'].isChecked()} interval={dlg.interval.value()}")

    # ---- 19. cancelling a dialog reverts live edits
    dlg.close()
    app.processEvents()
    settings.set("appearance", "bg_color", "#ABCDEF")
    app.processEvents()
    dlg2 = SettingsDialog(settings, None)
    dlg2.show()
    app.processEvents()
    settings.set("appearance", "bg_color", "#112233")
    app.processEvents()
    dlg2.reject()
    app.processEvents()
    check("cancel reverts live edits", settings.get("appearance", "bg_color") == "#ABCDEF",
          settings.get("appearance", "bg_color"))
    dlg2.close()
    if CFG.exists():
        CFG.unlink()

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
