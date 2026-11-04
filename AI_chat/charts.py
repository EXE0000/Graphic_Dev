# -*- coding: utf-8 -*-
"""用 QPainter 画图表（柱状/折线/饼图），输出 QImage。

不依赖 matplotlib / QtCharts：自己画坐标轴、网格、图例、数值标签，
这样导出的文档里能出现真正的图片，而不是一张表格。
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPen,
                           QPolygonF)

import fonts

WIDTH, HEIGHT = 900, 520
PALETTE = ["#0a84ff", "#34c759", "#ff9f0a", "#ff375f", "#af52de", "#5ac8fa"]
BG = QColor("#ffffff")
FG = QColor("#1f2328")
MUTED = QColor("#8b949e")
GRID = QColor("#e8ebef")


def _font(size: int, bold: bool = False) -> QFont:
    f = QFont(fonts.cjk_font(), size)
    f.setBold(bold)
    return f


def _nice_max(value: float) -> float:
    """把坐标轴上限取整到好看的刻度。"""
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for mul in (1, 2, 2.5, 5, 10):
        if value <= base * mul:
            return base * mul
    return value


def render_chart(spec: dict, width: int = WIDTH, height: int = HEIGHT) -> QImage:
    kind = (spec.get("kind") or "bar").lower()
    labels = [str(x) for x in (spec.get("labels") or [])]
    series = spec.get("series") or []
    title = str(spec.get("title") or "")

    img = QImage(width, height, QImage.Format.Format_RGB32)   # 无 alpha，避免嵌入后边缘发灰
    img.fill(BG)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    top = 70 if title else 34
    if title:
        p.setPen(QPen(FG))
        p.setFont(_font(15, True))
        p.drawText(QRectF(0, 18, width, 34), Qt.AlignmentFlag.AlignCenter, title)

    if kind == "pie":
        _draw_pie(p, spec, QRectF(40, top, width - 80, height - top - 40))
    else:
        _draw_cartesian(p, kind, labels, series,
                        QRectF(70, top, width - 110, height - top - 60))

    # 收一圈浅色描边：Word/PDF 里图片边缘本身会有一根细线，
    # 画成设计的一部分，比让它自然出现更整齐
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#e3e6ea"), 1))
    p.drawRect(QRectF(0.5, 0.5, width - 1, height - 1))

    p.end()
    return img


# ---------------------------------------------------------------- 柱状/折线
def _draw_cartesian(p: QPainter, kind: str, labels, series, area: QRectF):
    values = [v for s in series for v in (s.get("values") or []) if isinstance(v, (int, float))]
    vmax = _nice_max(max(values) if values else 1)

    # 网格 + y 轴刻度
    p.setFont(_font(9))
    ticks = 5
    for i in range(ticks + 1):
        y = area.bottom() - area.height() * i / ticks
        p.setPen(QPen(GRID, 1))
        p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        p.setPen(QPen(MUTED))
        p.drawText(QRectF(6, y - 9, area.left() - 12, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   _fmt(vmax * i / ticks))

    # 坐标轴
    p.setPen(QPen(QColor("#c9ced6"), 1.2))
    p.drawLine(QPointF(area.left(), area.top()), QPointF(area.left(), area.bottom()))
    p.drawLine(QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()))

    if not labels:
        labels = [str(i + 1) for i in range(len(series[0].get("values") or []))] if series else []

    n = max(1, len(labels))
    slot = area.width() / n

    if kind == "bar":
        groups = max(1, len(series))
        bar_w = slot * 0.72 / groups
        for si, s in enumerate(series):
            color = QColor(PALETTE[si % len(PALETTE)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            for i, value in enumerate(s.get("values") or []):
                if not isinstance(value, (int, float)):
                    continue
                h = area.height() * value / vmax
                x = area.left() + slot * i + slot * 0.14 + bar_w * si
                p.drawRoundedRect(QRectF(x, area.bottom() - h, bar_w, h), 4, 4)
                p.setPen(QPen(MUTED))
                p.setFont(_font(8))
                p.drawText(QRectF(x - 10, area.bottom() - h - 20, bar_w + 20, 16),
                           Qt.AlignmentFlag.AlignCenter, _fmt(value))
                p.setPen(Qt.PenStyle.NoPen)
    else:  # line
        for si, s in enumerate(series):
            color = QColor(PALETTE[si % len(PALETTE)])
            pts = []
            for i, value in enumerate(s.get("values") or []):
                if not isinstance(value, (int, float)):
                    continue
                x = area.left() + slot * (i + 0.5)
                y = area.bottom() - area.height() * value / vmax
                pts.append(QPointF(x, y))
            if len(pts) >= 2:
                p.setPen(QPen(color, 2.4))
                p.drawPolyline(QPolygonF(pts))
            p.setBrush(QBrush(BG))
            p.setPen(QPen(color, 2))
            for pt in pts:
                p.drawEllipse(pt, 3.6, 3.6)

    # x 轴标签
    p.setPen(QPen(MUTED))
    p.setFont(_font(9))
    for i, label in enumerate(labels):
        x = area.left() + slot * i
        p.drawText(QRectF(x, area.bottom() + 6, slot, 20),
                   Qt.AlignmentFlag.AlignCenter, label[:12])

    _draw_legend(p, series, area)


# ---------------------------------------------------------------- 饼图
def _draw_pie(p: QPainter, spec: dict, area: QRectF):
    labels = [str(x) for x in (spec.get("labels") or [])]
    series = spec.get("series") or [{}]
    values = [v for v in (series[0].get("values") or []) if isinstance(v, (int, float))]
    total = sum(values) or 1

    size = min(area.width() * 0.55, area.height()) - 20
    box = QRectF(area.left() + 10, area.center().y() - size / 2, size, size)

    start = 90 * 16
    for i, value in enumerate(values):
        span = -int(360 * 16 * value / total)
        p.setBrush(QBrush(QColor(PALETTE[i % len(PALETTE)])))
        p.setPen(QPen(BG, 2))
        p.drawPie(box, start, span)
        start += span

    # 图例
    p.setFont(_font(9))
    lx = box.right() + 30
    ly = box.top() + 10
    for i, (label, value) in enumerate(zip(labels, values)):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(PALETTE[i % len(PALETTE)])))
        p.drawRoundedRect(QRectF(lx, ly + i * 26, 12, 12), 3, 3)
        p.setPen(QPen(FG))
        p.drawText(QPointF(lx + 20, ly + i * 26 + 11),
                   f"{label}  {value} ({value * 100 / total:.0f}%)")


def _draw_legend(p: QPainter, series, area: QRectF):
    names = [s.get("name") for s in series if s.get("name")]
    if len(names) < 2:
        return
    p.setFont(_font(9))
    x = area.left()
    for i, name in enumerate(names):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(PALETTE[i % len(PALETTE)])))
        p.drawRoundedRect(QRectF(x, area.top() - 24, 10, 10), 3, 3)
        p.setPen(QPen(MUTED))
        p.drawText(QPointF(x + 15, area.top() - 15), str(name))
        x += 22 + p.fontMetrics().horizontalAdvance(str(name))


def _fmt(value: float) -> str:
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"
