# -*- coding: utf-8 -*-
"""附件处理：把图片 / PDF / DOCX / Markdown / 文本转成模型能吃的形式。

- 图片：缩放后转 base64 data URL，走视觉输入（image_url）
- 文本类：抽取成纯文本，拼进消息正文
"""

import base64
import re
import shutil
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument

import app_paths

# ---------------- 限制（直接影响 token 消耗） ----------------
MAX_IMAGE_SIDE = 1280          # 图片长边像素上限
IMAGE_QUALITY = 82             # JPEG 质量
MAX_IMAGES = 4                 # 一次最多几张图
MAX_CHARS_PER_FILE = 12000     # 单个文本附件的字符上限
MAX_TOTAL_CHARS = 24000        # 所有文本附件合计上限
PDF_MAX_PAGES = 60

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx", ".csv", ".json", ".log", ".py"}

FILE_FILTER = (
    "支持的文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.pdf *.md *.txt *.docx *.csv *.json);;"
    "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;"
    "文档 (*.pdf *.md *.txt *.docx);;"
    "所有文件 (*)"
)


class AttachmentError(RuntimeError):
    pass


# ---------------- 附件仓库 ----------------
_STORE_DIR = None


def _store_candidates():
    """按优先级给出候选目录：应用数据目录 → 家目录隐藏目录 → 临时目录。"""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    if base:
        yield Path(base) / "Graphic_Dev" / "AI_chat" / "attachments"
    yield Path.home() / ".graphic_dev" / "attachments"
    yield Path(tempfile.gettempdir()) / "graphic_dev_attachments"


def store_dir():
    """附件副本目录；数据目录不可写时返回 None（则不复制，直接用原路径）。"""
    global _STORE_DIR
    if _STORE_DIR is None:
        _STORE_DIR = app_paths.sub_dir("attachments")
    return _STORE_DIR


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", name)
    return cleaned[:80] or "file"


def copy_to_store(path: Path) -> Path:
    """把原文件复制一份到附件仓库，返回副本路径（历史消息只引用副本）。"""
    root = store_dir()
    if root is None:
        raise OSError("没有可写的附件目录")
    target = root / f"{int(time.time() * 1000)}_{_safe_name(path.name)}"
    shutil.copy2(path, target)
    return target


def cleanup_old(days: int = 7) -> int:
    """清掉仓库里超过 N 天的副本（历史消息不持久化，重启后它们就是垃圾）。"""
    root = store_dir()
    if root is None:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        for f in root.iterdir():
            if f.is_file() and f.name != ".write_test" and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
    except OSError:
        pass
    return removed


def delete_stored(path) -> None:
    """只删仓库里的副本，绝不碰用户原文件。"""
    if not path:
        return
    root = store_dir()
    if root is None:
        return
    try:
        p = Path(path).resolve()
        if root.resolve() in p.parents and p.is_file():
            p.unlink()
    except OSError:
        pass


# ---------------- 文本抽取 ----------------
def _norm(text: str) -> str:
    """把 PDF 里常见的康熙部首（⼀、⽂ 等）还原成正常汉字。

    只处理 CJK 部首区，不动全角标点——整体做 NFKC 会把「：（）」也改成半角。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in text:
        if "\u2e80" <= ch <= "\u2fdf":       # CJK 部首补充 + 康熙部首
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def _extract_pdf(path: Path) -> str:
    doc = QPdfDocument()
    try:
        err = doc.load(str(path))
        if err != QPdfDocument.Error.None_:
            raise AttachmentError(f"PDF 无法打开（{err.name}）")
        pages = min(doc.pageCount(), PDF_MAX_PAGES)
        if pages <= 0:
            raise AttachmentError("PDF 没有页面")
        chunks = []
        for i in range(pages):
            sel = doc.getAllText(i)
            if sel is not None:
                chunks.append(sel.text())
        text = "\n".join(chunks)
        if doc.pageCount() > pages:
            text += f"\n（文档共 {doc.pageCount()} 页，仅提取了前 {pages} 页）"
        return text
    finally:
        doc.close()


def _extract_docx(path: Path) -> str:
    """不用 python-docx：docx 本质是 zip，正文在 word/document.xml。"""
    try:
        with zipfile.ZipFile(path) as zf:
            xml_bytes = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise AttachmentError(f"不是有效的 DOCX 文件（{e}）") from e

    # w 命名空间
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as e:
        raise AttachmentError(f"DOCX 内容解析失败（{e}）") from e

    paragraphs = []
    for p in root.iter(f"{ns}p"):
        texts = [node.text or "" for node in p.iter(f"{ns}t")]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)


def _extract_plain(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk", errors="replace")


def extract_text(path) -> str:
    """按后缀抽取纯文本，超长会被截断。"""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix == ".docx":
        text = _extract_docx(path)
    elif suffix in TEXT_SUFFIXES:
        text = _extract_plain(path)
    else:
        raise AttachmentError(f"暂不支持的文件类型：{suffix or '无后缀'}")

    text = _norm(text).strip()
    if not text:
        raise AttachmentError("没能从这个文件里提取到文字（可能是扫描件或空文件）")
    if len(text) > MAX_CHARS_PER_FILE:
        text = text[:MAX_CHARS_PER_FILE] + f"\n…（内容过长，已截断到 {MAX_CHARS_PER_FILE} 字）"
    return text


# ---------------- 图片 ----------------
def image_to_data_url(path) -> str:
    """缩放 + 转 JPEG + base64 data URL。"""
    path = Path(path)
    img = QImage(str(path))
    if img.isNull():
        raise AttachmentError("图片无法读取")

    if max(img.width(), img.height()) > MAX_IMAGE_SIDE:
        img = img.scaled(
            MAX_IMAGE_SIDE, MAX_IMAGE_SIDE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    buf = QByteArray()
    qbuf = QBuffer(buf)
    qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
    # 有透明通道的图转 JPEG 会变黑底，先铺白底
    if img.hasAlphaChannel():
        from PySide6.QtGui import QPainter, QColor
        canvas = QImage(img.size(), QImage.Format.Format_RGB32)
        canvas.fill(QColor("#ffffff"))
        painter = QPainter(canvas)
        painter.drawImage(0, 0, img)
        painter.end()
        img = canvas
    ok = img.save(qbuf, "JPEG", IMAGE_QUALITY)
    qbuf.close()
    if not ok:
        raise AttachmentError("图片编码失败")

    b64 = base64.b64encode(bytes(buf)).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# ---------------- 统一入口 ----------------
def load_attachment(path) -> dict:
    """返回 {name, kind, text|data_url, size}。"""
    path = Path(path)
    if not path.is_file():
        raise AttachmentError("文件不存在")

    suffix = path.suffix.lower()
    size = path.stat().st_size

    if suffix in IMAGE_SUFFIXES:
        item = {"name": path.name, "kind": "image",
                "data_url": image_to_data_url(path), "size": size}
    else:
        item = {"name": path.name, "kind": "text",
                "text": extract_text(path), "size": size}

    # 复制一份到附件仓库：用户之后删掉原文件，历史消息里的链接依然有效
    try:
        stored = copy_to_store(path)
        item["path"] = str(stored)
        item["source"] = str(path)
    except OSError:
        item["path"] = str(path)
        item["source"] = str(path)
    return item


def summarize(attachments) -> str:
    """给聊天气泡显示用的附件摘要。"""
    lines = []
    for a in attachments:
        if a["kind"] == "image":
            lines.append(f"🖼 {a['name']}")
        else:
            lines.append(f"📄 {a['name']}（{len(a.get('text') or '')} 字）")
    return "\n".join(lines)
