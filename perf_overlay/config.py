"""Settings model, defaults, and JSON persistence."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from .metrics import ALL_METRIC_KEYS

CONFIG_ENV = "PERF_OVERLAY_CONFIG"


def config_dir() -> Path:
    """Platform config directory. Override with ``PERF_OVERLAY_CONFIG`` (a file path)."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        p = Path(override).expanduser()
        if p.suffix:  # points at a file
            p.parent.mkdir(parents=True, exist_ok=True)
            return p.parent
        p.mkdir(parents=True, exist_ok=True)
        return p

    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif os.sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    # follow the app name; keep using the legacy folder if it already has settings
    from . import __app_name__
    d = base / __app_name__
    legacy = base / "PerfOverlay"
    if not d.exists() and legacy.exists():
        d = legacy
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        p = Path(override).expanduser()
        return p if p.suffix else p / "settings.json"
    return config_dir() / "settings.json"


DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "window": {
        "x": 140,
        "y": 140,
        "width": 268,
        "height": 382,
        "locked": False,
        "click_through": False,
    },
    "appearance": {
        "bg_color": "#12141C",
        "accent_color": "#5B8CFF",
        "text_color": "#EAF0FA",     # 数值/主文字颜色
        "label_color": "#8A93A8",    # 标签/次级文字颜色
        "bg_opacity": 82,        # 0-100, alpha of the panel background
        "window_opacity": 100,   # 0-100, whole-window opacity
        "corner_radius": 14,
        "layout": "v",           # h = 横排显示 | v = 纵排显示
        "show_bars": True,
        "font_size": 12,
    },
    "metrics": {k: True for k in ALL_METRIC_KEYS},
    "sampling": {
        "interval_ms": 1000,
        "temp_unit": "C",        # C | F
        "gpu_index": 0,
        "fps_source": "auto",    # auto | presentmon | file | off
        "fps_file": "",
        "presentmon_path": "",
    },
}


def _deep_merge(base: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (other or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Settings:
    """Mutable settings object with dict-style access and change notification."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.data = _deep_merge(DEFAULTS, data or {})
        self._listeners = []

    # -- subscription -------------------------------------------------
    def subscribe(self, fn) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn(self)
            except Exception:  # noqa: BLE001 - never let a listener kill the app
                pass

    # -- access -------------------------------------------------------
    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node

    def set(self, *path_and_value: Any, notify: bool = True) -> None:
        *path, value = path_and_value
        node = self.data
        for p in path[:-1]:
            node = node.setdefault(p, {})
        node[path[-1]] = value
        if notify:
            self.notify()

    # -- helpers ------------------------------------------------------
    def metric_enabled(self, key: str) -> bool:
        return bool(self.get("metrics", key, default=False))

    def enabled_keys(self):
        return [k for k in ALL_METRIC_KEYS if self.metric_enabled(k)]

    # -- persistence --------------------------------------------------
    @classmethod
    def load(cls) -> "Settings":
        path = config_file()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = {}
        settings = cls(raw)
        settings._path = path
        return settings

    def save(self) -> Path:
        path = getattr(self, "_path", None) or config_file()
        tmp = path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            shutil.move(str(tmp), str(path))
        except OSError:
            # Fallback: write next to the app if the config dir is unwritable.
            fallback = Path(__file__).resolve().parent.parent / "settings.json"
            with open(fallback, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            path = fallback
        self._path = path
        return path

    def reset(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
        self.notify()
