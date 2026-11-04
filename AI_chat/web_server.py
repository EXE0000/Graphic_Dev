# -*- coding: utf-8 -*-
"""本地 Web 入口：把聊天能力通过 127.0.0.1 的 HTTP + SSE 暴露给浏览器。

设计要点：
- 只监听 127.0.0.1，不对外网开放；
- 每次启动生成随机 token，页面 URL 带 ?t=...，API 需要同一个 token；
- 真正的模型调用复用客户端里的 ChatWorker，保证行为和桌面端完全一致；
- 生成的图片通过 /media 走 HTTP，因为浏览器不能加载 file:// 资源。
"""

import base64
import json
import mimetypes
import pathlib
import queue
import secrets
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QUrl

import app_paths
import attachments as attach_mod
import exporters

WEB_DIR = app_paths.resource_dir() / "web"
DATA_DIR = app_paths.data_dir()
IMAGE_DIR = app_paths.sub_dir("images")
CONV_FILE = (app_paths.sub_dir("cache") or pathlib.Path(".")) / "web_conversations.json"
HISTORY_FILE = (app_paths.sub_dir("cache") or pathlib.Path(".")) / "web_history.json"
MAX_HISTORY = 60          # 最多保留多少条消息
MAX_HISTORY_BYTES = 4 * 1024 * 1024

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}


