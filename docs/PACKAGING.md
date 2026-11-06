# 打包流程（端到端工作流）

> 本篇讲"怎么打、按什么顺序打、打完怎么验证"。平台概览见 [BUILD.md](BUILD.md)，
> Windows / Linux 的逐步实操见 [BUILD_WINDOWS_LINUX.md](BUILD_WINDOWS_LINUX.md)。

---

## 0. 一个核心理念：冻结，而不是"运行时补依赖"

桌面软件打包的正确模型是 **构建时冻结（freeze）**：在**你的机器上**把
Python 解释器、PySide6、Qt 全部插件、QtWebEngine、requests、CA 证书一起塞进产物目录。
用户机器**什么都不用装**，双击即用、离线可用。

❌ 不要做"用户启动时 `pip install` / 自动下载缺失依赖"——它需要联网、需要写权限、
会因平台和 Python 版本不同而失败，是"在我机器上能跑、到用户机器就崩"的头号来源。

唯一**无法**冻结的是操作系统自带的服务（macOS Keychain 的 `security`、Windows DPAPI、
Linux `secret-tool`）——它们不是 pip 依赖，[secrets_store.py](../AI_chat/secrets_store.py)
已做"文件加密回退"，缺失时自动降级，不影响启动。

---

## 1. 三平台铁律：各自构建，不能交叉编译

PyInstaller **不是交叉编译器**。在 macOS 上只能产出 `.app`，打不出 Windows 的 `.exe`。
**每个目标系统都要在它本机上各打一次**：

| 在哪台机器构建 | 产出 |
|---|---|
| Windows | `dist/AI_chat/AI_chat.exe`（onedir 目录） |
| macOS | `dist/AI Chat.app`（应用包） |
| Linux | `dist/AI_chat/AI_chat`（onedir 目录） |

三台机器用**同一份代码、同一份 `AI_chat.spec`、同一个 `requirements.txt`（版本锁定）**，
只是各自在本机执行构建。

---

## 2. 端到端流程（六步）

```
① 备环境  →  ② 锁依赖  →  ③ 构建  →  ④ 冒烟验证  →  ⑤ 修复循环  →  ⑥ 分发
```

### ① 备环境
- 装 64 位 Python（建议 3.11–3.13；本机 macOS 用的是 3.14，PySide6 6.12 有对应 wheel 即可）。
- 建虚拟环境：`python -m venv .venv`，激活它。

### ② 锁依赖
```bash
pip install -r requirements.txt
```
`requirements.txt` 已**精确锁版本**（`PySide6==6.12.0`、`requests==2.34.2`、
`certifi==2026.10.23`、`pyinstaller==6.22.3`）。三平台务必装同一版本——
Qt 插件的 ABI 在小版本间会变，版本漂移是"打包后功能错误"的常见根因。

### ③ 构建
```bash
pyinstaller AI_chat.spec --noconfirm --clean
```
- `--clean`：清掉上次构建缓存，避免陈旧文件混入。
- 产物在 `dist/`。**用 onedir（目录），不要用 onefile**——WebEngine 在 onefile 下
  解压慢且辅助进程路径更易出错。

### ④ 冒烟验证（最关键，别省）
构建成功 ≠ 能跑。**每次构建后都要实际启动一次**，看控制台有没有报错：
- macOS/Linux：从终端直接跑可执行文件，stderr 会打印缺库/导入错误。
- Windows：双击后若闪退，改用 `cmd` 里跑 `dist\AI_chat\AI_chat.exe` 看报错。

再走一遍 [BUILD.md](BUILD.md) 末尾的「打包后自检清单」（发消息、联网搜索、公式、
PDF 附件、导出、文生图、重启保留配置）。

### ⑤ 修复循环
验证发现问题 → 改 `AI_chat.spec` 或源码 → 回到 ③ 重建 → 再验证。
本项目就是靠这个循环揪出 3 个真实打包错误（见下节）。

### ⑥ 分发
- macOS：签名 + 公证（否则 Gatekeeper 拦），见 [BUILD.md](BUILD.md)。
- Windows：建议代码签名（减少 SmartScreen / 杀软误报）。
- Linux：可进一步打成 AppImage / Flatpak，见 [BUILD_WINDOWS_LINUX.md](BUILD_WINDOWS_LINUX.md)。

---

## 3. 本项目踩过的坑（打包确实是门艺术）

这三个都是"构建 exit 0、但一跑就废"的典型，全靠**构建后冒烟测试**发现：

