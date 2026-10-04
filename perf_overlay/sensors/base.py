"""Sensor provider protocol and shared helpers."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from ..metrics import Metrics

Number = Optional[float]


class Provider:
    """A sensor source. ``poll`` fills in whatever fields it can."""

    name = "base"
    #: fields this provider is willing to contribute
    provides: Iterable[str] = ()

    def start(self) -> None:  # noqa: B027 - optional hook
        """Open handles / warm up caches. Called once."""

    def stop(self) -> None:  # noqa: B027 - optional hook
        """Release resources."""

    def poll(self, out: Metrics) -> None:  # noqa: B027 - abstract
        """Populate ``out`` using ``out.set(key, value, self.name)``."""

    # convenience -------------------------------------------------
    def _fill(self, out: Metrics, values: Dict[str, Number]) -> None:
        for key, value in values.items():
            out.set(key, value, self.name)


class NullProvider(Provider):
    name = "null"

    def poll(self, out: Metrics) -> None:
        return
