"""NVIDIA GPU telemetry via NVML (``nvidia-ml-py`` / ``pynvml``)."""

from __future__ import annotations

from typing import Optional

from ..metrics import Metrics
from .base import Provider


class NvidiaProvider(Provider):
    name = "nvidia"
    provides = ("gpu_temp", "gpu_power", "gpu_usage")

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self._nvml = None
        self._handle = None
        self._init()

    def _init(self) -> None:
        try:
            import pynvml  # type: ignore
        except Exception:
            return
        try:
            pynvml.nvmlInit()
        except Exception:
            return
        try:
            count = pynvml.nvmlDeviceGetCount()
            if count <= 0:
                return
            self.index = max(0, min(self.index, count - 1))
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self.index)
            self._nvml = pynvml
        except Exception:
            self._nvml = None

    @property
    def available(self) -> bool:
        return self._handle is not None

    def stop(self) -> None:
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        self._nvml = None
        self._handle = None

    def poll(self, out: Metrics) -> None:
        if self._handle is None:
            return
        nvml = self._nvml
        try:
            out.set("gpu_temp", nvml.nvmlDeviceGetTemperature(self._handle, nvml.NVML_TEMPERATURE_GPU), self.name)
        except Exception:
            pass
        try:
            mw = nvml.nvmlDeviceGetPowerUsage(self._handle)
            out.set("gpu_power", mw / 1000.0, self.name)  # mW -> W
        except Exception:
            pass
        try:
            util = nvml.nvmlDeviceGetUtilizationRates(self._handle)
            out.set("gpu_usage", util.gpu, self.name)
        except Exception:
            pass

    def name_str(self) -> Optional[str]:
        if self._handle is None:
            return None
        try:
            return self._nvml.nvmlDeviceGetName(self._handle).decode() \
                if isinstance(self._nvml.nvmlDeviceGetName(self._handle), bytes) \
                else str(self._nvml.nvmlDeviceGetName(self._handle))
        except Exception:
            return None
