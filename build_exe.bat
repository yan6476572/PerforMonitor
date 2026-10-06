@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title PerformanceMonitor - build exe
cd /d "%~dp0"

echo ============================================================
echo   PerformanceMonitor  -  打包为 PerformanceMonitor.exe
echo ============================================================
echo.

set "VPY=.venv\Scripts\python.exe"

rem ---- 1. locate python ------------------------------------------------
rem An existing project venv wins: it is already validated, and this skips
rem whatever python happens to sit first on PATH (e.g. MinGW ones).
if exist "%VPY%" (
  echo [1/5] 使用项目虚拟环境: %CD%\%VPY%
  goto deps
)

rem Prefer the official py launcher, then the first PATH python that is
rem NOT a MinGW / MSYS one.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
      echo %%i | findstr /i "mingw msys" >nul || set "PY=%%i"
    )
  )
)
if not defined PY (
  rem last resort: any python on PATH
  for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
)
if not defined PY (
  echo [X] 没有找到合适的 Python。
  echo     请安装官方 Python 3.10 - 3.13: https://www.python.org/downloads/
  echo     安装时务必勾选 "Add python.exe to PATH"
  pause
  exit /b 1
)
echo [1/5] 使用 Python: !PY!
!PY! -c "import sys;print('      版本', sys.version.split()[0])"
!PY! -c "import sys;sys.exit(0 if (3,9) <= sys.version_info[:2] < (3,14) else 1)"
if errorlevel 1 (
  echo [X] Python 版本不合适，需要 3.9 ~ 3.13
  pause
  exit /b 1
)

rem ---- 2. virtual env (keeps the machine clean) ------------------------
if exist "%VPY%" (
  echo [2/5] 复用已有 .venv
) else (
  echo [2/5] 创建虚拟环境 .venv ...
  !PY! -m venv .venv
  if errorlevel 1 ( echo [X] 创建虚拟环境失败 & pause & exit /b 1 )
)

:deps
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
  echo      产物: %CD%\dist\PerformanceMonitor.exe
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
