"""昔莲 · 如我所书：.写书 [一句话]、.翻书

所有人往同一本书里写一句，随机翻一页读出来。
"""
import datetime
import random

from _plugstore import db_get, db_set, state_path, clip

NAME = "如我所书"
DESCRIPTION = "把一句话写进翁法罗斯的书里：.写书 内容、.翻书"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_book")
MAX = 500


def register(api):
    async def write(ctx):
        text = clip(ctx.arg_text, 160)
        if not text:
            await ctx.reply("想写什么？一句话就好。「.写书 今天看到了很漂亮的云」。")
            return
        book = [x for x in (db_get(FILE, "pages", []) or []) if isinstance(x, dict)]
        total = int(db_get(FILE, "total", len(book)) or 0) + 1
        book.append({"text": text, "at": datetime.date.today().isoformat()})
        if len(book) > MAX:
            book = book[-MAX:]
        db_set(FILE, "pages", book)
        db_set(FILE, "total", total)
        await ctx.reply(f"🖋 写下了。\n「{text}」\n这是第 {total} 页。有一天，会有人翻到它的。")

    async def read(ctx):
        book = [x for x in (db_get(FILE, "pages", []) or []) if isinstance(x, dict)]
        if not book:
            await ctx.reply("书还是空的。第一页，留给你来写好不好？")
            return
        idx = random.randrange(len(book))
        it = book[idx]
        await ctx.reply(f"📖 翻到第 {idx + 1} 页 · {it.get('at', '')}\n「{it.get('text', '')}」\n"
                        f"——写这句话的人，当时大概很认真吧。")

    api.on_command("写书", write, aliases=("写一页",), help=".写书 内容 —— 写进《如我所书》")
    api.on_command("翻书", read, aliases=("如我所书",), help=".翻书 —— 随机读一页")
