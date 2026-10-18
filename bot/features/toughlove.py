"""涂山红红 · 护短：.护短 [对象]

涂山之王护人不讲道理。只问是谁说的，不问谁有理。
"""
import random

from _plugstore import safe_text

NAME = "护短"
DESCRIPTION = "替人出头：.护短 [对象]"
VERSION = "1.0"
CHARACTERS = ("honghong",)

LINES = [
    "谁说的。名字。",
    "当我的面说他，你胆子不小。",
    "他是什么人，我比你清楚。",
    "要议论，先过我这关。",
    "嘴闭上。人留下。",
    "这话我当没听见。再说一次，就不一样了。",
    "他轮不到你评。",
    "我不问原因。我只看谁动的手。",
]
OPENER = [
    "谁欺你了。说。",
    "站着。把话说完。",
    "谁让你不痛快。",
    "过来。站我后头。",
]


def register(api):
    async def shield(ctx):
        who = safe_text(ctx.arg_text, 14)
        if who:
            first = f"「{who}」的事，我接了。"
        else:
            first = random.choice(OPENER)
        await ctx.reply(
            f"{first}\n"
            f"{random.choice(LINES)}\n"
            "这事我来。"
        )

    api.on_command("护短", shield, help=".护短 [对象] —— 把事推给我", cooldown=5)
