# GitHub Actions 自动打包指引

> 一条 workflow 同时在 **Windows / Linux / macOS** 三个真 x86_64（mac 为 arm64）环境里
> 自动构建，产物（artifact）可直接下载。**你不用再装 Windows 虚拟机。**
>
> workflow 文件：[.github/workflows/build.yml](../.github/workflows/build.yml)
> 本地打包见 [PACKAGING.md](PACKAGING.md)，手动跨平台见 [BUILD_WINDOWS_LINUX.md](BUILD_WINDOWS_LINUX.md)。

---

## ⚠️ 前提：你的仓库现在在 Gitee，GitHub Actions 只在 GitHub 上跑

当前远程是 `gitee.com/jh2023/graphic_-dev`。**Gitee 不会执行 `.github/workflows/`**。
三选一：

| 方案 | 说明 | 推荐度 |
|---|---|---|
| **A. 在 GitHub 建镜像仓库** | Gitee 继续当主仓库，额外 push 一份到 GitHub 专门跑 CI | ⭐ 最省事 |
| B. 整体迁移到 GitHub | 把项目主仓库搬到 GitHub | 想长期用 GitHub 就选它 |
| C. 用 Gitee Go | Gitee 自家 CI，但**没有 macOS 构建机**、免费额度少、YAML 语法不同 | 不推荐（凑不齐三平台） |

下面按**方案 A**走。

---

## 一、启用步骤（方案 A：加一个 GitHub 镜像）

### 1. 在 GitHub 建一个空仓库
登录 github.com → New repository → 起个名（如 `graphic_dev`）→ **不要**勾
"Add README / .gitignore / license"（保持空仓库，避免推送冲突）。建好后复制它的地址，
形如 `https://github.com/<你的用户名>/graphic_dev.git`。

### 2. 把本地项目加上 GitHub 远程并推送
在项目根目录执行（把 URL 换成你自己的）：
```bash
# 现有的 Gitee 远程叫 origin，保持不动；新增一个叫 github 的远程
git remote add github https://github.com/<你的用户名>/graphic_dev.git

# 提交本次新增的 workflow 和文档
git add README.md .gitignore .gitattributes .github/ docs/ AI_chat.spec AI_chat/app.py requirements.txt
git commit -m "ci: 三平台 GitHub Actions 自动打包 + 打包文档"

# 推到 GitHub（第一次推 main 分支）
git push github main
```
> 之后日常：改完代码 `git push origin main`（Gitee）+ `git push github main`（触发 CI）。
> 嫌麻烦可设 `git push` 同时推两个远程，见文末「小技巧」。

### 3. 触发构建
- push 到 `main` 会**自动触发**；
- 或到 GitHub 仓库 → **Actions** 标签页 → 左侧选「跨平台打包」→ 右侧
  **Run workflow** 按钮手动触发（对应 `workflow_dispatch`）；
- 打 `v*` 标签也会触发（发版用）。

第一次进 Actions 页面可能需要点一下 "I understand my workflows, go ahead and enable them"。

---

## 二、下载产物

1. Actions → 点进那次运行（run）。
2. 拉到页面底部 **Artifacts** 区，会看到四个：
   - `AI_chat-Windows-x86_64`（.zip，解压是一个文件夹）
   - `AI_chat-Windows-x86_64-onefile`（`AI_chat.exe` 单文件，双击即用）
   - `AI_chat-Linux-x86_64`（.tar.gz）
   - `AI_chat-macOS-arm64`（.zip）
3. 点名字下载（需登录 GitHub）。产物默认保留 **30 天**。

> 每个平台是独立 job，某个失败不影响其它平台出包（`fail-fast: false`）。
> 失败的 job 会显示红 ❌，点进去看日志。

### Windows 为什么给两份

| | 文件夹版（onedir） | 单文件版（onefile） |
|---|---|---|
| 长得像软件 | ❌ 一个文件夹 + 一堆 dll | ✅ 就一个 `AI_chat.exe` |
| 启动速度 | 快，直接跑 | 慢，每次启动要把 300–500 MB 解到 `%TEMP%\_MEIxxxxxx`，机械硬盘/杀软扫描下首屏十几秒到几十秒 |
| 磁盘占用 | 解压后那份 | 临时目录里再多一份解包，退出才清理 |
| 杀软/SmartScreen | 一般 | 单文件自解压更容易被判定为可疑，误报率更高 |
| 适合 | 自己天天用 | 发给别人、截图好看 |

两个版本功能完全一样，是同一个 spec 用环境变量 `AI_CHAT_ONEFILE=1` 各构建一次；
本地想要单文件版：

```bash
set AI_CHAT_ONEFILE=1        # PowerShell 用 $env:AI_CHAT_ONEFILE="1"
pyinstaller AI_chat.spec --noconfirm --distpath dist_onefile --workpath build_onefile
# → dist_onefile\AI_chat.exe
```

