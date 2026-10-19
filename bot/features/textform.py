"""文本变形：``.变形 反转 你好`` / ``.变形 全角 abc``。

通用插件。纯离线、无状态。

为什么值得单独一个插件：这些操作天天要用（把一段话转大小写、把半角标点转全角、
把一堆行排序去重），但没人愿意为此打开网页工具。一次 `.变形` 就够。
"""
from __future__ import annotations

NAME = "文本变形"
NAME_EN = "Text transform"
DESCRIPTION = "大小写、全角半角、反转、排序、去重、整理空行。"
DESCRIPTION_EN = "Case, full-width/half-width, reverse, sort, dedupe, tidy."
VERSION = "1.0"

MAX_IN = 6000
MAX_OUT = 1800

# 操作 → (中文名, 英文别名)
OPS = {
    "upper": ("大写", ("大写", "upper", "up")),
    "lower": ("小写", ("小写", "lower", "low")),
    "title": ("首字母大写", ("首字母", "title")),
    "reverse": ("反转", ("反转", "reverse", "rev")),
    "full": ("全角", ("全角", "full", "fullwidth")),
    "half": ("半角", ("半角", "half", "halfwidth")),
    "sort": ("行排序", ("排序", "sort")),
    "uniq": ("行去重", ("去重", "uniq", "unique")),
    "tidy": ("整理空行", ("整理", "tidy", "trim")),
}
_ALIAS = {}
for _key, (_cn, _al) in OPS.items():
    for _a in _al:
        _ALIAS[_a.lower()] = _key


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def split_op(raw: str) -> tuple[str, str]:
    """'反转 你好' → ('reverse', '你好')。认不出操作时返回 ('', 原文)。"""
    s = str(raw or "").lstrip()
    if not s:
        return "", ""
    head, _, rest = s.partition(" ")
    key = _ALIAS.get(head.strip().lower())
    if key is None:
        return "", s
    return key, rest


def _fullwidth(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0x20:
            out.append("\u3000")
        elif 0x21 <= o <= 0x7E:
            out.append(chr(o + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _halfwidth(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0x3000:
            out.append(" ")
        elif 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def transform(op: str, text: str) -> str:
    if op == "upper":
        return text.upper()
    if op == "lower":
        return text.lower()
    if op == "title":
        # 逐词首字母大写；中文没有大小写，原样留着
        return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))
    if op == "reverse":
        return text[::-1]
    if op == "full":
        return _fullwidth(text)
    if op == "half":
        return _halfwidth(text)
    if op == "sort":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(sorted(lines))
    if op == "uniq":
        seen, out = set(), []
        for ln in text.splitlines():
            k = ln.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(ln)
        return "\n".join(out)
    if op == "tidy":
        out, blank = [], False
        for ln in text.splitlines():
            k = ln.rstrip()
            if not k.strip():
                if out and not blank:
                    out.append("")
                blank = True
            else:
                out.append(k)
                blank = False
        return "\n".join(out).strip()
    return text


def register(api):
    async def cmd(ctx):
        op, text = split_op(raw_args(ctx))
        if not op:
            names = "、".join(v[0] for v in OPS.values())
            await ctx.reply(f"用法：.变形 <操作> <文字>\n可用操作：{names}\n"
                            "例：.变形 反转 你好呀 / .变形 去重（贴多行）")
            return
        if not text.strip():
            await ctx.reply("要变形的文字呢？例：.变形 大写 hello world")
            return
        if len(text) > MAX_IN:
            await ctx.reply(f"太长了（{len(text)} 字，上限 {MAX_IN}）")
            return
        out = transform(op, text)
        if not out.strip():
            await ctx.reply("变完什么都没剩下…")
            return
        if len(out) > MAX_OUT:
            out = out[:MAX_OUT] + "\n…（太长了，截断）"
        await ctx.reply(out)

    api.on_command("变形", cmd, aliases=("transform",), cooldown=2,
                   help=".变形 <大写|小写|反转|全角|半角|排序|去重|整理> <文字>")
