# -*- coding: utf-8 -*-
"""Agentic RAG 知识库：本地 SQLite + FTS5(BM25) + 可选向量混合检索。

设计取舍（教育 Agent / 笔记本续航优先）：
- 不引入重型向量库/本地嵌入模型：全文检索用 SQLite 自带 FTS5，零依赖、零常驻内存；
  中文通过「字级 unigram+bigram」预分词喂给 unicode61，BM25 对教材关键词足够准。
- 向量是「可选增强」：配置了嵌入模型才走 API 算 embedding（网络算力，不烧本机电池），
  与 BM25 做 RRF 融合；没配置就纯 BM25，功能不缺席。
- 切块按标题/段落感知 + 滑窗重叠，保留章节上下文，便于引用定位。
"""

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import app_paths

_LOCK = threading.RLock()
_CONN = None

CHUNK_TARGET = 900        # 目标块长（字符）
CHUNK_OVERLAP = 120       # 滑窗重叠，避免语义被切断
BM25_CANDIDATES = 40
VEC_CANDIDATES = 40
RRF_K = 60

_CJK = r"一-鿿㐀-䶿"
_HEAD_RE = re.compile(
    r"^(#{1,6}\s+.+|第[一二三四五六七八九十百0-9]+[章节篇部分讲].*|"
    r"\d+(?:\.\d+)*[、.．]\s*\S.*|[一二三四五六七八九十]+[、.．]\s*\S.*)$")


# ---------------------------------------------------------------- 存储
def _db_file():
    root = app_paths.sub_dir("knowledge")
    return None if root is None else root / "kb.sqlite"


def conn():
    global _CONN
    with _LOCK:
        if _CONN is None:
            path = _db_file()
            if path is None:
                raise RuntimeError("没有可写的知识库目录")
            _CONN = sqlite3.connect(str(path), check_same_thread=False)
            _CONN.row_factory = sqlite3.Row
            _init_schema(_CONN)
        return _CONN


