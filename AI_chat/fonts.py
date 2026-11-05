# -*- coding: utf-8 -*-
"""跨平台字体选择。

Qt 在缺少字体会自动替换，但导出的 DOCX/PDF/XLSX 里字体名是写死的：
macOS 的 PingFang SC / Menlo 在 Windows、Linux 上并不存在，Word 会替换成
别的字体（中文通常还能显示，但字形和行宽会变）。

这里按当前平台挑一个本地可用的字体，保证"在本机生成、在本机查看"时一致。
"""

import sys
from pathlib import Path

_BUNDLED_CJK = None


def register_bundled_fonts(folder=None) -> list:
    """把 resources/fonts 下的字体注册进 Qt，返回注册到的字体族名。

    主要用于 Linux：很多发行版默认没有中文字体，随包带一个 Noto Sans CJK
    就能保证显示正常（OFL 协议允许随应用分发）。
    """
    global _BUNDLED_CJK
    from PySide6.QtGui import QFontDatabase

    base = Path(folder) if folder else _resource_fonts_dir()
    if base is None or not base.is_dir():
        return []

    families = []
    for path in sorted(list(base.glob("*.ttf")) + list(base.glob("*.otf"))):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id != -1:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))

    for name in families:
        if "cjk" in name.lower() or "noto sans sc" in name.lower():
            _BUNDLED_CJK = name
            break
    if _BUNDLED_CJK is None and families:
        _BUNDLED_CJK = families[0]
    return families


def _resource_fonts_dir():
    try:
        import app_paths
        return app_paths.resource_dir() / "resources" / "fonts"
    except Exception:
        return None


def cjk_font() -> str:
    """正文中文字体。"""
    if _BUNDLED_CJK:
        return _BUNDLED_CJK
    if sys.platform == "darwin":
        return "PingFang SC"
    if sys.platform.startswith("win"):
        return "Microsoft YaHei"
    return "Noto Sans CJK SC"


def mono_font() -> str:
    """等宽字体（代码块）。"""
    if sys.platform == "darwin":
        return "Menlo"
    if sys.platform.startswith("win"):
        return "Consolas"
    return "DejaVu Sans Mono"


def ui_font() -> str:
    """界面字体（QSS 用）。"""
    if sys.platform == "darwin":
        # 不要写 -apple-system：Qt 没有这个字体族，会触发一次昂贵的别名枚举
        return "'PingFang SC', 'Helvetica Neue', Arial, sans-serif"
    if sys.platform.startswith("win"):
        return "'Segoe UI', 'Microsoft YaHei', Arial, sans-serif"
    return "'Noto Sans CJK SC', 'DejaVu Sans', Arial, sans-serif"
