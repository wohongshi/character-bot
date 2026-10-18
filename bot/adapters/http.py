"""适配器共用的小工具：HTTP 请求、签名校验、access_token 缓存。

只依赖标准库。刻意不做重试风暴 —— 这些平台都有频控，退避留给调用方。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

__all__ = ["HttpError", "request_json", "sha1_signature", "TokenCache",
           "parse_qs", "header_lookup"]


class HttpError(RuntimeError):
    """平台返回非 2xx，或返回体不是合法 JSON。"""

    def __init__(self, status: int, body: str, url: str = ""):
        self.status = status
        self.body = body[:500]
        self.url = url
        super().__init__(f"HTTP {status} {url}：{self.body[:200]}")


def request_json(url: str, *, method: str = "POST", payload: Any = None,
                 headers: Optional[dict] = None, timeout: float = 20.0,
                 form: bool = False, raw: Optional[bytes] = None) -> dict:
    """发一个请求并解析 JSON。非 2xx 抛 HttpError（带上响应体，便于排错）。"""
    hdrs = {"Accept": "application/json", **(headers or {})}
    data: Optional[bytes] = None
    if form:
        data = urllib.parse.urlencode(payload or {}).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif raw is not None:
        data = raw
    elif payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310
            body = r.read(1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(2048).decode("utf-8", "replace")
        except OSError:
            body = ""
        raise HttpError(e.code, body, url) from e
    except (OSError, urllib.error.URLError) as e:
        raise HttpError(0, f"{type(e).__name__}: {e}", url) from e
    if not body.strip():
        return {}
    try:
        out = json.loads(body)
    except ValueError as e:
        raise HttpError(0, f"返回不是 JSON：{body[:200]}", url) from e
    return out if isinstance(out, dict) else {"data": out}


def sha1_signature(token: str, timestamp: str, nonce: str) -> str:
    """微信 / 企业微信的回调签名：token、timestamp、nonce 字典序拼接后 SHA1。"""
    parts = sorted([str(token), str(timestamp), str(nonce)])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or ""), str(b or ""))


def header_lookup(headers: dict, name: str, default: str = "") -> str:
    """HTTP 头大小写不敏感（server.py 传进来的是普通 dict）。"""
    low = name.lower()
    for k, v in (headers or {}).items():
        if str(k).lower() == low:
            return str(v)
    return default


def parse_qs(query: dict) -> dict:
    """把 `{k: [v]}` 或 `{k: v}` 统一成 `{k: v}`。"""
    out = {}
    for k, v in (query or {}).items():
        out[k] = v[0] if isinstance(v, (list, tuple)) and v else ("" if v is None else str(v))
    return out


class TokenCache:
    """access_token 缓存：提前 60 秒过期，避免边界上刚好失效。

    平台侧有频控（飞书/微信都限制每天取 token 的次数），所以必须缓存；
    多线程同时过期时只让一个线程去取（`_lock`），其余等它。
    """

    def __init__(self, name: str = "", ttl_default: float = 6000.0):
        self.name = name
        self.ttl_default = ttl_default
        self._lock = threading.Lock()
        self._value = ""
        self._expire = 0.0
        self._fail = ""

    def get(self, fetch):
        now = time.time()
        if self._value and now < self._expire:
            return self._value
        with self._lock:
            if self._value and time.time() < self._expire:      # 双检：别人刚取到
                return self._value
            value, ttl = fetch()
            if not value:
                raise RuntimeError(f"{self.name or 'token'} 获取失败")
            self._value = str(value)
            self._expire = time.time() + max(60.0, float(ttl or self.ttl_default) - 60.0)
            self._fail = ""
            return self._value

    def clear(self) -> None:
        with self._lock:
            self._value = ""
            self._expire = 0.0

    def peek(self) -> dict:
        """给面板/自检看：不暴露 token 本身。"""
        return {"has_token": bool(self._value),
                "expires_in": max(0, int(self._expire - time.time())) if self._value else 0,
                "last_error": self._fail}
