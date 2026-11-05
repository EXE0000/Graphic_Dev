# 打包与跨平台说明

> 配套文档：端到端流程与方法论见 [PACKAGING.md](PACKAGING.md)；
> Windows / Linux 逐步实操见 [BUILD_WINDOWS_LINUX.md](BUILD_WINDOWS_LINUX.md)。

## 一句话结论

| 平台 | 能不能把依赖全打进包里 | 说明 |
|---|---|---|
| **Windows** | ✅ 基本可以 | Python、PySide6、WebEngine、VC++ 运行库都能随包 |
| **macOS** | ✅ 可以 | 能打成 `.app`，但**必须签名/公证**，否则用户打开会被系统拦 |
| **Linux** | ⚠️ 部分可以 | glibc 和图形系统库**不能打**，需要目标的系统自带；推荐 AppImage/Flatpak |

---

## 打包命令

```bash
# 1. 装打包工具（只需一次）
pip install pyinstaller

# 2. 在项目根目录执行
pyinstaller AI_chat.spec --noconfirm

# 3. 产物
#   dist/AI_chat/            Windows/Linux
#   dist/AI Chat.app         macOS
```

预计体积：**300–500 MB**（WebEngine 占大头，压缩后仍大）。

---

## 包里都包含什么

由 `AI_chat.spec` 控制：

```
AI_chat/                     ← 解压后的运行目录
├── AI_chat                （可执行文件 / 或在 .app 里）
├── AI_chat/web/              chat.html、css、js、vendor/KaTeX（含字体文件）
├── AI_chat/resources/        theme.qss、check.svg、chevron-down.svg
│   └── fonts/                可选：随包中文字体（放 .otf/.ttf 即可自动加载）
└── _internal/                PySide6、Qt 框架、Python 运行时
```

代码里已经处理了"打包后路径变化"：

```python
def resource_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "AI_chat"
    return Path(__file__).resolve().parent
```

同理，**用户数据**（附件、图片、缓存）不会打进包里，始终写到系统数据目录：

| 平台 | 位置 |
|---|---|
| Windows | `%APPDATA%\Graphic_Dev\AI_chat\` |
| macOS | `~/Library/Application Support/Graphic_Dev/AI_chat/` |
| Linux | `~/.local/share/Graphic_Dev/AI_chat/` |

---

## 各平台细节

### Windows

**能打进去的：**
- Python 解释器、PySide6、Qt 所有 DLL
- QtWebEngine 进程（`QtWebEngineProcess.exe`）、ICU、语言包
- VC++ 运行库（PyInstaller 会带上 `VCRUNTIME140.dll` 等）

**要注意：**
- 目标机器需要 **Windows 10 1809+**（WebEngine 的硬要求）
- 用 `console=False` 已经关掉黑框
- 想加图标：准备 `.ico`，在 spec 里 `EXE(..., icon="icon.ico")`
- 杀毒软件可能误报（PyInstaller 通病），建议签名

### macOS

**能打进去的：** 全部（Python、Qt、WebEngine）。

**必须做的：**
```bash
# 1. 签名（需要开发者证书）
codesign --deep --force --sign "Developer ID Application: 你的名字 (TEAMID)" "dist/AI Chat.app"

# 2. 打包成 dmg（可选）
hdiutil create -volname "AI Chat" -srcfolder "dist/AI Chat.app" -ov -format UDZO "AI Chat.dmg"

# 3. 公证（否则用户首次打开会被 Gatekeeper 拦截）
xcrun notarytool submit "AI Chat.dmg" --apple-id ... --team-id ... --password ... --wait
xcrun stapler staple "dist/AI Chat.app"
```
没签名的话，用户需要右键 →「打开」，或者执行 `xattr -dr com.apple.quarantine`。

### Linux

**能打进去的：** Python、PySide6、Qt 库、KaTeX、字体。

**打不进去的（必须系统提供）：**

| 库 | 用途 | 常见安装 |
|---|---|---|
| `libnss3` | WebEngine 的 TLS | `apt install libnss3` |
| `libxcomposite1` `libxdamage1` `libxrandr2` `libxtst6` | Chromium 渲染 | 同左 |
| `libgbm1` | GPU/EGL | 同左 |
| `libasound2` | 音频（可选） | 同左 |
| `libxkbcommon0` | 键盘输入 | 同左 |
| glibc | 运行时 | **不能打包**，ABI 绑系统版本 |

**为什么不能打：** glibc 与内核/加载器强耦合，硬塞会直接崩溃；图形库同理，且涉及发行版差异。

**推荐做法：**
1. **AppImage**（最省事，把库都塞进一个文件）——但需要按目标发行版的库版本构建；
2. **Flatpak / Snap**——运行时自带，兼容性最好；
3. 或者简单点：发布 tar.gz + 一份 `install-deps.sh`。

**中文字体**：很多精简发行版没有 CJK 字体。解决办法我已经实现——把字体文件放进 `AI_chat/resources/fonts/`，启动时会自动注册：

```python
fonts.register_bundled_fonts()   # 扫描 resources/fonts/*.ttf|*.otf
```

推荐放 `NotoSansCJKsc-Regular.otf`（OFL 协议，允许随应用分发，约 16 MB）。

---

## 依赖清单（requirements.txt）

```
PySide6>=6.6        # 含 WebEngine / Pdf / PrintSupport
requests>=2.31
pyinstaller>=6.0    # 仅打包时需要
```

---

## 已知打包坑

1. **WebEngine 资源缺失** → 现象是聊天区白屏、公式不出。
   检查包内是否有：`QtWebEngineProcess`、`icudtl.dat`、`qtwebengine_resources*.pak`、`locales/`。
   PyInstaller 的 PySide6 hook 一般会自动收集，若缺失可加 `--collect-all PySide6`。

2. **noarchive / UPX** → 已在 spec 里关掉。对 WebEngine 的库做压缩会导致加载失败。

3. **首次启动慢** → 解压 300MB+ 需要几秒，属正常（onefile 模式更慢，建议用默认的 onedir）。

4. **路径含中文/空格** → 代码里已经用 `QUrl.fromLocalFile` + `FullyEncoded` 处理，打包路径本身也建议避免中文。

---

## 打包后自检清单

1. 启动不闪退，聊天区能显示欢迎语；
2. 发一条消息，流式输出正常；
3. 勾选「联网搜索」，能返回来源；
4. 公式 `$E=mc^2$` 正常渲染；
5. 添加一个 PDF 附件，能读出文字；
6. 导出一份 DOCX 和 PDF，打开检查字体和图片；
7. 生成一张图片，点「保存图片」能存到磁盘；
8. 重启程序，配置和模型列表还在。
