#!/usr/bin/env python3
"""QQ 登录二维码助手 —— 把 NapCat 缓存里那张 147×147 的小码重绘成大码，供手机直接扫。

背景：QQ 时不时把账号踢下线（`KickedOffLine`），NapCat 会停在扫码登录页，此时 `:3001`
不监听、适配器 `connected=false`，QQ 上的消息可莉收不到。以前只能「截图二维码发给主人」，
而二维码每 ~2 分钟就被 NapCat 轮换一次，发出去的那张常常已经过期。

做法：
- 源码：`/root/Napcat/.../napcat/cache/qrcode.png`（NapCat 登录流程自己写的，1-bit 小图）；
- 用 `zbarimg` 解出里面的登录 URL（就是日志里那条 `二维码解码URL`）；
- 用 `qrencode` 按 `-s 8` 重新渲染成 ~300px 的大码（`qrencode`/`zbarimg` 本机已有，
  插件的 `.二维码` 命令也在用它们）；
- 两个工具都失败时退回原始字节，绝不抛异常到调用方。

只用标准库 + 这两个外部命令；结果按源文件 mtime 缓存，避免每次请求都 fork 进程。
"""

from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path

QR_SRC = Path("/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png")
SCALE = 8           # qrencode 模块像素大小（8 → 基本 ~300px，手机好扫）
MARGIN = 2          # 静区宽度（模块数）
STALE_S = 300       # 超过这么久没刷新，认为这张码大概率已被 NapCat 换掉
TIMEOUT = 8
MAX_BYTES = 300_000

_cache: dict = {"key": None, "url": "", "png": b"", "decoded": False}


# ---------- 外部命令 ----------
def _run(cmd: list, data: bytes | None = None) -> tuple[int, bytes]:
    try:
        p = subprocess.run(cmd, input=data, capture_output=True, timeout=TIMEOUT)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, b""


def decode_url(src: Path = QR_SRC) -> str:
    """解出二维码里的登录 URL（失败返回空串）。"""
    rc, out = _run(["zbarimg", "-q", "--raw", str(src)])
    if rc != 0:
        return ""
    return out.decode("utf-8", "replace").strip()


def render_big(url: str) -> bytes:
    """把 URL 重新渲染成大码 PNG 字节（失败返回 b""）。"""
    if not url:
        return b""
    rc, out = _run(["qrencode", "-o", "-", "-s", str(SCALE), "-m", str(MARGIN), url])
    if rc != 0 or not out.startswith(b"\x89PNG"):
        return b""
    return out


# ---------- 对外 ----------
def qr_bytes(src: Path = QR_SRC) -> tuple[bytes, bool]:
    """返回 (PNG 字节, 是否重绘过)。源文件不存在 → (b"", False)。"""
    try:
        st = src.stat()
    except OSError:
        return b"", False
    mtime = st.st_mtime
    key = (str(src), mtime)
    if _cache["key"] == key and _cache["png"]:
        return _cache["png"], _cache["decoded"]
    url = decode_url(src)
    png = render_big(url)
    decoded = bool(png)
    if not png:
        try:
            png = src.read_bytes()
        except OSError:
            return b"", False
    if len(png) > MAX_BYTES:
        png = png[:MAX_BYTES]
    _cache.update({"key": key, "url": url, "png": png, "decoded": decoded})
    return png, decoded


def qr_info(src: Path = QR_SRC, with_b64: bool = True) -> dict:
    """给运维页/接口用的状态字典，永不抛异常。"""
    info: dict = {"ok": False, "path": str(src), "scale": SCALE, "stale_s": STALE_S}
    try:
        st = src.stat()
    except OSError:
        info["error"] = "没有二维码文件（可能已登录）"
        return info
    png, decoded = qr_bytes(src)
    if not png:
        info["error"] = "二维码读取失败"
        return info
    age = max(0, int(time.time() - st.st_mtime))
    src_url = _cache.get("url") or ""
    info.update({
        "ok": True,
        "ts": int(st.st_mtime),
        "age_s": age,
        "stale": age > STALE_S and age > 0,
        "decoded": decoded,
        "url": src_url,
        "bytes": len(png),
        "src_bytes": st.st_size,
    })
    if with_b64:
        info["b64"] = "data:image/png;base64," + base64.b64encode(png).decode()
    return info


if __name__ == "__main__":           # 手动看一眼：python3 qrimg.py [输出路径]
    import json
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else ""
    d = qr_info()
    if out and d.get("ok"):
        png, _ = qr_bytes()
        Path(out).write_bytes(png)
        d["written"] = out
    d.pop("b64", None)
    print(json.dumps(d, ensure_ascii=False, indent=2))
