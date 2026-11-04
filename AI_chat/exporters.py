# -*- coding: utf-8 -*-
"""把模型的 Markdown 回答导出成 md / txt / docx / pdf。

不依赖 python-docx、pandoc 等外部工具：
- md/txt：直接写文本
- pdf：QTextDocument（原生支持 Markdown）→ QPdfWriter
- docx：手写 OOXML（docx 本质是 zip + XML）
"""

import io
import json
import pathlib
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import charts as chart_mod
import fonts
import requests
from formulas import FormulaRenderer
from PySide6.QtCore import QBuffer, QByteArray, QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (QColor, QFont, QImage, QPageLayout, QPageSize,
                           QPainter, QPdfWriter, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument,
                           QTextFormat, QTextImageFormat, QTextLength,
                           QTextTable, QTextTableFormat)
from PySide6.QtPrintSupport import QPrinter  # noqa: F401  (保证 QtPrintSupport 被加载)


# ================================================================ 公式
MATH_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
MATH_INLINE = re.compile(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)")
MATH_TOKEN = re.compile(r"@@MATH(\d+)@@")

_RENDERER = None


def formula_renderer() -> FormulaRenderer:
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = FormulaRenderer()
    return _RENDERER


def extract_formulas(markdown: str):
    """把 $...$ / $$...$$ 换成占位符，返回 (新文本, [(latex, 是否独立公式)])。

    按「代码块 / 非代码块」切分后整体正则匹配，这样跨行的 $$...$$ 也能识别。
    """
    specs = []

    def take_display(match):
        specs.append((match.group(1).strip(), True))
        return f"@@MATH{len(specs) - 1}@@"

    def take_inline(match):
        specs.append((match.group(1).strip(), False))
        return f"@@MATH{len(specs) - 1}@@"

    parts = re.split(r"(```.*?```)", markdown, flags=re.S)
    out = []
    for part in parts:
        if part.startswith("```"):
            out.append(part)                     # 代码块原样保留
            continue
        part = MATH_DISPLAY.sub(take_display, part)
        part = MATH_INLINE.sub(take_inline, part)
        out.append(part)

    return "".join(out), specs


# ================================================================ 引用来源
CITATION_RE = re.compile(r"\[(\d{1,2})\]")


def linkify_citations(markdown: str, sources) -> str:
    """把正文里的 [1][2] 换成指向来源的 Markdown 链接（跳过代码块）。"""
    if not sources:
        return markdown

    parts = re.split(r"(```.*?```)", markdown, flags=re.S)
    out = []
    for part in parts:
        if part.startswith("```"):
            out.append(part)
            continue

        def repl(match):
            index = int(match.group(1)) - 1
            if not (0 <= index < len(sources)):
                return match.group(0)
            url = (sources[index] or {}).get("url") or ""
            if not url:
                return match.group(0)
            return f"[[{match.group(1)}]]({url})"

        out.append(CITATION_RE.sub(repl, part))
    return "".join(out)


def append_sources(markdown: str, sources) -> str:
    """在文末追加「参考来源」，把 URL 以纯文本列出来。"""
    if not sources:
        return markdown

    lines = ["", "## 参考来源", ""]
    for index, source in enumerate(sources, start=1):
        title = (source or {}).get("title") or (source or {}).get("url") or ""
        url = (source or {}).get("url") or ""
        lines.append(f"{index}. [{title}]({url}) — {url}")
    return markdown.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def prepare_with_sources(markdown: str, sources):
    return append_sources(linkify_citations(markdown, sources), sources)


# ================================================================ 图片
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE_MAX_BYTES = 12 * 1024 * 1024


