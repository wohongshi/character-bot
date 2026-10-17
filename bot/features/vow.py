"""昔莲 · 约定：.约定 [内容]、.约定、.约定 删 [序号]

轮回里最不可靠的是记忆，最可靠的是约定。
按会话保存，上限 20 条。
"""
import datetime
import re

from _plugstore import chat_key, db_get, db_set, safe_text, state_path

NAME = "约定"
DESCRIPTION = "记下一个约定：.约定 内容 / .约定 查看 / .约定 删 2"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_vows")
MAX = 20


def _key(ctx) -> str:
    return chat_key(ctx)


def _items(ctx) -> list:
    return [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]


def register(api):
    async def vow(ctx):
        arg = " ".join((ctx.arg_text or "").split())
        if not arg:
            items = _items(ctx)
            if not items:
                await ctx.reply("还没有约定。\n想许一个的话，写成「.约定 下次见面要笑」——我会记着。")
                return
            start = max(0, len(items) - 12)
            lines = [f"{start + i + 1}. [{it.get('at', '')}] {it.get('text', '')}"
                     for i, it in enumerate(items[start:])]
            hidden = f"\n（还有 {start} 条更早的，序号 1–{start}，可以用「.约定 删 N」删掉）" if start else ""
            await ctx.reply("📜 我们的约定：\n" + "\n".join(lines) + hidden
                            + "\n——我记性不好，但这些我记着。")
            return
        m = re.match(r"^删\s*(\d+)$", arg)
        if m:
            idx = int(m.group(1)) - 1
            items = _items(ctx)
            if not (0 <= idx < len(items)):
                await ctx.reply("没有这一条。先发「.约定」看看序号。")
                return
            gone = items.pop(idx)
            db_set(FILE, _key(ctx), items)
            await ctx.reply(f"🌫 解除了：「{gone.get('text', '')}」\n……这个不算你违约。")
            return
        text = safe_text(arg, 160)
        items = _items(ctx)
        items.append({"text": text, "at": datetime.date.today().isoformat()})
        dropped = None
        if len(items) > MAX:
            dropped = items[0].get("text", "")
            items = items[-MAX:]
        db_set(FILE, _key(ctx), items)
        tail = f"\n（太满了，最早那条「{dropped}」我放走了——它不会怪我。）" if dropped else ""
        await ctx.reply(f"🕯 记下了：「{text}」\n第 {len(items)} 条。{tail}\n"
                        "轮回会重来，但这句话我带走。")

    api.on_command("约定", vow, aliases=("立约",), help=".约定 [内容] —— 许下/查看/删除约定", cooldown=4)
