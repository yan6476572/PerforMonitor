"""Background polling thread that turns providers into a stream of `Metrics`."""

from __future__ import annotations

import time
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Settings
from ..metrics import Metrics
from .base import Provider
from .fps import FpsProvider
from .nvidia import NvidiaProvider
from .system import SystemProvider


class PollWorker(QThread):
    """Polls every provider on a fixed cadence and emits merged snapshots."""

    snapshot = Signal(object)
    status = Signal(str)

    def __init__(self, settings: Settings, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._running = True
        self._providers: List[Provider] = []
        self._fps: Optional[FpsProvider] = None
        self._last_emit = 0.0

    # -- lifecycle ----------------------------------------------------
    def build_providers(self) -> None:
        s = self.settings
        self._providers = []
        self._providers.append(SystemProvider())
        self._providers.append(NvidiaProvider(index=int(s.get("sampling", "gpu_index", default=0) or 0)))
        self._fps = FpsProvider(
            mode=str(s.get("sampling", "fps_source", default="auto")),
            file_path=str(s.get("sampling", "fps_file", default="") or ""),
            presentmon_path=str(s.get("sampling", "presentmon_path", default="") or ""),
            update_interval=max(0.2, int(s.get("sampling", "interval_ms", default=1000)) / 1000.0),
        )
        self._providers.append(self._fps)
        for p in self._providers:
            try:
                p.start()
            except Exception:
                pass

    def reconfigure(self) -> None:
        """Called from the GUI thread when the user changes sensor settings."""
        if self._fps is None:
            return
        s = self.settings
        try:
            self._fps.configure(
                mode=str(s.get("sampling", "fps_source", default="auto")),
                file_path=str(s.get("sampling", "fps_file", default="") or ""),
                presentmon_path=str(s.get("sampling", "presentmon_path", default="") or ""),
                interval=max(0.2, int(s.get("sampling", "interval_ms", default=1000)) / 1000.0),
            )
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False

    def shutdown(self) -> None:
        self._running = False
        for p in self._providers:
            try:
                p.stop()
            except Exception:
                pass
        self.wait(3000)

    # -- main loop ----------------------------------------------------
    def run(self) -> None:  # noqa: D102
        self.build_providers()
        self.status.emit("ready")
        while self._running:
            started = time.monotonic()
            snap = Metrics()
            for p in self._providers:
                try:
                    p.poll(snap)
                except Exception:
                    continue
            self.snapshot.emit(snap)

            interval = max(0.1, int(self.settings.get("sampling", "interval_ms", default=1000)) / 1000.0)
            elapsed = time.monotonic() - started
            self.msleep(int(max(20, (interval - elapsed) * 1000)))
        for p in self._providers:
            try:
                p.stop()
            except Exception:
                pass


class SensorManager(QObject):
    """Thin façade: start/stop the poller and expose the latest snapshot."""

    snapshot = Signal(object)

    def __init__(self, settings: Settings, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.latest: Metrics = Metrics()
        self._worker: Optional[PollWorker] = None

    def start(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = PollWorker(self.settings)
        self._worker.snapshot.connect(self._on_snapshot)
        self._worker.start()

    def _on_snapshot(self, snap: Metrics) -> None:
        self.latest = snap
        self.snapshot.emit(snap)

    def reconfigure(self) -> None:
        if self._worker:
            self._worker.reconfigure()

    def stop(self) -> None:
        if self._worker:
            self._worker.shutdown()
            self._worker = None

    @property
    def active_fps_source(self) -> str:
        return self._worker._fps.active_source if (self._worker and self._worker._fps) else ""
