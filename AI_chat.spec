# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（在项目根目录）：
    pyinstaller AI_chat.spec --noconfirm

产物：
    dist/AI_chat/            （Windows/Linux，含可执行文件）
    dist/AI Chat.app         （macOS 应用包）
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()          # noqa: F821  (SPECPATH 由 PyInstaller 注入)
SRC = ROOT / "AI_chat"

# ---------------------------------------------------------------- 随包资源
datas = [
    (str(SRC / "web"), "AI_chat/web"),           # chat.html / css / js / vendor(KaTeX)
    (str(SRC / "resources"), "AI_chat/resources"),  # theme.qss / 图标
]

# 可选：随包字体（Linux 上很需要，放 resources/fonts/*.otf 即可）
fonts_dir = SRC / "resources" / "fonts"
if fonts_dir.is_dir():
    datas.append((str(fonts_dir), "AI_chat/resources/fonts"))

# ---------------------------------------------------------------- 隐式依赖
hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtPdf",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",          # QSS 里的 SVG 图标
]

# ---------------------------------------------------------------- 裁剪体积
# PySide6 完整安装 1.2GB，其中绝大部分用不到；逐项排除可让包体小一大截
excludes = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtXml",
    "tkinter", "unittest", "pydoc", "doctest",
]

a = Analysis(                                        # noqa: F821
    [str(SRC / "app.py")],
    pathex=[str(SRC)],                               # 让 import app_paths 等能找到
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)                                    # noqa: F821

exe = EXE(                                           # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI_chat",
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

coll = COLLECT(                                      # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AI_chat",
)

if sys.platform == "darwin":
    app = BUNDLE(                                    # noqa: F821
        coll,
        name="AI Chat.app",
        icon=None,
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
