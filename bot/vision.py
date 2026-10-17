"""图片理解（QQ 图片段 → 真字节 → vision 模型描述）。

链路：事件里的 image 段只有 file/url，NapCat 关掉 enableLocalFile2Url 时 url 可能为空
→ 走 OneBot `get_image` 取 url / base64 → 下载（或直接解 base64）→ 嗅探魔数 →
按 OpenAI 兼容的 image_url(data URL) 交给 vision 模型 → 得到一句中文描述。

设计要点：
- 纯标准库；下载有体积/协议/类型闸门（不信任后缀，看魔数）。
- 模型没配 / 调用失败都抛 `VisionUnavailable` / `VisionError`，由 runtime 决定怎么友好降级
  —— 这里绝不把异常吞成空串（否则「看不懂」和「没配模型」分不清）。
- 不做缓存（同一张图被转发两次就理解两次），但描述截断到 MAX_DESC，防止灌爆上下文。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("klee.bot.vision")

MAX_BYTES = 8 * 1024 * 1024          # 单图上限 8MB（NapCat 默认图片也在这个量级）
MAX_DESC = 240                       # 描述截断
DATA_URL_PREFIX = "data:"

DEFAULT_PROMPT = (
    "你是可莉，一个活泼的 18 岁女孩子。用一两句中文口语说出这张图里最值得说的东西"
    "（谁/什么/在哪/在做什么），不要用 Markdown，不要写「图片中」这种客服腔，60 字以内。"
)

# 魔数 → MIME（只看头部，不信文件名后缀）
_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


class VisionError(RuntimeError):
    """图片取不到 / 不是图片 / 模型调用失败。"""


class VisionUnavailable(VisionError):
    """没有配置 vision 模型（要提示用户去面板配一个）。"""


def sniff_mime(raw: bytes) -> str:
    """按魔数判图片类型，认不出返回空串。"""
    if not raw:
        return ""
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:2] == b"BM":
        return "image/bmp"
    return ""


def limit_bytes(raw: bytes, max_bytes: int = MAX_BYTES) -> bytes:
    if not raw:
        raise VisionError("图片是空的")
    if len(raw) > max_bytes:
        raise VisionError(f"图片太大（{len(raw)} 字节 > {max_bytes}）")
    return raw


def to_data_url(raw: bytes, max_bytes: int = MAX_BYTES) -> str:
    """字节 → data URL；不是图片就拒绝（别把 HTML 错误页当图发给模型）。"""
    limit_bytes(raw, max_bytes)
    mime = sniff_mime(raw)
    if not mime:
        raise VisionError("不是能识别的图片格式（魔数不匹配）")
    return f"{DATA_URL_PREFIX}{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def check_image(raw: bytes, max_bytes: int = MAX_BYTES) -> bytes:
    """体积 + 魔数双闸门；不是图片就抛（别把 HTML 错误页当图发给模型）。"""
    limit_bytes(raw, max_bytes)
    if not sniff_mime(raw):
        raise VisionError("不是能识别的图片格式（魔数不匹配）")
    return raw


def _assert_public_host(host: str) -> None:
    """SSRF 闸门：拒绝回环/内网/链路本地/保留地址（含 DNS 解析后的 IP）。"""
    host = str(host or "").strip().strip("[]")
    if not host:
        raise VisionError("图片地址没有主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise VisionError(f"图片主机解析失败：{host}") from e
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise VisionError(f"拒绝访问内网地址：{host}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向目标也要过 SSRF 闸门（否则公网 URL 可 302 到内网）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D401
        u = urllib.parse.urlparse(str(newurl))
        if u.scheme not in ("http", "https"):
            raise VisionError(f"重定向到不支持的协议：{u.scheme}")
        _assert_public_host(u.hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req, timeout=None):
    """实际发起请求（测试打桩点；重定向逐跳过 SSRF 闸门）。"""
    return urllib.request.build_opener(_SafeRedirect()).open(req, timeout=timeout)


def _fetch(url: str, timeout: float = 15.0, max_bytes: int = MAX_BYTES) -> bytes:
    """下载图片字节（只放行 http/https + 公网地址；有体积闸门）。"""
    u = urllib.parse.urlparse(str(url or "").strip())
    if u.scheme not in ("http", "https"):
        raise VisionError(f"不支持的图片地址：{str(url)[:80]}")
    _assert_public_host(u.hostname or "")
    req = urllib.request.Request(url, headers={"User-Agent": "klee-bot/1.0"})  # noqa: S310
    try:
        with _open(req, timeout) as r:
            raw = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise VisionError(f"下载图片 HTTP {e.code}") from e
    except (OSError, urllib.error.URLError) as e:
        raise VisionError(f"下载图片失败：{type(e).__name__}: {e}") from e
    return check_image(raw, max_bytes)


def _from_b64(s: str, max_bytes: int = MAX_BYTES) -> bytes:
    """解 `base64://…` 或裸 base64；解出来必须是图片。"""
    payload = s[len("base64://"):] if s.startswith("base64://") else s
    if len(payload) > max_bytes * 4 // 3 + 16:                      # 先按编码长度拒绝
        raise VisionError(f"图片太大（base64 {len(payload)} 字符）")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as e:
        raise VisionError(f"base64 解码失败：{e}") from e
    return check_image(raw, max_bytes)


def fetch_image(ob, data: dict, *, timeout: float = 15.0,
                max_bytes: int = MAX_BYTES) -> bytes:
    """从 image 段 data 取图片字节：url → base64 → file → OneBot get_image 兜底。

    顺序是有讲究的：NapCat 在 HTTP 事件里通常给 url（快、不用回调），
    url 缺失时再问 `get_image`（它会回 url 或 base64://）。
    """
    data = data or {}
    url = str(data.get("url") or "").strip()
    if url:
        return _fetch(url, timeout=timeout, max_bytes=max_bytes)
    for key in ("file", "path"):
        v = str(data.get(key) or "").strip()
        if v.startswith(("base64://", "data:")):
            if v.startswith("data:"):
                head, sep, tail = v.partition(",")
                if not sep or ";base64" not in head:
                    raise VisionError("data URL 格式不对（缺逗号或不是 base64）")
                return _from_b64(tail, max_bytes)
            return _from_b64(v, max_bytes)
    file_ref = str(data.get("file") or "").strip()
    if not file_ref:
        raise VisionError("图片段没有 url / file，取不到图")
    if ob is None or not hasattr(ob, "get_image"):
        raise VisionError("没有 OneBot 客户端，取不到图")
    try:
        info = ob.get_image(file_ref)
    except Exception as e:                                        # noqa: BLE001
        raise VisionError(f"get_image 失败：{type(e).__name__}: {e}") from e
    info = info if isinstance(info, dict) else {}
    u2 = str(info.get("url") or "").strip()
    if u2:
        return _fetch(u2, timeout=timeout, max_bytes=max_bytes)
    b64 = str(info.get("base64") or info.get("file") or "").strip()
    if b64:
        return _from_b64(b64, max_bytes)
    raise VisionError("get_image 没返回 url/base64")


def _vision_target(models, provider: str = "", model: str = "") -> dict:
    """决定用哪个 provider/model 看图，并判断「到底配了没」。"""
    conf = {}
    try:
        conf = models.vision() or {}
    except Exception as e:                                        # noqa: BLE001
        logger.warning("读取 vision 配置失败：%s: %s", type(e).__name__, e)
        conf = {}
    pid = str(provider or conf.get("provider") or "").strip()
    mid = str(model or conf.get("model") or "").strip()
    # 只有「显式给了 model」或「配置里明确 configured」才算配好；只给 provider 不算
    configured = bool(conf.get("configured")) or bool(str(model or "").strip())
    if not pid:
        pid = str((models.active() or {}).get("provider") or "")
    if not mid:
        mid = str((models.active() or {}).get("model") or "")
    return {"provider": pid, "model": mid, "configured": bool(configured and mid)}


def configured(models) -> bool:
    """models.json 里配了 vision 模型没有（没配就别白下载图）。"""
    return bool(_vision_target(models)["configured"])


def describe(models, image_bytes: bytes, *, text: str = "", prompt: str = "",
             name: str = "可莉",
             provider: str = "", model: str = "", max_bytes: int = MAX_BYTES,
             timeout: float = 60.0) -> str:
    """让 vision 模型描述一张图；返回中文描述（已截断）。失败抛异常。"""
    target = _vision_target(models, provider, model)
    if not target["configured"]:
        raise VisionUnavailable("没有配置 vision 模型")
    data_url = to_data_url(image_bytes, max_bytes)
    q = str(text or "").strip() or "这张图里有什么？"
    question = f"用户发来一张图，附带的话：「{q}」。说说你看到了什么。"
    msgs = [
        {"role": "system",
         "content": str(prompt or DEFAULT_PROMPT.replace("可莉", str(name or "可莉")))},
        {"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    try:
        reply = models.chat(msgs, model=target["model"] or None,
                            provider=target["provider"] or None,
                            timeout=timeout, max_tokens=200)
    except VisionUnavailable:
        raise
    except Exception as e:                                        # noqa: BLE001
        raise VisionError(f"vision 模型调用失败：{type(e).__name__}: {e}") from e
    desc = " ".join(str(reply or "").split()).strip()
    if not desc:
        raise VisionError("vision 模型返回空描述")
    return desc[:MAX_DESC]


def image_key(raw: bytes) -> str:
    """图片内容指纹（调试/日志用，不落盘）。"""
    return hashlib.sha256(raw or b"").hexdigest()[:16]


def dumps(obj) -> str:
    """小工具：把 payload 打日志前压成一行（避免长 base64 刷屏）。"""
    try:
        return json.dumps(obj, ensure_ascii=False)[:300]
    except (TypeError, ValueError):
        return str(obj)[:300]
