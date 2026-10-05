"""Application bootstrap: overlay + sensors + tray + settings."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __app_name__, __version__, platform_win
from .boost import trim_working_sets
from .config import Settings, config_file
from .sensors import SensorManager
from .ui.overlay import OverlayWidget
from .ui.settings import SettingsDialog
from .ui import theme

# ------------------------------------------------------------------ DPI
# The HUD's pixel sizes (945x96 strip / 231x332 list / 48px gauge) are
# physical pixels.  Neutralise Windows display scaling so one logical
# pixel is one physical pixel; must run before the QApplication exists.
if sys.platform == "win32":
    try:
        import winreg
        _dpi = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Control Panel\Desktop\WindowMetrics"),
            "AppliedDPI")[0]
    except Exception:
        _dpi = 96
    try:
        _dpi = int(_dpi)
    except (TypeError, ValueError):
        _dpi = 96
    if _dpi and _dpi > 96:
        os.environ["QT_SCALE_FACTOR"] = f"{96.0 / _dpi:.6f}"


# ------------------------------------------------------------------ icon
def make_icon(accent: str = theme.DEFAULT_ACCENT, size: int = 64) -> QIcon:
    """A small self-drawn app icon: rounded tile with a live-looking pulse."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    a = QColor(accent)
    bg = QColor("#12141C")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(2, 2, size - 4, size - 4, size * 0.24, size * 0.24)

    pen = p.pen()
    pen.setColor(a)
    pen.setWidthF(size * 0.085)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    from PySide6.QtGui import QPolygon
    from PySide6.QtCore import QPoint
    s = size
    pts = [
        QPoint(int(s * 0.14), int(s * 0.56)),
        QPoint(int(s * 0.31), int(s * 0.56)),
        QPoint(int(s * 0.39), int(s * 0.31)),
        QPoint(int(s * 0.50), int(s * 0.72)),
        QPoint(int(s * 0.60), int(s * 0.46)),
        QPoint(int(s * 0.68), int(s * 0.56)),
        QPoint(int(s * 0.86), int(s * 0.56)),
    ]
    p.drawPolyline(QPolygon(pts))
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------------ app
class PerfOverlayApp:
    def __init__(self, argv: list[str]) -> None:
        self.qapp = QApplication(argv)
        self.qapp.setApplicationName(__app_name__)
        self.qapp.setApplicationDisplayName("PerformanceMonitor")
        self.qapp.setOrganizationName(__app_name__)
        self.qapp.setQuitOnLastWindowClosed(False)

        self.settings = Settings.load()
        self.sensors = SensorManager(self.settings)
        self.overlay = OverlayWidget(self.settings)
        self.dialog: Optional[SettingsDialog] = None
        self.tray: Optional[QSystemTrayIcon] = None

        self._restore_geometry()
        self._build_tray()

        self.sensors.snapshot.connect(self.overlay.set_metrics)
        self.overlay.settingsRequested.connect(self.open_settings)
        self.overlay.memoryBoostRequested.connect(self._on_memory_boost)
        self.settings.subscribe(self._on_settings_changed)
        self._boost_thread = None

        # keep the HUD above fullscreen games: instant WinEvent hook + timer
        self._top_guard = platform_win.TopmostGuard(self.overlay)
        self._top_guard.start()

    # -- lifecycle ----------------------------------------------------
    def run(self, show: bool = True) -> int:
        self.sensors.start()
        if show:
            self.overlay.show()
            platform_win.apply_overlay_styles(self.overlay)
            platform_win.force_topmost(self.overlay)
            if getattr(self, "_top_guard", None):
                self._top_guard.poke()
            if bool(self.settings.get("window", "click_through", default=False)):
                self.overlay._toggle_click_through(True)
        # make ctrl+c quit cleanly
        try:
            signal.signal(signal.SIGINT, lambda *_: self.qapp.quit())
        except ValueError:
            pass
        return self.qapp.exec()

    def shutdown(self) -> None:
        if getattr(self, "_top_guard", None):
            self._top_guard.stop()
        self.sensors.stop()
        self.settings.save()

    # -- geometry -----------------------------------------------------
    def _restore_geometry(self) -> None:
        s = self.settings
        x = int(s.get("window", "x", default=140))
        y = int(s.get("window", "y", default=140))
        w = int(s.get("window", "width", default=264))
        h = int(s.get("window", "height", default=336))
        # 圆形模式用圆形的最小尺寸；矩形模式用矩形最小尺寸
        if self.overlay._is_circular():
            # 旧默认的 60px 圆盘视为"未自定义"，升级后直接用新的更小尺寸
            if (w <= OverlayWidget.CIRC_LEGACY and h <= OverlayWidget.CIRC_LEGACY):
                w = h = OverlayWidget.CIRC_NICE
            w = max(self.overlay.CIRC_MIN, w)
            h = max(self.overlay.CIRC_MIN, h)
        else:
            w = max(OverlayWidget.MIN_WIDTH, w)
            h = max(OverlayWidget.MIN_HEIGHT, h)
        # 配置里可能残留越界/坏值（例如屏幕外），钳制到可见区，
        # 否则浮窗会"启动了但看不见"。
        x, y, w, h = self._clamp_on_screen(x, y, w, h)
        self.overlay.setGeometry(x, y, w, h)

    def _clamp_on_screen(self, x: int, y: int, w: int, h: int):
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail is None or avail.width() < 50:
                return x, y, w, h
            w = min(w, avail.width())
            h = min(h, avail.height())
            # 至少保留 40px 可见面积，允许用户把它拖到边缘
            vis = min(40, w), min(40, h)
            x = min(max(x, avail.left() - w + vis[0]), avail.right() - vis[0])
            y = min(max(y, avail.top() - h + vis[1]), avail.bottom() - vis[1])
        except Exception:
            pass
        return x, y, w, h

    def _refresh_topmost(self) -> None:
        if self.overlay.isVisible() and (self.overlay.windowFlags()
                                         & Qt.WindowType.WindowStaysOnTopHint):
            platform_win.keep_above_fullscreen(self.overlay)
            if getattr(self, "_top_guard", None):
                self._top_guard.poke()

    def _on_settings_changed(self, _s: Settings) -> None:
        self.sensors.reconfigure()
        if self.tray is not None:
            self.tray.setIcon(make_icon(self.settings.get("appearance", "accent_color",
                                                          default=theme.DEFAULT_ACCENT)))

    # -- tray ---------------------------------------------------------
    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        accent = self.settings.get("appearance", "accent_color", default=theme.DEFAULT_ACCENT)
        self.tray = QSystemTrayIcon(make_icon(accent))
        self.tray.setToolTip(f"{__app_name__} {__version__}")
        menu = QMenu()
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setStyleSheet(theme.menu_qss(accent))
        menu.setFont(theme.ui_font(16))

        act_show = QAction("显示 / 隐藏浮窗", menu)
        act_show.triggered.connect(self.toggle_overlay)
        menu.addAction(act_show)

        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)

        menu.addSeparator()

        act_lock = QAction("锁定位置", menu)
        act_lock.setCheckable(True)
        act_lock.setChecked(bool(self.settings.get("window", "locked", default=False)))
        act_lock.toggled.connect(lambda on: self.settings.set("window", "locked", bool(on)))
        menu.addAction(act_lock)

        act_click = QAction("鼠标穿透", menu)
        act_click.setCheckable(True)
        act_click.setChecked(bool(self.settings.get("window", "click_through", default=False)))
        act_click.toggled.connect(self.overlay._toggle_click_through)
        menu.addAction(act_click)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.qapp.quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_overlay()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_settings()

    # -- actions ------------------------------------------------------
    def _on_memory_boost(self) -> None:
        """Trim working sets off the GUI thread, then play the result."""
        import threading

        if getattr(self, "_boost_thread", None) and self._boost_thread.is_alive():
            return
        self.overlay.start_memory_boost()

        def worker() -> None:
            try:
                result = trim_working_sets() or {}
                freed = float(result.get("freed_mb") or 0.0)
            except Exception:
                freed = 0.0
            try:
                QTimer.singleShot(0, lambda: self.overlay.set_boost_result(freed))
            except Exception:
                pass

        t = threading.Thread(target=worker, daemon=True, name="mem-boost")
        self._boost_thread = t
        t.start()

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay._relayout()
            platform_win.force_topmost(self.overlay)
            if getattr(self, "_top_guard", None):
                self._top_guard.poke()

    def open_settings(self) -> None:
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.dialog = SettingsDialog(self.settings, None)
        self.dialog.finished.connect(lambda _r: setattr(self, "dialog", None))
        self.dialog.show()
        self.dialog.raise_()