def _init_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS bases(
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        embed_model TEXT DEFAULT '', created REAL);
    CREATE TABLE IF NOT EXISTS docs(
        id TEXT PRIMARY KEY, base_id TEXT, name TEXT, kind TEXT,
        chars INTEGER DEFAULT 0, chunks INTEGER DEFAULT 0, created REAL);
    CREATE TABLE IF NOT EXISTS chunks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        base_id TEXT, doc_id TEXT, ord INTEGER,
        heading TEXT DEFAULT '', text TEXT, vec TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(tok);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE INDEX IF NOT EXISTS idx_chunks_base ON chunks(base_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
    """)
    c.commit()


# ---------------------------------------------------------------- 分词 / 切块
def fts_tokens(text):
    """中文 bigram + 拉丁/数字词，供 unicode61 FTS 做 BM25。

    只索引 bigram（不索引单字）：单字区分度太低，会让「式」「法」这类字
    把不相关的块也召回；孤立单字才保留，保证单字查询仍有结果。
    """
    text = (text or "").lower()
    out = []
    for run in re.findall(r"[a-z0-9_]+|[" + _CJK + r"]+", text):
        if run[0] in "abcdefghijklmnopqrstuvwxyz0123456789_":
            out.append(run)
        elif len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return " ".join(out)


def _match_query(query):
    toks = fts_tokens(query).split()
    if not toks:
        return None
    return " OR ".join('"%s"' % t for t in toks[:64])


def chunk_text(text):
    """标题/段落感知切块：返回 [{heading, text}]，带滑窗重叠。"""
    text = re.sub(r"[ \t]+\n", "\n", (text or "").replace("\r\n", "\n"))
    blocks = []
    heading = ""
    buf = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if buf:
            body = "\n".join(buf).strip()
            if body:
                blocks.append({"heading": heading, "text": body})
        buf, buf_len = [], 0

    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if _HEAD_RE.match(para) and len(para) < 60:
            flush()
            heading = para.lstrip("#").strip()
            buf, buf_len = [para], len(para)
            continue
        if buf_len + len(para) > CHUNK_TARGET and buf_len > CHUNK_OVERLAP:
            tail = "\n".join(buf)[-CHUNK_OVERLAP:]
            flush()
            buf, buf_len = [tail], len(tail)
        buf.append(para)
        buf_len += len(para)
    flush()
    return [b for b in blocks if b["text"].strip()]


# ---------------------------------------------------------------- 向量（可选）
def embed_texts(texts, url, key, model):
    """走 OpenAI 兼容 /embeddings；失败返回 None（降级纯 BM25）。"""
    if not (url and key and model and texts):
        return None
    import requests
    endpoint = re.sub(r"/chat/completions/?$", "", url.rstrip("/")) + "/embeddings"
    try:
        r = requests.post(endpoint,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "input": texts}, timeout=60)
        if r.status_code != 200:
            return None
        data = r.json().get("data") or []
        data.sort(key=lambda d: d.get("index", 0))
        return [d.get("embedding") for d in data]
    except Exception:
        return None


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- 基 / 文档 / 块 CRUD
def list_bases():
    with _LOCK:
        rows = conn().execute(
            "SELECT b.*, (SELECT COUNT(*) FROM docs d WHERE d.base_id=b.id) AS docs,"
            " (SELECT COUNT(*) FROM chunks c WHERE c.base_id=b.id) AS chunks"
            " FROM bases b ORDER BY b.created").fetchall()
        return [dict(r) for r in rows]


def get_base(bid):
    with _LOCK:
        r = conn().execute("SELECT * FROM bases WHERE id=?", (bid,)).fetchone()
        return dict(r) if r else None


def create_base(name, embed_model=""):
    with _LOCK:
        bid = uuid.uuid4().hex[:12]
        conn().execute("INSERT INTO bases(id,name,embed_model,created) VALUES(?,?,?,?)",
                       (bid, name.strip() or "未命名知识库", embed_model, time.time()))
        conn().commit()
        return bid


def rename_base(bid, name):
    with _LOCK:
        conn().execute("UPDATE bases SET name=? WHERE id=?", (name, bid))
        conn().commit()


def set_base_embed(bid, model):
    with _LOCK:
        conn().execute("UPDATE bases SET embed_model=? WHERE id=?", (model, bid))
        conn().commit()


def delete_base(bid):
    with _LOCK:
        c = conn()
        for r in c.execute("SELECT id FROM chunks WHERE base_id=?", (bid,)).fetchall():
            c.execute("DELETE FROM chunks_fts WHERE rowid=?", (r["id"],))
        c.execute("DELETE FROM chunks WHERE base_id=?", (bid,))
        c.execute("DELETE FROM docs WHERE base_id=?", (bid,))
        c.execute("DELETE FROM bases WHERE id=?", (bid,))
        if get_active() == bid:
            set_active("")
        c.commit()


def set_active(bid):
    with _LOCK:
        conn().execute("INSERT INTO meta(key,value) VALUES('active',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (bid,))
        conn().commit()


def get_active():
    with _LOCK:
        r = conn().execute("SELECT value FROM meta WHERE key='active'").fetchone()
        return r["value"] if r else ""


def _index_chunk(c, cid, text, vec):
    c.execute("INSERT INTO chunks_fts(rowid, tok) VALUES(?,?)", (cid, fts_tokens(text)))
    if vec is not None:
        c.execute("UPDATE chunks SET vec=? WHERE id=?", (json.dumps(vec), cid))


def add_doc(base_id, name, kind, text, embedder=None):
    """切块 + 建索引；embedder(texts)->vecs 可选。返回 doc 记录。"""
    blocks = chunk_text(text)
    with _LOCK:
        c = conn()
        did = uuid.uuid4().hex[:12]
        vecs = embedder([b["text"] for b in blocks]) if embedder and blocks else None
        c.execute("INSERT INTO docs(id,base_id,name,kind,chars,chunks,created) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (did, base_id, name, kind, len(text), len(blocks), time.time()))
        for i, b in enumerate(blocks):
            cur = c.execute("INSERT INTO chunks(base_id,doc_id,ord,heading,text) "
                            "VALUES(?,?,?,?,?)",
                            (base_id, did, i, b["heading"], b["text"]))
            vec = vecs[i] if vecs and i < len(vecs) else None
            _index_chunk(c, cur.lastrowid, b["text"], vec)
        c.commit()
        return {"id": did, "name": name, "kind": kind,
                "chars": len(text), "chunks": len(blocks)}


def delete_doc(did):
    with _LOCK:
        c = conn()
        for r in c.execute("SELECT id FROM chunks WHERE doc_id=?", (did,)).fetchall():
            c.execute("DELETE FROM chunks_fts WHERE rowid=?", (r["id"],))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (did,))
        c.execute("DELETE FROM docs WHERE id=?", (did,))
        c.commit()


def list_docs(base_id):
    with _LOCK:
        rows = conn().execute("SELECT * FROM docs WHERE base_id=? ORDER BY created",
                              (base_id,)).fetchall()
        return [dict(r) for r in rows]


def list_chunks(base_id, doc_id=None, limit=200, offset=0):
    with _LOCK:
        q = ("SELECT c.*, d.name AS doc_name FROM chunks c "
             "LEFT JOIN docs d ON d.id=c.doc_id WHERE c.base_id=?")
        args = [base_id]
        if doc_id:
            q += " AND c.doc_id=?"
            args.append(doc_id)
        q += " ORDER BY c.doc_id, c.ord LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [dict(r) for r in conn().execute(q, args).fetchall()]


def add_chunk(base_id, heading, text, embedder=None):
    """手动投喂的知识点：挂在一个「手动笔记」文档下。"""
    with _LOCK:
        c = conn()
        did = _manual_doc(c, base_id)
        vecs = embedder([text]) if embedder else None
        ordn = c.execute("SELECT COALESCE(MAX(ord),0)+1 FROM chunks WHERE doc_id=?",
                         (did,)).fetchone()[0]
        cur = c.execute("INSERT INTO chunks(base_id,doc_id,ord,heading,text) "
                        "VALUES(?,?,?,?,?)", (base_id, did, ordn, heading, text))
        _index_chunk(c, cur.lastrowid, text, vecs[0] if vecs else None)
        c.execute("UPDATE docs SET chunks=chunks+1, chars=chars+? WHERE id=?",
                  (len(text), did))
        c.commit()
        return cur.lastrowid


def _manual_doc(c, base_id):
    r = c.execute("SELECT id FROM docs WHERE base_id=? AND kind='manual'",
                  (base_id,)).fetchone()
    if r:
        return r["id"]
    did = uuid.uuid4().hex[:12]
    c.execute("INSERT INTO docs(id,base_id,name,kind,chars,chunks,created) "
              "VALUES(?,?,?,?,0,0,?)", (did, base_id, "手动知识点", "manual", time.time()))
    return did


def update_chunk(cid, heading, text, embedder=None):
    with _LOCK:
        c = conn()
        vecs = embedder([text]) if embedder else None
        c.execute("UPDATE chunks SET heading=?, text=? WHERE id=?", (heading, text, cid))
        c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        _index_chunk(c, cid, text, vecs[0] if vecs else None)
        c.commit()


def delete_chunk(cid):
    with _LOCK:
        c = conn()
        r = c.execute("SELECT doc_id FROM chunks WHERE id=?", (cid,)).fetchone()
        c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        c.execute("DELETE FROM chunks WHERE id=?", (cid,))
        if r:
            c.execute("UPDATE docs SET chunks=MAX(chunks-1,0) WHERE id=?", (r["doc_id"],))
        c.commit()


def stats(base_id):
    with _LOCK:
        c = conn()
        docs = c.execute("SELECT COUNT(*) n FROM docs WHERE base_id=?", (base_id,)).fetchone()["n"]
        chunks = c.execute("SELECT COUNT(*) n FROM chunks WHERE base_id=?", (base_id,)).fetchone()["n"]
        vecs = c.execute("SELECT COUNT(*) n FROM chunks WHERE base_id=? AND vec IS NOT NULL",
                         (base_id,)).fetchone()["n"]
        return {"docs": docs, "chunks": chunks, "vectors": vecs}


# ---------------------------------------------------------------- 检索
def search(base_id, query, k=6, embedder=None):
    """BM25 + 可选向量，RRF 融合。返回带来源/标题/分数的块。"""
    match = _match_query(query)
    ranked = {}
    with _LOCK:
        c = conn()
        if match:
            rows = c.execute(
                "SELECT rowid AS id, bm25(chunks_fts) AS s FROM chunks_fts "
                "WHERE tok MATCH ? ORDER BY s LIMIT ?", (match, BM25_CANDIDATES)).fetchall()
            for rank, r in enumerate(rows):
                ranked.setdefault(r["id"], 0.0)
                ranked[r["id"]] += 1.0 / (RRF_K + rank)
        if embedder:
            qv = embedder([query])
            if qv and qv[0]:
                rows = c.execute("SELECT id, vec FROM chunks WHERE base_id=? AND vec IS NOT NULL",
                                 (base_id,)).fetchall()
                scored = []
                for r in rows:
                    try:
                        v = json.loads(r["vec"])
                    except (ValueError, TypeError):
                        continue
                    scored.append((_cosine(qv[0], v), r["id"]))
                scored.sort(reverse=True)
                for rank, (_, cid) in enumerate(scored[:VEC_CANDIDATES]):
                    ranked.setdefault(cid, 0.0)
                    ranked[cid] += 1.0 / (RRF_K + rank)
        if not ranked:
            return []
        order = sorted(ranked.items(), key=lambda kv: -kv[1])[:k]
        out = []
        for cid, score in order:
            r = c.execute("SELECT c.*, d.name AS doc_name FROM chunks c "
                          "LEFT JOIN docs d ON d.id=c.doc_id WHERE c.id=?", (cid,)).fetchone()
            if r:
                d = dict(r)
                d.pop("vec", None)
                d["score"] = round(score, 4)
                out.append(d)
        return out