1. **启动即 `ImportError: could not import PySide6.QtWebChannel`**
   `excludes` 为了瘦身排掉了 `QtWebChannel / QtWebSockets / QtQml / QtQuick /
   QtOpenGL / QtPositioning`，但这些是 **QtWebEngine 运行时必需**。
   → 修法：把整条 WebEngine 依赖链从排除列表移除（[AI_chat.spec](../AI_chat.spec) 的 `excludes`）。
   教训：裁剪体积前，先搞清楚被裁模块是不是某个保留模块的运行时依赖。

2. **手动 `collect_all` 反而搞坏布局**
   本想用 `collect_all("PySide6.QtWebEngineCore")` 兜底收集资源，结果把
   `QtWebEngineProcess.app` 放到了错误路径。
   → 修法：删掉手动收集，**信任 PyInstaller 内置的 PySide6 hook**（它对 Qt 框架最熟）。
   教训：内置 hook 已覆盖的，别画蛇添足。

3. **macOS 上 `could not find QtWebEngineProcess / resources`**
   PyInstaller + PySide6 6.12 会把 WebEngine 的辅助进程和 `.pak`/`icudtl` 错放到
   framework 的 `Versions/Resources/` 下，而根软链指向 `Versions/A/`（悬空）。
   → 修法：在 [app.py](../AI_chat/app.py) 加 `_fix_webengine_paths()`，**打包后的 macOS**
   启动时自动 glob 定位真实路径，设好 `QTWEBENGINEPROCESS_PATH` /
   `QTWEBENGINE_RESOURCES_PATH` / `QTWEBENGINE_LOCALES_PATH`，在 `QApplication` 之前生效。
   这是"自愈"写法：路径变了也能自己找到，重建不用改。
   教训：Qt 的报错信息通常会直接告诉你该设哪个环境变量——顺着它给的路径排查。

> 另外两个"沉默的坑"已提前防住：
> - **certifi 证书**：spec 里 `collect_data_files("certifi")`，否则打包后所有 HTTPS 请求 SSLError。
> - **UPX 压缩**：spec 里 `upx=False`，压 WebEngine 的 dylib/dll 会导致加载失败。

---

## 4. 关键文件一览

| 文件 | 作用 |
|---|---|
| [AI_chat.spec](../AI_chat.spec) | 打包配置：随包资源、隐式导入、排除项、macOS BUNDLE |
| [requirements.txt](../requirements.txt) | 锁定版本的依赖（含 pyinstaller） |
| [app_paths.py](../AI_chat/app_paths.py) | `resource_dir()` 处理 frozen 后的资源路径；用户数据写系统目录 |
| [app.py](../AI_chat/app.py) `_fix_webengine_paths()` | macOS WebEngine 路径自愈 |
| [theme.py](../AI_chat/theme.py) | 明暗主题引擎：按 `@RES@` 拼资源路径加载 QSS + 调色板，缺文件时静默跳过 |
| [fonts.py](../AI_chat/fonts.py) | 启动注册随包字体（Linux CJK 尤其需要） |
| [secrets_store.py](../AI_chat/secrets_store.py) | 系统钥匙串 + 文件回退 |
| [.gitattributes](../.gitattributes) | 换行符策略：仓库与签出一律 LF，字体/图片显式标 binary |
| [.github/workflows/build.yml](../.github/workflows/build.yml) | 前置校验 + 三平台打包（详见 [CI_BUILD.md](CI_BUILD.md) 第五节） |

随包资源（spec 的 `datas`）：`AI_chat/web`（starty.html / chat.html / css / js / vendor KaTeX）、
`AI_chat/resources`（theme.qss、theme_dark.qss、SVG、可选 fonts）、certifi 证书。
新增资源放进这两个目录就会被整目录带走，不用改 spec；但 CI 的「校验随包资源」
一步会盯住关键文件，改名或漏放会在那里报出来。

> `.gitattributes` 为什么必要：`starty.html` 与 `material/STARTY.html` 要逐字节一致，
> 而 Windows 机器上 `core.autocrlf=true` 会在签出时把 LF 改成 CRLF，让这条比对
> 和 `.ui`/生成物比对在 Windows 上凭空失败。

用户数据（附件、图片、缓存）**不进包**，写系统数据目录：

| 平台 | 位置 |
|---|---|
| Windows | `%APPDATA%\Graphic_Dev\AI_chat\` |
| macOS | `~/Library/Application Support/Graphic_Dev/AI_chat/` |
| Linux | `~/.local/share/Graphic_Dev/AI_chat/` |

---

## 5. 体积预期

约 **300–500 MB**（本机 macOS 实测 `.app` ≈ 400 MB）。QtWebEngine 占大头，属正常。
想再小只能裁 WebEngine，但公式渲染和网页端都依赖它，不建议。
