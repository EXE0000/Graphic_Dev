# -*- coding: utf-8 -*-
"""明暗主题：选 QSS、配调色板、跟随系统切换。

偏好只有三档（跟随系统 / 浅色 / 深色），存在 QSettings 的 theme_mode 里。
真正生效的是解析出来的 light 或 dark：Qt 端用它挑 QSS 和 QPalette，
聊天页（WebEngine）用同一个值切 <html data-theme>，两边不会各走各的。
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

import app_paths
import fonts

RES_DIR = app_paths.resource_dir() / "resources"

MODES = ("auto", "light", "dark")
MODE_ITEMS = (("跟随系统", "auto"), ("浅色", "light"), ("深色", "dark"))

# QPalette 管的是 QSS 盖不到的地方：占位符文字、失去焦点时的选中色、
# 以及个别仍然走原生绘制的细节。数值和两套 QSS 保持一致。
_TOKENS = {
    "light": {
        "Window": "#f5f6f8",
        "WindowText": "#1f2328",
        "Base": "#ffffff",
        "AlternateBase": "#fafbfc",
        "Text": "#1f2328",
        "Button": "#ffffff",
        "ButtonText": "#1f2328",
        "Highlight": "#cfe4ff",
        "HighlightedText": "#1f2328",
        "ToolTipBase": "#1f2328",
        "ToolTipText": "#ffffff",
        "PlaceholderText": "#9aa3ad",
        "Link": "#0969da",
        "DisabledText": "#a8b0ba",
    },
    "dark": {
        "Window": "#232830",
        "WindowText": "#e6edf3",
        "Base": "#2b313a",
        "AlternateBase": "#262c34",
        "Text": "#e6edf3",
        "Button": "#2b313a",
        "ButtonText": "#e6edf3",
        "Highlight": "#1e3a5f",
        "HighlightedText": "#e6edf3",
        "ToolTipBase": "#3a424d",
        "ToolTipText": "#f2f5f8",
        "PlaceholderText": "#7b8592",
        "Link": "#58a6ff",
        "DisabledText": "#6c757f",
    },
}

_COLOR_ROLES = ("Window", "WindowText", "Base", "AlternateBase", "Text",
                "Button", "ButtonText", "Highlight", "HighlightedText",
                "ToolTipBase", "ToolTipText", "PlaceholderText", "Link")


def resolve(mode):
    """把三档偏好压成实际生效的 "light" / "dark"。"""
    if mode in ("light", "dark"):
        return mode
    hints = QApplication.instance().styleHints()
    if not hasattr(hints, "colorScheme"):
        return "light"              # Qt < 6.5 拿不到系统配色，退回浅色
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def _palette(name):
    tokens = _TOKENS[name]
    palette = QPalette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role in _COLOR_ROLES:
            palette.setColor(group, getattr(QPalette.ColorRole, role),
                             QColor(tokens[role]))
    dimmed = QColor(tokens["DisabledText"])
    for role in ("WindowText", "Text", "ButtonText"):
        palette.setColor(QPalette.ColorGroup.Disabled,
                         getattr(QPalette.ColorRole, role), dimmed)
    return palette


def apply(mode):
    """加载对应的 QSS 和调色板，返回实际生效的 light/dark。"""
    name = resolve(mode)
    app = QApplication.instance()
    if app is None:
        return name

    qss_path = RES_DIR / ("theme_dark.qss" if name == "dark" else "theme.qss")
    if qss_path.exists():
        qss = qss_path.read_text(encoding="utf-8")
        # 这里必须用「普通绝对路径」：Qt 的 QSS 解析器不认 file:///，
        # 会把它当成相对路径拼到当前目录后面，导致 svg 加载失败。
        qss = (qss.replace("@RES@", RES_DIR.as_posix())
                  .replace("@UIFONT@", fonts.ui_font()))
        app.setStyleSheet(qss)
    app.setPalette(_palette(name))
    return name


def page_script(name):
    """聊天页的切主题脚本：只改一个属性，页面不用重新加载。"""
    return "document.documentElement.dataset.theme = %s;" % json.dumps(name)


def watch_system(callback):
    """系统明暗变化时回调；Qt 版本太旧没有这个信号就什么都不做。"""
    hints = QApplication.instance().styleHints()
    changed = getattr(hints, "colorSchemeChanged", None)
    if changed is None:
        return False
    changed.connect(lambda _scheme: callback())
    return True
