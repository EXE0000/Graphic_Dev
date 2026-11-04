# 把中文字体放这里（可选）

程序启动时会扫描本目录的 `*.ttf` / `*.otf` 并注册到 Qt，用于：
- 界面字体（Linux 上很多发行版默认没有 CJK 字体）
- 导出的 DOCX / PDF / XLSX 字体名

推荐：

| 文件 | 用途 | 大小 |
|---|---|---|
| `NotoSansCJKsc-Regular.otf` | 正文 | ~16 MB |
| `NotoSansMonoCJKsc-Regular.otf` | 代码块（可选） | ~16 MB |

许可证：SIL Open Font License 1.1（允许随应用分发）。

下载：https://github.com/notofonts/noto-cjk/releases

留空也可以，程序会回退到系统字体。
