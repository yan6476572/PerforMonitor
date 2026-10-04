"""Canonical metric definitions shared by the sensor layer and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Dict, Optional


@dataclass
class Metrics:
    """One polling snapshot. ``None`` means "sensor unavailable / not ready"."""

    cpu_temp: Optional[float] = None
    cpu_power: Optional[float] = None
    cpu_usage: Optional[float] = None
    gpu_temp: Optional[float] = None
    gpu_power: Optional[float] = None
    gpu_usage: Optional[float] = None
    mem_usage: Optional[float] = None
    fps: Optional[float] = None

    # network throughput in bytes/s (hover card on the memory-only gauge)
    net_up: Optional[float] = None
    net_down: Optional[float] = None

    # metric key -> name of the provider that produced it (for tooltips/debug)
    sources: Dict[str, str] = field(default_factory=dict)

    def set(self, key: str, value: Optional[float], source: str) -> None:
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if v != v:  # NaN
            return
        setattr(self, key, v)
        self.sources[key] = source

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "sources"}


@dataclass(frozen=True)
class MetricSpec:
    """Static description of a displayable metric."""

    key: str
    label: str
    short: str
    unit: str
    group: str          # cpu | gpu | mem | fps
    kind: str           # percent | temp | power | fps
    order: int


# Order here drives the default on-screen order.
METRIC_SPECS = (
    MetricSpec("cpu_temp",  "CPU 温度",  "CPU", "°C", "cpu", "temp",   0),
    MetricSpec("cpu_power", "CPU 功耗",  "CPU", "W",  "cpu", "power",  1),
    MetricSpec("cpu_usage", "CPU 占用",  "CPU", "%",  "cpu", "percent", 2),
    MetricSpec("gpu_temp",  "GPU 温度",  "GPU", "°C", "gpu", "temp",   3),
    MetricSpec("gpu_power", "GPU 功耗",  "GPU", "W",  "gpu", "power",  4),
    MetricSpec("gpu_usage", "GPU 占用",  "GPU", "%",  "gpu", "percent", 5),
    MetricSpec("mem_usage", "内存占用",  "RAM", "%",  "mem", "percent", 6),
    MetricSpec("fps",       "实时帧率",  "FPS", "FPS", "fps", "fps",   7),
)

METRIC_BY_KEY: Dict[str, MetricSpec] = {m.key: m for m in METRIC_SPECS}
ALL_METRIC_KEYS = tuple(m.key for m in METRIC_SPECS)

# Accent colour used for each metric family in the HUD.
GROUP_COLORS = {
    "cpu": "#5B8CFF",
    "gpu": "#34D399",
    "mem": "#FBBF24",
    "fps": "#C084FC",
}
