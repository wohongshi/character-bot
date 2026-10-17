"""二维码：``.二维码 <文本/链接>`` —— 把内容变成一张能扫的二维码。

本地 ``qrencode`` 生成（零联网、零依赖），存 ``state/qr/``；顺手清理 3 天前的旧图。
"""

import asyncio
import datetime
import hashlib
import subprocess
import time
from pathlib import Path

NAME = "二维码"
DESCRIPTION = "把文本/链接变成二维码：.二维码 <内容>"
VERSION = "1.0"

def _state_root() -> "pathlib.Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    import os
    base = os.environ.get("KLEE_STATE_DIR")
    from pathlib import Path as _P
    return _P(base) if base else _P(__file__).resolve().parent / "state"


BASE = _state_root()
MAX_CHARS = 300
KEEP_DAYS = 3


def _qr_dir(settings: dict) -> Path:
    root = settings.get("qr_dir")
    d = Path(str(root)) if root else BASE / "qr"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make(text: str, out: Path) -> None:
    try:
        subprocess.run(["qrencode", "-s", "8", "-m", "3", "-o", str(out), text],
                       check=True, timeout=10, capture_output=True)
    except FileNotFoundError as e:
        raise RuntimeError("no-qrencode") from e


def _cleanup(d: Path) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for p in d.glob("*.png"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def register(api):
    settings = dict(getattr(api, "settings", None) or {})

    async def qr_cmd(ctx):
        text = (ctx.arg_text or "").strip()
        if not text:
            await ctx.reply("用法：.二维码 <文本或链接>，比如 .二维码 https://example.com")
            return
        if len(text) > MAX_CHARS:
            await ctx.reply(f"内容太长啦（最多 {MAX_CHARS} 字）～短一点的更容易扫出来哦")
            return
        d = _qr_dir(settings)
        name = (hashlib.sha1(text.encode("utf-8")).hexdigest()[:10] + "-"
                + datetime.datetime.now().strftime("%m%d%H%M%S") + ".png")
        out = d / name
        try:
            await asyncio.to_thread(_make, text, out)
        except Exception:                                    # noqa: BLE001
            await ctx.reply("呜……二维码没画出来，等会儿再试试嘛")
            return
        try:
            await ctx.adapter.send_image_file(ctx.chat_id, str(out), caption="📱 扫扫看～")
        except Exception:                                    # noqa: BLE001
            return
        try:
            await asyncio.to_thread(_cleanup, d)
        except Exception:                                    # noqa: BLE001
            pass

    api.on_command("二维码", qr_cmd, aliases=("qr",), cooldown=10,
                   help=".二维码 <内容> —— 生成一张二维码图")
