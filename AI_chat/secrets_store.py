"""密钥存储：优先用系统钥匙串，失败时退回到 0600 权限的本地文件。

- macOS   : Keychain（通过 /usr/bin/security，密码走 stdin，不出现在命令行参数里）
- Windows : DPAPI（CryptProtectData，只有当前用户能解密）
- Linux   : Secret Service（secret-tool，GNOME Keyring / KWallet）
- 兜底    : 数据目录下的 secrets.json（0600），并在界面上提示一句

对上层只暴露 get / set / delete / backend_name。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import app_paths

SERVICE = "AI_chat"

# 记录最近一次实际落点：keychain / file，用于界面提示
_LAST = {"backend": None}


# ---------------------------------------------------------------- 工具
def _secrets_file() -> Path | None:
    root = app_paths.data_dir()
    return None if root is None else root / "secrets.json"


def _read_file() -> dict:
    path = _secrets_file()
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write_file(data: dict) -> None:
    path = _secrets_file()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- macOS
def _mac_available() -> bool:
    return sys.platform == "darwin" and bool(shutil.which("security"))


def _mac_get(key: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", key, "-w"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n") or None


def _mac_set(key: str, value: str) -> bool:
    """密码通过 stdin 传两次（security 会要求输入两遍）。"""
    payload = f"{value}\n{value}\n"
    try:
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", key, "-w"],
            input=payload, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _mac_delete(key: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", SERVICE, "-a", key],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- Windows (DPAPI)
def _win_crypt(data: bytes, protect: bool) -> bytes | None:
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        def to_blob(raw: bytes) -> DATA_BLOB:
            buf = ctypes.create_string_buffer(raw, len(raw))
            return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

        blob_in = to_blob(data)
        blob_out = DATA_BLOB()
        fn = (ctypes.windll.crypt32.CryptProtectData if protect
              else ctypes.windll.crypt32.CryptUnprotectData)
        if protect:
            ok = fn(ctypes.byref(blob_in), None, None, None, None, 0,
                    ctypes.byref(blob_out))
        else:
            ok = fn(ctypes.byref(blob_in), None, None, None, None, 0,
                    ctypes.byref(blob_out))
        if not ok:
            return None
        result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return result
    except Exception:                                  # noqa: BLE001
        return None


def _win_get(key: str) -> str | None:
    raw = _read_file().get(key)
    if not raw:
        return None
    from base64 import b64decode

    plain = _win_crypt(b64decode(raw), protect=False)
    return plain.decode("utf-8") if plain else None


def _win_set(key: str, value: str) -> bool:
    from base64 import b64encode

    blob = _win_crypt(value.encode("utf-8"), protect=True)
    if blob is None:
        return False
    data = _read_file()
    data[key] = b64encode(blob).decode("ascii")
    _write_file(data)
    return True


# ---------------------------------------------------------------- Linux
def _linux_available() -> bool:
    return sys.platform.startswith("linux") and bool(shutil.which("secret-tool"))


def _linux_get(key: str) -> str | None:
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", SERVICE, "key", key],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout or None


def _linux_set(key: str, value: str) -> bool:
    try:
        result = subprocess.run(
            ["secret-tool", "store", "--label", f"{SERVICE} {key}",
             "service", SERVICE, "key", key],
            input=value, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _linux_delete(key: str) -> None:
    try:
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "key", key],
                       capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- 对外接口
def backend_name() -> str:
    if _mac_available():
        return "系统钥匙串（macOS Keychain）"
    if sys.platform == "win32":
        return "系统凭据（Windows DPAPI）"
    if _linux_available():
        return "系统钥匙串（Secret Service）"
    return "本地文件（0600 权限）"


def status_text() -> str:
    """实际使用的存储方式（没写过就报可用的那个）。"""
    backend = _LAST["backend"]
    if backend == "file":
        return "本地文件（0600 权限，非钥匙串）"
    if backend == "keychain":
        return backend_name()
    return backend_name()


def get(key: str, default: str = "") -> str:
    if _mac_available():
        value = _mac_get(key)
        if value is not None:
            _LAST["backend"] = "keychain"
            return value
    elif sys.platform == "win32":
        value = _win_get(key)
        if value is not None:
            _LAST["backend"] = "keychain"
            return value
    elif _linux_available():
        value = _linux_get(key)
        if value is not None:
            _LAST["backend"] = "keychain"
            return value

    stored = _read_file().get(key)
    if isinstance(stored, str) and stored:
        _LAST["backend"] = "file"
        return stored
    return default


def set(key: str, value: str) -> bool:          # noqa: A001 - 语义明确
    """写入密钥；返回是否用上了系统钥匙串。"""
    if not value:
        delete(key)
        return False

    if _mac_available():
        if _mac_set(key, value):
            _LAST["backend"] = "keychain"
            data = _read_file()
            data.pop(key, None)                     # 钥匙串成功就不要明文副本
            _write_file(data)
            return True
    elif sys.platform == "win32":
        if _win_set(key, value):
            _LAST["backend"] = "keychain"
            return True
    elif _linux_available():
        if _linux_set(key, value):
            _LAST["backend"] = "keychain"
            data = _read_file()
            data.pop(key, None)
            _write_file(data)
            return True

    data = _read_file()
    data[key] = value
    _write_file(data)
    _LAST["backend"] = "file"
    return False


def delete(key: str) -> None:
    if _mac_available():
        _mac_delete(key)
    elif sys.platform == "win32":
        pass                                        # DPAPI 版本直接从文件删
    elif _linux_available():
        _linux_delete(key)

    data = _read_file()
    if key in data:
        data.pop(key, None)
        _write_file(data)


def clear(keys=("api_key", "image_key", "search_key")) -> None:
    """清空所有已知密钥，并抹掉兜底文件。"""
    for key in keys:
        delete(key)
    _write_file({})
