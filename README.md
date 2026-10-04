# PerfOverlay — 桌面性能监测浮窗

一个轻量、置顶、可自由拖动/缩放的桌面性能 HUD。
以半透明小浮窗实时显示 **CPU 温度 / 功耗 / 占用率**、**GPU 温度 / 功耗 / 占用率**、**内存占用率** 与 **实时帧率**，
始终悬浮在其他应用之上，不会被普通窗口遮挡。

```
┌────────────────────────────┐
│ ● PERFORMANCE          ⛭  │
│ ▌CPU 温度                 61°C │
│ ▌CPU 功耗                78.4 W │
│ ▌CPU 占用                 42 % │
│ ▌GPU 温度                 57°C │
│ ▌GPU 功耗               186.3 W │
│ ▌GPU 占用                 87 % │
│ ▌内存占用                 51 % │
│ ▌实时帧率                143   │
└────────────────────────────┘
<img width="993" height="108" alt="image" src="https://github.com/user-attachments/assets/14a3667b-51a9-4315-a1e4-3657b748f9d7" />
<img width="237" height="307" alt="image" src="https://github.com/user-attachments/assets/c731e397-1841-4759-ad53-ff397ae9589c" />
<img width="90" height="90" alt="image" src="https://github.com/user-attachments/assets/48b551af-0619-4c98-9f9e-041e93f49dba" />



```

---

## 功能

### 核心
| 功能 | 说明 |
| --- | --- |
| 8 项指标 | CPU 温度/功耗/占用、GPU 温度/功耗/占用、内存占用、实时帧率 |
| 横向排布 | 每个指标一行，`标签 …… 数值 单位` 左右横排；下方带实时进度条 |
| 始终置顶 | `WindowStaysOnTopHint` + Win32 `HWND_TOPMOST` 双重保证，并每 3 秒自动重新置顶 |
| 逐项开关 | 设置里可单独勾选要显示的参数，关闭即隐藏 |
| 透明度 | **背景透明度**（面板底色 alpha）与 **整体透明度**（整个窗口）两档独立调节 |
| 背景色 | 任意取色，另可自定义强调色 |
| 拖动移动 | 按住浮窗任意位置拖动；可一键锁定位置 |
| 边缘缩放 | 拖动四边或四角调整大小，光标自动切换为对应方向箭头 |

### 附加
- **系统托盘**：显示/隐藏浮窗、设置、锁定位置、鼠标穿透、退出
- **鼠标穿透**：浮窗完全不响应鼠标，游戏时不挡操作（从托盘恢复）
- **右键菜单**：设置、锁定位置、置顶、鼠标穿透、复制当前数据、退出
- **双击标题栏** → 打开设置
- **布局模式**：单列堆叠 / 双列紧凑
- **温度单位**：摄氏 °C / 华氏 °F
- **温度告警着色**：≥75°C 转黄、≥90°C 转红（°F 对应 167/194）
- **刷新间隔** 100–5000 ms 可调
- **位置与大小记忆**，重启后还原
- **复制当前数据**：一键把所有指标复制到剪贴板

---

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

需要 **Python 3.9+**（推荐 3.10/3.11/3.12）。

### 2. 运行

**方式 A — 直接用打包好的 exe（最快）**

`dist/PerfOverlay.exe` 是已经打包好的 Windows 绿色版，**双击即可运行，
不需要安装 Python**。想监控 GPU/CPU 温度请见下面的「数据来源」一节。

**方式 B — 从源码运行**

```bash
python main.py
```

常用参数（两种方式都适用）：

```bash
python main.py --settings   # 启动时直接打开设置
python main.py --hidden     # 启动后隐藏浮窗（仅托盘）
python main.py --reset      # 恢复默认设置
python main.py --config X   # 指定配置文件路径
python main.py --screenshot D   # 导出浮窗/设置界面 PNG 到目录 D 后退出
```

### 3. 打包成 exe（可选）

**一键打包**：双击项目根目录的 `build_exe.bat`，自动建环境、装依赖、打包，
产物在 `dist\PerfOverlay.exe`，双击即用、不需要 Python。

或手动：

```bash
pip install pyinstaller
pyinstaller PerfOverlay.spec
# 产物: dist/PerfOverlay.exe
```

详细说明（自定义图标 / 控制台 / 单文件与目录版 / 杀软误报）见
[`docs/BUILD-WINDOWS.md`](docs/BUILD-WINDOWS.md)。

---

## 使用

| 操作 | 效果 |
| --- | --- |
| 拖动浮窗任意区域 | 移动位置 |
| 拖动浮窗**边缘 / 四角** | 调整大小（光标会变成 ↕ ↔ ↘ 等箭头） |
| 双击标题栏 | 打开设置 |
| 右键浮窗 | 打开菜单 |
| 点击右上角 ⛭ | 打开设置 |
| 单击托盘图标 | 显示 / 隐藏浮窗 |
| 双击托盘图标 | 打开设置 |

---

## 设置说明

### 显示项目
每个指标一个复选框，取消勾选即在浮窗中隐藏，浮窗高度自动重新排版。

### 外观
- **背景颜色** — 面板底色
- **强调色** — 标题圆点、进度条、设置界面主色
- **背景透明度** — 0–100%，只作用于面板底色（文字保持清晰）
- **整体透明度** — 15–100%，作用于整个浮窗
- **字号** — 9–20 px
- **圆角** — 0–24 px
- **布局** — 单列堆叠 / 双列紧凑
- **显示标题栏 / 显示进度条**

