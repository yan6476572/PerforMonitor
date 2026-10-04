"""CPU / GPU sensors via LibreHardwareMonitorLib.dll (pythonnet + CoreCLR).

Preferred over the WMI bridge on Windows: the DLL is loaded in-process, so
LibreHardwareMonitor does not have to be running and WMI does not have to be
enabled.

Reading CPU temperature / package power needs administrator rights (MSR access
through the LHM kernel driver).  Without elevation those fields are reported
unavailable instead of the 0.0 the driver returns when it cannot read.

The assembly is looked up next to the executable / in ``LibreHardwareMonitor.NET.10``
(override with ``LHM_DLL_DIR``).  Requires the .NET 10 runtime.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_BUNDLE_NAME = "LibreHardwareMonitor.NET.10"
_ASSEMBLY = "LibreHardwareMonitorLib.dll"
_LOCK = threading.Lock()
_STATE = {"status": "new", "error": ""}  # new | ready | failed


# --------------------------------------------------------------------------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _candidate_dirs() -> List[Path]:
    out: List[Path] = []
    env = os.environ.get("LHM_DLL_DIR")
    if env:
        out.append(Path(env))
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(Path(meipass) / _BUNDLE_NAME)
        out.append(Path(sys.executable).resolve().parent / _BUNDLE_NAME)
    here = Path(__file__).resolve()
    out.append(here.parent.parent.parent / _BUNDLE_NAME)
    out.append(Path.cwd() / _BUNDLE_NAME)
    seen = set()
    uniq: List[Path] = []
    for d in out:
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def find_dll_dir() -> Optional[Path]:
    for d in _candidate_dirs():
        if (d / _ASSEMBLY).exists():
            return d
    return None


def _runtime_config_candidates(dll_dir: Path) -> List[Path]:
    here = Path(__file__).resolve().parent
    out = [
        here / "_lhm_runtimeconfig.json",
        dll_dir / "LibreHardwareMonitor.runtimeconfig.json",
    ]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.insert(0, Path(meipass) / "perf_overlay" / "sensors" / "_lhm_runtimeconfig.json")
    return out


def _ensure_clr(dll_dir: Path) -> None:
    """Boot CoreCLR and load LibreHardwareMonitorLib.  Idempotent."""
    if _STATE["status"] == "ready":
        return
    if _STATE["status"] == "failed":
        raise RuntimeError(_STATE["error"] or "LibreHardwareMonitor CLR already failed")

    import pythonnet  # type: ignore
    from clr_loader import get_coreclr  # type: ignore

    runtime_config = None
    for cand in _runtime_config_candidates(dll_dir):
        if cand.exists():
            runtime_config = str(cand)
            break
    if runtime_config is None:
        raise FileNotFoundError("no runtimeconfig.json found for LibreHardwareMonitorLib")

    rt = get_coreclr(runtime_config=runtime_config)
    pythonnet.set_runtime(rt)

    import clr  # type: ignore  # noqa: F401  (must come after set_runtime)

    # Resolve sibling managed dependencies (HidSharp, DiskInfoToolkit, ...) from disk.
    try:
        os.add_dll_directory(str(dll_dir))
    except (AttributeError, OSError):
        pass
    if str(dll_dir) not in sys.path:
        sys.path.append(str(dll_dir))

    clr.AddReference(str(dll_dir / _ASSEMBLY))


def _sensor_type_name(sensor) -> str:
    try:
        return str(sensor.SensorType)
    except Exception:
        return ""


def _sensor_name(sensor) -> str:
    try:
        return str(sensor.Name or "")
    except Exception:
        return ""


def _sensor_value(sensor) -> Optional[float]:
    try:
        v = sensor.Value
    except Exception:
        return None
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _walk_hardware(computer) -> List[object]:
    out: List[object] = []
    try:
        for hw in computer.Hardware:
            out.append(hw)
            try:
                for sub in hw.SubHardware:
                    out.append(sub)
            except Exception:
                pass
    except Exception:
        pass
    return out


class LhmNativeReader:
    """Reads sensors from LibreHardwareMonitorLib loaded via pythonnet."""

    def __init__(self, enable_gpu: bool = True) -> None:
        self._dll_dir: Optional[Path] = None
        self._computer = None
        self._enable_gpu = enable_gpu
        self._init_error = ""
        self._elevated = is_admin()
        self._connect()

    # -- lifecycle ----------------------------------------------------
    def _connect(self) -> None:
        with _LOCK:
            try:
                dll_dir = find_dll_dir()
                if dll_dir is None:
                    raise FileNotFoundError(
                        f"{_ASSEMBLY} not found (set LHM_DLL_DIR or keep {_BUNDLE_NAME} next to the exe)"
                    )
                _ensure_clr(dll_dir)
                self._dll_dir = dll_dir

                from LibreHardwareMonitor.Hardware import Computer  # type: ignore

                computer = Computer()
                computer.IsCpuEnabled = True
                computer.IsGpuEnabled = bool(self._enable_gpu)
                computer.IsMemoryEnabled = False
                computer.IsMotherboardEnabled = False
                computer.IsControllerEnabled = False
                computer.IsNetworkEnabled = False
                computer.IsStorageEnabled = False
                computer.Open()
                self._computer = computer
                _STATE["status"] = "ready"
                _STATE["error"] = ""
            except Exception as exc:  # noqa: BLE001 - any CLR/assembly failure degrades to fallbacks
                self._computer = None
                self._init_error = f"{type(exc).__name__}: {exc}"
                _STATE["status"] = "failed"
                _STATE["error"] = self._init_error

    @property
    def available(self) -> bool:
        return self._computer is not None

    @property
    def error(self) -> str:
        return self._init_error

    def close(self) -> None:
        computer, self._computer = self._computer, None
        if computer is None:
            return
        try:
            computer.Close()
        except Exception:
            pass

    # -- poll ---------------------------------------------------------
    def read(self) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {
            "cpu_temp": None, "cpu_power": None,
            "gpu_temp": None, "gpu_power": None, "gpu_usage": None,
        }
        computer = self._computer
        if computer is None:
            return out

        # Re-read elevation: the process may have been relaunched elevated.
        self._elevated = is_admin()

        buckets: Dict[str, List[Tuple[str, Optional[float]]]] = {}
        try:
            for hw in _walk_hardware(computer):
                try:
                    hw.Update()
                except Exception:
                    continue
                try:
                    htype = str(hw.HardwareType)
                except Exception:
                    htype = ""
                if htype.startswith("Cpu") or htype == "Cpu":
                    family = "cpu"
                elif htype.startswith("Gpu"):
                    family = "gpu"
                else:
                    continue
                for s in getattr(hw, "Sensors", []) or []:
                    st = _sensor_type_name(s).lower()
                    if st not in ("temperature", "power", "load"):
                        continue
                    buckets.setdefault(f"{family}:{st}", []).append(
                        (_sensor_name(s).lower(), _sensor_value(s))
                    )
        except Exception:
            return out

        def pick(family: str, stype: str, prefer: Tuple[str, ...],
                 agg: str = "max") -> Optional[float]:
            items = buckets.get(f"{family}:{stype}")
            if not items:
                return None
            for frag in prefer:
                for name, value in items:
                    if value is None:
                        continue
                    if frag in name:
                        return value
            values = [v for _, v in items if v is not None]
            if not values:
                return None
            return max(values) if agg == "max" else min(values)

        # CPU temperature / package power need the LHM ring0 driver => admin.
        if self._elevated:
            out["cpu_temp"] = pick("cpu", "temperature",
                                   ("package", "cpu package", "tctl", "tdie",
                                    "core max", "core average", "cpu"))
            out["cpu_power"] = pick("cpu", "power",
                                    ("package", "cpu package", "ppt", "cpu cores", "cpu"))
            # A 0.0 package-power with no temperature is the "driver unreadable" sentinel.
            if out["cpu_power"] == 0.0 and out["cpu_temp"] is None:
                out["cpu_power"] = None

        out["gpu_temp"] = pick("gpu", "temperature", ("gpu", "core", "hot spot", "memory"), agg="min")
        out["gpu_power"] = pick("gpu", "power", ("gpu", "board", "chip", "package"))
        out["gpu_usage"] = pick("gpu", "load", ("core", "gpu", "3d", "d3d 3d"), agg="max")
        return out
