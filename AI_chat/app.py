import json
import os
import pathlib
import queue
import re
import shutil
import sys
import threading
import time
from urllib.parse import quote, unquote

import requests

from PySide6.QtCore import (QEvent, QObject, QSettings, QThread, QTimer,
                            Qt, QUrl, Signal)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHeaderView,
                               QMainWindow, QMessageBox, QPushButton,
                               QTableWidgetItem)

from UI.main_window import Ui_MainWindow
from model_dialog import ModelPickerDialog

import app_paths
import attachments as attach_mod
import exporters
import fonts
import image_gen
import secrets_store
import theme
import web_server

from web_search import (search_web, format_search_block, to_sources,
                        format_tool_results, PROVIDERS, PROVIDER_ORDER,
                        DEFAULT_PROVIDER, provider_label,
                        enrich_results, dedupe_by_domain,
                        authority_domains, extract_date, is_authority)

ORG, APP_NAME = "Graphic_Dev", "AI_chat"
WEB_DIR = app_paths.resource_dir() / "web"


def file_url(path) -> str:
    """本地路径 → file:// URL。

    必须用 FullyEncoded：路径里常有空格（如 macOS 的 Application Support），
    空格不转义会让 Markdown 的 ![](...) 直接解析失败。
    """
    return QUrl.fromLocalFile(str(path)).toString(
        QUrl.ComponentFormattingOption.FullyEncoded)


def apply_theme(mode=None):
    """加载明暗主题（QSS + 调色板），返回实际生效的 "light" / "dark"。

    mode 为空时读设置里的偏好，main() 启动就是走这条路径。
    """
    if mode is None:
        mode = QSettings(ORG, APP_NAME).value("theme_mode", "auto", type=str)
    return theme.apply(mode if mode in theme.MODES else "auto")
DEFAULT_SYSTEM_PROMPT = """你是运行在 Graphic_Dev 桌面客户端中的 AI 助手。

- 风格：直接、具体、不绕弯子；不确定就明说，不编造。
- 语言：默认简体中文；用户使用其他语言时跟随用户。"""


# 下面这段是「内置能力说明」，无论用户怎么改系统提示词都会附加在末尾，
# 保证公式渲染、图表、配图、引用这些客户端能力始终可用。
CAPABILITY_PROMPT = """## 运行环境（内置说明，始终生效）
- 客户端使用 KaTeX 实时渲染 LaTeX，支持 \\frac、\\sqrt、\\int、\\sum 以及 aligned / matrix / pmatrix / cases 等环境。
- 回复按 Markdown 渲染，支持标题、表格、列表、代码块。

## 公式格式
- 数学表达式一律用 LaTeX：行内 $...$，独立成行 $$...$$。
- 禁止用 Unicode 符号（√ ± ∫ ∑）拼接公式，禁止用 ASCII 字符画分数线。
- 除用户明确要求查看源码，不要把公式放进 ```latex 代码块。

## 图表（导出文档时会渲染成图片）
需要数据可视化时，输出 chart 代码块，内容是 JSON：
```chart
{"kind": "bar", "title": "季度营收", "labels": ["Q1", "Q2"], "series": [{"name": "2025", "values": [120, 150]}]}
```
- kind 取 bar / line / pie；pie 只需要一组 values；
- 数值必须是数字，不要带单位或百分号（单位写在 title 里）；
- 不要用文字画条形图，也不要用 Markdown 表格代替图表。

## 行为约束
- 不要声称"无法渲染公式"或"只能用文字模拟图表"。
- 涉及事实时给出可核对的来源；不确定就说明不确定。"""


IMAGE_CAPABILITY = """## 配图（内置说明）
- 用户要求「画一张 / 生成图片 / 配个图 / 示意图」时，调用 generate_image 工具。
- 不要用 ASCII 字符画、表格或文字描述代替图片。
- 不要自己编造图片链接，工具生成的图会直接展示给用户。"""


SEARCH_CAPABILITY = """## 联网检索（内置说明）
- 问题涉及实时事件、近期数据、价格、新闻，或你不确定、可能过时的知识时，调用 web_search 工具；复杂问题可拆成多次调用。
- 引用搜索结果时用 [1][2] 标注编号，编号必须对应真实返回的结果。
- 没有检索到的内容不要编造引用或链接。"""


NON_CHAT_KEYWORDS = ("embedding", "rerank", "tts", "whisper", "moderation")


REASONING_LEVELS = ("off", "low", "medium", "high")
REASONING_LABELS = {"off": "关闭", "low": "低", "medium": "中", "high": "高"}
REASONING_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}


def thinking_params(url: str, level: str = "medium") -> dict:
    """把「推理强度」翻译成各家服务商的参数。

    level 取值：off / low / medium / high。
    对认不出的一线服务商按各自字段下发；未知的 OpenAI 兼容网关
    （LM Studio、Ollama、vLLM 等）显式下发 reasoning_effort，
    避免服务端用自己的默认值（有的默认是 xhigh，会拖慢几十倍）。
    """
    host = (url or "").lower()
    level = level if level in REASONING_LEVELS else "medium"

    dashscope = "dashscope" in host or "aliyuncs" in host or "qianwenai" in host
    deepseek = "deepseek" in host
    openai = "openai" in host
    moonshot = "moonshot" in host

    if level == "off":
        if dashscope:
            return {"enable_thinking": False}
        if deepseek:
            return {"thinking": {"type": "disabled"}}
        if openai:
            return {"reasoning_effort": "minimal"}
        if moonshot:
            return {"thinking": {"type": "disabled"}}
        # 未知的 OpenAI 兼容网关：用 minimal，避免服务端回落到默认的强推理
        return {"reasoning_effort": "minimal"}

    if dashscope:
        return {"enable_thinking": True,
                "thinking_budget": REASONING_BUDGET.get(level, 4096)}
    if deepseek:
        return {"thinking": {"type": "enabled"}}
    if openai:
        return {"reasoning_effort": level}
    if moonshot:
        return {"thinking": {"type": "enabled"}}

    return {"reasoning_effort": level}


# 明确不带推理参数的模型家族（命中就不允许调）
_NO_REASONING_HINTS = (
    "gpt-4o", "gpt-4-turbo", "gpt-3.5", "qwen2.5", "qwen2-", "qwen-turbo",
    "llama", "mistral", "mixtral", "gemma", "phi-", "chatglm", "baichuan",
)

# 明确支持推理强度的模型家族
_REASONING_HINTS = (
    "o1", "o3", "o4", "gpt-5", "qwq", "qwen3", "deepseek-reasoner",
    "reasoner", "thinking", "kimi-k2", "glm-4.5", "glm-4.6", "minimax-m2",
)

# 这些网关的通用接口基本都接受推理参数
_REASONING_HOSTS = ("dashscope", "aliyuncs", "qianwenai", "openai.com",
                    "deepseek.com", "moonshot")


def reasoning_support(url: str, model: str):
    """判断某个模型能不能调推理强度，返回 (是否支持, 说明)。"""
    name = (model or "").strip().lower()
    if not name:
        return False, "请先选择模型"

    for hint in _NO_REASONING_HINTS:
        if hint in name:
            return False, "该模型没有推理强度参数，滑块不可用"

    for hint in _REASONING_HINTS:
        if hint in name:
            return True, ""

    host = (url or "").lower()
    if any(key in host for key in _REASONING_HOSTS):
        return True, ""

    return True, "不确定该模型是否支持推理强度，可自行尝试"


def is_chat_model(name: str) -> bool:
    low = name.lower()
    return not any(k in low for k in NON_CHAT_KEYWORDS)


# ---------------- 联网搜索（工具调用）参数 ----------------
IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "根据文字描述生成一张图片并直接展示给用户。"
            "当用户要求「画一张」「生成图片」「配个图」「画个示意图」时调用。"
            "不要用 ASCII 字符画或表格代替图片。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "画面描述，越具体越好；中英文均可",
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x1792", "1792x1024", "768x1344"],
                    "description": "图片尺寸，不确定时省略，用客户端默认值",
                },
            },
            "required": ["prompt"],
        },
    },
}

MAX_TOTAL_SOURCES = 14     # 一次提问里，所有搜索加起来最多几条结果

# 客户端聊天记录落盘
HISTORY_FILE = (app_paths.sub_dir("cache") or pathlib.Path(".")) / "chat_history.json"
MAX_HISTORY = 60            # 最多保留多少条消息
MAX_HISTORY_BYTES = 6 * 1024 * 1024
PER_CALL_MIN = 3           # 单次搜索最少几条
MAX_SEARCH_ROUNDS = 2      # 最多几轮检索（每轮可由模型并行发起多个查询）
SEARCH_TIME_BUDGET = 25    # 单次提问花在检索上的总时间上限（秒）
PER_CALL_MAX = 8           # 单次搜索最多几条
PREFETCH_RESULTS = 6       # 降级到"直接检索"时用几条
SEARCH_TEST_TIMEOUT = 10   # 「检测」搜索 API 的单次超时（秒）
SEARCH_TEST_TTL = 120      # 勾选时的检测结论缓存时长（秒），避免反复勾选反复联网

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "联网搜索网页。当问题涉及实时事件、近期数据、价格、新闻，"
            "或你不确定、可能过时的知识时调用。复杂问题可以拆成多个子问题，"
            "分别调用多次。不需要最新信息时不要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，要具体，例如「2025 诺贝尔文学奖 得主」",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"本次要几条结果，范围 {PER_CALL_MIN}-{PER_CALL_MAX}。"
                                   "简单事实用 3，需要多角度对比用 6",
                    "minimum": PER_CALL_MIN,
                    "maximum": PER_CALL_MAX,
                },
                "reason": {
                    "type": "string",
                    "description": "为什么需要这次搜索（一句话）",
                },
            },
            "required": ["query"],
        },
    },
}


class CheckCellFilter(QObject):
    def __init__(self, table, column, on_toggle):
        super().__init__(table)
        self._table = table
        self._column = column
        self._on_toggle = on_toggle

    def eventFilter(self, obj, event):
        et = event.type()
        if et not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return super().eventFilter(obj, event)

        pos = event.position().toPoint()
        row = self._table.rowAt(pos.y())
        col = self._table.columnAt(pos.x())
        if row < 0 or col != self._column:
            return super().eventFilter(obj, event)

        if et == QEvent.Type.MouseButtonPress:
            self._on_toggle(row)
        self._table.setCurrentCell(row, col)
        return True

