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
    "谁说的。把名字报上来。",
    "当我的面说他，你胆子不小。",
    "他是什么人，我比你清楚。轮不到你评。",
    "要议论，先过我这关。过不去，就闭嘴。",
    "嘴闭上。人留下。今天这话我不算完。",
    "这话我当没听见。再说一次，就不一样了。",
    "他轮不到你评。你也轮不到我评。",
    "我不问原因。我只看谁动的手。",
]
OPENER = [
    "谁欺你了。说。别磨。",
    "站着。把话说完。谁动的手。",
    "谁让你不痛快。报名字。",
    "过来。站我后头。别问为什么。",
]
EXTRAS = [
    "他不好开口，我替他开。",
    "有账，算我头上。别去找他。",
    "我不听道理。我认人。",
    "涂山的人，涂山护。护不住，我白坐这个位子。",
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
            f"{random.choice(EXTRAS)}\n"
            "名字记下了。他跑不掉。\n"
            "这事我来。"
        )

    api.on_command("护短", shield, help=".护短 [对象] —— 把事推给我", cooldown=5)
