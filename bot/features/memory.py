"""昔莲 · 记忆存档：.存档 [一句话]、.记忆、.存档 删 [序号]

按会话保存；上限 50 条，超出时挤掉最早的一条。
"""
import datetime
import re

from _plugstore import chat_key, db_get, db_set, state_path

NAME = "记忆存档"
DESCRIPTION = "把一句话交给昔莲保管：.存档 内容、.记忆、.存档 删 2"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_memories")
MAX = 50


def _key(ctx) -> str:
    return chat_key(ctx)


def register(api):
    async def store(ctx):
        arg = (ctx.arg_text or "").strip()
        m = re.match(r"^删\s*(\d+)$", arg)
        if m:
            idx = int(m.group(1)) - 1
            items = [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]
            if not (0 <= idx < len(items)):
                await ctx.reply("没有这一条哦，先发 .记忆 看看序号。")
                return
            gone = items.pop(idx)
            db_set(FILE, _key(ctx), items)
            await ctx.reply(f"🍃 放下了：「{gone.get('text', '')}」\n它不会被风吹散，只是不再放在我这里了。")
            return
        if arg.startswith("删"):
            await ctx.reply("要删第几条？写成「.存档 删 2」。（想存以「删」开头的话，前面加个字）")
            return
        if not arg:
            await ctx.reply("想让我替你记住什么？写成「.存档 今天发生的事」。")
            return
        items = [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]
        items.append({"text": arg[:220], "at": datetime.date.today().isoformat()})
        if len(items) > MAX:
            items = items[-MAX:]
        db_set(FILE, _key(ctx), items)
        await ctx.reply(f"📖 记下了：「{arg[:220]}」\n第 {len(items)} 条。只要还有人念起，就不算离开。")

    async def recall(ctx):
        items = [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]
        if not items:
            await ctx.reply("这里还是空的。想留下什么，就发 .存档 吧。")
            return
        start = max(0, len(items) - 12)
        lines = [f"{start + i + 1}. [{it.get('at', '')}] {it.get('text', '')}"
                 for i, it in enumerate(items[start:])]
        more = "" if start == 0 else f"\n……还有 {start} 条更早的。"
        await ctx.reply("📚 你交给我的记忆：\n" + "\n".join(lines) + more)

    api.on_command("存档", store, aliases=("记忆存档",), help=".存档 内容 / .存档 删 序号 —— 交给昔莲保管")
    api.on_command("记忆", recall, aliases=("我的记忆",), help=".记忆 —— 看看存档了什么")
