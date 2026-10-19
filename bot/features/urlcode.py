"""网址编解码 + 拆链接：``.urlenc`` / ``.urldec`` / ``.链接 <url>``。

通用插件。纯离线。

``.链接`` 存在的理由：一条带一堆 query 的链接，人眼根本看不出参数是什么 ——
拆成「主机 / 路径 / 每个参数」比原文有用得多，而且顺便把中文参数解码。
"""
from __future__ import annotations

import urllib.parse

NAME = "网址工具"
NAME_EN = "URL tools"
DESCRIPTION = "URL 编码/解码，或者把一条链接拆成主机、路径和参数。"
DESCRIPTION_EN = "URL encode/decode, and break a link into host, path and query."
VERSION = "1.0"

MAX_IN = 6000
MAX_OUT = 1600


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _clip(s: str) -> str:
    s = str(s or "")
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + "\n…（太长了，截断）"


def parse_link(url: str) -> list[str]:
    """把链接拆成给人看的多行。不是 http(s) 也照样拆（ftp / file / 相对路径都行）。"""
    u = str(url or "").strip()
    if not u:
        raise ValueError("没给链接")
    if "://" not in u and not u.startswith("//"):
        u = "http://" + u                     # 容忍「example.com/a?b=1」
    p = urllib.parse.urlsplit(u)
    host = p.hostname or ""
    lines = [f"🌐 主机：{host or '（没有）'}" + (f" · 端口 {p.port}" if p.port else "")]
    scheme = p.scheme or "https"
    lines.append(f"协议：{scheme}" + ("（不是 http/https，注意）" if scheme not in ("http", "https") else ""))
    if p.path:
        lines.append("路径：" + urllib.parse.unquote(p.path))
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    if q:
        lines.append(f"参数（{len(q)} 个）：")
        for k, v in q[:12]:
            lines.append(f"  · {k} = {v or '（空）'}")
        if len(q) > 12:
            lines.append(f"  …还有 {len(q) - 12} 个")
    if p.fragment:
        lines.append("锚点：" + urllib.parse.unquote(p.fragment))
    return lines


def register(api):
    async def enc(ctx):
        text = raw_args(ctx)
        if not text.strip():
            await ctx.reply("用法：.urlenc 你好 世界（会编成 %E4%BD%A0%E5%A5%BD…）")
            return
        if len(text) > MAX_IN:
            await ctx.reply(f"太长了（上限 {MAX_IN} 字）")
            return
        await ctx.reply("🔗 " + _clip(urllib.parse.quote(text, safe="")))

    async def dec(ctx):
        text = raw_args(ctx).strip()
        if not text:
            await ctx.reply("用法：.urldec %E4%BD%A0%E5%A5%BD")
            return
        # 「+」在 query 里代表空格，但整段文本解码时别乱动别的
        await ctx.reply("🔓 " + _clip(urllib.parse.unquote(text.replace("+", " "))))

    async def info(ctx):
        text = raw_args(ctx).strip()
        if not text:
            await ctx.reply("用法：.链接 https://example.com/a?x=1&y=中文")
            return
        try:
            lines = parse_link(text)
        except ValueError as e:
            await ctx.reply(f"看不懂这条链接：{e}")
            return
        await ctx.reply("\n".join(lines))

    api.on_command("urlenc", enc, cooldown=2, help=".urlenc <文字> —— URL 编码")
    api.on_command("urldec", dec, cooldown=2, help=".urldec <%E4%BD%A0…> —— URL 解码")
    api.on_command("链接", info, aliases=("url",), cooldown=2,
                   help=".链接 <url> —— 拆开主机/路径/参数")