def load_image(src: str):
    """支持本地路径、file:// URL 和 http(s) 链接。"""
    try:
        if src.startswith(("http://", "https://")):
            resp = requests.get(src, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            if resp.status_code != 200:
                return None
            data = resp.content[:IMAGE_MAX_BYTES]
        elif src.startswith("file://"):
            path = pathlib.Path(QUrl(src).toLocalFile())
            if not path.is_file():
                return None
            data = path.read_bytes()[:IMAGE_MAX_BYTES]
        else:
            path = pathlib.Path(src).expanduser()
            if not path.is_file():
                return None
            data = path.read_bytes()[:IMAGE_MAX_BYTES]
    except (OSError, requests.RequestException):
        return None

    image = QImage()
    if not image.loadFromData(data) or image.isNull():
        return None
    return image


def extract_images(markdown: str):
    """把 ![alt](src) 换成占位符，返回 (新文本, [(src, alt)])。"""
    specs = []
    parts = re.split(r"(```.*?```)", markdown, flags=re.S)
    out = []
    for part in parts:
        if part.startswith("```"):
            out.append(part)
            continue

        def take(match):
            specs.append((match.group(2), match.group(1)))
            return f"@@IMG{len(specs) - 1}@@"

        out.append(IMAGE_RE.sub(take, part))
    return "".join(out), specs


# ================================================================ 图表块
# 模型按约定输出：
#   ```chart
#   {"kind":"bar","title":"...","labels":[...],"series":[{"name":"...","values":[...]}]}
#   ```
CHART_FENCE = re.compile(r"```chart[^\n]*\n(.*?)```", re.S)


def extract_charts(markdown: str):
    """把 chart 代码块换成占位符，返回 (新文本, [图表spec])。"""
    specs = []

    def repl(match):
        try:
            spec = json.loads(match.group(1))
        except ValueError:
            return match.group(0)
        if not isinstance(spec, dict):
            return match.group(0)
        specs.append(spec)
        return f"@@CHART{len(specs) - 1}@@"

    return CHART_FENCE.sub(repl, markdown), specs


# ================================================================ md / txt
def _fill_fonts(template: str) -> str:
    """把模板里的 @CJK@ / @MONO@ 换成当前平台可用的字体名。"""
    return (template.replace("@CJK@", fonts.cjk_font())
                    .replace("@MONO@", fonts.mono_font()))


def export_markdown(text: str, path, sources=None) -> None:
    Path(path).write_text(prepare_with_sources(text, sources), encoding="utf-8")


def export_text(text: str, path, sources=None) -> None:
    plain, _ = extract_charts(prepare_with_sources(text, sources))
    Path(path).write_text(strip_markdown(plain), encoding="utf-8")


def strip_markdown(text: str) -> str:
    """把常见 Markdown 标记去掉，得到纯文本。"""
    out = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            line = re.sub(r"^#{1,6}\s+", "", line)
            line = re.sub(r"^>\s?", "", line)
            line = re.sub(r"^\s*[-*+]\s+", "• ", line)
            line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            line = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
            line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        out.append(line)
    return "\n".join(out)


def _png_bytes(image) -> bytes:
    buffer = QByteArray()
    holder = QBuffer(buffer)
    holder.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(holder, "PNG")
    holder.close()
    return bytes(buffer)


# ================================================================ pdf
def _widen_tables(doc: QTextDocument) -> None:
    """Qt 解析出的表格默认按内容宽度，打印时会被挤成窄条——强制铺满。"""
    def walk(frame):
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                fmt = child.format()
                fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                fmt.setBorder(1)
                fmt.setCellPadding(6)
                fmt.setCellSpacing(0)
                child.setFormat(fmt)
            walk(child)
    walk(doc.rootFrame())


def document_title(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _new_resource_name(image) -> str:
    _new_resource_name.counter += 1
    return f"docimage{_new_resource_name.counter}_{image.width()}"


_new_resource_name.counter = 0


def _insert_doc_image(doc: QTextDocument, marker: str, image,
                      width=None, height=None) -> None:
    cursor = doc.find(marker)
    if cursor.isNull():
        return
    name = _new_resource_name(image)
    doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
    fmt = QTextImageFormat()
    fmt.setName(name)
    if height:
        fmt.setHeight(height)
        fmt.setWidth(height * image.width() / max(1, image.height()))
    else:
        fmt.setWidth(width or 560.0)
        fmt.setHeight((width or 560.0) * image.height() / max(1, image.width()))
    cursor.insertImage(fmt)


HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def heading_entries(markdown: str):
    """提取 1-3 级标题（跳过代码块）。"""
    entries, in_code = [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = HEADING_RE.match(line.strip())
        if match:
            entries.append((len(match.group(1)), match.group(2).strip()))
    return entries


def _toc_markdown(entries, pages) -> str:
    """目录写成 Markdown 表格，跟着正文一起排版，避免打乱版面。"""
    lines = ["## 目录", "", "| 章节 | 页码 |", "| --- | --- |"]
    for (level, text), page in zip(entries, pages):
        indent = "　" * (level - 1)
        lines.append(f"| {indent}{text} | {page} |")
    lines.append("")
    lines.append("@@TOCEND@@")      # 占位块：之后强制分页再删掉
    return "\n".join(lines) + "\n\n"


def _style_toc_table(doc: QTextDocument) -> None:
    """目录表格去掉边框、页码右对齐（它是文档里的第一张表）。"""
    tables = []

    def walk(frame):
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                tables.append(child)
            walk(child)

    walk(doc.rootFrame())
    if not tables:
        return

    table = tables[0]
    fmt = table.format()
    fmt.setBorder(0)
    fmt.setCellPadding(2)
    table.setFormat(fmt)

    for row in range(table.rows()):
        cell = table.cellAt(row, 1)
        cursor = cell.firstCursorPosition()
        block_format = cursor.blockFormat()
        block_format.setAlignment(Qt.AlignmentFlag.AlignRight)
        cursor.setBlockFormat(block_format)


def _apply_page_break_before(doc: QTextDocument, marker: str = "@@TOCEND@@") -> None:
    """在目录末尾的占位块上分页，然后清空文字（目录独占一页）。"""
    block = doc.firstBlock()
    while block.isValid():
        if block.text().strip() == marker:
            fmt = block.blockFormat()
            fmt.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
            cursor = QTextCursor(block)
            cursor.setBlockFormat(fmt)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                                QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            return
        block = block.next()


def _heading_pages(doc: QTextDocument, entries, content_h: float):
    """按块位置换算每个标题在第几页（跳过目录里的同名文字）。"""
    layout = doc.documentLayout()
    pages, index = [], 0
    block = doc.firstBlock()

    # 跳过目录区：从带「段前分页」的块之后开始统计
    while block.isValid():
        policy = block.blockFormat().pageBreakPolicy()
        if policy & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore:
            block = block.next()
            break
        block = block.next()

    while block.isValid() and index < len(entries):
        if block.text().strip() == entries[index][1]:
            top = layout.blockBoundingRect(block).top()
            pages.append(int(top // content_h) + 1)
            index += 1
        block = block.next()
    return pages + [1] * (len(entries) - len(pages))


def _build_pdf_doc(markdown, chart_specs, math_specs, image_specs,
                   content_w, content_h, toc, first_heading=""):
    body = markdown
    if toc:
        entries, pages = toc
        body = _toc_markdown(entries, pages) + markdown

    doc = QTextDocument()
    doc.setDefaultFont(QFont(fonts.cjk_font(), 11))
    doc.setMarkdown(body)
    _widen_tables(doc)
    doc.setPageSize(QSizeF(content_w, content_h))

    for i, spec in enumerate(chart_specs):
        _insert_doc_image(doc, f"@@CHART{i}@@", chart_mod.render_chart(spec), width=430.0)
    for j, (latex, display) in enumerate(math_specs):
        image = formula_renderer().render(latex, display)
        if image is not None:
            _insert_doc_image(doc, f"@@MATH{j}@@", image,
                              height=34.0 if display else 15.0)
    for k, (src, _alt) in enumerate(image_specs):
        image = load_image(src)
        if image is not None:
            _insert_doc_image(doc, f"@@IMG{k}@@", image, width=380.0)

    if toc:
        _style_toc_table(doc)
        _apply_page_break_before(doc)

    # 改过块格式后强制重排，否则页码会算错
    doc.setPageSize(QSizeF(content_w, content_h))
    return doc


def export_pdf(text: str, path, sources=None) -> None:
    text = prepare_with_sources(text, sources)
    """Markdown → PDF：带页眉、页脚、页码和目录（两遍排版求页码）。"""
    markdown, chart_specs = extract_charts(text)
    markdown, math_specs = extract_formulas(markdown)
    markdown, image_specs = extract_images(markdown)
    title = document_title(markdown)

    mm = 72.0 / 25.4                     # 1mm 折合多少点
    page_w, page_h = 595.28, 841.89      # A4
    margin = 18 * mm
    header_h = 9 * mm
    footer_h = 9 * mm
    content_w = page_w - margin * 2
    content_h = page_h - margin * 2 - header_h - footer_h

    entries = heading_entries(markdown)
    pages = None
    doc = None
    if entries:
        for _ in range(4):               # 目录插入会推移页码，迭代到稳定
            doc = _build_pdf_doc(markdown, chart_specs, math_specs, image_specs,
                                 content_w, content_h,
                                 (entries, pages) if pages else None,
                                 entries[0][1] if entries else "")
            computed = _heading_pages(doc, entries, content_h)
            if computed == pages:
                break
            pages = computed
    if doc is None:
        doc = _build_pdf_doc(markdown, chart_specs, math_specs, image_specs,
                             content_w, content_h, None, "")

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setTitle(title or "AI 回答")
    dpi = 300
    writer.setResolution(dpi)

    total = max(1, doc.pageCount())
    painter = QPainter(writer)
    painter.scale(dpi / 72.0, dpi / 72.0)

    head_font = QFont()
    head_font.setPixelSize(8)
    foot_font = QFont()
    foot_font.setPixelSize(8)
    muted = QColor("#8b949e")
    rule = QColor("#e3e6ea")

    for index in range(total):
        if index:
            writer.newPage()

        painter.save()
        painter.translate(margin, margin + header_h)
        painter.setClipRect(QRectF(0, 0, content_w, content_h))
        painter.translate(0, -index * content_h)
        doc.drawContents(painter, QRectF(0, index * content_h, content_w, content_h))
        painter.restore()

        painter.setFont(head_font)
        painter.setPen(muted)
        painter.drawText(QRectF(margin, margin * 0.45, content_w, header_h),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         title)
        painter.setPen(rule)
        painter.drawLine(int(margin), int(margin + header_h - 2),
                         int(page_w - margin), int(margin + header_h - 2))

        painter.setFont(foot_font)
        painter.setPen(muted)
        painter.drawText(
            QRectF(margin, page_h - margin - footer_h, content_w, footer_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            f"第 {index + 1} 页 / 共 {total} 页")

    painter.end()


# ================================================================ docx
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
                Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
                Target="styles.xml"/>"""

_DOC_RELS_ITEM = """
  <Relationship Id="{rid}"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
                Target="{target}"/>"""

_DOC_RELS_TAIL = """
</Relationships>"""

_LINK_REL = """
  <Relationship Id="{rid}"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
                Target="{target}" TargetMode="External"/>"""

_INLINE_IMAGE_RUN = """<w:r><w:drawing>
<wp:inline distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="{w}" cy="{h}"/><wp:docPr id="{pid}" name="Math{pid}"/>
<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>
<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:nvPicPr><pic:cNvPr id="0" name="math{pid}.png"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:ln w="0"><a:noFill/></a:ln></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"""

_IMAGE_XML = """<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>
<wp:inline distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="{w}" cy="{h}"/><wp:docPr id="{pid}" name="Chart{pid}"/>
<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>
<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:nvPicPr><pic:cNvPr id="0" name="chart{pid}.png"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:ln w="0"><a:noFill/></a:ln></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"""

_HEADER_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr>
      <w:pBdr><w:bottom w:val="single" w:sz="4" w:space="4" w:color="E3E6EA"/></w:pBdr>
      <w:jc w:val="right"/>
    </w:pPr>
    <w:r><w:rPr><w:sz w:val="16"/><w:color w:val="8B949E"/></w:rPr>
      <w:t xml:space="preserve">{title}</w:t></w:r>
  </w:p>
</w:hdr>"""

_FOOTER_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr><w:jc w:val="center"/></w:pPr>
    <w:r><w:rPr><w:sz w:val="16"/><w:color w:val="8B949E"/></w:rPr>
      <w:t xml:space="preserve">— </w:t></w:r>
    <w:fldSimple w:instr=" PAGE ">
      <w:r><w:rPr><w:sz w:val="16"/><w:color w:val="8B949E"/></w:rPr><w:t>1</w:t></w:r>
    </w:fldSimple>
    <w:r><w:rPr><w:sz w:val="16"/><w:color w:val="8B949E"/></w:rPr>
      <w:t xml:space="preserve"> —</w:t></w:r>
  </w:p>
</w:ftr>"""

_TOC_XML = r"""<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
  <w:r><w:t>目录</w:t></w:r></w:p>
<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>
  <w:r><w:instrText xml:space="preserve"> TOC \o "1-3" \h \z \u </w:instrText></w:r>
  <w:r><w:fldChar w:fldCharType="separate"/></w:r>
  <w:r><w:rPr><w:i/><w:color w:val="8B949E"/></w:rPr>
    <w:t>（在 Word 中右键此处 →「更新域」生成目录）</w:t></w:r>
  <w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
<w:p><w:r><w:br w:type="page"/></w:r></w:p>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr>
      <w:rFonts w:ascii="@CJK@" w:hAnsi="@CJK@" w:eastAsia="@CJK@"/>
      <w:sz w:val="22"/>
      <w:szCs w:val="22"/>
    </w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="30"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
    <w:pPr><w:spacing w:before="160" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="26"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Code">
    <w:name w:val="Code"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/>
           <w:shd w:val="clear" w:fill="F6F8FA"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="@MONO@" w:hAnsi="@MONO@" w:eastAsia="@MONO@"/><w:sz w:val="19"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Quote">
    <w:name w:val="Quote"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="420"/></w:pPr>
    <w:rPr><w:i/><w:color w:val="57606A"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Normal">
    <w:name w:val="Normal"/>
  </w:style>
</w:styles>"""


LINK_RE = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(([^)\s]+)\)")


def _run(text: str, *, bold=False, italic=False, code=False, formulas=None,
         links=None) -> str:
    """生成一段 w:r（支持 **粗体**、*斜体*、`代码`、@@MATHn@@ 公式图片）。"""
    pieces = []
    formulas = formulas or {}
    pattern = re.compile(
        r"(\*\*.+?\*\*|`[^`]+`|\[(?:[^\[\]]|\[[^\[\]]*\])*\]\([^)\s]+\)|"
        r"(?<!\*)\*(?!\*).+?(?<!\*)(?<!\*)\*(?!\*)|@@MATH\d+@@)")
    for part in pattern.split(text):
        if not part:
            continue
        if part.startswith("@@MATH") and part.endswith("@@"):
            pieces.append(formulas.get(int(part[6:-2]), escape(part)))
            continue

        link = LINK_RE.fullmatch(part)
        if link:
            label, url = link.group(1), link.group(2)
            rid = (links or {}).get(url)
            if rid:
                pieces.append(
                    f'<w:hyperlink r:id="{rid}">'
                    '<w:r><w:rPr><w:color w:val="0563C1"/>'
                    '<w:u w:val="single"/></w:rPr>'
                    f'<w:t xml:space="preserve">{escape(label)}</w:t></w:r></w:hyperlink>')
            else:
                pieces.append(f'<w:r><w:t xml:space="preserve">{escape(label)}</w:t></w:r>')
            continue
        b, i, c = bold, italic, code
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            part, b = part[2:-2], True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            part, c = part[1:-1], True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            part, i = part[1:-1], True

        props = []
        if b:
            props.append("<w:b/>")
        if i:
            props.append("<w:i/>")
        if c:
            mono = fonts.mono_font()
            props.append(f'<w:rFonts w:ascii="{mono}" w:hAnsi="{mono}" '
                         f'w:eastAsia="{mono}"/>')
            props.append('<w:shd w:val="clear" w:fill="F0F1F3"/>')
        rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
        pieces.append(
            f'<w:r>{rpr}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>'
        )
    return "".join(pieces) or "<w:r><w:t/></w:r>"


def _paragraph(text="", style=None, *, bold=False, formulas=None, links=None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{_run(text, bold=bold, formulas=formulas, links=links)}</w:p>"


def _table(rows, formulas=None, links=None) -> str:
    """rows: [[cell, ...], ...]，第一行当表头。"""
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="D8DEE4"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    out = [f'<w:tbl><w:tblPr><w:tblBorders>{borders}</w:tblBorders>'
           '<w:tblW w:w="0" w:type="auto"/></w:tblPr>']
    for index, row in enumerate(rows):
        out.append("<w:tr>")
        for col in range(cols):
            cell = row[col] if col < len(row) else ""
            shade = '<w:shd w:val="clear" w:fill="F7F8FA"/>' if index == 0 else ""
            out.append(
                f'<w:tc><w:tcPr>{shade}</w:tcPr>'
                f'{_paragraph(cell, bold=(index == 0), formulas=formulas, links=links)}</w:tc>'
            )
        out.append("</w:tr>")
    out.append("</w:tbl>" + _paragraph(""))
    return "".join(out)


def markdown_to_docx_body(markdown: str, images=None, formulas=None,
                          formula_blocks=None, pictures=None, toc=False,
                          links=None) -> str:
    body, lines, i = [], markdown.splitlines(), 0
    images = images or {}
    formulas = formulas or {}
    formula_blocks = formula_blocks or {}
    pictures = pictures or {}
    toc_pending = toc
    while i < len(lines):
        line = lines[i]

        block_math = re.match(r"^@@MATH(\d+)@@$", line.strip())
        if block_math:
            # formula_blocks 里存的是「图片 run」，必须包进 w:p 才是合法的段级元素，
            # 否则 Word/WPS 会整段丢弃（之前显示公式消失就是这个原因）
            run = formula_blocks.get(int(block_math.group(1)), "")
            if run:
                body.append(f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
                            f'{run}</w:p>')
            i += 1
            continue

        block_pic = re.match(r"^@@IMG(\d+)@@$", line.strip())
        if block_pic:
            body.append(pictures.get(int(block_pic.group(1)), ""))
            i += 1
            continue

        marker = re.match(r"^@@CHART(\d+)@@$", line.strip())
        if marker:
            body.append(images.get(int(marker.group(1)), ""))
            i += 1
            continue

        # 代码块
        if line.strip().startswith("```"):
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(_paragraph(lines[i], "Code", formulas=formulas, links=links))
                i += 1
            i += 1
            continue

        # 表格
        if line.strip().startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|?[\s:|-]+\|", lines[i + 1]):
            rows = []
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(header)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body.append(_table(rows, formulas, links))
            continue

        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            body.append(_paragraph(heading.group(2), f"Heading{level}", formulas=formulas, links=links))
            if toc_pending and level == 1:
                body.append(_TOC_XML)      # 一级标题后面插目录
                toc_pending = False
            i += 1
            continue

        if stripped.startswith(">"):
            body.append(_paragraph(stripped.lstrip("> ").strip(), "Quote", formulas=formulas, links=links))
            i += 1
            continue

        item = re.match(r"^[-*+]\s+(.*)$", stripped)
        if item:
            body.append(_paragraph("• " + item.group(1), formulas=formulas, links=links))
            i += 1
            continue

        ordered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if ordered:
            body.append(_paragraph(f"{ordered.group(1)}. " + ordered.group(2),
                                   formulas=formulas, links=links))
            i += 1
            continue

        body.append(_paragraph(stripped, formulas=formulas, links=links))
        i += 1
    return "".join(body)


def export_docx(text: str, path, sources=None) -> None:
    text = prepare_with_sources(text, sources)
    markdown, chart_specs = extract_charts(text)
    markdown, math_specs = extract_formulas(markdown)
    markdown, image_specs = extract_images(markdown)
    title = document_title(markdown)

    images, media = {}, {}
    rels = [_DOC_RELS_HEAD]
    counter = {"n": 0}

    # 正文/表格里的 [文字](链接) 需要各自的 relationship
    link_ids = {}
    for url in dict.fromkeys(url for _label, url in LINK_RE.findall(markdown)):
        link_ids[url] = f"rIdLink{len(link_ids) + 1}"
        rels.append(_LINK_REL.format(rid=link_ids[url],
                                     target=escape(url, {'"': "&quot;"})))

    def register(image, emu_height=None, emu_width=5_400_000):
        """把图片写进包，返回可直接内嵌的 drawing 片段。"""
        counter["n"] += 1
        n = counter["n"]
        media[f"word/media/docimg{n}.png"] = _png_bytes(image)
        rid = f"rIdDoc{n}"
        rels.append(_DOC_RELS_ITEM.format(rid=rid, target=f"media/docimg{n}.png"))

        if emu_height:
            height = emu_height
            width = int(height * image.width() / max(1, image.height()))
            run = _INLINE_IMAGE_RUN.format(w=width, h=height, pid=n, rid=rid)
            return run
        height = int(emu_width * image.height() / max(1, image.width()))
        return _IMAGE_XML.format(w=emu_width, h=height, pid=n, rid=rid)

    for i, spec in enumerate(chart_specs):
        images[("chart", i)] = register(chart_mod.render_chart(spec))

    formula_runs, formula_blocks = {}, {}
    for j, (latex, display) in enumerate(math_specs):
        image = formula_renderer().render(latex, display)
        if image is None:
            continue
        if display:
            formula_blocks[j] = register(image, emu_height=620_000)
        else:
            formula_runs[j] = register(image, emu_height=300_000)

    for k, (src, _alt) in enumerate(image_specs):
        image = load_image(src)
        if image is not None:
            images[("img", k)] = register(image)

    chart_blocks = {i: images[("chart", i)] for i in range(len(chart_specs)) if ("chart", i) in images}
    pic_blocks = {k: images[("img", k)] for k in range(len(image_specs)) if ("img", k) in images}

    body_html = markdown_to_docx_body(
        markdown, chart_blocks, formula_runs, formula_blocks,
        pictures=pic_blocks, toc=bool(title), links=link_ids)

    # 页眉 / 页脚关系
    rels.append('<Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/header" Target="header1.xml"/>')
    rels.append('<Relationship Id="rIdFtr" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
    rels.append(_DOC_RELS_TAIL)
    rels_xml = "".join(rels).replace("\n</Relationships>", "</Relationships>")

    section = (
        '<w:sectPr>'
        '<w:headerReference w:type="default" r:id="rIdHdr"/>'
        '<w:footerReference w:type="default" r:id="rIdFtr"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
        ' w:header="567" w:footer="567"/>'
        "</w:sectPr>"
    )

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        f"<w:body>{body_html}{section}</w:body></w:document>"
    )

    content_types = _CONTENT_TYPES.replace(
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Default Extension="png" ContentType="image/png"/>')
    content_types = content_types.replace(
        "</Types>",
        '  <Override PartName="/word/header1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.header+xml"/>\n'
        '  <Override PartName="/word/footer1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.footer+xml"/>\n</Types>')

    safe_title = escape(title or "AI 文档")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", _fill_fonts(_STYLES))
        zf.writestr("word/header1.xml", _HEADER_XML.format(title=safe_title))
        zf.writestr("word/footer1.xml", _FOOTER_XML)
        for name, data in media.items():
            zf.writestr(name, data)


# ================================================================ xlsx
_XLSX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml"
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  {sheets}
</Types>"""

_XLSX_SHEET_OVERRIDE = ('<Override PartName="/xl/worksheets/sheet{n}.xml" ContentType='
                        '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')

_XLSX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="@CJK@"/></font>
    <font><b/><sz val="11"/><name val="@CJK@"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF2F4F7"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD9DEE5"/></left>
      <right style="thin"><color rgb="FFD9DEE5"/></right>
      <top style="thin"><color rgb="FFD9DEE5"/></top>
      <bottom style="thin"><color rgb="FFD9DEE5"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"
        applyFont="1" applyFill="1" applyBorder="1"/>
  </cellXfs>
</styleSheet>"""


def _col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _sheet_xml(rows) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetData>']
    for r, row in enumerate(rows, start=1):
        out.append(f'<row r="{r}">')
        for c, cell in enumerate(row):
            style = ' s="1"' if r == 1 else ""
            text = escape(strip_markdown(str(cell)))
            out.append(
                '<c r="%s%d" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>'
                % (_col_name(c), r, style, text)
            )
        out.append("</row>")
    out.append("</sheetData>")
    out.append('<cols><col min="1" max="12" width="22" customWidth="1"/></cols>')
    out.append("</worksheet>")
    return "".join(out)


def _workbook_xml(names) -> str:
    sheets = "".join(
        f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, n in enumerate(names, start=1)
    )
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheets}</sheets></workbook>")


def _workbook_rels(names) -> str:
    items = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
        f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(names) + 1)
    )
    items += ('<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/'
              'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{items}</Relationships>")


def extract_tables(markdown: str):
    """取出 Markdown 里所有表格（跳过代码块）。"""
    tables, current, in_code = [], [], False
    pending_header = None

    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                continue                      # 分隔行
            if pending_header is None:
                pending_header = cells
                current = [cells]
            else:
                current.append(cells)
        else:
            if len(current) > 1:
                tables.append(current)
            current, pending_header = [], None

    if len(current) > 1:
        tables.append(current)
    return tables


def export_xlsx(text: str, path, sources=None) -> None:
    tables = extract_tables(text)
    if not tables:
        plain = strip_markdown(extract_charts(text)[0])
        tables = [[[line] for line in plain.splitlines() if line.strip()] or [["（无内容）"]]]

    names = []
    for i in range(1, len(tables) + 1):
        title = strip_markdown(str(tables[i - 1][0][0]))[:20] or "数据"
        title = re.sub(r'[\\/:*?\[\]]+', "_", title) or f"Sheet{i}"
        names.append(f"{title}" if names.count(title) == 0 else f"{title}{i}")

    sheets = "".join(_XLSX_SHEET_OVERRIDE.format(n=i) for i in range(1, len(tables) + 1))
    content_types = _XLSX_CONTENT_TYPES.replace("{sheets}", sheets)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                    'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                    "</Relationships>")
        zf.writestr("xl/workbook.xml", _workbook_xml(names))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(names))
        zf.writestr("xl/styles.xml", _fill_fonts(_XLSX_STYLES))
        for i, rows in enumerate(tables, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))


def suggest_filename(content: str, fmt: str) -> str:
    """用一级标题或首行当文件名。"""
    import re as _re
    import time as _time

    name = ""
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        name = line.lstrip("#").strip() if line.startswith("#") else line
        break
    name = _re.sub(r'[\\/:*?"<>|]+', "_", name)[:40] or "ai-answer"
    return f"{name}-{_time.strftime('%Y%m%d-%H%M')}.{fmt}"


def conversation_markdown(turns, title="对话导出") -> str:
    """把整段对话拼成一份 Markdown（含附件说明和每条回答的来源）。"""
    import time as _time

    lines = [f"# {title}", "",
             f"> 导出时间：{_time.strftime('%Y-%m-%d %H:%M')} · "
             f"共 {len(turns)} 条消息", ""]

    for message in turns:
        role = "你" if message.get("role") == "user" else "模型"
        lines.append(f"## {role}")
        lines.append("")

        content = (message.get("content") or "").strip() or "（空）"
        texts = message.get("texts") or []
        if texts:
            names = "、".join(t.get("name", "") for t in texts)
            content += f"\n\n**附件：** {names}"
        lines.append(content)

        sources = message.get("sources") or []
        if sources:
            lines.append("")
            lines.append("**参考来源：**")
            for index, source in enumerate(sources, start=1):
                label = source.get("title") or source.get("url") or ""
                url = source.get("url") or ""
                lines.append(f"{index}. [{label}]({url})")

        lines.append("")

    return "\n".join(lines)


EXPORTERS = {
    "md": (export_markdown, "Markdown 文件 (*.md)"),
    "txt": (export_text, "文本文件 (*.txt)"),
    "docx": (export_docx, "Word 文档 (*.docx)"),
    "pdf": (export_pdf, "PDF 文件 (*.pdf)"),
    "xlsx": (export_xlsx, "Excel 表格 (*.xlsx)"),
}
