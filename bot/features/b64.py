"""Base64 编解码：``.b64e 你好`` / ``.b64d 5L2g5aW9``。

通用插件。纯离线、无状态。
解码时容忍：换行/空格、urlsafe 的 ``-`` ``_``、缺省的 ``=`` 填充 —— 从别处
复制来的 base64 十有八九是这三种「脏」法之一。
"""
from __future__ import annotations

import base64
import binascii
import re

NAME = "Base64"
NAME_EN = "Base64"
DESCRIPTION = "把文字编成 base64，或者把 base64 解回文字。"
DESCRIPTION_EN = "Base64 encode and decode text."
VERSION = "1.0"

MAX_IN = 6000          # 输入上限：再长就该用文件了，聊天框里没意义
MAX_OUT = 1800         # 输出上限：一行一条消息，别刷屏


def raw_args(ctx) -> str:
    """命令后面的**原始**文本（保留空格/换行；`ctx.arg_text` 会把多个空格压成一个）。"""
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def encode(text: str) -> str:
    return base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")


def decode(s: str) -> str:
    """宽松解码。认不出就抛 ValueError（调用方转成人话）。"""
    t = re.sub(r"\s+", "", str(s or ""))
    if not t:
        raise ValueError("没有内容")
    t = t.replace("-", "+").replace("_", "/")      # urlsafe
    t += "=" * (-len(t) % 4)                       # 补 padding
    try:
        blob = base64.b64decode(t, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("这不是合法的 base64（字符数或字符集不对）") from None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        # 中文 UTF-8 解不出来说明原文不是文本（图/压缩包）；至少把字节数说清楚
        raise ValueError(f"解得出来，但不是文字（{len(blob)} 字节，可能是图片或压缩包）") from None


def _clip(s: str) -> str:
    s = str(s or "")
    if len(s) <= MAX_OUT:
        return s
    return s[:MAX_OUT] + f"\n…（还有 {len(s) - MAX_OUT} 字，太长了）"


def register(api):
    async def enc(ctx):
        text = raw_args(ctx)
        if not text.strip():
            await ctx.reply("用法：.b64e 要编码的文字")
            return
        if len(text) > MAX_IN:
            await ctx.reply(f"太长了（{len(text)} 字，上限 {MAX_IN}）～分两段来吧")
            return
        enc = encode(text)
        if len(enc) > MAX_OUT:
            # base64 会涨 4/3：截断后的编码解不回原文，等于给了一段废码。
            # 宁可说清楚能编多少。
            room = MAX_OUT * 3 // 4
            await ctx.reply(f"编出来太长（{len(enc)} 字，上限 {MAX_OUT}）～"
                            f"最多能编 {room} 字，分两段来吧")
            return
        await ctx.reply("🔐 " + enc)

    async def dec(ctx):
        text = raw_args(ctx)
        if not text.strip():
            await ctx.reply("用法：.b64d 5L2g5aW9（贴 base64 过来）")
            return
        try:
            out = decode(text)
        except ValueError as e:
            await ctx.reply(f"诶，解不开：{e}")
            return
        await ctx.reply("🔓 " + _clip(out))

    api.on_command("b64e", enc, cooldown=2, help=".b64e <文字> —— 编成 base64")
    api.on_command("b64d", dec, cooldown=2, help=".b64d <base64> —— 解回文字")
