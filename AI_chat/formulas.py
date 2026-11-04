# -*- coding: utf-8 -*-
"""把 LaTeX 公式渲染成图片：无头 QWebEngineView + 本地 KaTeX → 打印 PDF → 裁切。

导出的 DOCX/PDF 里就能出现真正的公式排版，而不是 $...$ 源码。
"""

import json
import pathlib
import re
import tempfile

from PySide6.QtCore import (QMarginsF, QSize, QSizeF, Qt, QTimer,
                            QEventLoop, QUrl)
from PySide6.QtGui import QImage, QPageLayout, QPageSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

import app_paths

WEB_DIR = app_paths.resource_dir() / "web"

SIZE = 26          # CSS 字号（px）
PAD = 14           # 画布留白（CSS px）：大运算符会溢出盒子，留够再裁
SCALE = 3.0        # PDF 渲染倍率，越大越清晰
TIMEOUT_MS = 15000

_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="vendor/katex.min.css">
<style>
  html, body {{ margin: 0; padding: 0; background: #ffffff; }}
  body {{ padding: {pad}px; display: inline-block; }}
  #out {{ font-size: {size}px; color: #1f2328; white-space: nowrap; }}
</style>
<script src="vendor/katex.min.js"></script>
</head>
<body><div id="out"></div>
<script>
  katex.render({latex_json}, document.getElementById("out"),
               {{throwOnError: false, displayMode: {display}}});
  var r = document.getElementById("out").getBoundingClientRect();
  window.__size = {{"w": Math.ceil(r.width) + {pad} * 2,
                    "h": Math.ceil(r.height) + {pad} * 2}};
</script>
</body></html>"""


class FormulaRenderer:
    """复用一个隐藏的 WebEngine 实例，逐个渲染公式并缓存。"""

    def __init__(self):
        self._view = None
        self._cache = {}
        self._failed = False

    # ------------------------------------------------------------ 内部
    def _ensure_view(self):
        if self._view is not None:
            return self._view
        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        view.resize(1200, 400)
        self._view = view
        return view

    def _load(self, html: str) -> bool:
        view = self._ensure_view()
        result = {"ok": False}
        loop = QEventLoop()
        view.loadFinished.connect(
            lambda ok: (result.__setitem__("ok", ok), loop.quit()))
        # 必须用 QUrl：Windows 下 "file://C:\..." 这种拼接是非法 URL，
        # 会导致 KaTeX 加载失败、公式静默退化成源码
        base = QUrl.fromLocalFile(str(WEB_DIR) + "/")
        view.setHtml(html, base)
        QTimer.singleShot(TIMEOUT_MS, loop.quit)
        loop.exec()
        return result["ok"]

    def _read_size(self):
        view = self._ensure_view()
        payload = {}
        loop = QEventLoop()

        def done(value):
            payload["v"] = value
            loop.quit()

        view.page().runJavaScript("JSON.stringify(window.__size)", 0, done)
        QTimer.singleShot(5000, loop.quit)
        loop.exec()

        try:
            data = json.loads(payload.get("v") or "{}")
        except (TypeError, ValueError):
            data = {}
        return max(60, int(data.get("w", 400))), max(30, int(data.get("h", 80)))

    def _print_pdf(self, width_px: int, height_px: int) -> bytes:
        view = self._ensure_view()
        layout = QPageLayout(
            QPageSize(QSizeF(width_px * 72.0 / 96.0, height_px * 72.0 / 96.0),
                      QPageSize.Unit.Point),
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
        )
        payload = {}
        loop = QEventLoop()

        def done(data):
            payload["pdf"] = bytes(data)
            loop.quit()

        view.page().printToPdf(done, layout)
        QTimer.singleShot(TIMEOUT_MS, loop.quit)
        loop.exec()
        return payload.get("pdf", b"")

    # ------------------------------------------------------------ 对外
    def render(self, latex: str, display: bool = False):
        """返回 QImage；失败返回 None（调用方退回显示源码）。"""
        if self._failed:
            return None

        key = (latex.strip(), display)
        if key in self._cache:
            return self._cache[key]

        html = _TEMPLATE.format(
            latex_json=json.dumps(latex, ensure_ascii=False),
            display="true" if display else "false",
            size=SIZE,
            pad=PAD,
        )
        if not self._load(html):
            self._failed = True
            return None

        width_px, height_px = self._read_size()
        pdf = self._print_pdf(width_px, height_px)
        if not pdf:
            return None

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
                fh.write(pdf)
                tmp_path = fh.name

            doc = QPdfDocument()
            doc.load(tmp_path)
            if doc.pageCount() < 1:
                doc.close()
                return None

            point = doc.pagePointSize(0)
            target = QSize(int(point.width() * SCALE * 1.333),
                           int(point.height() * SCALE * 1.333))
            image = doc.render(0, target)
            doc.close()
        finally:
            if tmp_path:
                try:
                    pathlib.Path(tmp_path).unlink()
                except OSError:
                    pass

        if image.isNull():
            return None

        cropped = self._crop(image)
        self._cache[key] = cropped
        return cropped

    @staticmethod
    def _crop(image: QImage, pad: int = 3) -> QImage:
        """按内容包围盒裁切（用 bytes 正则扫，比逐像素快得多）。"""
        gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
        width, height = gray.width(), gray.height()
        stride = gray.bytesPerLine()
        data = bytes(gray.constBits())

        dark = re.compile(rb"[\x00-\xf9]")      # 灰度 < 250 视为内容
        rows = [data[y * stride:y * stride + width] for y in range(height)]

        top = next((y for y, row in enumerate(rows) if dark.search(row)), None)
        if top is None:
            return image
        bottom = next((y for y in range(height - 1, -1, -1) if dark.search(rows[y])), top)

        left, right = width - 1, 0
        for y in range(top, bottom + 1):
            row = rows[y]
            m = dark.search(row)
            if m:
                left = min(left, m.start())
            m2 = dark.search(row[::-1])
            if m2:
                right = max(right, width - 1 - m2.start())

        left = max(0, left - pad)
        top = max(0, top - pad)
        right = min(width - 1, right + pad)
        bottom = min(height - 1, bottom + pad)
        return image.copy(left, top, right - left + 1, bottom - top + 1)
