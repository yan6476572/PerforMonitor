"""Cross-platform CPU / memory / temperature / package-power sensors.

Backends, in order of preference per field:

* CPU usage, memory usage  -> ``psutil`` (always available)
* CPU temperature          -> Linux ``psutil.sensors_temperatures``
                             Windows: LibreHardwareMonitorLib.dll (pythonnet)
                             -> LibreHardwareMonitor (WMI) -> ACPI thermal zone
* CPU package power        -> Linux RAPL ``/sys/class/powercap``
                             Windows: LibreHardwareMonitorLib.dll (pythonnet)
                             -> LibreHardwareMonitor (WMI)
* GPU (AMD, Linux)         -> ``amdgpu`` sysfs (hwmon + ``gpu_busy_percent``)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..metrics import Metrics
from .base import Provider

try:
    from .lhm_net import LhmNativeReader
except Exception:  # pragma: no cover - pythonnet optional at import time
    LhmNativeReader = None  # type: ignore

try:
    import psutil
except Exception:  # pragma: no cover - psutil is in requirements.txt
    psutil = None  # type: ignore


# --------------------------------------------------------------------------
# Linux RAPL
# --------------------------------------------------------------------------
class RaplReader:
    """Package power from the RAPL energy counters (Intel + AMD)."""

    def __init__(self) -> None:
        self._paths: List[Path] = []
        self._prev: Dict[Path, Tuple[float, float]] = {}
        self._refresh_paths()

    def _refresh_paths(self) -> None:
        root = Path("/sys/class/powercap")
        if not root.is_dir():
            return
        found: List[Path] = []
        for d in sorted(root.iterdir()):
            # Top-level zones only: "intel-rapl:0", "amd-rapl:0" (skip ":0:0" subzones)
            if d.name.count(":") != 1:
                continue
            energy = d / "energy_uj"
            if not energy.exists():
                continue
            found.append(energy)
        self._paths = found

    def read(self) -> Optional[float]:
        if not self._paths:
            self._refresh_paths()
            if not self._paths:
                return None
        total = 0.0
        got = False
        now = time.monotonic()
        for path in self._paths:
            try:
                energy = float(path.read_text().strip())
            except (OSError, ValueError):
                continue
            prev = self._prev.get(path)
            self._prev[path] = (energy, now)
            if prev is None:
                continue
            prev_energy, prev_t = prev
            dt = now - prev_t
            de = energy - prev_energy
            if dt <= 0 or de < 0:  # counter wrapped / bad sample
                continue
            total += (de / dt) / 1e6  # uJ/s -> W
            got = True
        return total if got else None


# --------------------------------------------------------------------------
# Windows: LibreHardwareMonitor via WMI (optional, no hard dependency)
# --------------------------------------------------------------------------
class LhmReader:
    """Reads sensors published by LibreHardwareMonitor / OpenHardwareMonitor.

    Requires the user to run LibreHardwareMonitor at least once (it installs a
    driver) and to enable ``WMI`` in its settings, plus ``pip install wmi``.
    """

    NAMESPACES = ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor")

    def __init__(self) -> None:
        self._wmi = None
        self._ns: Optional[str] = None
        self._connect()

    def _connect(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import wmi  # type: ignore
        except Exception:
            return
        for ns in self.NAMESPACES:
            try:
                self._wmi = wmi.WMI(namespace=ns)
                self._ns = ns
                return
            except Exception:
                continue

    @property
    def available(self) -> bool:
        return self._wmi is not None

    def read(self) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {
            "cpu_temp": None, "cpu_power": None,
            "gpu_temp": None, "gpu_power": None, "gpu_usage": None,
        }
        if not self._wmi:
            return out
        try:
            sensors = self._wmi.Sensor()
        except Exception:
            self._connect()
            return out

        buckets: Dict[str, List[Tuple[str, float]]] = {}
        for s in sensors:
            try:
                ident = str(getattr(s, "Identifier", "") or "").lower()
                stype = str(getattr(s, "SensorType", "") or "").lower()
                value = float(getattr(s, "Value", 0) or 0)
                name = str(getattr(s, "Name", "") or "")
            except (TypeError, ValueError):
                continue
            if "/cpu" in ident or ident.startswith("/cpu"):
                family = "cpu"
            elif "/gpu" in ident or ident.startswith("/gpu"):
                family = "gpu"
            else:
                continue
            buckets.setdefault(f"{family}:{stype}", []).append((name.lower(), value))

        def pick(family: str, stype: str, prefer: Tuple[str, ...], agg: str = "max") -> Optional[float]:
            items = buckets.get(f"{family}:{stype}")
            if not items:
                return None
            for frag in prefer:
                for name, value in items:
                    if frag in name:
                        return value
            values = [v for _, v in items]
            return (max(values) if agg == "max" else min(values)) if values else None

        out["cpu_temp"] = pick("cpu", "temperature", ("package", "cpu", "tctl", "tdie"))
        out["cpu_power"] = pick("cpu", "power", ("package", "cpu", "ppt"))
        out["gpu_temp"] = pick("gpu", "temperature", ("gpu", "core", "hot spot"), agg="min")
        out["gpu_power"] = pick("gpu", "power", ("gpu", "board", "chip"))
        out["gpu_usage"] = pick("gpu", "load", ("core", "gpu", "3d"))
        return out

    def read_acpi_temp(self) -> Optional[float]:
        """Very last-resort CPU temperature on Windows (ACPI thermal zones)."""
        if not self._wmi:
            return None
        try:
            import wmi  # type: ignore
            zones = wmi.WMI(namespace="root\\wmi").MSAcpi_ThermalZoneTemperature()
        except Exception:
            return None
        vals = []
        for z in zones:
            try:
                dK = float(z.CurrentTemperature) / 10.0
                vals.append(dK - 273.15)
            except (TypeError, ValueError):
                continue
        return max(vals) if vals else None


# --------------------------------------------------------------------------
# Linux AMD GPU (amdgpu sysfs)
# --------------------------------------------------------------------------
class AmdGpuReader:
    def __init__(self) -> None:
        self._hwmon: Optional[Path] = None
        self._busy: Optional[Path] = None
        self._discover()

    def _discover(self) -> None:
        drm = Path("/sys/class/drm")
        if not drm.is_dir():
            return
        for card in sorted(drm.glob("card[0-9]*")):
            if "-" in card.name:  # skip card0-DP-1 style connectors
                continue
            dev = card / "device"
            for hw in sorted(dev.glob("hwmon/hwmon*")):
                if (hw / "temp1_input").exists():
                    self._hwmon = hw
                    break
            busy = dev / "gpu_busy_percent"
            if busy.exists():
                self._busy = busy
            if self._hwmon or self._busy:
                return

    def read(self) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {"gpu_temp": None, "gpu_power": None, "gpu_usage": None}
        if self._hwmon:
            t = self._hwmon / "temp1_input"
            try:
                out["gpu_temp"] = float(t.read_text().strip()) / 1000.0
            except (OSError, ValueError):
                pass
            for name in ("power1_average", "power1_input"):
                p = self._hwmon / name
                if p.exists():
                    try:
                        out["gpu_power"] = float(p.read_text().strip()) / 1e6  # uW -> W
                    except (OSError, ValueError):
                        pass
                    break
        if self._busy:
            try:
                out["gpu_usage"] = float(self._busy.read_text().strip())
            except (OSError, ValueError):
                pass
        return out


# --------------------------------------------------------------------------
# Main cross-platform provider
# --------------------------------------------------------------------------
class SystemProvider(Provider):
    name = "system"
    provides = ("cpu_usage", "mem_usage", "cpu_temp", "cpu_power", "gpu_temp", "gpu_power", "gpu_usage")

    def __init__(self) -> None:
        self._rapl = RaplReader() if sys.platform.startswith("linux") else None
        self._lhm_native = None
        self._lhm = None
        if sys.platform == "win32":
            if LhmNativeReader is not None:
                try:
                    self._lhm_native = LhmNativeReader()
                except Exception:
                    self._lhm_native = None
            if self._lhm_native is None or not self._lhm_native.available:
                # fall back to the WMI bridge (requires LibreHardwareMonitor running)
                self._lhm = LhmReader()
        self._amd = AmdGpuReader() if sys.platform.startswith("linux") else None
        self._acpi_warned = False
        self._prev_net = None  # last (net_io_counters, monotonic_time)

    # -- temperatures -------------------------------------------------
    def _cpu_temp_linux(self) -> Optional[float]:
        if not hasattr(psutil, "sensors_temperatures"):
            return None
        try:
            temps = psutil.sensors_temperatures() or {}
        except Exception:
            return None
        if not temps:
            return None

        priority = ("coretemp", "k10temp", "zenpower", "cpu_thermal",
                    "soc_thermal", "acpitz", "thinkpad", "it87")
        ordered: List[Tuple[str, object]] = []
        for chip in priority:
            if chip in temps:
                ordered.append((chip, temps[chip]))
        for chip, entries in temps.items():
            if chip not in priority:
                ordered.append((chip, entries))

        for _chip, entries in ordered:
            if not entries:
                continue
            named = {}
            for e in entries:
                named[(e.label or "").lower()] = e.current
            for frag in ("package id 0", "package", "tctl", "tdie", "cpu"):
                for label, value in named.items():
                    if frag in label and value and value > 0:
                        return float(value)
            cores = [v for k, v in named.items() if "core" in k and v]
            if cores:
                return float(max(cores))
            vals = [e.current for e in entries if e.current and e.current > 0]
            if vals:
                return float(max(vals))
        return None

    def _cpu_temp(self, lhm: Optional[dict] = None) -> Optional[float]:
        if sys.platform.startswith("linux"):
            return self._cpu_temp_linux()
        data = lhm
        if data is None and self._lhm_native is not None and self._lhm_native.available:
            data = self._lhm_native.read()
        if data is not None:
            v = data.get("cpu_temp")
            if v is not None:
                return v
        if self._lhm and self._lhm.available:
            data = lhm if lhm is not None else self._lhm.read()
            v = data.get("cpu_temp")
            return v if v is not None else self._lhm.read_acpi_temp()
        return None

    def _net_speed(self, out: Metrics) -> None:
        """Global upload/download throughput (bytes/s) from psutil counters."""
        if psutil is None:
            return
        try:
            nc = psutil.net_io_counters()
            now = time.monotonic()
        except Exception:
            return
        prev = self._prev_net
        self._prev_net = (nc, now)
        if prev is None:
            return
        pc, pt = prev
        dt = now - pt
        if dt <= 0.05:
            return
        up = (nc.bytes_sent - pc.bytes_sent) / dt
        down = (nc.bytes_recv - pc.bytes_recv) / dt
        # counter reset (e.g. adapter reconnect) -> skip one sample
        if up >= 0:
            out.set("net_up", up, self.name)
        if down >= 0:
            out.set("net_down", down, self.name)

    # -- poll ---------------------------------------------------------
    def poll(self, out: Metrics) -> None:
        if psutil is not None:
            try:
                out.set("cpu_usage", psutil.cpu_percent(interval=None), self.name)
            except Exception:
                pass
            try:
                out.set("mem_usage", psutil.virtual_memory().percent, self.name)
            except Exception:
                pass
        self._net_speed(out)

        lhm = None
        if self._lhm_native is not None and self._lhm_native.available:
            lhm = self._lhm_native.read()
        if lhm is None and self._lhm and self._lhm.available:
            lhm = self._lhm.read()

        out.set("cpu_temp", self._cpu_temp(lhm), self.name)

        if self._rapl is not None:
            out.set("cpu_power", self._rapl.read(), self.name)

        if lhm is not None:
            out.set("cpu_power", lhm.get("cpu_power"), self.name)
            for key in ("gpu_temp", "gpu_power", "gpu_usage"):
                out.set(key, lhm.get(key), self.name)

        if self._amd is not None:
            amd = self._amd.read()
            for key, value in amd.items():
                # only fill gaps, NVIDIA wins if it reported first
                if out.as_dict().get(key) is None:
                    out.set(key, value, self.name)
