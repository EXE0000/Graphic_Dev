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
git add .github/ docs/ AI_chat.spec AI_chat/app.py requirements.txt .gitignore
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
2. 拉到页面底部 **Artifacts** 区，会看到三个：
   - `AI_chat-Windows-x86_64`（.zip）
   - `AI_chat-Linux-x86_64`（.tar.gz）
   - `AI_chat-macOS-arm64`（.zip）
3. 点名字下载（需登录 GitHub）。产物默认保留 **30 天**。

> 每个平台是独立 job，某个失败不影响其它平台出包（`fail-fast: false`）。
> 失败的 job 会显示红 ❌，点进去看日志。

---

## 三、下载后怎么跑（各平台注意）

### Windows（x86_64）
解压 zip → 进 `AI_chat\` → 双击 `AI_chat.exe`。
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

| 步骤 | 作用 |
|---|---|
| 检出代码 | 拉取仓库（含已入库的 spec / requirements / web 前端 vendor） |
| 安装 Python 3.12 | `actions/setup-python`，带 pip 缓存 |
| 安装 Linux 系统依赖 | 仅 Linux：WebEngine 运行/构建所需的 X、NSS、GBM、ALSA 等库 |
| 安装 Python 依赖 | `pip install -r requirements.txt`（版本已锁定） |
| PyInstaller 构建 | `pyinstaller AI_chat.spec --noconfirm --clean` |
| 校验产物 | 确认主程序 + `QtWebEngineProcess` + `.pak` + `icudtl.dat` 都在包里 |
| 打包 | Win→zip / Linux→tar.gz / mac→zip（`ditto` 保留 .app 软链） |
| 上传产物 | `actions/upload-artifact`，保留 30 天 |
| 启动冒烟（仅 Linux） | offscreen 启动，进程存活 20s 即判通过 |

> 冒烟测试只在 Linux 做（无头最可靠）；Windows/macOS 的 CI 是无界面环境，
> 强行跑 GUI 反而不稳，故只做"产物文件校验"。**完整功能测试请下载产物在真机上做**，
> 走 [BUILD.md](BUILD.md) 末尾的自检清单。

---

## 小技巧：一条 git push 同时推 Gitee 和 GitHub
```bash
# 让 origin 的 push 同时发往两个地址
git remote set-url --add --push origin https://gitee.com/jh2023/graphic_-dev.git
git remote set-url --add --push origin https://github.com/<你的用户名>/graphic_dev.git
```
之后 `git push origin main` 会同时推两边（Gitee 存档 + GitHub 触发 CI）。
