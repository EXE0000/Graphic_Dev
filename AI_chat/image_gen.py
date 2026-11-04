# -*- coding: utf-8 -*-
"""文生图：兼容两类接口。

1. OpenAI 兼容的 `/images/generations`
   - 地址可以是 base（自动补 `/images/generations`），也可以是完整端点
   - 返回 {"data":[{"b64_json"|"url"}]}

2. 百炼 / 通义万相的原生接口（qwen-image、wanx 系列）
   - 地址形如 .../services/aigc/multimodal-generation/generation
   - 返回 output.choices[].message.content[].image
   - 也可能返回 task_id（异步），需要轮询 /tasks/{id}
"""

import base64
import time

import requests


class ImageGenError(RuntimeError):
    pass


def _resolve(url: str):
    """返回 (实际请求地址, 接口类型)。"""
    target = url.strip().rstrip("/")
    lowered = target.lower()

    if lowered.endswith("/images/generations"):
        return target, "openai"
    if "multimodal-generation" in lowered:
        return target, "multimodal"
    if "text2image" in lowered:
        return target, "text2image"
    return target + "/images/generations", "openai"


def _headers(key: str, extra=None) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if extra:
        headers.update(extra)
    return headers


def _download(url: str) -> bytes:
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise ImageGenError(f"下载图片失败：HTTP {resp.status_code}")
    return resp.content


def _dashscope_size(size: str) -> str:
    return (size or "1024x1024").replace("x", "*").replace("X", "*")


def _parse_openai(payload: dict) -> bytes:
    data = payload.get("data")
    if isinstance(data, list) and data:
        item = data[0] or {}
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return _download(item["url"])
    raise ImageGenError("接口没有返回图片数据")


def _parse_dashscope(payload: dict) -> bytes:
    output = payload.get("output") or {}

    # 同步返回：multimodal-generation
    choices = output.get("choices") if isinstance(output, dict) else None
    if choices:
        message = (choices[0] or {}).get("message") or {}
        for part in message.get("content") or []:
            image = part.get("image") or part.get("url")
            if image:
                return _download(image)

    # 同步返回：text2image
    results = output.get("results") if isinstance(output, dict) else None
    if results:
        url = (results[0] or {}).get("url")
        if url:
            return _download(url)

    raise ImageGenError("接口没有返回图片数据")


def _poll_task(task_url: str, key: str, timeout: int = 180) -> dict:
    """异步任务轮询。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(task_url, headers=_headers(key), timeout=30)
        if resp.status_code != 200:
            raise ImageGenError(f"查询任务失败：HTTP {resp.status_code}")
        data = resp.json()
        status = ((data.get("output") or {}).get("task_status") or "").upper()
        if status in ("SUCCEEDED", "SUCCESS"):
            return data
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            message = (data.get("output") or {}).get("message") or status
            raise ImageGenError(f"任务失败：{message}")
        time.sleep(2)
    raise ImageGenError("任务超时")


def generate(base_url: str, key: str, model: str, prompt: str,
             size: str = "1024x1024", timeout: int = 180) -> bytes:
    """返回图片二进制。失败抛 ImageGenError。"""
    if not base_url:
        raise ImageGenError("没有配置图片生成接口地址")
    if not model:
        raise ImageGenError("没有配置图片模型")
    if not prompt.strip():
        raise ImageGenError("图片描述不能为空")

    url, kind = _resolve(base_url)

    if kind == "openai":
        payload = {
            "model": model,
            "prompt": prompt.strip(),
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }
    elif kind == "multimodal":
        payload = {
            "model": model,
            "input": {"messages": [
                {"role": "user", "content": [{"text": prompt.strip()}]}
            ]},
            "parameters": {
                "size": _dashscope_size(size),
                "n": 1,
                "prompt_extend": True,
                "watermark": False,
            },
        }
    else:  # text2image（wanx 老接口）
        payload = {
            "model": model,
            "input": {"prompt": prompt.strip()},
            "parameters": {"size": _dashscope_size(size), "n": 1},
        }

    headers = _headers(key)

    def post(async_mode: bool):
        h = dict(headers)
        if async_mode:
            h["X-DashScope-Async"] = "enable"
        return requests.post(url, headers=h, json=payload, timeout=timeout)

    # 默认走同步：很多网关（如企业版/代理）不支持异步调用，加异步头会直接 403
    try:
        resp = post(False)
        body = resp.text[:500]
        if resp.status_code != 200 and kind != "openai" and "async" in body.lower():
            resp = post(True)          # 该模型只支持异步，再试一次
            body = resp.text[:500]
    except requests.exceptions.RequestException as e:
        raise ImageGenError(f"网络错误：{e}") from e

    if resp.status_code != 200:
        raise ImageGenError(f"HTTP {resp.status_code}：{body}")

    try:
        data = resp.json()
    except ValueError as e:
        raise ImageGenError("接口返回的不是 JSON") from e

    if isinstance(data, dict) and data.get("code") and data.get("message"):
        raise ImageGenError(f"{data['code']}：{data['message']}"[:300])

    output = data.get("output") or {}
    task_id = output.get("task_id") if isinstance(output, dict) else None
    if task_id:
        base = url.split("/services/")[0]
        data = _poll_task(f"{base}/tasks/{task_id}", key, timeout)

    if kind == "openai":
        return _parse_openai(data)
    return _parse_dashscope(data)


def save_image(data: bytes, folder, name: str = "") -> str:
    """把图片写进数据目录，返回路径。"""
    import pathlib

    target_dir = pathlib.Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = name or time.strftime("img-%Y%m%d-%H%M%S")
    path = target_dir / f"{stem}.png"

    index = 1
    while path.exists():
        path = target_dir / f"{stem}-{index}.png"
        index += 1

    path.write_bytes(data)
    return str(path)
