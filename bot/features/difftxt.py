"""文本对比：``.对比 旧文本 || 新文本``。

通用插件。纯离线。

两种呈现，按内容自动选：
- 都是短单行 → **行内**标出改动：``这是[-旧-]{+新+}的``（一眼看到改了哪几个字）；
- 多行 / 长文本 → **逐行** diff（``- 旧行`` / ``+ 新行``）。

分隔符认三种：``||``、单独一行的 ``---``、以及 ``===``。从别处复制两段话时
几乎总能撞上其中一种。
"""
from __future__ import annotations

import asyncio
import difflib

NAME = "文本对比"
NAME_EN = "Text diff"
DESCRIPTION = "对比两段文字，标出改了哪里。"
DESCRIPTION_EN = "Compare two texts and mark what changed."
VERSION = "1.0"

MAX_IN = 8000
MAX_LINES = 40
INLINE_MIN_RATIO = 0.45          # 相似度太低就别行内标了，满屏都是标记没法看


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def split_pair(raw: str) -> tuple[str, str] | None:
    """把「旧 || 新」拆开。认 ``||``、``---`` 独立行、``===``。"""
    s = str(raw or "")
    for sep in ("||", "｜｜"):
        if sep in s:
            a, _, b = s.partition(sep)
            return a.strip(), b.strip()
    for line_sep in ("\n---\n", "\n===\n"):
        if line_sep in s:
            a, _, b = s.partition(line_sep)
            return a.strip(), b.strip()
    return None


def inline_diff(old: str, new: str) -> str:
    """逐字符标改动：相同原样，删掉的包 [- -]，新增的包 {+ +}。"""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(old[i1:i2])
        elif tag == "delete":
            out.append("[-" + old[i1:i2] + "-]")
        elif tag == "insert":
            out.append("{+" + new[j1:j2] + "+}")
        else:                                        # replace
            out.append("[-" + old[i1:i2] + "-]{+" + new[j1:j2] + "+}")
    return "".join(out)


def line_diff(old: str, new: str, limit: int = MAX_LINES) -> list[str]:
    a = old.splitlines()
    b = new.splitlines()
    out: list[str] = []
    seen_hunk = False
    for ln in difflib.unified_diff(a, b, lineterm="", n=0):
        if not seen_hunk:
            # 头部 `--- a` / `+++ b` 只在第一个 @@ 之前；按前缀过滤会把
            # 内容本身以 `--` / `++` 开头的行（如 "--注释"）也一起丢掉。
            if not ln.startswith("@@"):
                continue
            seen_hunk = True
        if ln.startswith("@@"):
            out.append(ln)
            continue
        out.append(ln)
        if len(out) >= limit:
            out.append("…（还有更多差异，先看这些）")
            break
    return out


def similarity(old: str, new: str) -> float:
    return difflib.SequenceMatcher(None, old, new, autojunk=False).ratio()


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx)
        if "||" not in raw and "\n---\n" not in raw and "\n===\n" not in raw and "｜｜" not in raw:
            await ctx.reply("用法：.对比 旧的说法 || 新的说法\n"
                            "多行文本用单独一行 --- 隔开也行。")
            return
        pair = split_pair(raw)
        if not pair:
            await ctx.reply("没找到分隔符～两段之间用 || 或者单独一行 --- 隔开。")
            return
        old, new = pair
        if not old and not new:
            await ctx.reply("两边都是空的，没法比呀")
            return
        if len(old) + len(new) > MAX_IN:
            await ctx.reply(f"太长了（合计 {len(old) + len(new)} 字，上限 {MAX_IN}）")
            return
        if old == new:
            await ctx.reply("✨ 两段一模一样，一个字都没改。")
            return
        # SequenceMatcher(autojunk=False) 是纯 CPU：8000 字重复文本能卡住事件循环几百毫秒
        ratio = await asyncio.to_thread(similarity, old, new)
        head = f"🔍 相似度 {ratio * 100:.0f}%（{len(old)} 字 → {len(new)} 字）"
        single = ("\n" not in old) and ("\n" not in new)
        if single and max(len(old), len(new)) <= 300 and ratio >= INLINE_MIN_RATIO:
            body = await asyncio.to_thread(inline_diff, old, new)
            await ctx.reply(f"{head}\n{body}\n（[-删掉-] {{+新增+}}）")
            return
        lines = await asyncio.to_thread(line_diff, old, new)
        if not lines:
            await ctx.reply(head + "\n只差在空白字符上。")
            return
        await ctx.reply(head + "\n" + "\n".join(lines))

    api.on_command("对比", cmd, aliases=("diff",), cooldown=3,
                   help=".对比 <旧> || <新> —— 标出改动")
