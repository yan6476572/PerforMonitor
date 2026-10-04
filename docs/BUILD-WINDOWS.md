# 在 Windows 上打包 PerfOverlay.exe

三种方式，按省事程度排序。

---

## 方式一：一键脚本（推荐）

**前提**：装好 [Python 3.10 ~ 3.12](https://www.python.org/downloads/)，安装时勾选 **Add python.exe to PATH**。

双击项目根目录的 **`build_exe.bat`**。

脚本会自动：创建虚拟环境 → 安装依赖 → PyInstaller 打包 → 打开 `dist` 文件夹。

产物：`dist\PerfOverlay.exe`，双击即可运行，**不需要 Python**。

---

## 方式二：手动命令

```bat
cd PerfOverlay

py -3 -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
pip install pyinstaller

pyinstaller --noconfirm --clean PerfOverlay.spec
```

产物同样在 `dist\PerfOverlay.exe`。

---

## 方式三：免安装的绿色版（不打包 exe 也想直接用）

如果只是自己用，其实不需要 exe：

```bat
cd PerfOverlay
pip install -r requirements.txt
pythonw main.py
```

`pythonw` 不会弹黑框。想开机自启就把这个快捷方式丢进
`shell:startup`。

---

## 自定义

`PerfOverlay.spec` 里可以改：

| 位置 | 作用 |
| --- | --- |
| `name='PerfOverlay'` | exe 文件名 |
| `console=False` | `True` 会带黑色控制台（调试用） |
| `icon=None` | 换成 `icon='icon.ico'` 就有图标了 |
| `upx=True` | 装了 UPX 会压缩体积，没装可改 `False` |

想生成带图标的版本：准备一个 `icon.ico` 放在项目根目录，然后把 spec 里的
`icon=None` 改成 `icon='icon.ico'`，再跑一次打包。

---

## 常见问题

**Q：打包出来的 exe 有 60~90 MB，正常吗？**
正常。PySide6 本身就要几十 MB。这是「带完整 GUI 运行时」的代价。

**Q：双击 exe 闪退 / 报毒？**
- 闪退：从 `cmd` 里运行 exe 看报错，多半是缺 `PresentMon.exe` 之类的可选组件（不影响主程序，可忽略）。
- 杀软误报：PyInstaller 单文件包常见。加白名单即可；或者改成多文件模式（把 spec 里 `a.binaries, a.datas` 拆出 `COLLECT()`，生成 `dist/PerfOverlay/` 目录版）。

**Q：怎么同时出单文件和目录版？**
目录版启动更快、不易误报。把 spec 最后改成：

```python
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PerfOverlay', console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, name='PerfOverlay')
```

**Q：想监控 GPU/CPU 温度，exe 版还需要装什么？**
见主 README 的「数据来源」一节。GPU 指标装好 NVIDIA 驱动即可；
CPU 温度/功耗需要 LibreHardwareMonitor；帧率需要 PresentMon。
这些都是**外部可选组件**，不装只是对应项显示 `--`，程序本身照常跑。
