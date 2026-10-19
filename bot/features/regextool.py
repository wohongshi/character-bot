"""正则小工具：``.正则 <pattern> <文字>`` / ``.正则替换 <pattern> :: <替换> :: <文字>``。

通用插件。纯离线。

写正则最烦的不是写，是**不知道到底匹配到了什么**。这里把每处命中、分组、
位置都列出来，一眼能看出哪里想错了。

长度上限 3000 字：Python 的 re 没有超时机制，给一段病态的正则配上一段长文本
能把整个事件循环卡住 —— 与其做限时，不如先把输入摁住。
"""
from __future__ import annotations

import re

NAME = "正则工具"
NAME_EN = "Regex tools"
DESCRIPTION = "测正则匹配到哪里、分组是什么；或者直接做替换。"
DESCRIPTION_EN = "Show regex matches, groups and spans, or apply a substitution."
VERSION = "1.0"

MAX_LEN = 3000
MAX_HITS = 20


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


# 灾难性回溯（`(a+)+` / `(a|aa)+` / `(\w+\s?)+`）在 Python 的 re 里没有任何超时机制，
# 30 个字符的输入就能卡死整个事件循环。这里的命令跑在 bot 的事件循环里，
# 没有线程超时可用 —— 只能提前拒。两条规则，都尽量不收误报：
#   ① 分组以量词收尾、组外再套量词：`(a+)+`、`(a*)*`、`(\w+\s?)+`
#   ② 交替分组里有一个分支是另一个的前缀，且组外带量词：`(a|aa)+`、`(foo|foobar)+`
#      （`(?:x|y)+` 这种纯字面量交替不会被误伤）
_GROUP_QUANT = re.compile(r"\(([^()]*)\)\s*[*+{]")
_TRAIL_QUANT = re.compile(r"[*+?]$")


def risky_pattern(pattern: str) -> str:
    """看起来会指数回溯的模式返回原因，否则返回空串。"""
    for m in _GROUP_QUANT.finditer(pattern):
        inner, full = m.group(1), m.group(0)
        body = inner[3:] if inner.startswith("?:") else inner
        if _TRAIL_QUANT.search(body):
            return f"这个写法（{full}）容易指数回溯"
        branches = [b for b in body.split("|") if b]
        if len(branches) > 1:
            for a in branches:
                for b in branches:
                    if a is not b and b.startswith(a) and len(a) < len(b):
                        return f"这个写法（{full}）里的交替有前缀关系，容易指数回溯"
    if pattern.count("(") > 12:
        return "分组太多了"
    return ""


def match_report(pattern: str, text: str, limit: int = MAX_HITS) -> list[str]:
    """返回给人看的多行；编译失败抛 re.error。"""
    why = risky_pattern(pattern)
    if why:
        raise ValueError(f"{why}，换种写法吧（比如 (a+)+ 改成 a+，(a|aa)+ 改成 (?:aa?)+ 或加锚点）")
    rx = re.compile(pattern, re.MULTILINE)
    lines = []
    n = 0
    shown = 0
    for m in rx.finditer(text):
        n += 1
        if n > limit:
            continue                      # 只数不列：要不然后面报「共 N 处」会把 N 说多
        shown += 1
        s, e = m.span()
        head = f"{n}. [{s}:{e}] 「{m.group(0)}」"
        if m.groups():
            groups = "、".join(f"${i}={g!r}" for i, g in enumerate(m.groups(), 1))
            head += "\n   " + groups[:300]
        if m.groupdict():
            named = "、".join(f"{k}={v!r}" for k, v in m.groupdict().items())
            head += "\n   " + named[:300]
        lines.append(head)
    if not n:
        lines.append("没有匹配到任何东西。")
    else:
        lines.insert(0, f"共 {n} 处命中：" if n <= limit else f"共 {n} 处命中（只列前 {shown} 处）：")
    return lines


def register(api):
    async def test(ctx):
        raw = raw_args(ctx)
        if not raw.strip():
            await ctx.reply("用法：.正则 <pattern> <文字>\n例：.正则 \\d+ 我的电话 13800001111")
            return
        parts = raw.split(None, 1)
        pattern = parts[0]
        text = parts[1] if len(parts) > 1 else ""
        if not text:
            await ctx.reply("还差要匹配的文字：.正则 <pattern> <文字>")
            return
        if len(text) > MAX_LEN:
            await ctx.reply(f"文字太长了（{len(text)}，上限 {MAX_LEN}）")
            return
        try:
            lines = match_report(pattern, text)
        except re.error as e:
            await ctx.reply(f"这个正则写错了：{e}")
            return
        except ValueError as e:            # 会指数回溯的写法：提前拒，别卡住事件循环
            await ctx.reply(f"❌ {e}")
            return
        await ctx.reply("\n".join(lines)[:1800])

    async def sub(ctx):
        raw = raw_args(ctx)
        pieces = [p.strip() for p in raw.split("::")]
        if len(pieces) < 3:
            await ctx.reply("用法：.正则替换 <pattern> :: <替换成> :: <文字>\n"
                            "例：.正则替换 \\d+ :: *** :: 我的号码 13800001111")
            return
        pattern, repl, text = pieces[0], pieces[1], "::".join(pieces[2:])
        if len(text) > MAX_LEN:
            await ctx.reply(f"文字太长了（{len(text)}，上限 {MAX_LEN}）")
            return
        try:
            why = risky_pattern(pattern)
            if why:
                # 替换和匹配走的是同一个 re 引擎：漏了这道闸，
                # `.正则替换 (a+)+ :: x :: aaaa…` 照样能把事件循环卡死。
                raise ValueError(f"{why}，换种写法吧")
            out, n = re.subn(pattern, repl, text, flags=re.MULTILINE)
        except re.error as e:
            await ctx.reply(f"这个正则写错了：{e}")
            return
        except ValueError as e:
            await ctx.reply(f"❌ {e}")
            return
        if not n:
            await ctx.reply("一处都没匹配到，原样返回：\n" + text[:1200])
            return
        await ctx.reply(f"✅ 替换了 {n} 处：\n{out[:1500]}")

    api.on_command("正则", test, aliases=("regex",), cooldown=2,
                   help=".正则 <pattern> <文字> —— 看到匹配和分组")
    api.on_command("正则替换", sub, cooldown=2,
                   help=".正则替换 <pattern> :: <替换> :: <文字>")
