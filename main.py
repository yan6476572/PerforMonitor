#!/usr/bin/env python3
"""PerformanceMonitor entry point.

    python main.py            # start the HUD
    python main.py --settings # start with the settings dialog open
    python main.py --reset    # restore defaults
"""

from __future__ import annotations

import sys

from perf_overlay.app import run

if __name__ == "__main__":
    sys.exit(run())
