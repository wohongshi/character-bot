"""茜特菈莉 · 收藏：.收藏 [物件]、.藏品

她嘴上嫌屋子乱，柜子却一直留着空位。
"""
import random
import time

from _plugstore import chat_key, db_get, db_update, safe_text, state_path

NAME = "收藏"
DESCRIPTION = "把一件舍不得扔的小东西寄放在她那儿：.收藏 [物件]、.藏品"
VERSION = "1.0"
CHARACTERS = ("citlali",)

FILE = state_path("citlali_relics")
MAX = 30

POOL = [
    "一枚烧黑的火种壳",
    "半截写断的炭笔",
    "干掉的烟叶梗",
    "没有光泽的黑曜石",
    "写错名字的旧木牌",
    "喝空的甜酒瓶",
    "轻小说第三卷的扉页",
    "熔岩冷下来的一小片薄壳",
    "掉了毛的羽毛笔",
    "磨圆的骰子",
]


def _names(items):
    return [str(x.get("n") or "") for x in items if isinstance(x, dict)]


def register(api):
    async def keep(ctx):
        key = chat_key(ctx)
        item = safe_text(ctx.arg_text, 20)
        if not item:
            samples = "、".join(random.sample(POOL, 3))
            await ctx.reply(
                "📦 要收什么？说个名字。\n"
                "手里空着，我怎么替你收。\n"
                f"比如：{samples}\n"
                "——随口编的也行，反正最后都堆我这儿。"
            )
            return

        outcome = {}

        def add(cur):
            items = list(cur) if isinstance(cur, list) else []
            items = [x for x in items if isinstance(x, dict) and x.get("n")]
            if item in _names(items):
                outcome["kind"] = "dup"
                return items
            if len(items) >= MAX:
                outcome["kind"] = "full"
                return items
            items.append({"n": item, "t": int(time.time())})
            outcome["kind"] = "ok"
            outcome["count"] = len(items)
            return items

        db_update(FILE, key, add, default=[])

        if outcome.get("kind") == "dup":
            await ctx.reply(
                f"📦 这个我已经有了。\n「{item}」在左边那堆里，你自己翻翻。\n"
                "——同一件东西收两次，是觉得奶奶我记性不好？拿回去也行。"
            )
        elif outcome.get("kind") == "full":
            await ctx.reply(
                "📦 放不下了。\n"
                "三十件，一件不多。这屋子再塞就要塌。\n"
                "——想再收，先挑一件丢出去。别看我，我不替你选，也不替你心疼。哎，反正最后心疼的还是我。"
            )
        else:
            await ctx.reply(
                f"📦 收下了：{item}\n"
                f"第 {outcome.get('count', 1)} / {MAX} 件。屋子已经够乱了，你还往里头塞。\n"
                "——放左边第三堆，别压着我的书。我记着了。"
            )

    async def shelf(ctx):
        items = db_get(FILE, chat_key(ctx), []) or []
        items = [x for x in items if isinstance(x, dict) and x.get("n")]
        if not items:
            await ctx.reply(
                "📦 藏品 · 空的\n"
                "你还没往我这儿寄放过东西。\n"
                "——挺好，屋子清净，我也清净。等你有舍不得扔的，再来。"
            )
            return

        shown = items[-8:]
        rows = []
        for row_start in range(0, len(shown), 4):
            chunk = shown[row_start:row_start + 4]
            rows.append("　".join(
                f"{row_start + i + 1} {safe_text(x.get('n'), 10)}"
                for i, x in enumerate(chunk)
            ))
        await ctx.reply(
            f"📦 藏品 · 共 {len(items)} 件（看最近 {len(shown)} 件）\n"
            + "\n".join(rows)
            + "\n——都在左边第三堆。别乱翻，我按自己的顺序摆的。"
        )

    api.on_command("收藏", keep, help=".收藏 [物件] —— 把一件小东西寄放在她那儿", cooldown=4)
    api.on_command("藏品", shelf, help=".藏品 —— 看看最近收了些什么", cooldown=5)