class WebChatServer:
    """持有后端配置和一段会话历史。"""

    def __init__(self, controller):
        self.controller = controller          # MainWindow：提供配置和 worker 工厂
        self.token = secrets.token_urlsafe(16)
        self.started_at = int(time.time())
        self.conversations = {}               # id -> {id,title,created,updated,messages}
        self.attachments = []                 # 待发送附件（复用 attachments 模块）
        self._load_conversations()
        self._httpd = None
        self._thread = None
        self.port = 0

    # ------------------------------------------------------------ 会话存储
    def _load_conversations(self):
        """读取多个会话；首次运行会把旧的单会话迁移进来。"""
        try:
            if CONV_FILE.is_file():
                data = json.loads(CONV_FILE.read_text(encoding="utf-8"))
                items = data.get("conversations") if isinstance(data, dict) else None
                if isinstance(items, dict):
                    self.conversations = {
                        cid: conv for cid, conv in items.items()
                        if isinstance(conv, dict) and isinstance(conv.get("messages"), list)
                    }
        except (OSError, ValueError):
            self.conversations = {}

        if self.conversations:
            return

        # 迁移旧的单会话文件
        messages = []
        try:
            if HISTORY_FILE.is_file():
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                turns = data.get("turns") if isinstance(data, dict) else data
                if isinstance(turns, list):
                    messages = [t for t in turns if isinstance(t, dict)
                                and t.get("role") in ("user", "assistant")]
        except (OSError, ValueError):
            messages = []

        conv = self._new_record("之前的对话" if messages else "新对话")
        conv["messages"] = messages
        self.conversations[conv["id"]] = conv
        self._save_conversations()

    def _new_record(self, title="新对话"):
        now = time.time()
        return {"id": secrets.token_hex(6), "title": title,
                "created": now, "updated": now, "messages": []}

    def create_conversation(self, title="新对话"):
        conv = self._new_record(title)
        self.conversations[conv["id"]] = conv
        self._save_conversations()
        return conv

    def get_conversation(self, cid):
        if cid and cid in self.conversations:
            return self.conversations[cid]
        # 没传/传错就退回最近更新的一条
        if not self.conversations:
            self.create_conversation()
        return max(self.conversations.values(),
                   key=lambda c: c.get("updated") or 0)

    def search_conversations(self, keyword: str, limit: int = 60):
        """按标题和正文全文搜索会话。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return {"items": []}
        needle = keyword.lower()

        hits = []
        for conv in sorted(self.conversations.values(),
                           key=lambda c: c.get("updated") or 0, reverse=True):
            title = conv.get("title") or "新对话"
            title_hit = needle in title.lower()

            matched = None
            for index, message in enumerate(conv.get("messages") or []):
                text = (message.get("content") or "")
                pos = text.lower().find(needle)
                if pos < 0:
                    continue
                start = max(0, pos - 30)
                snippet = text[start:pos + len(keyword) + 50].replace("\n", " ")
                matched = {"index": index,
                           "role": message.get("role"),
                           "snippet": ("…" if start > 0 else "") + snippet}
                break

            if title_hit or matched:
                hits.append({
                    "id": conv["id"],
                    "title": title,
                    "updated": conv.get("updated") or 0,
                    "title_hit": title_hit,
                    "match": matched,
                })
            if len(hits) >= limit:
                break

        return {"items": hits}

    def conversation_list(self):
        items = sorted(self.conversations.values(),
                       key=lambda c: c.get("updated") or 0, reverse=True)
        return [{"id": c["id"], "title": c.get("title") or "新对话",
                 "updated": c.get("updated") or 0,
                 "count": len(c.get("messages") or [])}
                for c in items]

    def _save_conversations(self):
        """原子写入；单个会话也做条数裁剪。"""
        payload = {"conversations": {}}
        for cid, conv in self.conversations.items():
            messages = []
            for message in (conv.get("messages") or [])[-MAX_HISTORY:]:
                messages.append({
                    "role": message.get("role"),
                    "content": message.get("content") or "",
                    "reasoning": message.get("reasoning") or "",
                    "sources": message.get("sources") or [],
                    "texts": message.get("texts") or [],
                    "images": [],
                })
            payload["conversations"][cid] = {
                "id": cid,
                "title": conv.get("title") or "新对话",
                "created": conv.get("created") or time.time(),
                "updated": conv.get("updated") or time.time(),
                "messages": messages,
            }

        try:
            text = json.dumps(payload, ensure_ascii=False)
            if len(text.encode("utf-8")) > MAX_HISTORY_BYTES * 2:
                # 超限就丢掉最老的会话，保留最近 20 个
                keep = self.conversation_list()[:20]
                payload["conversations"] = {
                    item["id"]: payload["conversations"][item["id"]]
                    for item in keep
                }
                text = json.dumps(payload, ensure_ascii=False)
            tmp = CONV_FILE.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(CONV_FILE)
        except OSError:
            pass

    @staticmethod
    def _auto_title(messages, fallback="新对话"):
        for message in messages:
            if message.get("role") == "user" and (message.get("content") or "").strip():
                text = message["content"].strip().replace("\n", " ")
                return text[:24] + ("…" if len(text) > 24 else "")
        return fallback

    # ------------------------------------------------------------ 生命周期
    def start(self):
        if self._httpd is not None:
            return self.url()

        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):    # 静默，别污染控制台
                pass

            def do_GET(self):
                server.handle_get(self)

            def do_POST(self):
                server.handle_post(self)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    # ------------------------------------------------------------ 路由
    def handle_get(self, handler):
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/chat", "/index.html"):
            if query.get("t", [""])[0] != self.token:
                return self._send_text(handler, 403, "无效的访问令牌，请从客户端重新打开。")
            return self._send_page(handler)

        if path.startswith("/static/"):
            target = (WEB_DIR / path[len("/static/"):]).resolve()
            if WEB_DIR.resolve() not in target.parents and target != WEB_DIR:
                return self._send_text(handler, 403, "forbidden")
            return self._send_file(handler, target)

        if path == "/media":
            if query.get("t", [""])[0] != self.token:
                return self._send_text(handler, 403, "forbidden")
            return self._send_media(handler, query.get("p", [""])[0])

        if path == "/api/search":
            if query.get("t", [""])[0] != self.token:
                return self._send_text(handler, 403, "forbidden")
            return self._send_json(handler,
                                   self.search_conversations(query.get("q", [""])[0]))

        if path == "/api/conversations":
            if query.get("t", [""])[0] != self.token:
                return self._send_text(handler, 403, "forbidden")
            return self._send_json(handler, {"items": self.conversation_list(),
                                             "active": self.get_conversation(
                                                 query.get("conv", [""])[0])["id"]})

        if path == "/api/history":
            if query.get("t", [""])[0] != self.token:
                return self._send_text(handler, 403, "forbidden")
            conv = self.get_conversation(query.get("conv", [""])[0])
            return self._send_json(handler, {"id": conv["id"],
                                             "turns": self.public_history(conv)})

        if path == "/api/config":
            if query.get("t", [""])[0] != self.token:
                return self._send_text(handler, 403, "forbidden")
            return self._send_json(handler, self.config())

        return self._send_text(handler, 404, "not found")

    def handle_post(self, handler):
        parsed = urllib.parse.urlparse(handler.path)
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("t", [""])[0] != self.token:
            return self._send_text(handler, 403, "forbidden")

        if parsed.path == "/api/conversations/new":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
            current = query.get("conv", [""])[0]
            existing = self.conversations.get(current)
            # 当前会话本来就是空的，就别再造一个新的（否则会堆一屏"新对话"）
            if existing is not None and not existing.get("messages"):
                conv = existing
            else:
                conv = self.create_conversation()
            return self._send_json(handler, {"id": conv["id"],
                                             "title": conv["title"],
                                             "items": self.conversation_list()})
        if parsed.path == "/api/conversations/rename":
            return self.handle_rename(handler)
        if parsed.path == "/api/conversations/delete_batch":
            return self.handle_delete_conversations(handler)
        if parsed.path == "/api/conversations/delete_empty":
            removed = [cid for cid, conv in self.conversations.items()
                       if not (conv.get("messages") or [])]
            for cid in removed:
                self.conversations.pop(cid, None)
            if not self.conversations:
                self.create_conversation()
            self._save_conversations()
            return self._send_json(handler, {"items": self.conversation_list(),
                                             "removed": len(removed),
                                             "active": self.get_conversation("")["id"]})
        if parsed.path == "/api/conversations/delete":
            return self.handle_delete_conversation(handler)
        if parsed.path == "/api/chat":
            return self.handle_chat(handler)
        if parsed.path == "/api/export_all":
            return self.handle_export_all(handler)
        if parsed.path in ("/api/delete", "/api/delete_batch"):
            return self.handle_delete(handler)
        if parsed.path == "/api/stop":
            self.controller.cancel_web_stream()
            return self._send_json(handler, {"ok": True})
        if parsed.path == "/api/setting":
            return self.handle_setting(handler)
        if parsed.path == "/api/upload":
            return self.handle_upload(handler)
        if parsed.path == "/api/export":
            return self.handle_export(handler)
        if parsed.path == "/api/attachments/clear":
            self.attachments.clear()
            return self._send_json(handler, {"items": []})
        if parsed.path == "/api/attachments/remove":
            return self.handle_remove_attachment(handler)
        if parsed.path == "/api/reset":
            conv = self.get_conversation(query.get("conv", [""])[0])
            conv["messages"] = []
            conv["updated"] = time.time()
            self._save_conversations()
            return self._send_json(handler, {"ok": True, "id": conv["id"]})
        return self._send_text(handler, 404, "not found")

    # ------------------------------------------------------------ 业务
    def config(self):
        return self.controller.web_config()

    def handle_chat(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
        except ValueError:
            return self._send_text(handler, 400, "invalid json")

        text = (payload.get("message") or "").strip()
        if not text:
            return self._send_text(handler, 400, "empty message")

        cfg = self.controller.web_config()
        if not cfg.get("ready"):
            return self._send_text(handler, 400, cfg.get("reason") or "配置不完整")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        conv = self.get_conversation(query.get("conv", [""])[0])

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        # 没有 Content-Length 也不是 chunked，必须声明 close，
        # 否则 HTTP/1.1 的客户端会一直等"还有没有数据"
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True

        def send(event, data):
            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            handler.wfile.write(chunk.encode("utf-8"))
            handler.wfile.flush()

        texts = [{"name": a["name"], "text": a.get("text", "")}
                 for a in self.attachments if a["kind"] == "text"]
        images = [a.get("data_url") for a in self.attachments
                  if a["kind"] == "image"]
        conv["messages"].append({"role": "user", "content": text,
                                 "texts": texts, "images": images})
        self.attachments.clear()
        conv["updated"] = time.time()
        if not conv.get("title") or conv["title"] == "新对话":
            conv["title"] = self._auto_title(conv["messages"])
        self._save_conversations()

        messages = self._build_messages(conv)

        final = {"content": "", "reasoning": "", "sources": []}
        stream = self.controller.web_stream(messages)
        try:
            for kind, data in stream:
                if kind == "chunk":
                    final["content"] += data
                    send("chunk", {"text": data})
                elif kind == "think":
                    final["reasoning"] += data
                    send("think", {"text": data})
                elif kind == "note":
                    send("note", {"text": data})
                elif kind == "sources":
                    final["sources"] = data or []
                    send("sources", {"items": data})
                elif kind == "image":
                    send("image", {"markdown": data})
                elif kind == "error":
                    send("error", {"text": data})
                    break
                elif kind == "done":
                    # 用 worker 的最终内容：它已经把生成的图片拼进正文了。
                    # 只累加 chunk 的话，图片 markdown 永远进不了历史/导出。
                    final["content"] = data or final["content"]
                    break
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:                     # 兜底，别让服务线程崩掉
            try:
                send("error", {"text": f"{type(e).__name__}: {e}"})
            except Exception:
                pass
        finally:
            stream.close()                         # 触发 worker 清理

        if final["content"] or final["reasoning"]:
            conv["messages"].append({"role": "assistant",
                                     "content": final["content"],
                                     "reasoning": final["reasoning"],
                                     "sources": final["sources"]})
            conv["updated"] = time.time()
            self._save_conversations()
        try:
            send("done", {"ok": True})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _build_messages(self, conv=None):
        """把会话历史拼成 API messages（附件按桌面端的规则展开）。"""
        messages = [{"role": "system",
                     "content": self.controller.system_prompt()
                                + self.controller._identity_note()}]
        conv = conv or self.get_conversation("")
        for m in conv["messages"]:
            texts = m.get("texts") or []
            images = m.get("images") or []
            if m["role"] == "user" and (texts or images):
                text = m["content"]
                for t in texts:
                    text += f"\n\n【附件：{t['name']}】\n{t.get('text', '')}"
                if images:
                    parts = [{"type": "text", "text": text}]
                    parts += [{"type": "image_url", "image_url": {"url": u}}
                              for u in images]
                    messages.append({"role": "user", "content": parts})
                else:
                    messages.append({"role": "user", "content": text})
            else:
                messages.append({"role": m["role"], "content": m["content"]})
        return messages

    def _webify(self, text: str) -> str:
        """把正文里的 file:// 图片换成网页可访问的 /media 地址。"""
        import re as _re

        data_root = pathlib.Path(DATA_DIR) if DATA_DIR else None
        if data_root is None:
            return text

        pattern = _re.compile(r"\]\((file://[^)]+)\)")

        def repl(match):
            local = QUrl(match.group(1)).toLocalFile()
            try:
                rel = pathlib.Path(local).relative_to(data_root)
            except (ValueError, TypeError):
                return match.group(0)
            return "](/media?t={}&p={})".format(
                self.token, urllib.parse.quote(rel.as_posix()))

        return pattern.sub(repl, text)

    def public_history(self, conv=None):
        """给前端恢复会话用（隐藏密钥，图片换成 HTTP 地址）。"""
        conv = conv or self.get_conversation("")
        turns = []
        for index, message in enumerate(conv["messages"]):
            turns.append({
                "index": index,
                "role": message.get("role"),
                "content": self._webify(message.get("content") or ""),
                "reasoning": message.get("reasoning") or "",
                "sources": message.get("sources") or [],
                "attachments": [
                    {"name": t.get("name")} for t in (message.get("texts") or [])
                ],
            })
        return turns

    def handle_setting(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
        except ValueError:
            return self._send_text(handler, 400, "invalid json")
        search = payload.get("search")
        thinking = payload.get("thinking")
        self.controller.apply_web_settings(search=search, thinking=thinking)

        cfg = self.controller.web_config()
        if search is not None:              # 立刻回显，避免排队执行带来的时序差
            cfg["search"] = bool(search)
        if thinking is not None:
            cfg["thinking"] = bool(thinking)
        return self._send_json(handler, cfg)

    def handle_delete(self, handler):
        """删除单条或批量删除；规则和客户端一致：问答成对删。"""
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
        except ValueError:
            return self._send_text(handler, 400, "invalid json")

        if "indexes" in payload:
            raw = payload.get("indexes") or []
        else:
            raw = [payload.get("index")]

        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        conv = self.get_conversation(query.get("conv", [""])[0] or
                                     str(payload.get("conv") or ""))
        messages = conv["messages"]

        targets = set()
        for value in raw:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if not (0 <= index < len(messages)):
                continue
            targets.add(index)
            message = messages[index]
            if message.get("role") == "user" and index + 1 < len(messages) \
                    and messages[index + 1].get("role") == "assistant":
                targets.add(index + 1)
            elif message.get("role") == "assistant" and index > 0 \
                    and messages[index - 1].get("role") == "user":
                targets.add(index - 1)

        if targets:
            conv["messages"] = [m for i, m in enumerate(messages) if i not in targets]
            conv["updated"] = time.time()
            self._save_conversations()

        return self._send_json(handler, {"turns": self.public_history(conv),
                                         "id": conv["id"]})

    def handle_rename(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            cid = str(payload.get("id") or "")
            title = (payload.get("title") or "").strip()
        except ValueError:
            return self._send_text(handler, 400, "invalid json")

        conv = self.conversations.get(cid)
        if conv is None:
            return self._send_text(handler, 404, "会话不存在")
        if not title:
            return self._send_text(handler, 400, "标题不能为空")

        conv["title"] = title[:60]
        conv["updated"] = time.time()
        self._save_conversations()
        return self._send_json(handler, {"items": self.conversation_list()})

    def handle_delete_conversations(self, handler):
        """批量删除会话。"""
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            ids = payload.get("ids") or []
        except ValueError:
            return self._send_text(handler, 400, "invalid json")

        removed = 0
        for cid in ids:
            if cid in self.conversations:
                self.conversations.pop(cid, None)
                removed += 1

        if not self.conversations:
            self.create_conversation()
        self._save_conversations()

        return self._send_json(handler, {"items": self.conversation_list(),
                                         "removed": removed,
                                         "active": self.get_conversation("")["id"]})

    def handle_delete_conversation(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            cid = str(payload.get("id") or "")
        except ValueError:
            return self._send_text(handler, 400, "invalid json")

        if cid in self.conversations:
            self.conversations.pop(cid, None)
            self._save_conversations()

        if not self.conversations:
            self.create_conversation()

        return self._send_json(handler, {"items": self.conversation_list(),
                                         "active": self.get_conversation("")["id"]})

    # ------------------------------------------------------------ 附件
    def _attachment_public(self):
        return [{"id": i, "name": a["name"], "kind": a["kind"],
                 "size": a.get("size", 0)}
                for i, a in enumerate(self.attachments)]

    def handle_upload(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        if length > 30 * 1024 * 1024:
            return self._send_text(handler, 413, "文件太大（上限 30MB）")
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            name = (payload.get("name") or "file").strip()
            raw = base64.b64decode(payload.get("data") or "")
        except Exception:
            return self._send_text(handler, 400, "上传数据无效")

        suffix = pathlib.Path(name).suffix
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
                fh.write(raw)
                tmp = fh.name
            item = attach_mod.load_attachment(tmp)
        except attach_mod.AttachmentError as e:
            return self._send_text(handler, 400, str(e))
        except Exception as e:                       # noqa: BLE001
            return self._send_text(handler, 400, f"{type(e).__name__}: {e}")
        finally:
            if tmp:
                try:
                    pathlib.Path(tmp).unlink()
                except OSError:
                    pass

        item["name"] = name or item["name"]

        if item["kind"] == "image":
            if sum(1 for a in self.attachments if a["kind"] == "image") >= attach_mod.MAX_IMAGES:
                return self._send_text(handler, 400,
                                       f"一次最多 {attach_mod.MAX_IMAGES} 张图片")
        else:
            used = sum(len(a.get("text") or "") for a in self.attachments
                       if a["kind"] == "text")
            if used + len(item.get("text") or "") > attach_mod.MAX_TOTAL_CHARS:
                return self._send_text(handler, 400, "附件文字总量超出上限")

        self.attachments.append(item)
        return self._send_json(handler, {"items": self._attachment_public()})

    def handle_remove_attachment(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            index = int(payload.get("id"))
        except Exception:
            return self._send_text(handler, 400, "参数无效")
        if 0 <= index < len(self.attachments):
            item = self.attachments.pop(index)
            attach_mod.delete_stored(item.get("path"))
        return self._send_json(handler, {"items": self._attachment_public()})

    # ------------------------------------------------------------ 导出
    def handle_export(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            index = int(payload.get("index"))
            fmt = str(payload.get("fmt") or "").lower()
        except Exception:
            return self._send_text(handler, 400, "参数无效")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        conv = self.get_conversation(query.get("conv", [""])[0] or
                                     str(payload.get("conv") or ""))
        if not (0 <= index < len(conv["messages"])):
            return self._send_text(handler, 404, "消息不存在")
        message = conv["messages"][index]
        if message.get("role") != "assistant":
            return self._send_text(handler, 400, "只能导出模型回答")

        entry = exporters.EXPORTERS.get(fmt)
        if entry is None:
            return self._send_text(handler, 400, "不支持的格式")
        exporter, _file_filter = entry

        content = (message.get("content") or "").strip()
        if not content:
            return self._send_text(handler, 400, "这条回答没有内容")

        try:
            data = self.controller.run_in_gui(
                lambda: self._export_bytes(content, fmt,
                                           message.get("sources") or []))
        except Exception as e:                       # noqa: BLE001
            return self._send_text(handler, 500, f"{type(e).__name__}: {e}")

        filename = exporters.suggest_filename(content, fmt)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
        handler.end_headers()
        handler.wfile.write(data)

    def handle_export_all(self, handler):
        """导出整段对话。"""
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(handler.rfile.read(length) or b"{}")
            fmt = str(payload.get("fmt") or "md").lower()
        except ValueError:
            return self._send_text(handler, 400, "invalid json")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        conv = self.get_conversation(query.get("conv", [""])[0] or
                                     str(payload.get("conv") or ""))
        if not conv["messages"]:
            return self._send_text(handler, 400, "还没有对话内容")
        entry = exporters.EXPORTERS.get(fmt)
        if entry is None:
            return self._send_text(handler, 400, "不支持的格式")
        exporter, _file_filter = entry

        markdown = exporters.conversation_markdown(conv["messages"])
        try:
            data = self.controller.run_in_gui(
                lambda: self._export_bytes(markdown, fmt, []))
        except Exception as e:                       # noqa: BLE001
            return self._send_text(handler, 500, f"{type(e).__name__}: {e}")

        import time as _time
        filename = "对话导出-" + _time.strftime("%Y%m%d-%H%M") + "." + fmt
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _export_bytes(content: str, fmt: str, sources) -> bytes:
        """真正调用导出器（必须在 GUI 线程执行）。"""
        exporter, _file_filter = exporters.EXPORTERS[fmt]
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix="." + fmt) as fh:
                tmp = fh.name
            exporter(content, tmp, sources)
            return pathlib.Path(tmp).read_bytes()
        finally:
            if tmp:
                try:
                    pathlib.Path(tmp).unlink()
                except OSError:
                    pass

    def _send_page(self, handler):
        """返回页面，并给 css/js 加上版本号，避免浏览器用旧缓存。"""
        import re as _re

        path = WEB_DIR / "webchat.html"
        if not path.is_file():
            return self._send_text(handler, 404, "not found")

        html = path.read_text(encoding="utf-8")
        version = str(self.started_at)
        html = _re.sub(r'(/static/[^"\']+\.(?:css|js))"',
                       r'\1?v=' + version + '"', html)

        data = html.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    # ------------------------------------------------------------ 静态资源
    def _send_file(self, handler, path: pathlib.Path):
        if not path.is_file():
            return self._send_text(handler, 404, "not found")
        data = path.read_bytes()
        ctype = MIME.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        handler.send_response(200)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    def _send_media(self, handler, rel: str):
        """只允许读数据目录里的图片。"""
        if DATA_DIR is None:
            return self._send_text(handler, 404, "no data dir")
        target = (pathlib.Path(DATA_DIR) / rel).resolve()
        if pathlib.Path(DATA_DIR).resolve() not in target.parents:
            return self._send_text(handler, 403, "forbidden")
        return self._send_file(handler, target)

    def _send_text(self, handler, code, text):
        data = text.encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _send_json(self, handler, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
