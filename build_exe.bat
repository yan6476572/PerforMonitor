@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title PerformanceMonitor - build exe
cd /d "%~dp0"

echo ============================================================
echo   PerformanceMonitor  -  打包为 PerformanceMonitor.exe
echo ============================================================
echo.

rem ---- 1. locate python -------------------------------------------------
set "PY="
where python >nul 2>nul && for /f "delims=" %%i in ('where python') do if not defined PY set "PY=%%i"
where py >nul 2>nul && if not defined PY set "PY=py -3"
if not defined PY (
  echo [X] 没有找到 Python。
  echo     请安装 Python 3.10 / 3.11 / 3.12: https://www.python.org/downloads/
  echo     安装时务必勾选 "Add python.exe to PATH"
  pause
  exit /b 1
)
echo [1/5] 使用 Python: %PY%
%PY% -c "import sys;v=sys.version_info;print('      版本 %d.%d.%d'%v[:3]);sys.exit(0 if (3,9)<=v[:2]<(3,14) else 1)"
if errorlevel 1 (
  echo [X] Python 版本不合适，需要 3.9 ~ 3.13
  pause
  exit /b 1
)

rem ---- 2. virtual env (keeps the machine clean) ------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] 创建虚拟环境 .venv ...
  %PY% -m venv .venv
  if errorlevel 1 ( echo [X] 创建虚拟环境失败 & pause & exit /b 1 )
) else (
  echo [2/5] 复用已有 .venv
)
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. dependencies -------------------------------------------------
echo [3/5] 安装依赖（PySide6 较大，请耐心等待）...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo [X] 依赖安装失败 & pause & exit /b 1 )
"%VPY%" -m pip install pyinstaller --quiet
if errorlevel 1 ( echo [X] PyInstaller 安装失败 & pause & exit /b 1 )

rem ---- 4. build --------------------------------------------------------
echo [4/5] 开始打包 ...
"%VPY%" -m PyInstaller --noconfirm --clean PerfOverlay.spec
if errorlevel 1 ( echo [X] 打包失败，请把上方报错发给我 & pause & exit /b 1 )

rem ---- 5. done ---------------------------------------------------------
echo.
echo ============================================================
if exist "dist\PerformanceMonitor.exe" (
  echo [OK] 打包完成!
  echo      产物: %cd%\dist\PerformanceMonitor.exe
  for %%A in ("dist\PerformanceMonitor.exe") do echo      大小: %%~zA 字节
  echo.
  echo      直接双击即可运行，无需安装 Python。
  echo.
  start "" explorer "dist"
) else (
  echo [X] 没找到 dist\PerformanceMonitor.exe，请检查上方日志
)
echo ============================================================
pause