CI 里单文件版有一次 offscreen 启动冒烟，但**先设成不卡门禁**（`continue-on-error`）：
Windows 无头会话起 GUI 本来就不稳，而且单文件 + WebEngine 是新链路，先观察几轮。
你在真机上确认 `AI_chat.exe` 能正常打开、聊天页能渲染，就把那一步的
`continue-on-error: true` 删掉，让它变成硬校验。

---

## 二·五、发版：打 tag 自动建 Release

推 `v*` 标签时，三平台产物上传完会自动多出一个 `release` job，把三个包挂到
GitHub Release 上（普通 push `main` 不会发版）：

```bash
git tag -a v1.3.0 -m "1.3.0：暗色主题 + 附件系统预览"
git push github v1.3.0
```

- Release 的说明由 `--generate-notes` 根据标签之间的 PR/commit 自动生成，随后可在
  GitHub 上手动润色。
- **任何一个平台构建失败，release job 整个跳过**（`needs: build`），不会发出半个版本；
  修好后重推同一个 tag，Release 已存在时会用 `--clobber` 覆盖同名产物，不会报错。
- 只有这个 job 带 `contents: write` 权限，`checks` 和 `build` 仍是只读。
- 想改版本号：删掉远端 tag（`git push github :refs/tags/v1.3.0`）再重新打。

> 依赖提醒：`.github/dependabot.yml` 会每周检查 pip 依赖、每月检查 Actions 版本。
> 本仓库版本是刻意锁死的（PySide6 一动就可能重新踩打包坑），所以 dependabot 只开 PR、
> 不自动合并；合并前请在三平台各跑一次打包冒烟。

---

## 三、下载后怎么跑（各平台注意）

### Windows（x86_64）
**文件夹版**：解压 zip → 进 `AI_chat\` → 双击 `AI_chat.exe`。
**单文件版**：下 `AI_chat-Windows-x86_64-onefile`，拿到就是一个 `AI_chat.exe`，双击即可
（第一次启动会先解包，慢十几秒到几十秒；见第二节「Windows 为什么给两份」）。
- 目标机需 **Windows 10 1809+**。
- 未签名，SmartScreen 可能拦 → 点「更多信息 → 仍要运行」。
- 极精简系统若报缺 `MSVCP140.dll`，装「VC++ 2015–2022 Redistributable (x64)」。

### Linux（x86_64）
```bash
tar -xzf AI_chat-Linux-x86_64.tar.gz
# 装 WebEngine 运行所需系统库（Debian/Ubuntu）
sudo apt install -y libnss3 libnspr4 libxcomposite1 libxdamage1 libxrandr2 \
  libxtst6 libgbm1 libxkbcommon0 libegl1 libgl1 libasound2t64 fonts-noto-cjk
./AI_chat/AI_chat
```
- 无桌面/容器里崩：`QTWEBENGINE_DISABLE_SANDBOX=1 ./AI_chat/AI_chat`。
- glibc 不能打包：CI 在 ubuntu-latest（较新）上构建，拿到**更老**的发行版上可能跑不起，
  老系统建议本地构建或换更老的构建镜像。

### macOS（arm64 / Apple Silicon）
解压 zip 得到 `AI Chat.app`。**因为是从网上下载的，带 quarantine 属性，直接开会被拦**：
```bash
xattr -cr "AI Chat.app"      # 清除隔离属性
open "AI Chat.app"
```
- 这是 **arm64（Apple Silicon）** 版，**Intel Mac 跑不了**。要 Intel 版见下节。
- 未做 Developer ID 签名/公证，只能这样本地放行；对外分发仍需签名公证（见 [BUILD.md](BUILD.md)）。

---

## 四、常见调整

### 想要 Intel（x86_64）mac 版
在 [build.yml](../.github/workflows/build.yml) 的 `matrix.include` 里加一项：
```yaml
          - os: macos-13         # macos-13 是 Intel x86_64 runner
            label: macOS-x86_64
            artifact: AI_chat-macOS-x86_64
