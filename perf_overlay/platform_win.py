"""Windows-only window plumbing: keep the HUD on top of games, non-activating,
click-through and out of the taskbar / Alt-Tab."""

from __future__ import annotations

import sys
from typing import Callable, Optional

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:  # pragma: no cover - exercised on the target machine
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    GWL_EXSTYLE = -20
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    WS_EX_APPWINDOW = 0x00040000

    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    HWND_TOP = 0
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_NOOWNERZORDER = 0x0200
    SWP_NOSENDCHANGING = 0x0400

    # WinEvent constants
    EVENT_SYSTEM_FOREGROUND = 0x0003
    EVENT_SYSTEM_MOVESIZESTART = 0x000A
    EVENT_OBJECT_REORDER = 0x8004
    EVENT_OBJECT_LOCATIONCHANGE = 0x800B
    OBJID_WINDOW = 0
    WINEVENT_OUTOFCONTEXT = 0x0000

    WinEventProcType = ctypes.WINFUNCTYPE(
        None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
        wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        WinEventProcType, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    ]
    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]

    def _hwnd(widget) -> int:
        return int(widget.winId())

    def apply_overlay_styles(widget) -> None:
        """Non-activating tool window: no taskbar / Alt-Tab, no focus steal."""
        try:
            hwnd = _hwnd(widget)
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            ex |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TOPMOST
            ex &= ~WS_EX_APPWINDOW
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    def force_topmost(widget) -> None:
        """Pin *widget* above every other window without stealing focus."""
        try:
            hwnd = _hwnd(widget)
            # keep the style bit in sync (games sometimes clear it)
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if not (ex & WS_EX_TOPMOST):
                user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING,
            )
        except Exception:
            pass

    def set_click_through(widget, enabled: bool) -> None:
        try:
            hwnd = _hwnd(widget)
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if enabled:
                ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                ex &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    def foreground_covers_screen(our_hwnd: Optional[int] = None) -> bool:
        """True when a *different* window is fullscreen (typical borderless game)."""
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd or hwnd == our_hwnd:
                return False
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            sw = user32.GetSystemMetrics(0)   # SM_CXSCREEN
            sh = user32.GetSystemMetrics(1)   # SM_CYSCREEN
            # allow 1px slack for window borders
            return (rect.left <= 1 and rect.top <= 1
                    and rect.right >= sw - 1 and rect.bottom >= sh - 1)
        except Exception:
            return False

    def keep_above_fullscreen(widget) -> None:
        """Re-assert topmost after another window tried to take the z-order."""
        force_topmost(widget)

    # ------------------------------------------------------------------
    # TopmostGuard: instant re-assert when a game takes the foreground
    # ------------------------------------------------------------------
    class TopmostGuard:
        """Keeps *widget* above games.

        Games that call SetWindowPos(HWND_TOPMOST) will stack over a static
        topmost HUD.  We listen for foreground / z-order changes and push
        ourselves back on top immediately (without stealing focus), plus a
        slow timer as a safety net.  While a fullscreen window is in front
        the timer tightens up.
        """

        FAST_MS = 400      # fullscreen game in front
        SLOW_MS = 1500     # ordinary desktop

        def __init__(self, widget) -> None:
            self._widget = widget
            self._hooks = []
            self._callback = None
            self._timer = None
            self._want = True
            self._cool_until = 0.0
            self._poke_count = 0

        # -- lifecycle -------------------------------------------------
        def start(self) -> None:
            self._want = True
            self._install_hooks()
            self._start_timer(self.SLOW_MS)
            self.poke()

        def stop(self) -> None:
            self._want = False
            self._remove_hooks()
            self._stop_timer()

        def set_enabled(self, on: bool) -> None:
            self._want = bool(on)
            if on:
                self.poke()

        # -- internals -------------------------------------------------
        def _should_poke(self) -> bool:
            if not self._want:
                return False
            w = self._widget
            try:
                if w is None or not w.isVisible():
                    return False
                # respect the user's "窗口置顶" toggle
                from PySide6.QtCore import Qt
                return bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            except Exception:
                return False

        def poke(self) -> None:
            if not self._should_poke():
                return
            force_topmost(self._widget)
            self._poke_count += 1
            # retune the timer when a game is covering the screen
            try:
                hwnd = _hwnd(self._widget)
                fast = foreground_covers_screen(hwnd)
            except Exception:
                fast = False
            self._start_timer(self.FAST_MS if fast else self.SLOW_MS)

        def _on_win_event(self, _hook, event, hwnd, _id_obj, _id_child,
                          _thread, _time) -> None:
            try:
                mine = _hwnd(self._widget) if self._widget is not None else 0
            except Exception:
                mine = 0
            if hwnd and hwnd == mine:
                return
            # only care about foreground flips / z-order churn of top-level windows
            self.poke()

        def _install_hooks(self) -> None:
            if self._hooks or not self._want:
                return
            self._callback = WinEventProcType(self._on_win_event)
            for ev_min, ev_max in (
                (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
                (EVENT_OBJECT_REORDER, EVENT_OBJECT_REORDER),
                (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
            ):
                h = user32.SetWinEventHook(
                    ev_min, ev_max, 0, self._callback, 0, 0, WINEVENT_OUTOFCONTEXT)
                if h:
                    self._hooks.append(h)

        def _remove_hooks(self) -> None:
            for h in self._hooks:
                try:
                    user32.UnhookWinEvent(h)
                except Exception:
                    pass
            self._hooks = []
            self._callback = None

        def _start_timer(self, ms: int) -> None:
            try:
                from PySide6.QtCore import QTimer
            except Exception:
                return
            if self._timer is None:
                self._timer = QTimer()
                self._timer.setSingleShot(False)
                self._timer.timeout.connect(self._on_timer)
            if self._timer.interval() != ms or not self._timer.isActive():
                self._timer.setInterval(ms)
                self._timer.start()

        def _stop_timer(self) -> None:
            if self._timer is not None:
                self._timer.stop()

        def _on_timer(self) -> None:
            self.poke()

else:  # pragma: no cover - trivial stubs
    def apply_overlay_styles(widget) -> None:
        return

    def force_topmost(widget) -> None:
        try:
            widget.raise_()
        except Exception:
            pass

    def set_click_through(widget, enabled: bool) -> None:
        return

    def foreground_covers_screen(our_hwnd=None) -> bool:
        return False

    def keep_above_fullscreen(widget) -> None:
        return

    class TopmostGuard:
        def __init__(self, widget) -> None:
            self._widget = widget

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def set_enabled(self, on: bool) -> None:
            pass

        def poke(self) -> None:
            try:
                self._widget.raise_()
            except Exception:
                pass
