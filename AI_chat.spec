# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（在项目根目录）：
    pyinstaller AI_chat.spec --noconfirm

    # 想要"一个文件就是软件"（目前只在 CI 里对 Windows 这么做）：
    AI_CHAT_ONEFILE=1 pyinstaller AI_chat.spec --noconfirm \
        --distpath dist_onefile --workpath build_onefile

产物：
    dist/AI_chat/            （Windows/Linux，含可执行文件）
    dist/AI Chat.app         （macOS 应用包）
    dist_onefile/AI_chat.exe （单文件模式，只有一个可执行文件）
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve()          # noqa: F821  (SPECPATH 由 PyInstaller 注入)
SRC = ROOT / "AI_chat"

# 单文件模式：设 AI_CHAT_ONEFILE=1 后产物是一个 exe（内部资源启动时解到临时目录）。
# 默认还是 onedir（一个文件夹），因为单文件每次启动都要解包几百 MB，明显更慢。
ONEFILE = os.environ.get("AI_CHAT_ONEFILE", "").strip().lower() in ("1", "true", "yes")

# ---------------------------------------------------------------- 随包资源
datas = [
    (str(SRC / "web"), "AI_chat/web"),           # chat.html / css / js / vendor(KaTeX)
    (str(SRC / "resources"), "AI_chat/resources"),  # theme.qss / 图标
]

# 可选：随包字体（Linux 上很需要，放 resources/fonts/*.otf 即可）
fonts_dir = SRC / "resources" / "fonts"
if fonts_dir.is_dir():
    datas.append((str(fonts_dir), "AI_chat/resources/fonts"))

# 运行时窗口/任务栏图标（由 icons/logo.svg 生成，见 icons/make_icons.py）
datas.append((str(ROOT / "icons" / "appicon.png"), "AI_chat/icons"))

# ---------------------------------------------------------------- 软件图标
# 三平台各认一种格式：Windows 要 .ico，macOS 要 .icns，Linux 的 tar.gz 没有
# 图标注册机制，只能靠随包的 png + 桌面入口自己去指。
ICON_DIR = ROOT / "icons"
ICON_CANDIDATES = {
    "win32": "appicon.ico",
    "darwin": "appicon.icns",
}


def _icon_path():
    """返回当前平台能用的图标文件路径，没有就返回 None（不阻断打包）。"""
    name = ICON_CANDIDATES.get(sys.platform)
    if name is None:
        return None                       # Linux：exe 上没有图标这一说
    path = ICON_DIR / name
    if not path.is_file():
        print("警告：找不到 %s，本次打包不带软件图标；"
              "跑一次 python icons/make_icons.py 生成" % path)
        return None
    return str(path)


ICON = _icon_path()

binaries = []

# ---------------------------------------------------------------- 隐式依赖
hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtPdf",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",          # QSS 里的 SVG 图标
]

# certifi 的 CA 根证书：requests 走 HTTPS 必需，漏掉会"打包后所有网络请求 SSLError"
datas += collect_data_files("certifi")

# 注意：不要对 PySide6.QtWebEngine* 手动 collect_all——PyInstaller 内置的
# PySide6 hook 已经会正确摆放 QtWebEngineCore.framework（含 Helpers/
# QtWebEngineProcess.app）。手动收集会把辅助进程放到 Versions/Resources/
# 这种错误路径，导致运行时 "could not find QtWebEngineProcess"。


# ---------------------------------------------------------------- 裁剪体积
# PySide6 完整安装 1.2GB，其中绝大部分用不到；逐项排除可让包体小一大截
# 注意：不要排除 QtWebEngine 的依赖链——QtWebChannel / QtWebSockets / QtQml /
# QtQuick / QtPositioning / QtOpenGL 都是 WebEngine 运行时必需，排掉会导致
# 打包后启动即 ImportError（libshiboken: could not import ...）。
excludes = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc", "PySide6.QtQuick3D", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtXml",
    "tkinter", "unittest", "pydoc", "doctest",
]

a = Analysis(                                        # noqa: F821
    [str(SRC / "app.py")],
    pathex=[str(SRC)],                               # 让 import app_paths 等能找到
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)                                    # noqa: F821

if ONEFILE:
    # 单文件：binaries/datas 一起塞进 exe，启动时解到 %TEMP%\_MEIxxxx（macOS/Linux
    # 是 /tmp/_MEIxxxx），所以 app_paths.resource_dir() 走 sys._MEIPASS 那条分支即可。
    # 代价是每次启动都要解包（包体 300–500MB，慢机器上首屏要等十几秒到几十秒），
    # 且未签名的单文件更容易被 SmartScreen/杀软拦——日常自用建议还是 onedir。
    exe = EXE(                                       # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="AI_chat",
        icon=ICON,                 # 单文件 exe 的图标同样由 ICON 决定
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,             # 不弹黑框
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(                                       # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="AI_chat",
        icon=ICON,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                 # WebEngine 的 dylib 压了反而容易出问题
        console=False,             # 不弹黑框
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    coll = COLLECT(                                  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="AI_chat",
    )

    if sys.platform == "darwin":
        app = BUNDLE(                                # noqa: F821
            coll,
            name="AI Chat.app",
            icon=ICON,
            bundle_identifier="com.graphicdev.aichat",
            info_plist={
                "CFBundleName": "AI Chat",
                "CFBundleDisplayName": "AI Chat",
                "NSHighResolutionCapable": True,
                "LSMinimumSystemVersion": "12.0",
                # 允许访问用户选择的文件（附件）
                "NSDocumentsFolderUsageDescription": "读取你选择的附件用于分析",
                "NSDownloadsFolderUsageDescription": "保存生成的图片和文档",
            },
        )
