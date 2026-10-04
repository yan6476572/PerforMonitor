"""开机自启动（Windows 任务计划程序）。

带 requireAdministrator 的程序放进启动文件夹会在登录时弹 UAC 或被静默拦下，
所以这里走 Task Scheduler：登录触发 + 最高权限 + 用户已登录时才跑（免 UAC）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

TASK_NAME = "PerformanceMonitor"

# 用 PowerShell ScheduledTasks 注册 InteractiveToken 任务。
# 注意：schtasks /NP 会把任务降级成 S4U（无交互式桌面，GUI 窗口不显示），
# 必须用 New-ScheduledTaskPrincipal -LogonType Interactive。
_PS_INSTALL = """$ErrorActionPreference='Stop'
$action = New-ScheduledTaskAction -Execute '{exe}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User '{user}'
$principal = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName '{name}' -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
"""

_PS_UNINSTALL = """$ErrorActionPreference='Continue'
Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false -ErrorAction SilentlyContinue
"""


def _run_ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _username() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def current_exe() -> str:
    """打包后是 exe 本身；源码运行时给出 python main.py 的等效命令。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    main = Path(__file__).resolve().parent.parent / "main.py"
    return f"{sys.executable} \"{main}\""


def install(exe: Optional[str] = None) -> Dict[str, Any]:
    """注册登录自启任务（需管理员，InteractiveToken 可显示 GUI）。"""
    target = exe or current_exe()
    script = _PS_INSTALL.format(exe=target, name=TASK_NAME, user=_username())
    out = _run_ps(script)
    ok = out.returncode == 0
    return {
        "ok": ok,
        "task": TASK_NAME,
        "exe": target,
        "error": "" if ok else (out.stderr or out.stdout).strip(),
    }


def uninstall() -> Dict[str, Any]:
    out = _run_ps(_PS_UNINSTALL.format(name=TASK_NAME))
    ok = out.returncode == 0
    return {"ok": ok, "task": TASK_NAME,
            "error": "" if ok else (out.stderr or out.stdout).strip()}


def status() -> Dict[str, Any]:
    """任务是否存在且为 InteractiveToken。"""
    out = _run_ps(
        f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue)"
        f".Principal.LogonType"
    )
    enabled = out.returncode == 0 and "Interactive" in (out.stdout or "")
    return {"ok": True, "task": TASK_NAME, "enabled": enabled,
            "logon_type": (out.stdout or "").strip()}


def set_enabled(on: bool) -> Dict[str, Any]:
    return install() if on else uninstall()