```
> 注意 GitHub 会逐步下线老的 `macos-13`；若不可用，Intel 版就在真 Intel Mac 上本地构建。

### 省 minutes（构建额度）
- **公开仓库**：GitHub Actions 免费、基本无限额，随便跑。
- **私有仓库**：每月 2000 分钟免费额度，且 **macOS 按 10 倍计、Windows 按 2 倍计**。
  三平台一次构建可能吃掉不少。想省，把触发条件改窄——只手动 + 打标签触发：
  ```yaml
  on:
    workflow_dispatch:
    push:
      tags: [ 'v*' ]        # 去掉 branches: [ main ]，日常 push 就不再自动构建
  ```

### CI 用的 Python 版本
workflow 里固定 **3.12**（PySide6 6.12 在 Win/Linux/mac 都有 cp312 wheel，最稳），
和你本机的 3.14 不同没关系——**每个平台各自独立构建**，Python 小版本不需要一致。
代码只用到 3.10+ 语法，3.12 完全兼容。

### 中文字体
CI 的 Linux 机装了 `fonts-noto-cjk` 供**冒烟测试**用，但**产物本身不内置 CJK 字体**
（`AI_chat/resources/fonts/` 里的字体被 .gitignore 排除，没进仓库）。
若要让 Linux 产物在没装字体的机器上也正常显示中文，构建前把
`NotoSansCJKsc-Regular.otf` 放进 `AI_chat/resources/fonts/`（随包自动注册）。

---

## 五、workflow 都做了什么（对照排查）

分两个 job：`前置校验`（几秒钟）→ `构建`（三平台矩阵）。前置校验没过就不会浪费三平台的额度。

### job 1：前置校验（`checks`）

| 步骤 | 作用 |
|---|---|
| Python 语法编译 | `python -m compileall -q AI_chat`，抓缩进/语法错 |
| 校验外壳 HTML 未被改动 | `cmp AI_chat/web/starty.html material/STARTY.html`，红线：外壳必须逐字节一致，新功能只能加在 `app.js` / `app.css` 外挂层 |
| 校验前端脚本语法 | `node --check` 跑 `app.js` / `chat.js` / `webchat.js`，避免漏个括号只在运行时报 |
| 校验主题与图标引用 | 明暗两套 `theme.qss` / `theme_dark.qss` 都在、`@RES@` 占位符没丢、QSS 里引用的 svg 真的存在（写错不报错，只是图标运行时静默消失） |
| 扫描误提交的密钥 | 在 `AI_chat/`、`docs/` 里找 `sk-…`/`ghp_…`/`AKIA…` 这类形态的串；密钥只该待在客户端钥匙串（`AI_chat/secrets_store.py`） |

### job 2：三平台构建（`build`，`needs: checks`）

| 步骤 | 作用 |
|---|---|
| 检出代码 | 拉取仓库（含已入库的 spec / requirements / web 前端 vendor） |
| 安装 Python 3.12 | `actions/setup-python`，带 pip 缓存 |
| 安装 Linux 系统依赖 | 仅 Linux：WebEngine 运行/构建所需的 X、NSS、GBM、ALSA 等库 |
| 安装 Python 依赖 | `pip install -r requirements.txt`（版本已锁定） |
| 校验 .ui 与生成代码同步 | 用 `pyside6-uic` 重新生成到临时目录和入库的 `UI/*.py` 比对；只改了 `.ui` 忘了重新生成会在这里失败（本仓库运行时装的是生成物）。比对前先剥 `\r`：Windows 上 uic 写的是 CRLF，逐字节比会整篇不同 |
| PyInstaller 构建 | `pyinstaller AI_chat.spec --noconfirm --clean`（文件夹版） |
| PyInstaller 构建（Windows 单文件） | 仅 Windows：带 `AI_CHAT_ONEFILE=1` 再构建一次，输出到 `dist_onefile`/`build_onefile`，避免和文件夹版互相覆盖 |
| 校验单文件产物（Windows） | 确认 `dist_onefile` 里**只有一个** `AI_chat.exe`——多出来文件说明 onefile 没生效 |
| 校验产物 | 确认主程序 + `QtWebEngineProcess` + `.pak` + `icudtl.dat` 都在包里 |
| 校验随包资源 | `find dist -name …` 确认 `theme_dark.qss`、`starty.html`、`app.js`、KaTeX 等进了包；缺了的表现是"包能启动但界面是坏的" |
| 打包 | Win→zip / Linux→tar.gz / mac→zip（`ditto` 保留 .app 软链） |
| 上传产物 | `actions/upload-artifact`，保留 30 天；Windows 额外上传 `-onefile` 那个产物 |
| 启动冒烟（Linux + macOS） | offscreen 启动产物，进程存活 20s 即判通过 |
| 启动冒烟（Windows 单文件） | offscreen 跑单文件 exe 60s，**当前 `continue-on-error` 只观察不卡门禁** |

### job 3：发版（`release`，只在推 `v*` 标签时跑）

| 步骤 | 作用 |
|---|---|
| 下载三平台产物 | `actions/download-artifact`，拿到 zip / tar.gz |
| 创建或补全 Release | `gh release create --generate-notes`；同名 Release 已存在则 `--clobber` 覆盖上传 |

> 冒烟测试在 Linux 和 macOS 用 `QT_QPA_PLATFORM=offscreen` 做（无头最可靠）。
> Windows 无头会话里起 GUI 进程不稳，故只做产物与资源校验；要是 macOS 那步在
> runner 上表现不稳，直接删掉对应 step 即可——产物上传在它之前就已经完成了。
> **完整功能测试请下载产物在真机上做**，走 [BUILD.md](BUILD.md) 末尾的自检清单。

---

## 小技巧：一条 git push 同时推 Gitee 和 GitHub
```bash
# 让 origin 的 push 同时发往两个地址
git remote set-url --add --push origin https://gitee.com/jh2023/graphic_-dev.git
git remote set-url --add --push origin https://github.com/<你的用户名>/graphic_dev.git
```
之后 `git push origin main` 会同时推两边（Gitee 存档 + GitHub 触发 CI）。
