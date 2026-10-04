"""One-click memory boost.

Trims the working set of every process (the same trick RAMMap / 火绒 / 电脑管家
use) so cold pages are paged out and available memory goes up.  Nothing is
killed and no file is deleted — only the standby pressure on RAM is released.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Dict, Optional

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:  # pragma: no cover - exercised on the target machine
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    SE_PRIVILEGE_ENABLED = 0x00000002
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", LUID_AND_ATTRIBUTES * 1),
        ]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    def _mb(nbytes: float) -> float:
        return float(nbytes) / (1024.0 * 1024.0)

    def _memory_status() -> MEMORYSTATUSEX:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st

    def _enable_debug_privilege() -> bool:
        """SeDebugPrivilege lets us open other users' processes to trim them."""
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(token),
        ):
            return False
        try:
            luid = LUID()
            if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
                return False
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            return bool(advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None))
        finally:
            kernel32.CloseHandle(token)

    def _iter_pids():
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                return
            while True:
                yield int(entry.th32ProcessID), str(entry.szExeFile)
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snap)

    # prototypes
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID),
    ]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    kernel32.SetProcessWorkingSetSize.argtypes = [
        wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t,
    ]

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def _working_set(h) -> int:
        """WorkingSetSize in bytes (0 if unavailable)."""
        try:
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
        except Exception:
            pass
        return 0

    def trim_working_sets() -> Dict[str, Any]:
        """Release RAM held by idle working sets.  Safe: nothing is terminated.

        ``freed_mb`` is the *working set we actually trimmed* (sum of
        WorkingSetSize deltas), not a noisy global "available memory" delta —
        that number swings with unrelated activity and reads wrong on screen.
        """
        _enable_debug_privilege()
        before_status = _memory_status()
        me = kernel32.GetCurrentProcess()
        touched = 0
        skipped = 0
        freed_bytes = 0

        def _trim(h) -> None:
            nonlocal touched, skipped, freed_bytes
            ws0 = _working_set(h)
            ok = False
            try:
                ok = bool(psapi.EmptyWorkingSet(h))
            except Exception:
                ok = False
            ws1 = _working_set(h)
            if ok:
                touched += 1
                if ws0 > 0 and ws1 < ws0:
                    freed_bytes += (ws0 - ws1)
            else:
                skipped += 1

        # our own process first — cheap and always allowed
        try:
            kernel32.SetProcessWorkingSetSize(me, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass
        _trim(me)

        for pid, _name in _iter_pids():
            if pid in (0, 4):  # System / Idle
                continue
            access = PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
            h = kernel32.OpenProcess(access, False, pid)
            if not h:
                h = kernel32.OpenProcess(
                    PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                skipped += 1
                continue
            try:
                _trim(h)
            finally:
                kernel32.CloseHandle(h)

        after_status = _memory_status()
        return {
            "ok": True,
            "processes": touched,
            "skipped": skipped,
            "freed_mb": round(_mb(freed_bytes), 1),
            "before_avail_mb": round(_mb(before_status.ullAvailPhys), 1),
            "after_avail_mb": round(_mb(after_status.ullAvailPhys), 1),
            "total_mb": round(_mb(before_status.ullTotalPhys), 1),
            "load_before": int(before_status.dwMemoryLoad),
            "load_after": int(after_status.dwMemoryLoad),
        }

else:  # pragma: no cover - non-Windows stub
    def trim_working_sets() -> Dict[str, Any]:
        return {"ok": False, "error": "Windows only", "freed_mb": 0.0}
