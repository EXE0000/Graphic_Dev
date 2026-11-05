# Windows / Linux 打包指引

> 拿去在 Windows、Linux 机器上**各自构建并测试**。macOS 已验证通过，流程见
> [PACKAGING.md](PACKAGING.md)，平台概览见 [BUILD.md](BUILD.md)。
>
> 记住铁律：**PyInstaller 不能交叉编译**，Windows 的包必须在 Windows 上打，
> Linux 的包必须在 Linux 上打。三平台共用同一份代码 / `AI_chat.spec` / `requirements.txt`。

---

## 通用前提（两平台都适用）

1. 把整个项目拷到目标机器（含 `AI_chat/`、`AI_chat.spec`、`requirements.txt`）。
   **路径不要带中文和空格**，省掉一类诡异问题。
2. 装 **64 位 Python**（建议 3.11–3.13；只要 `pip install PySide6==6.12.0` 能装上对应
   wheel 即可）。不要用手头已有的 32 位 Python。
3. 依赖版本**严格按 `requirements.txt`**，别升级 PySide6——版本漂移是打包后出错的主因。
4. **构建完必须实际启动测一次**（exit 0 不等于能跑）。macOS 上就是靠这一步揪出 3 个坑的。

---

# 一、Windows

## 1. 装 Python
- 到 python.org 下 **Windows installer (64-bit)**，安装时勾 **Add python.exe to PATH**。
- ⚠️ 别用 Microsoft Store 版 Python（路径受限，venv/打包容易出幺蛾子）。
- 验证：
  ```cmd
  python --version
  python -c "import struct; print(struct.calcsize('P')*8, 'bit')"   :: 应输出 64 bit
  ```

## 2. 建虚拟环境并装依赖
在项目根目录：
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
`requirements.txt` 里已含 `pyinstaller==6.22.3`，装完即可用。

## 3. 构建
```cmd
pyinstaller AI_chat.spec --noconfirm --clean
```
产物：`dist\AI_chat\AI_chat.exe`（onedir 目录，exe 旁边有 `_internal\`）。
> spec 里的 `BUNDLE`（.app）只在 macOS 生效，Windows 会自动跳过，只出 `dist\AI_chat\`。

## 4. 验证（关键）
**先从命令行跑**，这样闪退时能看到报错：
```cmd
dist\AI_chat\AI_chat.exe
```
- 能弹出主窗口、控制台无红色报错 → 基本成功。
- 再走一遍功能自检（见文末清单）：发消息、联网搜索、公式 `$E=mc^2$`、PDF 附件、
  导出 DOCX/PDF、文生图、重启后配置还在。

确认 WebEngine 辅助进程打进去了：
```cmd
dir /s /b "dist\AI_chat\QtWebEngineProcess.exe"
dir /s /b "dist\AI_chat\*qtwebengine_resources*.pak"
dir /s /b "dist\AI_chat\icudtl.dat"
```
三者都应能找到（一般在 `dist\AI_chat\_internal\PySide6\Qt\...` 下）。

## 5. Windows 注意事项
- **系统要求**：目标机需 **Windows 10 1809+**（QtWebEngine 硬要求）。
- **VC++ 运行库**：PyInstaller 会带上 `VCRUNTIME140.dll` 等；极精简系统若仍报缺
  `MSVCP140.dll`，装一下「VC++ 2015–2022 Redistributable (x64)」。
- **黑框**：spec 里 `console=False` 已关掉控制台窗口。
- **杀毒软件误报**：PyInstaller 通病。对外分发建议做**代码签名**（`signtool`），
  能显著减少 SmartScreen 拦截和杀软误杀。
- **图标**：想要 exe 图标，准备 `.ico`，在 spec 的 `EXE(..., icon="icon.ico")` 里指定。

## 6. 分发
直接把整个 `dist\AI_chat\` 目录压缩发给用户，解压后运行 `AI_chat.exe` 即可
（**不要只发 exe**，`_internal\` 是运行必需的）。想做成安装包可再套 Inno Setup / NSIS。

---

# 二、Linux

## 1. 装构建所需的系统库
PySide6 在 Linux 上**导入时**就要链接一批 X/GLib 库，构建机（=测试机）得先装齐，
否则 `pyinstaller` 分析阶段或跑产物时会缺库。Debian/Ubuntu：
```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-pip \
  libgl1 libegl1 libglib2.0-0 \
  libxkbcommon0 libxkbcommon-x11-0 \
  libdbus-1-3 libfontconfig1 libfreetype6 \
  libnss3 libnspr4 \
  libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxcursor1 libxi6 \
  libgbm1 libasound2
