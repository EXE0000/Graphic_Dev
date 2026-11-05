# -*- coding: utf-8 -*-
"""联网搜索：支持多家服务商，统一返回 [{title, url, content}]。

新增一家服务只需要往 PROVIDERS 里加一条配置：
    method    GET / POST
    endpoint  接口地址
    headers   key -> 请求头
    json      (key, query, n) -> 请求体（POST）
    params    (key, query, n) -> 查询参数（GET）
    parse     response_json -> [{title, url, content}]
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import urlparse

import requests

import app_paths

SNIPPET_CHARS = 600        # 搜索摘要每条保留多少字符
FETCH_CHARS = 3000         # 抓取正文时每条保留多少字符
FETCH_TIMEOUT = 8.0        # 单个网页抓取超时
FETCH_TOP_N = 2            # 抓取前几条的正文（越多越慢）
PER_DOMAIN_MAX = 2         # 同一域名最多保留几条，避免全是同一个站

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ---------------------------------------------------------------- 解析器
def _snippet(text):
    return (text or "").strip()[:SNIPPET_CHARS]


def _parse_tavily(data):
    return [
        {
            "title": (r.get("title") or "").strip(),
            "url": (r.get("url") or "").strip(),
            "content": _snippet(r.get("content")),
        }
        for r in (data.get("results") or [])
        if r.get("url") and r.get("content")
    ]


def _parse_bocha(data):
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    return [
        {
            "title": (r.get("name") or "").strip(),
            "url": (r.get("url") or "").strip(),
            "content": _snippet(r.get("summary") or r.get("snippet")),
        }
        for r in pages
        if r.get("url") and (r.get("summary") or r.get("snippet"))
    ]


def _parse_brave(data):
    results = (data.get("web") or {}).get("results") or []
    return [
        {
            "title": (r.get("title") or "").strip(),
            "url": (r.get("url") or "").strip(),
            "content": _snippet(r.get("description")),
        }
        for r in results
        if r.get("url") and r.get("description")
    ]


# ---------------------------------------------------------------- 权威源与日期
# 关键词 → 官方域名：命中时自动追加一次 site: 限定检索，尽量拿一手原文
AUTHORITY_DOMAINS = (
    # 越具体的写在前面，避免 "CPI" 这种宽泛词误配到中国统计局
    (("美国CPI", "美国非农", "美国失业率", "美国通胀"), "bls.gov"),
    (("美联储", "FOMC", "Federal Reserve"), "federalreserve.gov"),
    (("美国证监会", "SEC filing"), "sec.gov"),
    (("央行", "人民银行", "降准", "存款准备金", "公开市场操作"), "pbc.gov.cn"),
    (("国家统计局", "统计局", "社会消费品零售", "居民消费价格"), "stats.gov.cn"),
    (("证监会", "IPO 审核", "上市公司监管"), "csrc.gov.cn"),
    (("财政部", "国债发行", "财政政策"), "mof.gov.cn"),
    (("发改委", "产业政策", "价格听证"), "ndrc.gov.cn"),
    (("国务院", "国常会"), "gov.cn"),
    (("卫健委", "医疗机构", "疾控"), "nhc.gov.cn"),
    (("教育部", "高考", "学历认证"), "moe.gov.cn"),
    (("工信部", "制造业增加值"), "miit.gov.cn"),
    (("WHO", "世界卫生组织"), "who.int"),
    (("IMF", "世界银行"), "imf.org"),
)

_DATE_PATTERNS = (
    re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})"),
    re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日"),
    re.compile(r"(20\d{2})\.(\d{1,2})\.(\d{1,2})"),
)


def authority_domains(query: str):
    """命中关键词时返回应优先检索的官方域名列表。"""
    hits = []
    for keywords, domain in AUTHORITY_DOMAINS:
        if any(word in query for word in keywords) and domain not in hits:
            hits.append(domain)
    return hits


def extract_date(item) -> str:
    """从 url/标题/正文里找发布日期，找不到返回空串。"""
    for field in ("url", "title", "content"):
        text = item.get(field) or ""
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            year, month, day = (int(x) for x in match.groups())
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def is_authority(url: str, domains) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(host.endswith(domain) for domain in domains)


# ---------------------------------------------------------------- 时效与缓存
FRESHNESS_DAY, FRESHNESS_WEEK, FRESHNESS_MONTH = "day", "week", "month"

_DAY_WORDS = ("今天", "今日", "刚才", "刚刚", "最新", "latest", "today", "breaking")
_WEEK_WORDS = ("本周", "这周", "最近一周", "最近几天", "this week", "past week")
_MONTH_WORDS = ("本月", "这个月", "最近一个月", "近一个月", "this month", "past month")

_CACHE = {}                 # "provider|query|n|freshness" -> (ts, results)
CACHE_TTL = 600             # 10 分钟内同样的查询直接复用
CACHE_KEEP_SECONDS = 24 * 3600   # 落盘只保留 24 小时内的条目
CACHE_MAX_ENTRIES = 500          # 落盘最多存多少条
_SAVE_INTERVAL = 5               # 最快每 5 秒写一次盘

_cache_loaded = False
_cache_lock = threading.Lock()
_last_save = 0.0


def cache_file():
    folder = app_paths.sub_dir("cache")
    return folder / "search_cache.json" if folder else None


def _load_cache():
    """首次使用时把磁盘缓存读进内存。"""
    global _cache_loaded
    with _cache_lock:
        if _cache_loaded:
            return
        _cache_loaded = True

    path = cache_file()
    if path is None or not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    now = time.time()
    with _cache_lock:
        for key, item in (data or {}).items():
            ts = item.get("ts", 0)
            results = item.get("results") or []
            if now - ts <= CACHE_KEEP_SECONDS:
                _CACHE[key] = (ts, results)


def _save_cache(force=False):
    """原子写入（先写临时文件再替换），并按时间裁剪条目。"""
    global _last_save
    now = time.time()
    with _cache_lock:
        if not force and now - _last_save < _SAVE_INTERVAL:
            return
        _last_save = now
        items = [(k, v) for k, v in _CACHE.items()
                 if now - v[0] <= CACHE_KEEP_SECONDS]
        items.sort(key=lambda kv: kv[1][0], reverse=True)
        payload = {k: {"ts": v[0], "results": v[1]}
                   for k, v in items[:CACHE_MAX_ENTRIES]}

    path = cache_file()
    if path is None:
        return
    try:
        # 临时文件名带线程标识：多个线程同时落盘时不会写到同一个 .tmp 上
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def clear_cache():
    """清空内存和磁盘缓存。"""
    global _cache_loaded
    with _cache_lock:
        _CACHE.clear()
        _cache_loaded = True
    path = cache_file()
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass


def cache_stats():
    _load_cache()
    with _cache_lock:
        return len(_CACHE)


def infer_freshness(query: str):
    """从问题里推断时效要求，用于过滤过期内容。"""
    q = (query or "").lower()
    if any(w in q for w in _DAY_WORDS):
        return FRESHNESS_DAY
    if any(w in q for w in _WEEK_WORDS):
        return FRESHNESS_WEEK
    if any(w in q for w in _MONTH_WORDS):
        return FRESHNESS_MONTH
    return None


def _cache_get(key):
    _load_cache()
    with _cache_lock:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return [dict(r) for r in hit[1]]
    return None


def _cache_put(key, results):
    with _cache_lock:
        _CACHE[key] = (time.time(), [dict(r) for r in results])
    _save_cache()
    return results


_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9]{2,}")


def _tokens(text):
    """粗分词：中文按字，英文按词。够用来算重叠度。"""
    text = (text or "").lower()
    return set(_CJK.findall(text)) | set(_WORD.findall(text))


def relevance(query, item):
    """查询与结果的词汇重叠度（0~1），代替商业 rerank 的廉价近似。"""
    q = _tokens(query)
    if not q:
        return 0.0
    doc = _tokens((item.get("title") or "") + " " + (item.get("content") or ""))
    return len(q & doc) / len(q)


def drop_duplicates(results):
    """内容开头高度重复的视为同一篇，丢掉后面的。"""
    seen, out = set(), []
    for r in results:
        key = re.sub(r"\s+", "", (r.get("content") or "")[:120])
        if key and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def rank_results(results, query=""):
    """重排：provider 分数 + 查询相关度 + 正文长度。"""
    def key(r):
        provider_score = r.get("score")
        provider_score = float(provider_score) if isinstance(provider_score, (int, float)) else 0.0
        authority = 1.0 if r.get("authority") else 0.0
        dated = 1.0 if (r.get("date") or extract_date(r)) else 0.0
        return (authority, relevance(query, r), dated, provider_score,
                len(r.get("content") or ""))
    return sorted(results, key=key, reverse=True)


def clean_results(results, min_chars=60):
    """丢掉内容过短/没有 url 的条目。"""
    out = [r for r in results if r.get("url") and len(r.get("content") or "") >= min_chars]
    return out or [r for r in results if r.get("url")]


# ---------------------------------------------------------------- 后处理
_STRIP_RE = re.compile(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BLOCK_RE = re.compile(r"</?(p|div|br|li|h[1-6]|tr|section|article|blockquote)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def fetch_page_text(url: str, timeout: float = FETCH_TIMEOUT,
                    max_chars: int = FETCH_CHARS) -> str:
    """抓取网页正文（粗暴但够用的 HTML → 文本）。"""
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")

    ctype = resp.headers.get("Content-Type", "")
    if ctype and "html" not in ctype and "text" not in ctype:
        raise RuntimeError(f"不是网页（{ctype}）")

    html = _COMMENT_RE.sub(" ", resp.text)
    html = _STRIP_RE.sub(" ", html)
    html = _BLOCK_RE.sub("\n", html)
    text = unescape(_TAG_RE.sub(" ", html))

    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:max_chars]


def dedupe_by_domain(results, per_domain: int = PER_DOMAIN_MAX):
    """同一域名最多留几条，避免结果被一个站包场。"""
    seen, out = {}, []
    for r in results:
        host = (urlparse(r.get("url") or "").netloc or "").lower().removeprefix("www.")
        if seen.get(host, 0) >= per_domain:
            continue
        seen[host] = seen.get(host, 0) + 1
        out.append(r)
    return out


def enrich_results(results, top_n: int = FETCH_TOP_N, max_chars: int = FETCH_CHARS,
                   deadline=None):
    """并发抓取前几条的正文，替换掉短摘要；失败就保留原摘要。

    deadline 为 time.time() 时间戳，超过就不再抓（整体耗时可控）。
    """
    import time as _t
    if deadline is not None and _t.time() >= deadline:
        return results
    targets = results[:top_n]
    if not targets:
        return results

    def work(item):
        try:
            text = fetch_page_text(item["url"], max_chars=max_chars)
        except Exception:
            return
        # 抓到的正文至少不比摘要短、且不是空白页，就采用它
        if len(text) >= 200 and len(text) >= len(item.get("content") or ""):
            item["content"] = text
            item["fetched"] = True

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        list(pool.map(work, targets))
    return results


# ---------------------------------------------------------------- 服务商
PROVIDERS = {
    "bocha": {
        "label": "博查 Bocha（中文最佳）",
        "method": "POST",
        "endpoint": "https://api.bochaai.com/v1/web-search",
        "headers": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        "json": lambda key, q, n, f: {
            "query": q,
            "count": n,
            "summary": True,
            "freshness": {"day": "oneDay", "week": "oneWeek",
                          "month": "oneMonth"}.get(f, "noLimit"),
        },
        "parse": _parse_bocha,
    },
    "tavily": {
        "label": "Tavily（通用，免费额度）",
        "method": "POST",
        "endpoint": "https://api.tavily.com/search",
        "headers": lambda key: {"Content-Type": "application/json"},
        "json": lambda key, q, n, f: {
            "api_key": key,
            "query": q,
            "max_results": n,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            **({"days": {"day": 1, "week": 7, "month": 30}[f]} if f else {}),
        },
        "parse": _parse_tavily,
    },
    "brave": {
        "label": "Brave Search（独立索引）",
        "method": "GET",
        "endpoint": "https://api.search.brave.com/res/v1/web/search",
        "headers": lambda key: {
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
        "params": lambda key, q, n, f: {
            "q": q,
            "count": n,
            "text_decorations": 0,
            **({"freshness": {"day": "pd", "week": "pw", "month": "pm"}[f]} if f else {}),
        },
        "parse": _parse_brave,
    },
}

# 下拉框里的顺序（也是默认顺序，第一项为默认）
PROVIDER_ORDER = ["bocha", "tavily", "brave"]
DEFAULT_PROVIDER = PROVIDER_ORDER[0]


def provider_label(provider):
    cfg = PROVIDERS.get(provider)
    return cfg["label"] if cfg else str(provider)


# ---------------------------------------------------------------- 主入口
def search_web(api_key, query, max_results=5, timeout=20.0, provider=DEFAULT_PROVIDER,
               freshness="auto"):
    """返回 [{title, url, content}]，失败抛异常由上层降级处理。

    freshness: "auto" 按问题推断 / None 不限 / "day"|"week"|"month"
    """
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise RuntimeError(f"未知的搜索服务：{provider}")
    if not api_key:
        raise RuntimeError(f"没有配置 {cfg['label']} 的 API Key")

    fresh = infer_freshness(query) if freshness == "auto" else freshness

    # JSON 的键必须是字符串，用分隔符拼成稳定 key
    cache_key = "|".join([provider, query, str(max_results), fresh or ""])
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    kwargs = {"headers": cfg["headers"](api_key), "timeout": timeout}
    if cfg["method"] == "GET":
        kwargs["params"] = cfg["params"](api_key, query, max_results, fresh)
    else:
        kwargs["json"] = cfg["json"](api_key, query, max_results, fresh)

    try:
        resp = requests.request(cfg["method"], cfg["endpoint"], **kwargs)
    except requests.RequestException as e:
        raise RuntimeError(f"{cfg['label']} 请求失败：{e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"{cfg['label']} HTTP {resp.status_code}：{resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"{cfg['label']} 返回内容不是 JSON") from e

    results = clean_results(rank_results(cfg["parse"](data), query))
    results = drop_duplicates(results)
    return _cache_put(cache_key, results)


def format_tool_results(results, query):
    """工具调用时回填给模型的结果（比 format_search_block 更紧凑）。"""
    if not results:
        return f"没有检索到与「{query}」相关的结果。"

    lines = [f"检索「{query}」的结果："]
    for i, r in enumerate(results, 1):
        date = r.get("date") or extract_date(r)
        stamp = f"[{date}] " if date else "[日期未知] "
        tag = "（一手来源）" if r.get("authority") else ""
        fetched = "（已读全文）" if r.get("fetched") else ""
        lines.append(f"[{i}] {stamp}{r['title']}{tag}{fetched}\n{r['url']}\n{r['content']}")
    return "\n\n".join(lines)


def to_sources(results):
    """给前端用的精简来源列表：[{title, url}]。"""
    out = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        title = (r.get("title") or "").strip() or url
        out.append({"title": title[:120], "url": url,
                    "date": r.get("date") or extract_date(r)})
    return out


def format_search_block(results, query):
    """把搜索结果拼成一段可注入 system 的文本（降级模式用）。"""
    if not results:
        return ""

    lines = [
        "## 实时搜索结果",
        f"以下是针对「{query}」刚刚检索到的网页内容（不是你的记忆）。",
        "回答要求：",
        "1. 优先依据这些结果回答，不要凭记忆编造；",
        "2. 引用时在句末标注 [1]、[2] 这样的编号；",
        "3. 如果结果不足以回答，明确说明缺什么。",
        "",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"URL: {r['url']}")
        lines.append(r["content"])
        lines.append("")
    return "\n".join(lines)
