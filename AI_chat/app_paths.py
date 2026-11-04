# -*- coding: utf-8 -*-
"""应用数据目录：附件、搜索缓存等落盘都放这里。

按优先级探测可写目录，避免只读安装目录或受限环境导致功能失效：
    1. 系统应用数据目录（macOS: ~/Library/Application Support）
    2. 家目录隐藏目录 ~/.graphic_dev
    3. 临时目录
"""

import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths

_DATA_DIR = None


def resource_dir() -> Path:
    """只读资源目录（web/、resources/）。

    打包后（PyInstaller）资源被解到 sys._MEIPASS 下，不能再靠 __file__。
    """
    frozen = getattr(sys, "frozen", False)
    if frozen:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "AI_chat"
    return Path(__file__).resolve().parent


def _candidates():
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    if base:
        yield Path(base) / "Graphic_Dev" / "AI_chat"
    yield Path.home() / ".graphic_dev"
    yield Path(__import__("tempfile").gettempdir()) / "graphic_dev"


def data_dir() -> Path | None:
    """返回第一个可写的数据目录；全都不可写时返回 None。"""
    global _DATA_DIR
    if _DATA_DIR is not None:
        return _DATA_DIR

    for candidate in _candidates():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        _DATA_DIR = candidate
        return candidate
    return None


def sub_dir(name: str) -> Path | None:
    root = data_dir()
    if root is None:
        return None
    path = root / name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path
