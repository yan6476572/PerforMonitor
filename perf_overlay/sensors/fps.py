"""Real-time frame-rate capture.

Sources, tried in this order when ``fps_source == "auto"``:

``presentmon``
    Windows. Streams Intel's PresentMon CSV on stdout and turns per-frame
    "present" events into an FPS figure for the foreground / busiest process.
    Works with DirectX 11/12, Vulkan, OpenGL and UWP titles.

``file``
    Universal escape hatch. Reads a number from a text file that any other
    tool keeps updating — e.g. MangoHud's ``output_file`` on Linux, or a
    small user script.

``none``
    Nothing available; the HUD shows ``--`` and a hint in the tooltip.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Optional, Tuple

from ..metrics import Metrics
from .base import Provider

_FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+")

_WINDOW_SEC = 2.0
_MIN_EVENTS = 4

#: never used as the "busiest process" FPS fallback
_IGNORED_APPS = frozenset({
    "dwm.exe", "csrss.exe", "idle.exe", "explorer.exe", "system",
    "applicationframehost.exe", "shellexperiencehost.exe", "searchhost.exe",
    "widgets.exe", "startmenuexperiencehost.exe", "textinputhost.exe",
    "conhost.exe", "svchost.exe", "perfoverlay.exe", "presentmon64.exe",
    "presentmon.exe", "<unknown>", "lockapp.exe", "runtimebroker.exe",
})


# --------------------------------------------------------------------------
def foreground_pid() -> Optional[int]:
    """PID of the process owning the foreground window (Windows only)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        u32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