### 数据与采样
- **刷新间隔** — 100–5000 ms
- **温度单位** — °C / °F
- **GPU 序号** — 多显卡时选择要监控的 NVIDIA GPU
- **帧率来源** — 自动检测 / PresentMon / 数据文件 / 关闭

### 窗口行为
- **锁定位置** — 禁止拖动，防止游戏中误移
- **鼠标穿透** — 浮窗完全不吃鼠标事件
- **窗口置顶** — 始终开启

---

## 数据来源

| 指标 | Windows | Linux |
| --- | --- | --- |
| CPU 占用率 | `psutil` | `psutil` |
| 内存占用率 | `psutil` | `psutil` |
| CPU 温度 | LibreHardwareMonitor (WMI) → ACPI 热区 | `psutil.sensors_temperatures`（coretemp / k10temp / zenpower …） |
| CPU 功耗 | LibreHardwareMonitor (WMI) | RAPL `/sys/class/powercap/*/energy_uj` |
| GPU 温度/功耗/占用 | NVIDIA NVML（`nvidia-ml-py`）或 LibreHardwareMonitor | NVML；AMD 读 `amdgpu` sysfs |
| 实时帧率 | **Intel PresentMon** | 数据文件（如 MangoHud `output_file`） |

某个传感器读不到时，对应数值显示 `--`，其余指标照常工作——**任何单项缺失都不会影响其它功能**。

### Windows 完整监控（推荐）

1. **GPU 全套指标**：装好 NVIDIA 驱动即可，无需额外软件。
2. **CPU 温度 / 功耗**：
   - 安装 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
   - 在 LHM 的 `Options → WMI` 里勾选启用
   - `pip install wmi pywin32`
3. **实时帧率**：
   - 下载 [Intel PresentMon](https://github.com/GameTechDev/PresentMon/releases)，解压任意目录
   - 设置 → 数据与采样 → 帧率来源选 `PresentMon`，填入 `PresentMon.exe` 路径
   - 也可以直接把 exe 丢到本程序同目录，自动查找

> PresentMon 以流式 CSV 采集每帧 `Present` 事件，按前台进程滚动统计 FPS，支持
> DirectX 11/12、Vulkan、OpenGL 与 UWP 应用。

### Linux 帧率

任意工具只要把数字写进一个文本文件即可。例如用 MangoHud：

```bash
MANGOHUD_CONFIG="output_folder=/tmp/mh,output_file=fps.txt" mangohud %command%
```

然后在设置里把 **帧率来源** 设为 `数据文件`，路径填 `/tmp/mh/fps.txt`。

---

## 常见问题

**Q：游戏里看不到浮窗？**
独占全屏（Exclusive Fullscreen）会覆盖所有普通窗口。请把游戏设为 **无边框窗口化 / Borderless Windowed**，浮窗即可正常显示。

**Q：某个指标显示 `--`？**
该传感器当前不可用。Windows 下 CPU 温度/功耗需要 LibreHardwareMonitor；GPU 指标需要 NVIDIA 驱动（或 AMD + amdgpu）；帧率需要 PresentMon 或数据文件。

**Q：浮窗被别的程序挡住了？**
右键浮窗 → 确认「窗口置顶」勾选。程序也会每 3 秒自动重新置顶。

**Q：配置文件在哪里？**
Windows: `%APPDATA%\PerfOverlay\settings.json`
Linux: `~/.config/PerfOverlay/settings.json`
macOS: `~/Library/Application Support/PerfOverlay/settings.json`
也可用 `--config` 指定路径，或设环境变量 `PERF_OVERLAY_CONFIG`。

**Q：CPU 占用会不会很高？**
不会。传感器轮询在独立线程，默认 500 ms 一次，单次耗时通常 < 5 ms。

---

## 项目结构

```
PerfOverlay/
├── main.py                     # 入口
├── perf_overlay/
│   ├── app.py                  # 应用装配：托盘 / 信号 / 生命周期
│   ├── config.py               # 设置模型 + JSON 持久化
│   ├── metrics.py              # 指标定义（键、标签、单位、配色）
│   ├── platform_win.py         # Win32 置顶 / 无激活 / 鼠标穿透
│   ├── sensors/
│   │   ├── base.py             # Provider 协议
│   │   ├── system.py           # psutil + RAPL + hwmon + LHM(WMI) + AMD
│   │   ├── nvidia.py           # NVML
│   │   ├── fps.py              # PresentMon / 文件
│   │   └── manager.py          # 轮询线程
│   └── ui/
│       ├── theme.py            # 配色、字体、绘制助手、QSS
│       ├── overlay.py          # 浮窗（绘制 / 拖动 / 缩放 / 菜单）
│       └── settings.py         # 设置面板（实时预览）
├── tools/preview.py            # 用假数据渲染 UI 截图
├── tools/selftest.py           # 功能自测（拖动/缩放/开关/持久化）
├── dist/PerfOverlay.exe        # 打包好的 Windows 可执行文件
├── docs/screenshots/           # UI 截图（含 exe 自导出的验证图）
├── docs/BUILD-WINDOWS.md       # Windows 打包说明
├── build_exe.bat               # Windows 一键打包脚本
├── PerfOverlay.spec            # PyInstaller 打包配置
└── requirements.txt
```

## 开发

```bash
# 用假数据渲染 UI 截图（无需真实传感器）
QT_QPA_PLATFORM=offscreen python tools/preview.py

# 功能自测：拖动 / 边缘缩放 / 逐项开关 / 设置持久化 / 置顶 / 穿透
QT_QPA_PLATFORM=offscreen python tools/selftest.py
```

设置采用**实时预览**：在设置面板里的每一次改动都会立即作用到浮窗，
点「取消」整体回滚，点「保存」写入配置。

## License

MIT