# ------------------------------------------------------------------ entry
def _quiet_stdio() -> None:
    """A windowed (no-console) bootloader leaves stdio fds unusable.

    Point stdout/stderr at the null device so argparse/prints don't blow up
    and turn a clean exit into exit code 1.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        broken = stream is None
        if not broken:
            try:
                stream.write("")
                stream.flush()
            except Exception:
                broken = True
        if broken:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


def _is_elevated() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return True  # non-Windows or unknown: don't block startup


def _relaunch_elevated() -> int:
    """Re-run this exe as administrator (UAC), then exit the current instance.

    CPU temperature / package power need MSR access through the
    LibreHardwareMonitor driver, which only works from an elevated process.
    """
    try:
        import ctypes
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f"\"{a}\"" if " " in a else a for a in sys.argv[1:])
        else:
            exe = sys.executable
            script = str(Path(__file__).resolve().parent.parent / "main.py")
            params = " ".join([f"\"{script}\""] + [f"\"{a}\"" if " " in a else a for a in sys.argv[1:]])
        # mark so a cancelled UAC does not loop
        if "--elevated" not in sys.argv:
            params = (params + " --elevated").strip()
        rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", exe, params, None, 1
        )
        return 1 if rc <= 32 else 0
    except Exception:
        return 1


def _probe_sensors(out_path: str) -> int:
    """Dump one live sensor snapshot as JSON (diagnostic for packaged builds)."""
    import json
    import time as _time

    from .metrics import Metrics
    from .sensors.system import SystemProvider

    from .sensors.fps import FpsProvider

    provider = SystemProvider()
    fps = FpsProvider(
        mode="auto",
        presentmon_path=str(__import__("os").environ.get("PRESENTMON_PATH", "")),
        update_interval=0.5,
    )
    snap = Metrics()
    _time.sleep(0.4)
    provider.poll(snap)
    # PresentMon needs a couple of seconds to fill the FPS window
    for _ in range(6):
        _time.sleep(0.5)
        provider.poll(snap)
        fps.poll(snap)
    payload = {
        "elevated": _is_elevated(),
        "native_available": bool(getattr(getattr(provider, "_lhm_native", None), "available", False)),
        "native_error": getattr(getattr(provider, "_lhm_native", None), "error", ""),
        "metrics": snap.as_dict(),
        "sources": snap.sources,
    }
    provider.stop()
    fps.stop()
    Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


def _wants_cpu_sensors() -> bool:
    s = Settings.load()
    return bool(s.metric_enabled("cpu_temp") or s.metric_enabled("cpu_power"))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perf-overlay",
        description="PerformanceMonitor — a lightweight always-on-top desktop performance HUD.",
    )
    p.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    p.add_argument("--settings", action="store_true", help="open the settings dialog on start")
    p.add_argument("--config", metavar="PATH", help="use an alternate settings file")
    p.add_argument("--hidden", action="store_true", help="start hidden (tray only)")
    p.add_argument("--screenshot", metavar="DIR",
                   help="render the HUD and the settings card to PNGs in DIR, then exit")
    p.add_argument("--reset", action="store_true", help="reset settings to defaults and exit")
    p.add_argument("--elevated", action="store_true",
                   help=argparse.SUPPRESS)  # internal: relaunched via UAC
    p.add_argument("--no-elevate", action="store_true",
                   help="do not request administrator rights on start")
    p.add_argument("--probe-sensors", metavar="PATH",
                   help=argparse.SUPPRESS)  # diagnostic: dump live sensor JSON
    p.add_argument("--install-autostart", action="store_true",
                   help="register the app to start at logon (needs admin)")
    p.add_argument("--uninstall-autostart", action="store_true",
                   help="remove the logon auto-start task")
    p.add_argument("--autostart-status", action="store_true",
                   help="print whether logon auto-start is registered")
    return p


def render_screenshots(outdir: str) -> int:
    """Grab the HUD + settings card to PNGs without needing real sensors.

    Handy for documentation and for sanity-checking a packaged build.
    """
    from PySide6.QtCore import QTimer

    from .metrics import Metrics
    from .ui.settings import SettingsDialog

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    app = PerfOverlayApp([sys.argv[0]])
    app.overlay.set_metrics(Metrics(
        cpu_temp=61.0, cpu_power=78.4, cpu_usage=42.0,
        gpu_temp=57.0, gpu_power=186.3, gpu_usage=87.0,
        mem_usage=51.0, fps=143.0,
    ))
    app.overlay.setGeometry(0, 0, 268, 382)
    app.overlay.show()

    dialog: Optional[SettingsDialog] = None

    def grab_hud() -> None:
        app.overlay.grab().save(str(out / "hud.png"))
        nonlocal dialog
        dialog = SettingsDialog(app.settings, None)
        dialog.resize(580, 760)
        dialog.show()
        QTimer.singleShot(1800, grab_settings)

    def grab_settings() -> None:
        if dialog is not None:
            dialog.grab().save(str(out / "settings.png"))
            dialog.close()
        app.qapp.quit()

    QTimer.singleShot(1800, grab_hud)
    app.qapp.exec()
    for name in ("hud.png", "settings.png"):
        print(f"wrote {out / name}")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    # Qt teardown under a windowed bootloader can raise and turn a clean run
    # into exit code 1; the job is already done, so exit deterministically.
    os._exit(0)
    return 0  # pragma: no cover


def run(argv: Optional[list[str]] = None) -> int:
    _quiet_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_arg_parser().parse_args(argv)

    if args.config:
        os.environ["PERF_OVERLAY_CONFIG"] = args.config

    # ---- autostart management (no GUI needed) ----
    if args.install_autostart or args.uninstall_autostart or args.autostart_status:
        from . import autostart
        if args.autostart_status:
            st = autostart.status()
            print(f"autostart: {'on' if st['enabled'] else 'off'} ({st['task']})")
            return 0
        r = autostart.install() if args.install_autostart else autostart.uninstall()
        verb = "installed" if args.install_autostart else "removed"
        if r.get("ok"):
            print(f"autostart {verb}: {r.get('task')}")
            return 0
        print(f"autostart failed: {r.get('error')}")
        return 1

    if args.probe_sensors:
        return _probe_sensors(args.probe_sensors)

    # CPU temp / power need an elevated process for the LHM driver.
    # Relaunch ourselves with UAC before any window appears (unless opted out
    # or we were already relaunched and the user declined).
    if (sys.platform == "win32" and not args.elevated and not args.no_elevate
            and not args.reset and not args.screenshot and _wants_cpu_sensors()
            and not _is_elevated()):
        rc = _relaunch_elevated()
        if rc == 0:
            return 0  # elevated copy is running; quit this one quietly

    if args.reset:
        s = Settings.load()
        s.reset()
        path = s.save()
        print(f"settings reset -> {path}")
        return 0

    if args.screenshot:
        return render_screenshots(args.screenshot)

    app = PerfOverlayApp([sys.argv[0]])
    if args.settings:
        QTimer.singleShot(0, app.open_settings)
    rc = app.run(show=not args.hidden)
    app.shutdown()
    return rc
