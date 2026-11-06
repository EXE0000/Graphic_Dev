# -*- coding: utf-8 -*-
"""把 icons/logo.svg 渲染成三平台的软件图标。

用法（在项目根目录，需要项目虚拟环境里的 PySide6）：
    .venv/bin/python icons/make_icons.py

产出（都提交进仓库，打包/CI 直接用，不参与构建时生成）：
    icons/appicon.ico    Windows 可执行文件图标（onedir 和 onefile 共用）
    icons/appicon.icns   macOS 应用包图标
    icons/appicon.png    Linux 随包图标 + 运行时窗口/任务栏图标（256px）

为什么提前生成好提交，而不是打包时现做：
- ICO/ICNS 是二进制产物，让构建结果跟"本机有没有 iconutil / QtSvg"无关，CI 三个
  平台都能少一步；
- 换图标时改 logo.svg 再跑一次本脚本，产物的差异在 git diff 里一眼可见。
"""

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
SRC_SVG = HERE / "logo.svg"

# Windows 图标要带齐尺寸：任务栏/标题栏在 100%、125%、150% DPI 下取的都是小尺寸，
# 只塞一张 256 会被系统缩得很糊。
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# macOS 的 iconset 用固定文件名表示尺寸，iconutil 靠名字认；一个尺寸只能出现一次。
ICONSET_NAMES = {
    16: "icon_16x16.png",
    32: "icon_16x16@2x.png",
    64: "icon_32x32@2x.png",
    128: "icon_128x128.png",
    256: "icon_128x128@2x.png",
    512: "icon_256x256@2x.png",
    1024: "icon_512x512@2x.png",
}


def render_png(size: int, out: Path, fill: float) -> Path:
    """把 SVG 按等比放到 size×size 的透明画布正中。"""
    renderer = QSvgRenderer(str(SRC_SVG))
    if not renderer.isValid():
        sys.exit("SVG 解析失败：%s" % SRC_SVG)

    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    side = size * fill
    pad = (size - side) / 2.0
    renderer.render(painter, QRectF(pad, pad, side, side))
    painter.end()

    # 不传 quality：Qt 的 PNG writer 在 quality≥95 时会退化成"不压缩"，
    # 一张 256px 能从 7KB 涨到 263KB，ico/icns 会跟着虚胖。
    if not img.save(str(out), "PNG", -1):
        sys.exit("PNG 写出失败：%s" % out)
    return out


def build_ico(pngs: list[tuple[int, Path]], out: Path) -> None:
    """组装多尺寸 ICO，每个尺寸用 PNG 压缩条目。

    PNG 条目是 Vista 之后 ICO 的合法写法，Windows 资源管理器/任务栏都认；好处是
    256 这张能保住 alpha 又不至于让文件涨到几 MB。
    """
    count = len(pngs)
    header_size = 6 + 16 * count
    body = b""
    entries = b""
    offset = header_size
    for size, path in pngs:
        data = path.read_bytes()
        # 目录项里宽高各占 1 字节，256 记作 0
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    out.write_bytes(struct.pack("<HHH", 0, 1, count) + entries + body)


def build_icns(pngs: dict[int, Path], out: Path) -> bool:
    """用系统的 iconutil 打包 .icns（只有 macOS 有这个工具）。"""
    if sys.platform != "darwin":
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "appicon.iconset"
        iconset.mkdir()
        for size, name in ICONSET_NAMES.items():
            (iconset / name).write_bytes(pngs[size].read_bytes())
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                       check=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="从 logo.svg 生成三平台软件图标")
    ap.add_argument("--fill", type=float, default=0.86,
                    help="图形占画布边长的比例（留白用，默认 0.86）")
    ap.add_argument("--out", default=str(HERE), help="输出目录，默认 icons/")
    args = ap.parse_args()

    if not SRC_SVG.is_file():
        sys.exit("找不到源图：%s" % SRC_SVG)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    png_dir = out_dir / ".render_cache"          # 中间 PNG，跑完就删
    png_dir.mkdir(exist_ok=True)

    sizes = sorted(set(ICO_SIZES) | set(ICONSET_NAMES) | {256})
    pngs = {s: render_png(s, png_dir / ("appicon_%d.png" % s), args.fill) for s in sizes}

    ico = out_dir / "appicon.ico"
    build_ico([(s, pngs[s]) for s in ICO_SIZES], ico)
    print("OK %s（%d 个尺寸：%s）" % (ico.name, len(ICO_SIZES),
                                     "×".join(str(s) for s in ICO_SIZES)))

    if build_icns(pngs, out_dir / "appicon.icns"):
        print("OK appicon.icns")
    else:
        print("跳过 .icns：iconutil 只在 macOS 上有，请在 Mac 上重跑本脚本")

    runtime_png = out_dir / "appicon.png"
    runtime_png.write_bytes(pngs[256].read_bytes())
    print("OK %s（运行时窗口图标）" % runtime_png.name)

    for f in png_dir.glob("*.png"):
        f.unlink()
    png_dir.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