# --------------------------------------------------------------------------
class _RollingCounter:
    """Per-(pid, swapchain) present history -> FPS.

    Each PID can own several DXGI swap chains (game world + UI, DWM, ...).
    Merging them inflates the count well past the real frame rate, so every
    present is bucketed by swap chain and we report the dominant chain.

    FPS comes from ``MsBetweenPresents`` (what every other overlay shows),
    not from raw event counts.
    """

    def __init__(self, window: float = _WINDOW_SEC) -> None:
        self.window = window
        # (pid, swap) -> deque[(mono_t, ms_between_or_None)]
        self._events: Dict[Tuple[int, str], Deque[Tuple[float, Optional[float]]]] = defaultdict(deque)
        self._names: Dict[int, str] = {}
        self._lock = threading.Lock()

    def add(self, pid: int, name: str = "", swap: str = "",
            ms_between: Optional[float] = None) -> None:
        now = time.monotonic()
        key = (int(pid), swap or "")
        with self._lock:
            dq = self._events[key]
            dq.append((now, ms_between if (ms_between and ms_between > 0) else None))
            if name:
                self._names[int(pid)] = name
            self._prune_locked(now)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window
        for key in list(self._events):
            dq = self._events[key]
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            if not dq:
                del self._events[key]

    def _fps_locked(self, dq: Deque[Tuple[float, Optional[float]]]) -> Optional[float]:
        if len(dq) < _MIN_EVENTS:
            return None
        # preferred: reciprocal of the mean present interval
        intervals = [ms for _t, ms in dq if ms is not None]
        if len(intervals) >= max(2, _MIN_EVENTS // 2):
            mean_ms = sum(intervals) / len(intervals)
            if mean_ms > 0.05:
                return 1000.0 / mean_ms
        # fallback: event rate over the window
        span = dq[-1][0] - dq[0][0]
        if span <= 0:
            return None
        return (len(dq) - 1) / span

    def _pid_fps_locked(self, pid: int) -> Optional[float]:
        """Dominant swap chain of *pid* -> FPS (never the sum of chains)."""
        chains = [(key[1], dq) for key, dq in self._events.items() if key[0] == pid]
        if not chains:
            return None
        # busiest chain first; a single real swap chain always wins over extras
        chains.sort(key=lambda item: len(item[1]), reverse=True)
        for _swap, dq in chains:
            v = self._fps_locked(dq)
            if v is not None:
                return v
        return None

    def fps(self, prefer_pid: Optional[int] = None) -> Tuple[Optional[float], Optional[str]]:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            if not self._events:
                return None, None

            if prefer_pid is not None and self._pid_fps_locked(prefer_pid) is not None:
                return self._pid_fps_locked(prefer_pid), self._names.get(prefer_pid)

            # busiest non-system process
            counts: Dict[int, int] = defaultdict(int)
            for (pid, _swap), dq in self._events.items():
                counts[pid] += len(dq)
            ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            for pid, _n in ranked:
                name = (self._names.get(pid) or "").strip().lower()
                if name in _IGNORED_APPS:
                    continue
                v = self._pid_fps_locked(pid)
                if v is not None:
                    return v, self._names.get(pid)
            # nothing but system processes: fall back to the busiest chain overall
            best = max(self._events.items(), key=lambda kv: len(kv[1]))
            return self._fps_locked(best[1]), self._names.get(best[0][0])


# --------------------------------------------------------------------------
class PresentMonSource:
    name = "presentmon"

    # PresentMon 2.x first (no --no_top; --stop_existing_session avoids a
    # stuck session after a crash), then 1.x fallbacks.
    CANDIDATE_ARGS = (
        ["--output_stdout", "--no_console_stats", "--stop_existing_session"],
        ["--output_stdout", "--stop_existing_session"],
        ["--output_stdout"],
        ["--output_stdout", "--no_top"],
        ["-output_stdout", "-no_top"],
    )

    def __init__(self, exe: str = "") -> None:
        self.exe = exe or self._locate()
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._counter = _RollingCounter()
        self._stop = threading.Event()
        self._columns: Dict[str, int] = {}
        self._args: Optional[list] = None
        self._csv_path = ""
        self._read_offset = 0

    # -- discovery ----------------------------------------------------
    @staticmethod
    def _locate() -> str:
        env = os.environ.get("PRESENTMON_PATH")
        if env and Path(env).exists():
            return env
        names = ("PresentMon.exe", "PresentMon64.exe", "PresentMon-x64.exe",
                 "PresentMon-2.6.0-x64.exe")
        here = Path(__file__).resolve().parent.parent.parent
        folders = [here, here / "bin", here / "PresentMon", Path.cwd()]
        # frozen onefile: extracted bundle next to the collected modules
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            folders.extend([Path(meipass), Path(meipass) / "bin",
                            Path(meipass) / "PresentMon"])
        # onefile/onedir: alongside the exe itself
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            folders.extend([exe_dir, exe_dir / "bin", exe_dir / "PresentMon"])
        seen = set()
        for folder in folders:
            key = str(folder)
            if key in seen:
                continue
            seen.add(key)
            for n in names:
                fp = folder / n
                try:
                    if fp.exists():
                        return str(fp)
                except OSError:
                    continue
        for n in names:
            found = shutil.which(n)
            if found:
                return found
        if sys.platform == "win32":
            for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                         os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")):
                for sub in ("PresentMon", r"Intel\PresentMon"):
                    for n in names:
                        fp = Path(base) / sub / n
                        if fp.exists():
                            return str(fp)
        return ""

    @property
    def available(self) -> bool:
        return bool(self.exe) and Path(self.exe).exists()

    # -- lifecycle ----------------------------------------------------
    def start(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        if not self.available:
            return False

        # Stream CSV on stdout.  PresentMon holds its --output_file with an
        # exclusive handle (unreadable while running), so stdout is the only
        # live feed.  Candidate flags cover PresentMon 2.x / 1.x.
        candidates = (
            ["--output_stdout", "--no_console_stats", "--stop_existing_session"],
            ["--output_stdout", "--stop_existing_session"],
            ["--output_stdout"],
            ["--output_stdout", "--no_top"],
            ["-output_stdout", "-no_top"],
        )
        if self._args:
            candidates = [self._args]
        for args in candidates:
            try:
                self._proc = subprocess.Popen(
                    [self.exe, *args],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1, universal_newlines=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                self._proc = None
                continue
            # PresentMon rejects unknown flags by exiting immediately
            time.sleep(0.2)
            if self._proc.poll() is not None:
                self._proc = None
                continue
            self._args = list(args)
            break
        if self._proc is None:
            return False

        self._stop.clear()
        self._columns = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="presentmon-reader")
        self._reader.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None

    # -- parsing ------------------------------------------------------
    def _read_loop(self) -> None:
        """Read PresentMon CSV rows from stdout until asked to stop."""
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                self._handle(line.strip("\r\n"))
        except Exception:
            pass

    def _col(self, cols, name: str) -> Optional[str]:
        idx = self._columns.get(name)
        if idx is None or idx >= len(cols):
            return None
        return cols[idx]

    def _handle(self, line: str) -> None:
        if not line:
            return
        if "Application" in line and "ProcessID" in line:
            self._columns = {
                c.strip().lstrip("\ufeff"): i for i, c in enumerate(line.split(","))
            }
            return
        if not self._columns:
            return
        cols = line.split(",")
        try:
            pid = int(float(self._col(cols, "ProcessID") or ""))
        except (TypeError, ValueError):
            return
        name = (self._col(cols, "Application") or "").strip()
        swap = (self._col(cols, "SwapChainAddress") or "").strip()
        # Displayed-frame interval first: this is what every other overlay
        # (NVIDIA / RTSS) reports.  MsBetweenPresents can run higher when the
        # game renders faster than the screen refresh.
        ms_between = None
        for col_name in ("MsBetweenDisplayChange", "MsBetweenPresents"):
            raw = self._col(cols, col_name)
            if not raw or raw.strip().upper() == "NA":
                continue
            try:
                val = float(raw.strip())
            except ValueError:
                continue
            if val > 0:
                ms_between = val
                break
        self._counter.add(pid, name, swap, ms_between)

    def fps(self) -> Tuple[Optional[float], Optional[str]]:
        return self._counter.fps(foreground_pid())


# --------------------------------------------------------------------------
class FileSource:
    name = "file"

    def __init__(self, path: str) -> None:
        self.path = path
        self._cache: Tuple[float, float, Optional[float]] = (0.0, 0.0, None)  # mtime, read_t, value

    @property
    def available(self) -> bool:
        return bool(self.path)

    def start(self) -> bool:
        return self.available

    def stop(self) -> None:
        return

    def fps(self) -> Tuple[Optional[float], Optional[str]]:
        if not self.path:
            return None, None
        try:
            st = os.stat(self.path)
        except OSError:
            return None, None
        mtime, _read_t, value = self._cache
        if st.st_mtime == mtime and value is not None:
            return value, Path(self.path).name
        try:
            text = Path(self.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, None
        # Accept "fps: 144", "144.2", JSON, or a bare CSV cell.
        m = re.search(r"fps\"?\s*[:=,]?\s*([-+]?\d*\.?\d+)", text, re.IGNORECASE)
        if m:
            value = float(m.group(1))
        else:
            bare = _FLOAT_RE.search(text)
            value = float(bare.group(0)) if bare else None
        self._cache = (st.st_mtime, time.monotonic(), value)
        return value, Path(self.path).name


# --------------------------------------------------------------------------
class FpsProvider(Provider):
    name = "fps"
    provides = ("fps",)

    MODES = ("auto", "presentmon", "file", "off")

    def __init__(self, mode: str = "auto", file_path: str = "",
                 presentmon_path: str = "", update_interval: float = 0.5) -> None:
        self.mode = mode if mode in self.MODES else "auto"
        self.file_path = file_path
        self.presentmon_path = presentmon_path
        self.update_interval = max(0.2, update_interval)
        self._source = None
        self._source_name = ""
        self._last_try = 0.0
        self._select_source()

    # -- source management --------------------------------------------
    def _select_source(self) -> None:
        old = self._source
        if old:
            old.stop()
        self._source = None
        self._source_name = ""

        if self.mode == "off":
            return

        if self.mode in ("auto", "presentmon"):
            pm = PresentMonSource(self.presentmon_path)
            if pm.available and pm.start():
                self._source, self._source_name = pm, "presentmon"
                return
            if self.mode == "presentmon":
                self._source, self._source_name = None, "presentmon(missing)"
                return

        if self.mode in ("auto", "file") and self.file_path:
            fs = FileSource(self.file_path)
            if fs.start():
                self._source, self._source_name = fs, "file"
                return

        self._source = None
        self._source_name = "none"

    def configure(self, mode: str = "auto", file_path: str = "",
                  presentmon_path: str = "", interval: float = 0.5) -> None:
        changed = (mode != self.mode or file_path != self.file_path
                   or presentmon_path != self.presentmon_path)
        self.mode = mode if mode in self.MODES else "auto"
        self.file_path = file_path
        self.presentmon_path = presentmon_path
        self.update_interval = max(0.2, interval)
        if changed or self._source is None:
            self._select_source()

    @property
    def active_source(self) -> str:
        return self._source_name

    def stop(self) -> None:
        if self._source:
            self._source.stop()

    # -- poll ---------------------------------------------------------
    def poll(self, out: Metrics) -> None:
        now = time.monotonic()
        if self._source is None:
            # Re-probe occasionally: the user may install PresentMon later.
            if self.mode == "auto" and now - self._last_try > 15.0:
                self._last_try = now
                self._select_source()
            return
        try:
            value, who = self._source.fps()
        except Exception:
            value, who = None, None
        out.set("fps", value, self._source_name)
        if who:
            out.sources["_fps_app"] = who  # type: ignore[index]