class ChatPage(QWebEnginePage):
    """聊天页面：点击链接用系统浏览器打开，不让页面自己被导航走。"""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class ReasoningProbeWorker(QThread):
    """判断当前模型能不能调推理强度。

    只看模型名不可靠，只看"接口没报错"也不可靠（LM Studio 会静默忽略
    reasoning_effort）。所以按顺序验证：

      1. LM Studio 原生接口 /api/v0/models 的 capabilities（最权威）
      2. 用「我们实际会发的参数」去测：各厂商机制不同
         （OpenAI 系=reasoning_effort，DeepSeek/Moonshot=thinking，
           DashScope=enable_thinking + thinking_budget）
      3. 未知网关：要求两次请求的思考量有明显差异才算支持
    """

    done = Signal(str, str)          # key, "yes" / "no" / "unknown"

    # 一线厂商：只要参数被接受就认为可控（思考量不一定在小样本上体现）
    KNOWN_HOSTS = ("openai.com", "deepseek.com", "dashscope", "aliyuncs",
                   "qianwenai", "moonshot")

    def __init__(self, url, key, model, cap_key):
        super().__init__()
        self.url = url
        self.key = key
        self.model = model
        self.cap_key = cap_key
        self._stopped = False

    def cancel(self):
        self._stopped = True

    # ---------- 1. 原生能力 ----------
    def _native_capabilities(self):
        base = self.url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            resp = requests.get(f"{base}/api/v0/models", timeout=8)
        except requests.exceptions.RequestException:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json().get("data") or []
        except ValueError:
            return None

        for item in data:
            ident = str(item.get("id") or "")
            if ident != self.model and not ident.endswith("/" + self.model) \
                    and self.model not in ident:
                continue
            caps = item.get("capabilities")
            if isinstance(caps, dict):
                caps = list(caps.keys())
            return caps if isinstance(caps, list) else []
        return None

    # ---------- 2. 实测 ----------
    def _ask(self, extra):
        """返回思考 token 数 / "rejected" / None（网络或其他错误）。"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "2+2=?"}],
            "max_tokens": 64,
            "stream": False,
        }
        payload.update(extra or {})

        try:
            resp = requests.post(
                f"{self.url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=60,
            )
        except requests.exceptions.RequestException:
            return None

        if resp.status_code in (400, 422):
            text = (resp.text or "").lower()
            hints = ("reasoning_effort", "thinking", "unsupported", "unrecognized",
                     "not support", "unknown", "invalid", "extra", "budget")
            return "rejected" if any(h in text for h in hints) else None
        if resp.status_code != 200:
            return None

        try:
            data = resp.json()
        except ValueError:
            return None

        message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        tokens = details.get("reasoning_tokens")
        if isinstance(tokens, int):
            return tokens
        return len(message.get("reasoning_content") or "")

    def _param_sets(self):
        """(off 参数, high 参数) 的候选列表：厂商机制 + 通用 reasoning_effort。"""
        sets = []
        try:
            provider_high = thinking_params(self.url, "high")
            provider_off = thinking_params(self.url, "off")
            if provider_high and provider_high != {"reasoning_effort": "high"}:
                sets.append((provider_off, provider_high))
        except Exception:                        # noqa: BLE001
            pass
        sets.append(({"reasoning_effort": "minimal"}, {"reasoning_effort": "high"}))
        return sets

    def run(self):
        caps = self._native_capabilities()
        if self._stopped:
            return
        if caps is not None:
            has = any("reason" in str(c).lower() for c in caps)
            self.done.emit(self.cap_key, "yes" if has else "no")
            return

        known = any(h in self.url.lower() for h in self.KNOWN_HOSTS)
        accepted = False
        network_error = False

        for off_params, high_params in self._param_sets():
            if self._stopped:
                return
            low = self._ask(off_params)
            if self._stopped:
                return
            high = self._ask(high_params)
            if self._stopped:
                return

            if low is None and high is None:
                network_error = True
                continue
            if low == "rejected" and high == "rejected":
                continue                          # 这套机制不支持，换下一套
            if not (isinstance(low, int) and isinstance(high, int)):
                accepted = True                   # 接受了但拿不到 token 细节
                continue

            accepted = True
            if high >= max(low * 1.5, low + 8):
                self.done.emit(self.cap_key, "yes")
                return

        if self._stopped:
            return
        if accepted and known:
            self.done.emit(self.cap_key, "yes")
        elif accepted:
            self.done.emit(self.cap_key, "no")
        elif network_error:
            self.done.emit(self.cap_key, "unknown")
        else:
            self.done.emit(self.cap_key, "no")


class ChatWorker(QThread):
    chunk = Signal(str)            # 正文增量
    think = Signal(str)            # 思考增量
    finished = Signal(str, str)    # (完整正文, 完整思考)
    error_occurred = Signal(str)
    search_note = Signal(str)
    sources = Signal(list)         # 搜索来源 [{title, url}]
    reset_stream = Signal()        # 要求前端清空当前流式气泡（决定搜索后重新作答）
    image_generated = Signal(str)  # 生成好一张图，通知前端立刻显示

    def __init__(self, url, key, model, history, use_search=False, search_key="",
                 search_provider=DEFAULT_PROVIDER, reasoning_level="medium",
                 image_config=None):
        super().__init__()
        self.url = url
        self.key = key
        self.model = model
        self.history = history
        self.use_search = use_search
        self.search_key = search_key
        self.search_provider = search_provider
        self.reasoning_level = reasoning_level
        self.image_config = image_config or None
        self.generated_images = []
        self._thinking_fallback = False
        self._stopped = False
        self._sources_count = 0

    def cancel(self):
        self._stopped = True

    @property
    def stopped(self):
        return self._stopped

    def run(self):
        messages = list(self.history)
        try:
            if self._tool_defs():
                if self._run_with_tools(messages):
                    return          # 工具分支内部已经 emit 过 finished
            else:
                # 没有任何工具：明确告诉模型"你上不了网"，避免它空想
                messages = self._annotate_no_search(messages)

            content, reasoning, _ = self._stream_round(messages)
            self.finished.emit(self._ensure_content(self._with_images(content)),
                               reasoning)
        except requests.exceptions.ReadTimeout:
            self.error_occurred.emit("读取超时：模型太久没有输出")
        except Exception as e:
            self.error_occurred.emit(f"{type(e).__name__}: {e}")

    # ---------- 联网开关关闭时的两处保护 ----------
    NO_SEARCH_NOTE = (
        "\n\n## 联网能力\n"
        "当前客户端【未开启】联网搜索，你无法访问任何实时信息。\n"
        "如果用户的问题需要最新、实时或你没有把握的信息：\n"
        "1. 直接说明你现在无法联网查询；\n"
        "2. 提示用户可以勾选窗口左下角的「联网搜索」后重试；\n"
        "3. 不要假装已经搜索，不要编造来源、链接或最新数据。\n"
    )

    SEARCH_NOTE = (
        "\n\n## 联网搜索规范\n"
        "1. 只依据检索到的内容作答，不要凭记忆补充细节；\n"
        "2. 每个关键结论后面标注来源编号，例如 [1]、[2]；同一句可标多个；\n"
        "3. 结果之间如果冲突，要指出分歧并说明你倾向哪一个；\n"
        "4. 结果不足以回答时，明确说缺什么，不要编造；\n"
        "5. 注意时效：优先采用带具体日期、且时间较新的信息；\n"
        "6. 查询词必须自包含：不要用「它/这个/上面说的」这类指代，"
        "要把主语写清楚；\n"
        "7. 涉及国外机构、英文资料或专有名词时，**必须**再发一条英文查询，不要只用中文检索；\n"
        "8. 涉及机构公告、政策、统计数字时，优先检索官方域名（可用 site:gov.cn 这类限定），并检查结果里是否有一手来源。\n"
    )

    IMAGE_NOTE = (
        "\n\n## 图片生成\n"
        "用户要求画图、生成图片、配图、示意图时，调用 generate_image 工具。\n"
        "不要用 ASCII 字符画、Markdown 表格或代码块代替图片。\n"
        "工具返回后，用一两句话说明画了什么即可，不要输出图片链接。\n"
    )

    def _annotate_image(self, messages):
        messages = list(messages)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system",
                           "content": messages[0]["content"] + self.IMAGE_NOTE}
        return messages

    def _annotate_search(self, messages):
        messages = list(messages)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system",
                           "content": messages[0]["content"] + self.SEARCH_NOTE}
        return messages

    def _annotate_no_search(self, messages):
        messages = list(messages)
        if messages and messages[0].get("role") == "system":
            messages[0] = {
                "role": "system",
                "content": messages[0]["content"] + self.NO_SEARCH_NOTE,
            }
        else:
            messages.insert(0, {"role": "system",
                                "content": self.NO_SEARCH_NOTE.strip()})
        return messages

    def _ensure_content(self, content):
        """模型只思考、没给正文时，补一句明确提示，别让界面空着。"""
        if content.strip():
            return content
        if self._stopped:          # 用户主动停止，不补提示
            return content
        fallback = (
            "（模型没有返回正文。）\n\n"
            "如果这个问题需要最新信息，请勾选窗口左下角的「联网搜索」后重新发送。"
        )
        self.chunk.emit(fallback)
        return fallback


    def _tool_defs(self):
        """当前可用的工具列表（联网搜索 / 文生图）。"""
        defs = []
        if self.use_search and self.search_key:
            defs.append(WEB_SEARCH_TOOL)
        if self.image_config:
            defs.append(IMAGE_TOOL)
        return defs

    def _with_images(self, text):
        """把生成过的图片拼进最终回答。"""
        if not self.generated_images:
            return text
        extra = "\n\n".join(self.generated_images)
        if extra in text:
            return text
        return (text.rstrip() + "\n\n" + extra).strip()

    # ---------- 工具循环：边流式判断，边按需搜索/画图 ----------
    def _run_with_tools(self, messages):
        """流式判断 → 按需检索（可多轮）→ 流式作答。返回 True 表示已收尾。"""
        self._budget = MAX_TOTAL_SOURCES
        self._all_sources = []
        self._search_deadline = time.time() + SEARCH_TIME_BUDGET

        if self.image_config:
            messages[:] = self._annotate_image(messages)

        searching = bool(self.use_search and self.search_key)
        if searching:
            messages[:] = self._annotate_search(messages)
            self.search_note.emit("正在判断是否需要联网搜索…")
        else:
            messages[:] = self._annotate_no_search(messages)
            if self.image_config:
                self.search_note.emit("正在判断是否需要生成图片…")

        for round_no in range(MAX_SEARCH_ROUNDS):
            if self._stopped:
                break
            try:
                content, reasoning, calls = self._stream_round(messages, tools=True)
            except RuntimeError as e:
                # 网关或模型不支持 tools → 降级为"直接检索"
                self.search_note.emit(f"模型不支持工具调用，改用直接检索（{e}）")
                messages[:] = self._prefetch(messages)
                return False

            if not calls:
                if round_no == 0:
                    self.search_note.emit("模型判断无需调用工具，直接作答")
                self.finished.emit(
                    self._ensure_content(self._with_images(content)), reasoning)
                return True

            has_image_call = any(
                (c.get("function") or {}).get("name") == "generate_image"
                for c in calls)

            # 只有已经流出了"答案正文"才清屏；思考过程保留
            if content.strip():
                self.reset_stream.emit()

            if has_image_call:
                self.search_note.emit("正在生成图片…")
            else:
                self.search_note.emit(
                    "模型决定联网搜索…" if round_no == 0 else "模型决定补充检索…")
            assistant_msg = {"role": "assistant",
                             "content": content,
                             "tool_calls": calls}
            # DeepSeek 等厂商要求：带 tools 的请求必须回传上一轮的 reasoning_content，
            # 否则下一轮直接 400。没有思考内容时不要带空字段。
            if reasoning.strip():
                assistant_msg["reasoning_content"] = reasoning
            messages.append(assistant_msg)
            self._execute_tools(calls, messages)

            if self._all_sources:
                self.search_note.emit(
                    f"已获取 {len(self._all_sources)} 条结果，"
                    f"正在判断是否还需补充…")

        # 轮数用尽：去掉 tools 强制收尾
        final_content, final_reasoning, _ = self._stream_round(messages)
        self.finished.emit(self._ensure_content(self._with_images(final_content)),
                           final_reasoning)
        return True

    def _execute_tools(self, calls, messages):
        for call in calls:
            if self._stopped:
                break
            try:
                args = json.loads((call.get("function") or {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            name = (call.get("function") or {}).get("name") or "web_search"
            if name == "generate_image":
                self._run_image_tool(call, args, messages)
                continue

            query = (args.get("query") or "").strip()
            if not query:
                continue

            try:
                want = int(args.get("max_results") or 5)
            except (TypeError, ValueError):
                want = 5
            want = max(0, min(want, PER_CALL_MAX))

            if self._budget <= 0:
                messages.append({
                    "role": "tool", "tool_call_id": call.get("id", ""),
                    "content": "本轮搜索条数已用完，请直接基于已有结果作答。"})
                continue

            want = min(want, self._budget)
            self.search_note.emit(f"搜索「{query}」（{want} 条）")
            try:
                results = search_web(self.search_key, query, max_results=want,
                                     provider=self.search_provider)
                fetched_main = len(results)   # 主搜索实际取到的条数（extra 另行扣减）

                # 命中机构关键词时，追加 site: 限定检索，尽量拿一手原文
                domains = authority_domains(query)
                if domains:
                    self.search_note.emit(f"补检官方来源：{domains[0]}")
                extra = []
                for domain in domains[:2]:
                    if self._budget <= 3:
                        break
                    try:
                        more = search_web(self.search_key,
                                          f"site:{domain} {query}",
                                          max_results=3,
                                          provider=self.search_provider)
                    except Exception:
                        continue
                    for item in more:
                        if is_authority(item.get("url", ""), [domain]):
                            item["authority"] = True
                    extra.extend(more)
                    self._budget -= len(more)

                if extra:
                    seen = {r.get("url") for r in results}
                    results += [r for r in extra if r.get("url") not in seen]

                results = enrich_results(dedupe_by_domain(results),
                                         deadline=self._search_deadline)
                for item in results:
                    item.setdefault("date", extract_date(item))
            except Exception as e:
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": f"搜索失败：{e}"})
                continue

            self._budget -= fetched_main
            self._all_sources.extend(to_sources(results))
            self._sources_count = len(self._all_sources)
            self.sources.emit(self._all_sources)      # 边搜边显示
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": format_tool_results(results, query)})

    def _run_image_tool(self, call, args, messages):
        """执行一次文生图，并把图片挂到回答末尾。"""
        prompt = (args.get("prompt") or "").strip()
        tool_call_id = call.get("id", "")
        if not prompt:
            messages.append({"role": "tool", "tool_call_id": tool_call_id,
                             "content": "缺少 prompt，无法生成图片。"})
            return

        cfg = self.image_config or {}
        size = (args.get("size") or cfg.get("size") or "1024x1024").strip()

        try:
            data = image_gen.generate(cfg.get("base", ""), cfg.get("key", ""),
                                      cfg.get("model", ""), prompt, size)
        except image_gen.ImageGenError as e:
            text = str(e)
            if "model not exist" in text.lower() or "model" in text.lower() and "404" in text:
                text += ("；该模型名不在接口支持列表中，"
                         "请到「API 配置」点「选择…」重新获取可用模型")
            messages.append({"role": "tool", "tool_call_id": tool_call_id,
                             "content": f"图片生成失败：{text}"})
            self.search_note.emit(f"图片生成失败：{text}")
            return
        except Exception as e:
            messages.append({"role": "tool", "tool_call_id": tool_call_id,
                             "content": f"图片生成失败：{e}"})
            self.search_note.emit(f"图片生成失败：{e}")
            return

        folder = app_paths.sub_dir("images")
        if folder is None:
            messages.append({"role": "tool", "tool_call_id": tool_call_id,
                             "content": "图片生成失败：没有可写的目录"})
            return

        try:
            path = image_gen.save_image(data, folder)
        except Exception as e:
            messages.append({"role": "tool", "tool_call_id": tool_call_id,
                             "content": f"图片保存失败：{e}"})
            self.search_note.emit(f"图片保存失败：{e}")
            return
        snippet = "![生成的图片]({})".format(file_url(path))
        self.generated_images.append(snippet)
        self.image_generated.emit(snippet)
        self.search_note.emit(f"已生成图片：{pathlib.Path(path).name}")

        messages.append({
            "role": "tool", "tool_call_id": tool_call_id,
            "content": ("图片已生成并展示给用户。不要在正文里重复输出图片链接，"
                        "用一两句话说明画的是什么即可。"),
        })

    # ---------- 降级：直接检索后注入 system ----------
    def _prefetch(self, messages):
        messages = list(messages)
        query = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                query = m["content"]
                break
        try:
            results = search_web(self.search_key, query,
                                 max_results=PREFETCH_RESULTS,
                                 provider=self.search_provider)
            results = enrich_results(dedupe_by_domain(results),
                                     deadline=getattr(self, "_search_deadline", None))
            block = format_search_block(results, query)
            if block and messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system",
                               "content": messages[0]["content"] + "\n\n" + block}
            self.search_note.emit(f"已检索 {len(results)} 条结果")
            self.sources.emit(to_sources(results))
        except Exception as e:
            self.search_note.emit(f"搜索失败，按无搜索结果回答：{e}")
        return messages

    # ---------- 流式请求：正文、思考、工具调用一起处理 ----------
    def _stream_round(self, messages, tools=False):
        """跑一次流式请求，返回 (content, reasoning, tool_calls)。"""
        payload = {"model": self.model, "messages": messages, "stream": True}
        defs = self._tool_defs() if tools else []
        if defs:
            payload["tools"] = defs
            payload["tool_choice"] = "auto"

        thinking = {}
        if not self._thinking_fallback and self.reasoning_level != "none":
            thinking = thinking_params(self.url, self.reasoning_level)
            payload.update(thinking)

        content, reasoning = [], []
        calls = {}          # index -> {id, type, function:{name, arguments}}

        def post(body):
            return requests.post(
                f"{self.url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json"},
                json=body,
                stream=True,
                timeout=(15, 600),    # (连接超时, 读取超时)：思考久也不会被掐断
            )

        # 网关不认思考参数时，去掉它重试一次
        resp = post(payload)
        if resp.status_code == 400 and thinking:
            self._thinking_fallback = True
            for key in thinking:
                payload.pop(key, None)
            resp.close()
            resp = post(payload)

        with resp:
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}：{resp.text[:300]}")

            for raw in resp.iter_lines():
                if self._stopped:
                    break
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue

                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                piece = delta.get("reasoning_content")
                if piece:
                    reasoning.append(piece)
                    self.think.emit(piece)

                piece = delta.get("content")
                if piece:
                    content.append(piece)
                    self.chunk.emit(piece)

                # 工具调用是分片下发的：id/name 只在第一片出现，arguments 要累积拼接
                for tc in (delta.get("tool_calls") or []):
                    idx = tc.get("index", 0)
                    slot = calls.setdefault(idx, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

        ordered = [calls[i] for i in sorted(calls) if calls[i]["function"]["name"]]
        return "".join(content), "".join(reasoning), ordered

class ModelsWorker(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, url, key):
        super().__init__()
        self.url = url
        self.key = key

    def run(self):
        try:
            resp = requests.get(
                f"{self.url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {self.key}"},
                timeout=30
            )
            if resp.status_code != 200:
                self.failed.emit(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return
            data = resp.json()
            ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
            self.done.emit(sorted(ids))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")

class MainWindow(QMainWindow, Ui_MainWindow):
    probe_requested = Signal()
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        self.chat_history = []
        self._pending = False
        self._web_ready = False
        self._initial_loaded = False
        self._theme_mode = "auto"          # 偏好：跟随系统 / 浅色 / 深色
        self._theme = "light"              # 解析后真正生效的那一套
        self._pending_sources = []
        self._attachments = []
        self._streaming = False
        self._settings = QSettings(ORG, APP_NAME)
        self._reasoning_caps = {}         # "url|model" -> "yes"/"no"
        self._probe_worker = None
        self.probe_requested.connect(self._maybe_probe_reasoning)
        self.web_server = None
        self._web_worker = None
        self._gui_tasks = queue.Queue()

        self._models_by_url = {}
        self._selected_by_url = {}
        self._reasoning_caps = {}
        self._current_url = ""
        self._search_test_cache = {}      # (provider, key) -> (ts, ok, msg)

        self._init_ui()
        self._gui_timer = QTimer(self)
        self._gui_timer.timeout.connect(self._drain_gui_tasks)
        self._gui_timer.start(20)      # HTTP 线程的任务在这里执行
        self._connect_signals()
        self._load_settings()
        theme.watch_system(self._on_system_scheme_changed)
        self._ui_ready = True             # 加载完成后再允许探测能力
        self._load_history()
        attach_mod.cleanup_old()          # 清掉过期的附件副本

    def _init_ui(self):
        header = self.models_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)

        self.models_table.setColumnWidth(1, 80)
        self.models_table.setColumnWidth(2, 80)
        self.models_table.verticalHeader().setVisible(False)   # 不要行号，更干净
        self.models_table.setShowGrid(False)

        for key in PROVIDER_ORDER:
            self.search_provider_combo.addItem(PROVIDERS[key]["label"], key)

        for label, data in theme.MODE_ITEMS:
            self.theme_mode_combo.addItem(label, data)

        # 附件相关控件来自 main_window.ui（attach_button / attach_bar / attach_layout）
        self.attach_layout.setSpacing(6)

        self._check_filter = CheckCellFilter(self.models_table, 1, self._toggle_row)
        self.models_table.viewport().installEventFilter(self._check_filter)

        page = ChatPage(self.textBrowser)
        if hasattr(page, "newWindowRequested"):        # 兜底：target=_blank 之类的新窗口请求
            page.newWindowRequested.connect(self._on_new_window)
        # 允许页面读取本地文件：生成的图片是 file:// 路径
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.textBrowser.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.textBrowser.setPage(page)                 # 链接走系统浏览器
        self.textBrowser.titleChanged.connect(self._on_web_title)
        self.textBrowser.loadFinished.connect(self._on_web_loaded)
        self.textBrowser.load(QUrl.fromLocalFile(str(WEB_DIR / "chat.html")))

    SECRET_KEYS = ("api_key", "image_key", "search_key")

    def _secret(self, name):
        """读密钥：钥匙串优先；旧的明文值迁移过去并从 QSettings 删除。"""
        value = secrets_store.get(name)
        if value:
            return value

        legacy = self._settings.value(name, "", type=str)
        if legacy:
            secrets_store.set(name, legacy)
            self._settings.remove(name)
            self._settings.sync()
        return legacy

    def _load_settings(self):
        s = self._settings
        key = self._secret("api_key")
        if key:
            self.api_key_text.setText(key)
        url = s.value("api_url", "", type=str)
        if url:
            self.api_url_text.setText(url)
        saved_prompt = s.value("system_prompt", "", type=str)
        # 旧版本把「能力说明」也存进了用户提示词里，这里识别并替换成新版人设，
        # 否则会和内置能力说明重复一份。
        if (saved_prompt.strip().startswith("你是运行在 Graphic_Dev")
                and "## 输出格式" in saved_prompt):
            saved_prompt = DEFAULT_SYSTEM_PROMPT
        self.prompt_text.setPlainText(saved_prompt or DEFAULT_SYSTEM_PROMPT)

        def _read(name):
            raw = s.value(name, "", type=str)
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {}

        self._models_by_url = _read("models_by_url")
        self._selected_by_url = _read("selected_by_url")
        self._current_url = self._norm_url(self.api_url_text.text())
        self._load_models_for_url(self._current_url)
        self.search_key_text.setText(self._secret("search_key"))
        self.ensure_delete_conversation_again_checkbox.setChecked(
            str(s.value("confirm_delete", "true")).lower() == "true")
        self.reasoning_check.setChecked(
            str(s.value("deep_thinking", "true")).lower() == "true")
        self.capability_prompt_check.setChecked(
            str(s.value("capability_prompt", "true")).lower() == "true")
        saved_theme = s.value("theme_mode", "auto", type=str)
        self._theme_mode = saved_theme if saved_theme in theme.MODES else "auto"
        theme_index = self.theme_mode_combo.findData(self._theme_mode)
        if theme_index >= 0:
            self.theme_mode_combo.setCurrentIndex(theme_index)
        # main() 里已经按设置刷过一遍，这里只记下实际生效的明暗，不再重复加载
        self._theme = theme.resolve(self._theme_mode)
        try:
            cached = json.loads(s.value("reasoning_caps", "{}", type=str)) or {}
            # 只保留当前探测版本（v2）的结论，旧版本结论可能不准
            self._reasoning_caps = {k: v for k, v in cached.items()
                                    if isinstance(k, str) and k.startswith("v3|")}
        except (ValueError, TypeError):
            self._reasoning_caps = {}
        self.image_model_text.setText(s.value("image_model", "", type=str))
        self.image_url_text.setText(s.value("image_url", "", type=str))
        self.image_key_text.setText(self._secret("image_key"))
        saved_size = s.value("image_size", "1024x1024", type=str)
        size_index = self.image_size_combo.findText(saved_size)
        if size_index >= 0:
            self.image_size_combo.setCurrentIndex(size_index)
        saved_provider = s.value("search_provider", DEFAULT_PROVIDER, type=str)
        idx = self.search_provider_combo.findData(saved_provider)
        if idx >= 0:
            self.search_provider_combo.setCurrentIndex(idx)
        self.search_check.setChecked(str(s.value("use_search", "false")).lower() == "true")

        if not self.capability_prompt_check.isChecked():
            # 延后一点，避免被初始化阶段其它状态提示覆盖
            QTimer.singleShot(1200, lambda: self.statusbar.showMessage(
                "内置能力说明已关闭，公式 / 图表 / 配图可能不渲染", 10000))

        self._update_reasoning_control()

        tip = "密钥保存在：" + secrets_store.status_text()
        for widget in (self.api_key_text, self.image_key_text, self.search_key_text):
            widget.setToolTip(tip)

    def _save_settings(self):
        s = self._settings
        for name, widget in (("api_key", self.api_key_text),
                             ("image_key", self.image_key_text),
                             ("search_key", self.search_key_text)):
            secrets_store.set(name, widget.text().strip())
            s.remove(name)                      # 旧版本可能留下明文副本
        s.setValue("api_url", self.api_url_text.text().strip())
        s.setValue("system_prompt", self.prompt_text.toPlainText())
        if self._current_url:
            self._models_by_url[self._current_url] = self._existing_models()
            self._selected_by_url[self._current_url] = self.current_model()
        s.setValue("models_by_url", json.dumps(self._models_by_url, ensure_ascii=False))
        s.setValue("selected_by_url", json.dumps(self._selected_by_url, ensure_ascii=False))
        s.setValue("search_provider", self.search_provider_combo.currentData() or DEFAULT_PROVIDER)
        s.setValue("confirm_delete",
                   self.ensure_delete_conversation_again_checkbox.isChecked())
        s.setValue("deep_thinking", self.reasoning_check.isChecked())
        s.setValue("capability_prompt", self.capability_prompt_check.isChecked())
        s.setValue("use_search", self.search_check.isChecked())
        s.setValue("image_model", self.image_model_text.text().strip())
        s.setValue("image_url", self.image_url_text.text().strip())
        s.setValue("image_size", self.image_size_combo.currentText())
        s.sync()                                # 立刻落盘，别让旧明文留在 plist 里

    def system_prompt(self):
        """用户提示词 + 内置能力说明（后者不可被用户覆盖）。"""
        text = self.prompt_text.toPlainText().strip()
        if not self.capability_prompt_check.isChecked():
            return text or DEFAULT_SYSTEM_PROMPT      # 用户选择不使用内置说明

        parts = [text or DEFAULT_SYSTEM_PROMPT, CAPABILITY_PROMPT]

        if self.image_model_text.text().strip():
            parts.append(IMAGE_CAPABILITY)
        if self.search_check.isChecked() and self.search_key_text.text().strip():
            parts.append(SEARCH_CAPABILITY)

        return "\n\n".join(part for part in parts if part.strip())

    def closeEvent(self, event):
        # 生成中直接关窗口会让 QThread 被 GC —— Qt 会 abort（进程崩溃）。
        # 先让 worker 停下来并等它退出。
        for worker in (getattr(self, "worker", None),
                       getattr(self, "_web_worker", None),
                       getattr(self, "models_worker", None),
                       getattr(self, "_probe_worker", None)):
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    if hasattr(worker, "cancel"):
                        worker.cancel()
                    if not worker.wait(3000):
                        worker.terminate()      # 仍不退出就强制结束，避免销毁运行中的线程导致崩溃
                        worker.wait(1000)
            except RuntimeError:
                pass

        if self.web_server is not None:
            self.web_server.stop()
        self._save_settings()
        super().closeEvent(event)

    def _on_new_window(self, request):
        """页面里的新窗口请求（如 target=_blank）也交给系统浏览器。"""
        QDesktopServices.openUrl(request.requestedUrl())

    # ---------- 停止生成 / 删除消息 ----------
    def _set_streaming(self, streaming: bool):
        self._streaming = streaming
        self.send_button.setText("停止" if streaming else "发送")

    def on_send_or_stop(self):
        if self._streaming:
            if self.worker is not None:
                self.worker.cancel()
            self.statusbar.showMessage("正在停止…", 3000)
            return
        self.on_send()

    def _on_web_title(self, title: str):
        """接住前端通过 document.title 发来的动作。"""
        if not title.startswith("dsh:"):
            return
        parts = title.split(":")            # dsh:<动作>:<index>:[<参数>]:<ts>
        if len(parts) < 3:
            return
        try:
            index = int(parts[2])
        except ValueError:
            return

        if parts[1] == "delete":
            self._delete_turn(index)
        elif parts[1] == "export" and len(parts) >= 4:
            self._export_message(index, parts[3])
        elif parts[1] == "saveimage" and len(parts) >= 4:
            self._save_image(unquote(parts[3]))

    def _delete_turn(self, index: int):
        if self._streaming:
            self.statusbar.showMessage("生成中不能删除，请先停止", 3000)
            return
        if not (0 <= index < len(self.chat_history)):
            return

        msg = self.chat_history[index]
        targets = [index]

        # 用户消息和它的回答一起删；删回答时连带上面的问题
        if msg["role"] == "user" and index + 1 < len(self.chat_history) \
                and self.chat_history[index + 1]["role"] == "assistant":
            targets.append(index + 1)
        elif msg["role"] == "assistant" and index > 0 \
                and self.chat_history[index - 1]["role"] == "user":
            targets.append(index - 1)

        # 需要确认时先弹框，默认按钮是"否"，避免误触
        if self.ensure_delete_conversation_again_checkbox.isChecked():
            preview = ""
            for i in sorted(targets):
                m = self.chat_history[i]
                who = "你" if m["role"] == "user" else "模型"
                text = (m.get("content") or "").strip().replace("\n", " ")
                preview += f"{who}：{text[:60]}{'…' if len(text) > 60 else ''}\n"

            ret = QMessageBox.question(
                self, "确认删除",
                f"确定要删除这条对话吗？\n\n{preview}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        for i in sorted(targets, reverse=True):
            removed = self.chat_history.pop(i)
            for f in (removed.get("files") or []):
                attach_mod.delete_stored(f.get("path"))

        self._save_history()
        self._render_chat()
        self.statusbar.showMessage("已删除这条对话", 3000)

    def _save_image(self, src: str):
        """把聊天里的图片另存到用户选的位置。"""
        src = (src or "").strip()
        if not src:
            return

        name = "image.png"
        if src.startswith("file://"):
            local = pathlib.Path(QUrl(src).toLocalFile())
            if not local.is_file():
                QMessageBox.warning(self, "保存失败", "找不到这张图片文件，可能已被删除。")
                return
            name = local.name
        else:
            local = None

        target, _ = QFileDialog.getSaveFileName(
            self, "保存图片", str(pathlib.Path.home() / "Downloads" / name),
            "图片 (*.png *.jpg *.jpeg *.webp)")
        if not target:
            return

        try:
            if local is not None:
                shutil.copyfile(local, target)
            else:
                resp = requests.get(src, timeout=60)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                pathlib.Path(target).write_bytes(resp.content)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"{type(e).__name__}: {e}")
            return
        self.statusbar.showMessage(f"图片已保存：{target}", 8000)

    def _export_message(self, index: int, fmt: str):
        if not (0 <= index < len(self.chat_history)):
            return
        msg = self.chat_history[index]
        if msg.get("role") != "assistant":
            return

        content = (msg.get("content") or "").strip()
        if not content:
            self.statusbar.showMessage("这条回答没有可导出的内容", 3000)
            return

        entry = exporters.EXPORTERS.get(fmt)
        if entry is None:
            return
        exporter, file_filter = entry

        default = pathlib.Path.home() / "Downloads" / self._suggest_filename(content, fmt)
        path, _ = QFileDialog.getSaveFileName(self, "导出回答", str(default), file_filter)
        if not path:
            return

        try:
            self.statusbar.showMessage("正在生成文档…")
            QApplication.processEvents()          # 让状态栏先刷新，导出期间界面不假死
            exporter(content, path, msg.get("sources") or [])
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        self.statusbar.showMessage(f"已导出：{path}", 8000)

    @staticmethod
    def _suggest_filename(content: str, fmt: str) -> str:
        """用一级标题或首行当文件名。"""
        name = ""
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                name = line.lstrip("#").strip()
            else:
                name = line
            break
        name = re.sub(r'[\\/:*?"<>|]+', "_", name)[:40] or "ai-answer"
        stamp = time.strftime("%Y%m%d-%H%M")
        return f"{name}-{stamp}.{fmt}"

    def _on_web_loaded(self, ok):
        if ok:
            self._web_ready = True
            self._initial_loaded = True
            self._apply_web_theme()     # 先定明暗，再渲染，避免浅色闪一下
            self._render_chat()
            return

        # 注意：点击被 ChatPage 拦下的链接时，WebEngine 也会补发一次
        # loadFinished(False)，但页面其实没变。只有首次加载失败才算真错误。
        if not self._initial_loaded:
            QMessageBox.critical(self, "页面加载失败", f"无法加载 {WEB_DIR / 'chat.html'}")

    def _render_chat(self):
        if not self._web_ready:
            return
        turns = []
        for m in self.chat_history:
            files = [
                {"name": f.get("name", ""),
                 "kind": f.get("kind", "text"),
                 "url": file_url(f["path"])}
                for f in (m.get("files") or []) if f.get("path")
            ]
            turns.append({
                "role": m["role"],
                "content": m["content"].strip(),
                "reasoning": (m.get("reasoning") or "").strip(),
                "sources": m.get("sources") or [],
                "attachments": files,
            })
        script = f"renderChat({json.dumps(turns, ensure_ascii=True)}, {"true" if self._pending else "false"});"
        self.textBrowser.page().runJavaScript(script)

    def current_model(self):
        for r in range(self.models_table.rowCount()):
            name = self.models_table.item(r, 0)
            check = self.models_table.item(r, 1)
            if name is not None and check is not None and check.checkState() == Qt.CheckState.Checked:
                return name.text()
        return ""

    def _existing_models(self):
        return [self.models_table.item(r, 0).text()
                for r in range(self.models_table.rowCount())
                if self.models_table.item(r, 0) is not None]

    @staticmethod
    def _norm_url(url):
        return (url or "").strip().rstrip("/")

    def _on_api_url_changed(self):
        url = self._norm_url(self.api_url_text.text())
        if url == self._current_url:
            return
        if self._current_url:
            self._models_by_url[self._current_url] = self._existing_models()
            self._selected_by_url[self._current_url] = self.current_model()
        self._current_url = url
        self._load_models_for_url(url)

    def _load_models_for_url(self, url):
        models = self._models_by_url.get(url, [])
        selected = self._selected_by_url.get(url, [])

        self.models_table.blockSignals(True)
        self.models_table.setRowCount(0)
        self.models_table.blockSignals(False)

        for name in models:
            self._add_model_row(name, checked=(name == selected))

        self._refresh_check_marks()
        self._refresh_model_label()

        if models:
            self.statusbar.showMessage(f"已恢复该地址的 {len(models)} 个模型", 3000)
        else:
            self.statusbar.showMessage("新地址还没有模型，点「添加模型」获取", 5000)

    def _drop_stale_models(self, available):
        available = set(available)
        for name in list(self._existing_models()):
            if name in available:
                continue
            for r in range(self.models_table.rowCount()):
                item = self.models_table.item(r, 0)
                if item is not None and item.text() == name:
                    self.models_table.removeRow(r)
                    break
        self._refresh_check_marks()
        self._refresh_model_label()

    def _add_model_row(self, name, checked=None):
        if name in self._existing_models():
            return
        row = self.models_table.rowCount()
        self.models_table.insertRow(row)

        name_item = QTableWidgetItem(name)
        name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        name_item.setToolTip(name)
        self.models_table.setItem(row, 0, name_item)

        check_item = QTableWidgetItem("")
        check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        check_item.setCheckState(Qt.CheckState.Checked if checked
                                 else Qt.CheckState.Unchecked)
        self.models_table.setItem(row, 1, check_item)

        del_btn = QPushButton("删除")
        del_btn.clicked.connect(lambda _=False, it=name_item: self._delete_model_row(it))
        self.models_table.setCellWidget(row, 2, del_btn)

    def _delete_model_row(self, name_item):
        row = name_item.row()
        if row < 0:
            return
        self.models_table.removeRow(row)
        self._refresh_model_label()

    def _on_capability_toggled(self, checked):
        """关闭内置能力说明时提醒后果；重新打开也确认一下。"""
        if checked:
            self.statusbar.showMessage("已启用内置能力说明", 5000)
        else:
            self.statusbar.showMessage(
                "内置能力说明已关闭，公式 / 图表 / 配图可能不渲染", 10000)

    def _set_theme_mode(self, mode):
        """切主题：记住偏好，同时刷 Qt 端（QSS + 调色板）和聊天页。"""
        self._theme_mode = mode if mode in theme.MODES else "auto"
        index = self.theme_mode_combo.findData(self._theme_mode)
        if index >= 0 and self.theme_mode_combo.currentIndex() != index:
            self.theme_mode_combo.setCurrentIndex(index)
        self._theme = apply_theme(self._theme_mode)
        self._settings.setValue("theme_mode", self._theme_mode)
        self._settings.sync()
        self._apply_web_theme()

    def _on_theme_selected(self, index):
        """个性化页的主题下拉框，只有用户点击才会触发。"""
        self._set_theme_mode(self.theme_mode_combo.itemData(index))
        self.statusbar.showMessage(
            "已切换到%s主题" % ("深色" if self._theme == "dark" else "浅色"), 4000)

    def _on_system_scheme_changed(self):
        """选了「跟随系统」时，macOS 切明暗要立刻跟上；另外两档忽略。"""
        if self._theme_mode != "auto":
            return
        self._theme = apply_theme("auto")
        self._apply_web_theme()

    def _apply_web_theme(self):
        """把明暗同步给聊天页：只改 <html data-theme>，页面不用重新加载。"""
        if not self._web_ready:
            return
        self.textBrowser.page().runJavaScript(theme.page_script(self._theme))

    def on_clear_config(self):
        """一键清空本机配置（含钥匙串里的密钥），聊天记录不受影响。"""
        ret = QMessageBox.question(
            self,
            "清空所有配置",
            "确定要清空所有配置吗？\n\n"
            "会删除：API 地址与密钥、图片接口配置、搜索 Key、\n"
            "模型列表、系统提示词、界面偏好。\n\n"
            "聊天记录和已生成的图片不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # 1) 落盘配置
        self._settings.clear()
        self._settings.sync()

        # 2) 密钥（钥匙串 + 兜底文件）
        try:
            secrets_store.clear()
        except Exception:                              # noqa: BLE001
            pass

        # 3) 界面复位
        self._models_by_url = {}
        self._selected_by_url = {}
        self._reasoning_caps = {}
        self._current_url = ""
        self.models_table.setRowCount(0)
        self.api_key_text.clear()
        self.api_url_text.clear()
        self.search_key_text.clear()
        self.image_key_text.clear()
        self.image_url_text.clear()
        self.image_model_text.clear()
        self.prompt_text.setPlainText(DEFAULT_SYSTEM_PROMPT)
        self.image_size_combo.setCurrentIndex(0)
        index = self.search_provider_combo.findData(DEFAULT_PROVIDER)
        if index >= 0:
            self.search_provider_combo.setCurrentIndex(index)
        self.ensure_delete_conversation_again_checkbox.setChecked(True)
        self.reasoning_check.setChecked(True)
        self.capability_prompt_check.setChecked(True)
        self.search_check.setChecked(False)
        self._set_theme_mode("auto")    # 界面偏好也回到「跟随系统」

        if hasattr(self, "model_select_name"):
            self.model_select_name.setText("")
        self._refresh_model_label()

        self._save_settings()
        self.statusbar.showMessage("配置已清空，请重新填写 API 地址和密钥", 8000)

    def clear_all_models(self):
        count = self.models_table.rowCount()
        if count == 0:
            self.statusbar.showMessage("模型列表已经是空的", 3000)
            return

        ret = QMessageBox.question(
            self,
            "确认清空",
            f"确定要删除全部 {count} 个模型吗？\n"
            "（只会清空当前 API 地址下的列表，不影响其它地址）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self.models_table.setRowCount(0)  # 连同「删除」按钮一起移除
        self._models_by_url[self._current_url] = []
        self._selected_by_url[self._current_url] = ""
        self._refresh_check_marks()
        self._refresh_model_label()
        self.statusbar.showMessage(f"已清空 {count} 个模型", 3000)

    def _toggle_row(self, row):
        self.models_table.blockSignals(True)
        for r in range(self.models_table.rowCount()):
            item = self.models_table.item(r, 1)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked if r == row
                                   else Qt.CheckState.Unchecked)
        self.models_table.blockSignals(False)
        self._refresh_check_marks()
        self._refresh_model_label()

    def _refresh_check_marks(self):
        self.models_table.blockSignals(True)
        for r in range(self.models_table.rowCount()):
            check = self.models_table.item(r, 1)
            name = self.models_table.item(r, 0)
            if check is None:
                continue
            chosen = check.checkState() == Qt.CheckState.Checked
            if name is not None:
                font = name.font()
                font.setBold(chosen)
                name.setFont(font)
        self.models_table.blockSignals(False)

    @staticmethod
    def _cap_key(url: str, model: str) -> str:
        # v2：探测方式变了，旧结论作废
        return "v3|" + (url or "").strip().rstrip("/") + "|" + (model or "").strip()

    def _reasoning_state(self):
        """推理强度是否可用：优先用探测结果，其次用名字启发式。"""
        model = self.current_model()
        cfg = self.get_api_config()
        url = cfg[0] if cfg else ""

        if not model:
            return False, "请先选择模型"

        cached = self._reasoning_caps.get(self._cap_key(url, model))
        if cached == "yes":
            return True, ""
        if cached == "no":
            return False, "该模型/服务端不支持调整推理强度（已实测）"

        supported, note = reasoning_support(url, model)
        if supported and cfg and getattr(self, "_ui_ready", False):
            # 还没测过：抛个信号让 GUI 线程去测（HTTP 线程里不能直接建 QThread）
            self.probe_requested.emit()
        return supported, note

    def _probe_state(self):
        """pending = 还没测出结果，网页端据此稍后重拉配置。"""
        cfg = self.get_api_config()
        model = self.current_model()
        if not cfg or not model:
            return ""
        cached = self._reasoning_caps.get(self._cap_key(cfg[0], model))
        if cached:
            return cached
        return "pending" if reasoning_support(cfg[0], model)[0] else "no"

    def _maybe_probe_reasoning(self):
        """后台实测一次，结果缓存到配置里，只测每个 (地址, 模型) 一次。"""
        cfg = self.get_api_config()
        model = self.current_model()
        if not cfg or not model:
            return
        cap_key = self._cap_key(cfg[0], model)
        if cap_key in self._reasoning_caps:
            return
        if self._probe_worker is not None and self._probe_worker.isRunning():
            return

        self._probe_worker = ReasoningProbeWorker(cfg[0], cfg[1], model, cap_key)
        self._probe_worker.done.connect(self._on_reasoning_probe)
        self._probe_worker.start()

    def _on_reasoning_probe(self, cap_key: str, verdict: str):
        if verdict == "unknown":
            return                                  # 测不出来就保持启发式
        self._reasoning_caps[cap_key] = verdict
        try:
            self._settings.setValue("reasoning_caps",
                                    json.dumps(self._reasoning_caps, ensure_ascii=False))
            self._settings.sync()
        except Exception:                            # noqa: BLE001
            pass
        self._update_reasoning_control()

    def _update_reasoning_control(self):
        """模型不支持推理强度时，把客户端复选框置灰。"""
        supported, note = self._reasoning_state()
        self.reasoning_check.setEnabled(supported)
        self.reasoning_check.setToolTip(
            note or "关闭后直接作答，速度更快；部分模型不支持关闭思考")

    def _refresh_model_label(self):
        self.model_select_name.setText(self.current_model() or "（未选择）")
        self._update_reasoning_control()

    # ---------- 图片模型列表 ----------
    BUILTIN_IMAGE_MODELS = [
        "qwen-image", "qwen-image-plus", "qwen-image-turbo",
        "qwen-image-2.1-turbo", "qwen-image-2.1-plus",
        "wanx2.1-t2i-turbo", "wanx2.1-t2i-plus", "wanx-v1",
        "gpt-image-1", "dall-e-3",
        "flux.1-schnell", "flux.1-dev",
        "stable-diffusion-3.5-large", "kolors", "cogview-3-plus",
    ]
    IMAGE_MODEL_HINTS = ("image", "wanx", "flux", "dall", "sd-", "sdxl",
                         "stable", "kolors", "t2i", "seedream", "cogview",
                         "imagen", "kandinsky")

    @staticmethod
    def _models_endpoints(base: str):
        """从图片接口地址推出候选的 /models 端点（按可能性排序）。"""
        url = (base or "").strip().rstrip("/")
        if not url:
            return []

        if "/services/" in url:                     # 百炼原生：.../api/v1/services/aigc/...
            url = url.split("/services/")[0]
        if url.endswith("/images/generations"):
            url = url[: -len("/images/generations")]

        root = url
        for suffix in ("/compatible-mode/v1", "/api/v1", "/v1"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break

        return [
            root + "/compatible-mode/v1/models",    # 百炼兼容模式
            root + "/v1/models",                    # OpenAI 兼容 / LM Studio
            root + "/api/v1/models",                # 百炼原生（多数不提供）
        ]

    def pick_image_model(self):
        """拉取接口上的图片模型，选一个填进输入框。"""
        base = (self.image_url_text.text().strip()
                or self.api_url_text.text().strip())
        key = (self.image_key_text.text().strip()
               or self.api_key_text.text().strip())

        models, error = [], ""
        endpoints = self._models_endpoints(base)
        if endpoints and key:
            self.statusbar.showMessage("正在获取图片模型…")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                for endpoint in endpoints:
                    try:
                        resp = requests.get(
                            endpoint, headers={"Authorization": f"Bearer {key}"},
                            timeout=15)
                    except Exception as e:              # 换下一个候选
                        error = f"{type(e).__name__}: {e}"
                        continue
                    if resp.status_code != 200:
                        error = f"HTTP {resp.status_code}"
                        continue
                    ids = [m.get("id") for m in (resp.json().get("data") or [])
                           if m.get("id")]
                    found = [m for m in ids
                             if any(h in m.lower() for h in self.IMAGE_MODEL_HINTS)]
                    if found:
                        models, error = found, ""
                        break
                    error = "接口有响应，但没有图片类模型"
            finally:
                QApplication.restoreOverrideCursor()

        # 拉到了就只列真实存在的；拉不到才用内置建议（并明确标注）
        current = self.image_model_text.text().strip()
        if models:
            merged = list(dict.fromkeys(models))
            title = "选择图片模型"
            self.statusbar.showMessage(f"已获取 {len(models)} 个图片模型", 4000)
        else:
            merged = list(dict.fromkeys(self.BUILTIN_IMAGE_MODELS))
            title = "选择图片模型（内置建议，可能不被接口支持）"
            reason = f"\n\n最后一次失败：{error}" if error else ""
            QMessageBox.information(
                self, "拿不到图片模型列表",
                "这个接口没有提供「列出模型」的能力，所以下面显示的是常见模型名，"
                "它们不一定被你的账号支持。\n\n"
                "常见原因：\n"
                "· 百炼原生接口（/services/aigc/...）本身不提供 /models；\n"
                "· KEY 没有模型列表权限；\n"
                "· 网络问题。"
                + reason)
            self.statusbar.showMessage("没能从接口拿到模型列表，下面只是常见名字", 8000)

        dlg = ModelPickerDialog(merged, [], self, single=True, title=title,
                                checked=[current] if current else [])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = dlg.checked_models()
        if picked:
            self.image_model_text.setText(picked[0])
            self.statusbar.showMessage(f"已选择图片模型：{picked[0]}", 4000)

    def image_config(self):
        """文生图配置；没填模型名就视为不启用。"""
        model = self.image_model_text.text().strip()
        if not model:
            return None
        cfg = self.get_api_config()
        base = self.image_url_text.text().strip() or (cfg[0] if cfg else "")
        key = self.image_key_text.text().strip() or (cfg[1] if cfg else "")
        if not base:
            return None
        return {"base": base, "key": key, "model": model,
                "size": self.image_size_combo.currentText()}

    # ---------- 跨线程执行 ----------
    def _drain_gui_tasks(self):
        while True:
            try:
                task = self._gui_tasks.get_nowait()
            except queue.Empty:
                return
            task()

    def run_in_gui(self, fn, timeout=180):
        """在 GUI 线程同步执行 fn。

        导出要用 QWebEngine 渲染公式、Qt 打印 PDF，这些都只能在 GUI 线程做；
        HTTP 线程直接调用会崩或者静默失败。
        """
        # 万一从 GUI 线程调用，等待会死锁（排队的任务只能由 GUI 线程自己执行）
        if QThread.currentThread() is self.thread():
            return fn()

        done = threading.Event()
        box = {}

        def task():
            try:
                box["value"] = fn()
            except Exception as exc:          # noqa: BLE001
                box["error"] = exc
            finally:
                done.set()

        self._gui_tasks.put(task)
        if not done.wait(timeout):
            raise RuntimeError("导出超时")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # ---------- 聊天记录持久化 ----------
    def _load_history(self):
        """启动时恢复上次的对话（图片从附件副本重新生成 data URL）。"""
        self.chat_history = []
        try:
            if not HISTORY_FILE.is_file():
                return
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            turns = data.get("turns") if isinstance(data, dict) else data
        except (OSError, ValueError):
            return
        if not isinstance(turns, list):
            return

        restored = []
        for message in turns:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                continue
            item = {
                "role": message["role"],
                "content": message.get("content") or "",
                "reasoning": message.get("reasoning") or "",
                "sources": message.get("sources") or [],
            }
            texts = message.get("texts") or []
            if texts:
                item["texts"] = texts
            files = message.get("files") or []
            if files:
                item["files"] = files
                images = []
                for f in files:
                    path = f.get("path")
                    if f.get("kind") != "image" or not path:
                        continue
                    try:
                        if pathlib.Path(path).is_file():
                            images.append(
                                attach_mod.image_to_data_url(pathlib.Path(path)))
                    except Exception:            # 图片丢了不影响文字上下文
                        continue
                if images:
                    item["images"] = images
            restored.append(item)

        self.chat_history = restored[-MAX_HISTORY:]
        if getattr(self, "_web_ready", False):
            self._render_chat()      # 页面已就绪时立刻补一次渲染

    def _save_history(self):
        """原子写入，按条数/体积裁剪。"""
        turns = []
        for message in self.chat_history[-MAX_HISTORY:]:
            turns.append({
                "role": message.get("role"),
                "content": message.get("content") or "",
                "reasoning": message.get("reasoning") or "",
                "sources": message.get("sources") or [],
                "texts": message.get("texts") or [],
                "files": message.get("files") or [],
            })

        try:
            payload = json.dumps({"turns": turns}, ensure_ascii=False)
            while turns and len(payload.encode("utf-8")) > MAX_HISTORY_BYTES:
                turns = turns[2:]
                payload = json.dumps({"turns": turns}, ensure_ascii=False)
            tmp = HISTORY_FILE.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(HISTORY_FILE)
        except OSError:
            pass

    # ---------- 本地 Web 入口 ----------
    def open_web_ui(self):
        """启动本地服务并在系统浏览器里打开。"""
        if self.web_server is None:
            self.web_server = web_server.WebChatServer(self)
        url = self.web_server.start()
        QDesktopServices.openUrl(QUrl(url))
        self.statusbar.showMessage(f"Web 入口已启动：{url}", 10000)

    def cancel_web_stream(self):
        """网页端点「停止」时取消当前生成。"""
        worker = self._web_worker
        if worker is not None:
            worker.cancel()

    def select_web_model(self, name):
        """网页端选模型 → 勾选客户端列表里的同名行（跨线程，排队到 GUI 线程）。

        模型列表和勾选状态只属于桌面端，网页端只发一个名字过来。
        """
        target = (name or "").strip()
        if not target:
            return False

        def apply():
            for r in range(self.models_table.rowCount()):
                item = self.models_table.item(r, 0)
                if item is not None and item.text() == target:
                    self._toggle_row(r)      # 同时刷新加粗、标签文字和推理控件
                    return True
            return False

        try:
            return bool(self.run_in_gui(apply))
        except Exception:                    # GUI 线程未就绪或超时
            return False

    def open_local_file(self, path):
        """用系统默认程序打开本地文件（网页端点附件 chip 时调用）。

        浏览器碰不到 file://，只能由客户端替它调用系统应用。
        """
        def open_it():
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        try:
            # 用户点了就该有反馈，不能用默认的分钟级超时吊着浏览器
            return bool(self.run_in_gui(open_it, timeout=10))
        except Exception:                    # GUI 线程未就绪或超时
            return False

    def apply_web_settings(self, search=None, thinking=None):
        """网页端切换开关 → 同步到客户端复选框（跨线程，排队执行）。"""
        from PySide6.QtCore import QMetaObject, Q_ARG

        if search is not None:
            QMetaObject.invokeMethod(self.search_check, "setChecked",
                                     Qt.ConnectionType.QueuedConnection,
                                     Q_ARG(bool, bool(search)))
        if thinking is not None:
            QMetaObject.invokeMethod(self.reasoning_check, "setChecked",
                                     Qt.ConnectionType.QueuedConnection,
                                     Q_ARG(bool, bool(thinking)))

    def web_config(self):
        """给网页看的配置状态（不含密钥）。"""
        cfg = self.get_api_config()
        model = self.current_model()
        ready = bool(cfg and model)
        if not cfg:
            reason = "请先在客户端「API 配置」填写地址和 KEY"
        elif not model:
            reason = "请先选择模型"
        else:
            reason = ""
        search_available = bool(self.search_key_text.text().strip())
        return {
            "ready": ready,
            "reason": reason,
            "model": model,
            "models": self._existing_models(),
            "search": self.search_check.isChecked() and search_available,
            "search_available": search_available,
            "image": bool(self.image_config()),
            "thinking": self.reasoning_check.isChecked(),
            "reasoning_supported": self._reasoning_state()[0],
            "reasoning_note": self._reasoning_state()[1],
            "reasoning_probe": self._probe_state(),
            "exports": list(exporters.EXPORTERS.keys()),
        }

    def reasoning_level(self):
        """桌面端复选框 → 推理强度（网页端会另外指定）。"""
        if not self._reasoning_state()[0]:
            return "none"                    # 模型不支持就完全不下发参数
        return "medium" if self.reasoning_check.isChecked() else "off"

    def web_stream(self, messages, reasoning=None):
        """把 ChatWorker 的信号转成生成器，供 HTTP 响应逐条写出。"""
        cfg = self.get_api_config()
        model = self.current_model()
        if cfg is None or not model:
            yield ("error", "客户端配置不完整")
            return

        url, key = cfg
        q = queue.Queue()
        worker = ChatWorker(
            url, key, model, messages,
            use_search=self.search_check.isChecked(),
            search_key=self.search_key_text.text().strip(),
            search_provider=self.search_provider_combo.currentData() or DEFAULT_PROVIDER,
            reasoning_level=("none" if not self._reasoning_state()[0] else
                             (reasoning or "medium")
                             if (reasoning or self.reasoning_check.isChecked())
                             else "off"),
            image_config=self.image_config(),
        )
        # 必须用 DirectConnection：这些 lambda 没有 QObject 归属，
        # 默认连接方式会把信号投递到"接收者线程"的事件循环，而 HTTP 线程没有
        # 事件循环 → 永远收不到，请求会挂死。
        direct = Qt.ConnectionType.DirectConnection
        worker.chunk.connect(lambda t: q.put(("chunk", t)), direct)
        worker.think.connect(lambda t: q.put(("think", t)), direct)
        worker.search_note.connect(lambda t: q.put(("note", t)), direct)
        worker.sources.connect(lambda items: q.put(("sources", items)), direct)
        worker.image_generated.connect(
            lambda md: q.put(("image", self._web_image(md))), direct)
        worker.finished.connect(
            lambda content, reasoning: q.put(("done", content)), direct)
        worker.error_occurred.connect(lambda e: q.put(("error", e)), direct)
        self._web_worker = worker
        worker.start()

        try:
            while True:
                try:
                    kind, data = q.get(timeout=300)     # 兜底：5 分钟无响应
                except queue.Empty:
                    yield ("error", "响应超时")
                    break
                yield kind, data
                if kind in ("done", "error"):
                    break
        finally:
            # 浏览器中途刷新/关闭时，生成器会被丢弃：
            # 必须停掉 worker 并等它退出，否则 QThread 被 GC 时会 abort
            if worker.isRunning():
                worker.cancel()
                worker.wait(3000)
            if self._web_worker is worker:      # 可能已被另一路生成覆盖
                self._web_worker = None

    def _web_image(self, markdown_snippet: str) -> str:
        """把 file:// 图片换成网页能访问的 /media 地址。"""
        match = re.search(r"!\[([^\]]*)\]\(([^)]+)\)", markdown_snippet)
        if not match or self.web_server is None:
            return markdown_snippet

        local = QUrl(match.group(2)).toLocalFile()
        try:
            rel = pathlib.Path(local).relative_to(app_paths.data_dir())
        except (ValueError, TypeError):
            return markdown_snippet
        return "![{}](/media?t={}&p={})".format(
            match.group(1), self.web_server.token, quote(rel.as_posix()))

    def get_api_config(self):
        url = self.api_url_text.text().strip()
        key = self.api_key_text.text().strip()
        if not url or not key:
            return None
        return url, key

    def _connect_signals(self):
        self.send_button.clicked.connect(self.on_send_or_stop)
        self.clear_button.clicked.connect(self.on_clear)
        self.add_models_button.clicked.connect(self.add_model)
        self.api_key_test.clicked.connect(self.on_test_api)
        self.search_key_test.clicked.connect(self.on_test_search)
        self.api_url_text.editingFinished.connect(self._on_api_url_changed)
        self.clear_models_button.clicked.connect(self.clear_all_models)
        # 用 clicked（仅用户点击触发）而非 toggled：程序里 setChecked 恢复配置时
        # 不应联网检测，否则启动/同步设置会被一次搜索请求卡住。
        self.search_check.clicked.connect(self._on_search_toggled)
        self.attach_button.clicked.connect(self.on_attach)
        self.image_models_button.clicked.connect(self.pick_image_model)
        self.web_open_button.clicked.connect(self.open_web_ui)
        self.clear_config_button.clicked.connect(self.on_clear_config)
        self.capability_prompt_check.toggled.connect(
            self._on_capability_toggled)
        # activated 只响应用户操作：_load_settings 恢复选项时不会顺带刷一遍主题
        self.theme_mode_combo.activated.connect(self._on_theme_selected)

    def _search_test(self, force=False):
        """实测当前搜索服务是否可用，返回 (ok, 说明)。

        结论按 (provider, key) 短期缓存：勾选框反复点不会每次都打网络。
        """
        provider = self.search_provider_combo.currentData() or DEFAULT_PROVIDER
        key = self.search_key_text.text().strip()
        if not key:
            return False, f"还没有填写 {provider_label(provider)} 的搜索 KEY"

        cache_key = (provider, key)
        now = time.time()
        if not force:
            hit = self._search_test_cache.get(cache_key)
            if hit and now - hit[0] < SEARCH_TEST_TTL:
                return hit[1], hit[2]

        ok, msg = self._search_probe(provider, key)
        self._search_test_cache[cache_key] = (now, ok, msg)
        return ok, msg

    @staticmethod
    def _search_probe(provider, key):
        """对搜索服务发一次最小请求（不走 search_web 的结果缓存），返回 (ok, 说明)。

        不用 search_web 是因为它的缓存键不含 api_key：换了 key 仍会命中旧 key
        的成功缓存，检测就会失真。
        """
        cfg = PROVIDERS.get(provider)
        if cfg is None:
            return False, f"未知的搜索服务：{provider}"
        label = cfg["label"]
        kwargs = {"headers": cfg["headers"](key), "timeout": SEARCH_TEST_TIMEOUT}
        if cfg["method"] == "GET":
            kwargs["params"] = cfg["params"](key, "连通性测试", 1, None)
        else:
            kwargs["json"] = cfg["json"](key, "连通性测试", 1, None)
        try:
            resp = requests.request(cfg["method"], cfg["endpoint"], **kwargs)
        except requests.RequestException as e:
            return False, f"{label} 请求失败：{e}"
        if resp.status_code != 200:
            return False, f"{label} HTTP {resp.status_code}：{resp.text[:200]}"
        return True, f"{label} 连接正常"

    def on_test_search(self):
        """「检测」按钮：强制实测一次搜索 API 并弹窗反馈。"""
        ok, msg = self._search_test(force=True)
        if ok:
            QMessageBox.information(self, "成功", msg)
        else:
            QMessageBox.critical(self, "搜索 API 检测失败", msg)

    def _on_search_toggled(self, checked):
        """勾选联网搜索时先实测搜索 API；不可用就直接取消勾选，避免带着坏配置去聊天。"""
        if not checked:
            return
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, msg = self._search_test()
        finally:
            self.unsetCursor()
        if ok:
            self.statusbar.showMessage(msg, 5000)
            return
        self.search_check.setChecked(False)      # 不可用：禁止勾选
        QMessageBox.warning(self, "无法启用联网搜索",
                            f"搜索 API 检测未通过，已取消勾选「联网搜索」。\n\n{msg}")

    # ---------- 附件 ----------
    def on_attach(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文件", str(pathlib.Path.home()), attach_mod.FILE_FILTER)
        if not paths:
            return

        errors, added = [], 0
        for one in paths:
            try:
                item = attach_mod.load_attachment(one)
            except attach_mod.AttachmentError as e:
                errors.append(f"{pathlib.Path(one).name}：{e}")
                continue
            except Exception as e:                      # 兜底，别让一个坏文件卡死
                errors.append(f"{pathlib.Path(one).name}：{type(e).__name__}: {e}")
                continue

            if item["kind"] == "image":
                if sum(1 for a in self._attachments if a["kind"] == "image") >= attach_mod.MAX_IMAGES:
                    errors.append(f"{item['name']}：一次最多 {attach_mod.MAX_IMAGES} 张图片")
                    continue
            else:
                used = sum(len(a.get("text") or "")
                           for a in self._attachments if a["kind"] == "text")
                if used + len(item["text"]) > attach_mod.MAX_TOTAL_CHARS:
                    errors.append(f"{item['name']}：附件文字总量超过 "
                                  f"{attach_mod.MAX_TOTAL_CHARS} 字")
                    continue

            self._attachments.append(item)
            added += 1

        self._rebuild_attach_bar()
        if added:
            self.statusbar.showMessage(f"已添加 {added} 个附件", 4000)
        if errors:
            QMessageBox.warning(self, "部分文件未添加", "\n".join(errors))

    def _rebuild_attach_bar(self):
        """待发送附件：每个都做成可点击移除的 chip。"""
        while self.attach_layout.count():
            item = self.attach_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        for index, a in enumerate(self._attachments):
            icon = "🖼" if a["kind"] == "image" else "📄"
            chip = QPushButton(f"{icon} {a['name']}    ✕")
            chip.setObjectName("attach_chip")   # 配色交给 QSS，明暗两套主题各自定义
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("点击移除这个附件")
            chip.clicked.connect(lambda _=False, i=index: self._remove_attachment(i))
            self.attach_layout.addWidget(chip)

        self.attach_layout.addStretch(1)
        self.attach_bar.setVisible(bool(self._attachments))

    def _remove_attachment(self, index):
        if 0 <= index < len(self._attachments):
            item = self._attachments.pop(index)
            attach_mod.delete_stored(item.get("path"))   # 撤掉仓库里的副本
            self._rebuild_attach_bar()
            self.statusbar.showMessage("已移除附件", 2000)

    def _identity_note(self) -> str:
        """把当前模型标识告诉模型，避免它说"不知道自己是哪个模型"。"""
        model = self.current_model()
        if not model:
            return ""
        return ("\n\n## 运行环境\n"
                f"- 你由 Graphic_Dev 桌面客户端通过 API 调用，当前模型标识：{model}\n"
                "- 用户问「你是什么模型」时，直接回答这个标识即可；"
                "不要声称自己不知道具体是哪个模型。\n")

    def _build_messages(self):
        """把 chat_history 转成 API 需要的 messages（图片走 image_url 分片）。"""
        messages = [{"role": "system",
                     "content": self.system_prompt() + self._identity_note()}]
        for m in self.chat_history:
            texts = m.get("texts") or []
            images = m.get("images") or []

            if m["role"] == "user" and (texts or images):
                text = m["content"]
                for t in texts:
                    text += f"\n\n【附件：{t['name']}】\n{t['text']}"

                if images:
                    parts = [{"type": "text", "text": text}]
                    for url in images:
                        parts.append({"type": "image_url",
                                      "image_url": {"url": url}})
                    messages.append({"role": "user", "content": parts})
                else:
                    messages.append({"role": "user", "content": text})
            else:
                messages.append({"role": m["role"], "content": m["content"]})
        return messages

    def on_send(self):
        # ---------- 第一阶段：只做校验，不改任何界面状态 ----------
        user_input = self.user_text.toPlainText().strip()
        if not user_input and not self._attachments:
            self.statusbar.showMessage("请输入消息或添加附件", 3000)
            return

        cfg = self.get_api_config()
        if cfg is None:
            QMessageBox.warning(self, "配置缺失", "请先在「API 配置」页填写地址和 KEY。")
            self.tabWidget.setCurrentWidget(self.page_api_config)
            return

        url, key = cfg
        model = self.current_model()
        if not model:
            QMessageBox.warning(self, "未选择模型", "请先在下方表格勾选一个模型，或点「添加模型」。")
            self.tabWidget.setCurrentWidget(self.page_api_config)
            return

        use_search = self.search_check.isChecked()
        search_key = self.search_key_text.text().strip()
        search_provider = self.search_provider_combo.currentData() or DEFAULT_PROVIDER
        if use_search and not search_key:
            QMessageBox.warning(
                self, "缺少搜索 KEY",
                f"已勾选「联网搜索」，但没有填写 {provider_label(search_provider)} 的 API Key。\n\n"
                "请到「API 配置」页填写，或取消勾选「联网搜索」后再发送。")
            self.tabWidget.setCurrentWidget(self.page_api_config)
            self.search_key_text.setFocus()
            return

        # ---------- 第二阶段：校验全部通过，才开始改状态 ----------
        texts = [{"name": a["name"], "text": a["text"]}
                 for a in self._attachments if a["kind"] == "text"]
        images = [a["data_url"] for a in self._attachments if a["kind"] == "image"]

        # 附件的名字现在由气泡下方的 chip 渲染，正文里不再重复一遍
        display = user_input

        files = [{"name": a["name"], "path": a.get("path", ""), "kind": a["kind"]}
                 for a in self._attachments]

        self.chat_history.append({"role": "user", "content": display,
                                  "texts": texts, "images": images, "files": files})
        self._save_history()
        self.user_text.clear()
        self._attachments = []
        self._rebuild_attach_bar()

        self._pending = False
        self._render_chat()
        self.textBrowser.page().runJavaScript("beginStream();")

        self._set_streaming(True)
        thinking = "深度思考" if self.reasoning_check.isChecked() else "快速回答"
        self.statusbar.showMessage(f"模型 {model} 正在回答（{thinking}）...")

        messages = self._build_messages()

        self._pending_sources = []
        self.worker = ChatWorker(url, key, model, messages,
                                 use_search=use_search,
                                 search_key=search_key,
                                 search_provider=search_provider,
                                 reasoning_level=self.reasoning_level(),
                                 image_config=self.image_config())
        self.worker.search_note.connect(self._on_search_note)
        self.worker.reset_stream.connect(self._on_reset_stream)
        self.worker.sources.connect(self._on_sources)
        self.worker.image_generated.connect(self._on_image_generated)
        self.worker.chunk.connect(self._on_stream_body)
        self.worker.think.connect(self._on_stream_think)
        self.worker.finished.connect(self.on_chat_finished)
        self.worker.error_occurred.connect(self.on_chat_error)
        self.worker.start()

    def _push_stream(self, kind, text):
        self.textBrowser.page().runJavaScript(
            "streamAppend({}, {});".format(
                json.dumps(kind, ensure_ascii=True),
                json.dumps(text, ensure_ascii=True),
            )
        )

    def _on_stream_body(self, text):
        self._push_stream("body", text)

    def _on_stream_think(self, text):
        self._push_stream("think", text)

    def _on_search_note(self, msg):
        self.statusbar.showMessage(msg)

    def _on_reset_stream(self):
        self.textBrowser.page().runJavaScript("resetStream();")

    def _on_image_generated(self, snippet):
        """图片生成好就立刻塞进当前气泡。"""
        self._push_stream("body", "\n\n" + snippet + "\n\n")

    def _on_sources(self, items):
        """搜索完成后立刻把来源推到前端，回答流式期间就能看到。"""
        self._pending_sources = items or []
        if not self._pending_sources:
            return
        self.textBrowser.page().runJavaScript(
            "streamSources({});".format(json.dumps(self._pending_sources, ensure_ascii=True))
        )

    def on_chat_finished(self, reply, reasoning):
        self.textBrowser.page().runJavaScript("endStream();")   # 先清理流式状态
        self._pending = False
        if reply.strip() or reasoning.strip():
            self.chat_history.append({"role": "assistant",
                                      "content": reply,
                                      "reasoning": reasoning,
                                      "sources": self._pending_sources})
        self._pending_sources = []
        self._save_history()
        self._render_chat()
        self._set_streaming(False)
        self.statusbar.showMessage("已停止" if (self.worker and self.worker.stopped) else "已完成")

    def on_chat_error(self, error_msg):
        self.textBrowser.page().runJavaScript("endStream();")
        self._pending = False
        self._render_chat()
        self._set_streaming(False)
        self.statusbar.showMessage("请求失败")
        hint = ""
        if self.chat_history and self.chat_history[-1].get("images"):
            hint = ("\n\n提示：这条消息包含图片，当前模型可能不支持图片输入，"
                    "可以换一个多模态模型再试。")
        QMessageBox.warning(self, "请求失败", error_msg + hint)

    def on_clear(self):
        # 清空聊天时，把仓库里对应的附件副本一并删掉
        for a in self._attachments:
            attach_mod.delete_stored(a.get("path"))
        for m in self.chat_history:
            for f in (m.get("files") or []):
                attach_mod.delete_stored(f.get("path"))

        self.chat_history.clear()
        self._attachments = []
        self._rebuild_attach_bar()
        self._pending = False
        self._save_history()
        self._render_chat()
        self.statusbar.showMessage("已清空")

    def add_model(self):
        cfg = self.get_api_config()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先填写 API 地址和 KEY。")
            return
        url, key = cfg

        self.add_models_button.setEnabled(False)
        self.statusbar.showMessage("正在获取模型列表...")

        self.models_worker = ModelsWorker(url, key)
        self.models_worker.done.connect(self._on_models_loaded)
        self.models_worker.failed.connect(self._on_models_failed)
        self.models_worker.start()

    def _on_models_loaded(self, models):
        self.add_models_button.setEnabled(True)

        chat_models = [m for m in models if is_chat_model(m)]
        dropped = len(models) - len(chat_models)

        msg = f"共 {len(chat_models)} 个可用对话模型"
        if dropped:
            msg += f"（已过滤 {dropped} 个非对话模型）"
        self.statusbar.showMessage(msg)

        if not chat_models:
            QMessageBox.information(self, "没有模型", "接口没有返回可用的对话模型。")
            return

        dlg = ModelPickerDialog(chat_models, self._existing_models(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        picked = dlg.checked_models()
        for name in picked:
            self._add_model_row(name)

        if picked and not self.current_model():
            for r in range(self.models_table.rowCount()):
                if self.models_table.item(r, 0).text() == picked[0]:
                    self.models_table.item(r, 1).setCheckState(Qt.CheckState.Checked)
                    break

        self._drop_stale_models(chat_models)
        self._refresh_check_marks()
        self._refresh_model_label()

    def _on_models_failed(self, msg):
        self.add_models_button.setEnabled(True)
        self.statusbar.showMessage("获取模型失败")
        QMessageBox.critical(self, "获取模型失败", msg)

    def on_test_api(self):
        cfg = self.get_api_config()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先填写 API 地址和 KEY")
            return
        url, key = cfg
        base = url.rstrip("/")
        try:
            resp = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
            if resp.status_code == 200:
                QMessageBox.information(self, "成功", "API 连接正常")
            else:
                QMessageBox.critical(self, "失败", f"HTTP {resp.status_code}\n{resp.text[:200]}")

        except requests.exceptions.RequestException as e:
            QMessageBox.critical(self, "失败", f"网络错误: {e}")

def _onefile_root():
    """单文件包（onefile）返回临时解包目录，其它情况返回 None。

    PyInstaller 两种模式都会设 sys._MEIPASS：onedir 指向 exe 旁边的 _internal，
    只有 onefile 才指向临时目录里的解包处。onedir 那套布局 Qt 自己能找对，
    不需要我们插手，所以必须区分开——否则每次启动都白扫一遍目录树。
    """
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    root = pathlib.Path(meipass)
    if not root.is_dir():
        return None
    if root.resolve() == (pathlib.Path(sys.executable).parent / "_internal").resolve():
        return None
    return root


def _fix_webengine_paths():
    """定位 QtWebEngine 的辅助进程和资源，设好三个环境变量。

    两种打包布局需要救，表现都是启动时报
    "could not find QtWebEngineProcess / resources"：
    - macOS 文件夹版：PySide6 6.12 把它们错放进 framework 的 Versions/Resources/，
      而 framework 根软链指向 Versions/A/，Qt 顺着软链找不到；
    - 单文件包（各平台）：东西在临时解包目录里，Qt 只按 exe 同级目录找。
    必须在 QApplication / WebEngine 初始化之前调用。
    """
    if not getattr(sys, "frozen", False):
        return

    candidates = []
    if sys.platform == "darwin":
        contents = pathlib.Path(sys.executable).resolve().parent.parent   # <App>.app/Contents
        candidates += [contents / "Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework",
                       contents / "Frameworks" / "PySide6"]
    onefile = _onefile_root()
    if onefile is not None:
        # 只扫 PySide6/Qt 这一支，别整个解包目录翻一遍（单文件包有上万个文件）
        qt = onefile / "PySide6" / "Qt"
        candidates.append(qt if qt.is_dir() else onefile)

    root = next((c for c in candidates if c.is_dir()), None)
    if root is None:
        return
    hits = list(root.rglob("*"))

    def _find(pred):
        return next((h for h in hits if pred(h)), None)

    if not os.environ.get("QTWEBENGINEPROCESS_PATH"):
        # Windows 上这个辅助进程带 .exe 后缀，而且文件系统没有可执行位，
        # 只能按名字认；类 Unix 平台再要求真正可执行，避免挑到同名普通文件。
        proc_name = "QtWebEngineProcess.exe" if sys.platform == "win32" else "QtWebEngineProcess"
        proc = _find(lambda h: h.name.lower() == proc_name.lower()
                     and h.is_file()
                     and (sys.platform == "win32" or os.access(h, os.X_OK)))
        if proc:
            os.environ["QTWEBENGINEPROCESS_PATH"] = str(proc)

    if not os.environ.get("QTWEBENGINE_RESOURCES_PATH"):
        pak = _find(lambda h: h.name == "qtwebengine_resources.pak")
        if pak:
            os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(pak.parent)
            locales = pak.parent / "qtwebengine_locales"
            if locales.is_dir() and not os.environ.get("QTWEBENGINE_LOCALES_PATH"):
                os.environ["QTWEBENGINE_LOCALES_PATH"] = str(locales)


if __name__ == "__main__":
    # Linux 上以 root 运行时，Chromium 沙箱会直接崩溃
    if sys.platform.startswith("linux") and getattr(os, "geteuid", None) \
            and os.geteuid() == 0:
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

    _fix_webengine_paths()          # 必须在 QApplication / WebEngine 初始化前
    app = QApplication(sys.argv)
    families = fonts.register_bundled_fonts()   # 随包字体（Linux 尤其需要）
    if families:
        print("已加载内置字体:", ", ".join(families[:3]))
    apply_theme()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