```
> `libnss3 / libgbm1 / libxcomposite1 ...` 这些是 **QtWebEngine（Chromium）运行必需**，
> 缺了会白屏或直接崩。Fedora/Arch 用对应包名（`dnf` / `pacman`）。

## 2. 装 Python 与虚拟环境
- 用发行版自带的 Python 3.11/3.12 通常最稳（`python3 --version` 看一下）。
- 若版本过老或过新导致 `PySide6==6.12.0` 没有对应 wheel，改装 3.11–3.13
  （deadsnakes PPA 或 pyenv），**不要去改 PySide6 的版本锁**。
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 构建
```bash
pyinstaller AI_chat.spec --noconfirm --clean
```
产物：`dist/AI_chat/AI_chat`（onedir 目录 + `_internal/`）。

## 4. 验证（关键）
从终端直接跑，好看报错：
```bash
./dist/AI_chat/AI_chat
```
确认 WebEngine 组件在位：
```bash
find dist/AI_chat -name 'QtWebEngineProcess' -o -name 'qtwebengine_resources*.pak' -o -name 'icudtl.dat'
```

**Chromium 沙箱**：在无桌面/容器/root 环境里，WebEngine 沙箱常导致崩溃。
- [app.py](../AI_chat/app.py) 已处理：**以 root 运行时自动加 `--no-sandbox`**。
- 若在 Docker/CI 里以非 root 跑仍崩，临时设环境变量再启动：
  ```bash
  QTWEBENGINE_DISABLE_SANDBOX=1 ./dist/AI_chat/AI_chat
  ```
- 纯命令行无显示器的机器上冒烟测试，可用 offscreen（注意：offscreen 下会刷一堆
  Skia/GPU 报错，那是无头渲染的正常噪音，**不是崩溃**，看进程是否存活即可）：
  ```bash
  QT_QPA_PLATFORM=offscreen ./dist/AI_chat/AI_chat & sleep 10; kill %1
  ```

## 5. 中文字体（CJK）
很多精简发行版没装中文字体，会导致界面/导出里中文变方框。两种解法：
- 系统装字体：`sudo apt install fonts-noto-cjk`；或
- **随包字体**（推荐，跨发行版稳）：把 `NotoSansCJKsc-Regular.otf`（OFL 协议，可随应用分发）
  放进 `AI_chat/resources/fonts/`，spec 会自动收集，启动时 [fonts.py](../AI_chat/fonts.py)
  的 `register_bundled_fonts()` 自动注册。

## 6. glibc 与"打不进去"的库
**glibc 不能冻结**（与内核/加载器强耦合，硬塞必崩），图形系统库同理。所以：
- 在**较老的发行版**上构建，产物能向上兼容较新系统（glibc 向后兼容）；
  在太新的系统上构建，拿到老系统上可能跑不起来。
- 目标机仍需自带上面第 1 步那批系统库。

## 7. 分发（可选，做成单文件/免依赖）
onedir 目录可直接打包成 tar.gz 发布，附一份"装系统依赖"的说明。想更省心：
- **AppImage**：把库塞进单个可执行文件（用 `linuxdeploy` + Qt/WebEngine 插件）；
- **Flatpak / Snap**：运行时自带，兼容性最好，适合上架。
先跑通 onedir 测试，再考虑这层封装。

---

## 打包后功能自检清单（两平台通用）

1. 启动不闪退，聊天区显示欢迎语；
2. 选模型后发一条消息，流式输出正常；
3. 勾选「联网搜索」（需已配搜索 KEY），能返回来源；未配 KEY 时该勾选应被禁用；
4. 公式 `$E=mc^2$` 正常渲染（验证 WebEngine + KaTeX）；
5. 添加一个 PDF 附件，能读出文字（验证 QtPdf）；
6. 导出一份 DOCX 和 PDF，打开检查字体和图片；
7. 生成一张图片，点「保存图片」能落盘；
8. 重启程序，配置和模型列表还在；
9. 网页端（客户端里开启的本地 web）能打开、能聊天。

---

## 常见错误对照表

| 现象 | 大概率原因 | 处理 |
|---|---|---|
| 启动即 `ImportError: could not import PySide6.QtXxx` | `excludes` 排掉了 WebEngine 依赖链 | 确认 spec 的 excludes 不含 QtWebChannel/QtWebSockets/QtQml/QtQuick/QtOpenGL/QtPositioning |
| `could not find QtWebEngineProcess` | 辅助进程没打进包 / 路径错 | Windows/Linux 先查 `find`/`dir` 有没有该文件；macOS 已由 `_fix_webengine_paths()` 自愈 |
| 聊天区白屏、公式不出 | WebEngine 资源（.pak/icudtl/locales）缺失 | 确认资源在包内；Linux 补 `libnss3`、`libgbm1` 等系统库 |
| 所有 HTTPS 请求 SSLError | certifi 证书没打进去 | spec 已含 `collect_data_files("certifi")`，别删 |
| Linux 一跑就崩、提到 sandbox | Chromium 沙箱受限 | root 下已自动 `--no-sandbox`；否则设 `QTWEBENGINE_DISABLE_SANDBOX=1` |
| 中文显示方框 | 缺 CJK 字体 | 随包放 `NotoSansCJKsc-Regular.otf` 到 `resources/fonts/` |
| Windows 双击闪退无提示 | 需从命令行跑看报错 | `cmd` 里执行 `dist\AI_chat\AI_chat.exe` |
| 构建成功但运行缺 DLL/so | 目标机缺系统运行库 | Windows 装 VC++ Redist；Linux 装第 1 步那批库 |
