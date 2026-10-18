"""涂山红红 · 契：.契 [内容]、.契、.契 解除 N

说定的事写下来。一个会话最多八条，立了就别赖。
"""
import re

from _plugstore import chat_key, db_get, db_update, safe_text, state_path

NAME = "契"
DESCRIPTION = "立一条契或翻旧账：.契 [内容]、.契、.契 解除 N"
VERSION = "1.0"
CHARACTERS = ("honghong",)

FILE = state_path("honghong_contract")
MAX = 8


def register(api):
    async def pact(ctx):
        key = chat_key(ctx)
        arg = safe_text(ctx.arg_text, 12)
        rows = [x for x in (db_get(FILE, key, []) or []) if isinstance(x, str)]

        m = re.match(r"^解除\s*(\d+)$", arg)
        if m:
            idx = int(m.group(1)) - 1
            gone = {"text": None}

            def drop(cur):
                items = [x for x in (cur or []) if isinstance(x, str)]
                if 0 <= idx < len(items):
                    gone["text"] = items.pop(idx)
                return items

            db_update(FILE, key, drop, [])
            if gone["text"] is None:
                await ctx.reply(
                    f"没有第 {idx + 1} 条。\n"
                    "簿子上的数，不是随便报的。\n"
                    "先发 .契 看清编号。别让我替你数。"
                )
                return
            await ctx.reply(
                f"解了。\n"
                f"「{gone['text']}」不算数了。\n"
                "下不为例。\n"
                "再立一次，我可记得你解过。想清楚再开口。\n"
                "涂山不认第二次反悔。"
            )
            return
        if arg.startswith("解除"):
            await ctx.reply("解哪条。\n说编号。\n.契 解除 3")
            return

        if not arg:
            if not rows:
                await ctx.reply(
                    "契 · 空。\n"
                    "一条都没立。\n"
                    "要立什么，说清楚。\n"
                    "别写一半，也别写你做不到的。\n"
                    "我不收空话。现在就说。"
                )
                return
            items = [f"{i + 1}. {x}" for i, x in enumerate(rows)]
            chunks = ["｜".join(items[i:i + 4]) for i in range(0, len(items), 4)]
            await ctx.reply(
                f"契 · 已立 {len(rows)} 条。\n"
                + "\n".join(chunks)
                + "\n字是我记的。赖不掉。\n要解哪条，说编号。"
            )
            return

        full = {"hit": False}

        def push(cur):
            items = [x for x in (cur or []) if isinstance(x, str)]
            if len(items) >= MAX:
                full["hit"] = True
                return items
            items.append(arg)
            return items

        db_update(FILE, key, push, [])
        if full["hit"]:
            await ctx.reply(
                "契满了。八条。\n"
                "要立新的，先解一条。\n"
                "八条是上限。当初说好的。涂山不认第九条。\n"
                "解了再立。别想偷偷加。\n"
                ".契 解除 N"
            )
            return
        await ctx.reply(
            f"「{arg}」\n"
            "字是你说的。我记下了。\n"
            "涂山不认反悔。\n"
            "立了。别赖。\n"
            "违约的，我亲自来。到了那天，别说我没提醒。"
        )

    api.on_command("契", pact, help=".契 [内容] / .契 / .契 解除 N —— 立字，或者销账", cooldown=5)
