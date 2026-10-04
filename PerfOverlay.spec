# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - produces a single-file PerformanceMonitor.exe.

    pyinstaller PerfOverlay.spec
"""

import os
from pathlib import Path

block_cipher = None

# LibreHardwareMonitorLib.dll (net10.0) + its managed dependencies.
# Loaded in-process via pythonnet so CPU temperature / power work without
# keeping LibreHardwareMonitor running in the background.
_LHM_SRC = Path(SPECPATH) / "LibreHardwareMonitor.NET.10"
_LHM_BUNDLE = "LibreHardwareMonitor.NET.10"

_lhm_datas = []
if _LHM_SRC.is_dir():
    for pattern in ("*.dll", "*.json", "*.config"):
        for f in sorted(_LHM_SRC.glob(pattern)):
            # skip the GUI shell / docs; the sensor lib does not need them
            if f.name in ("LibreHardwareMonitor.dll",
                          "LibreHardwareMonitor.exe",
                          "LibreHardwareMonitor.config",
                          "LibreHardwareMonitorLib.pdb",
                          "LibreHardwareMonitorLib.xml",
                          "Aga.Controls.dll",
                          "OxyPlot.dll",
                          "OxyPlot.WindowsForms.dll",
                          "Microsoft.Win32.TaskScheduler.dll"):
                continue
            _lhm_datas.append((str(f), _LHM_BUNDLE))
    # minimal runtime config (Microsoft.NETCore.App 10.0) used by pythonnet
    _rt = Path(SPECPATH) / "perf_overlay" / "sensors" / "_lhm_runtimeconfig.json"
    if _rt.exists():
        _lhm_datas.append((str(_rt), os.path.join("perf_overlay", "sensors")))

# Intel PresentMon CLI - real-time FPS capture (bundled so the HUD works out of the box)
_pm = Path(SPECPATH) / "bin" / "PresentMon64.exe"
if _pm.exists():
    _lhm_datas.append((str(_pm), "bin"))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=_lhm_datas,
    hiddenimports=[
        'pynvml',
        'perf_overlay.boost',
        'pythonnet',
        'clr_loader',
        'clr',
        'wmi',
        'pythoncom',
        'win32com',
        'win32api',
        'pywintypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'email', 'http', 'xml', 'pydoc'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PerformanceMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / 'PixPin_2026-09-28_22-22-58.ico'),
    uac_admin=True,  # requireAdministrator: LHM needs MSR access for CPU temp/power
)
