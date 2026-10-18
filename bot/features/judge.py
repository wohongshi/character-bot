"""涂山红红 · 判词：.判词 [事情的描述]

不劝，不安慰。听完，给一句判断。
"""
import random

from _plugstore import safe_text

NAME = "判词"
DESCRIPTION = "对一件事下一句判断：.判词 [事情的描述]"
VERSION = "1.0"
CHARACTERS = ("honghong",)

VERDICTS = [
    "不必问。你心里已经有答案。",
    "该断。拖下去只会更难看。",
    "留着。还有用。别急着扔。",
    "你想多了。对方没那个意思。",
    "做。后果我兜一半。",
    "错不在你。别再回头看了。",
    "再等三天。三天后还这样，就走。",
    "这件事没有两全。选一个，别后悔。",
    "说是非的人，比事本身麻烦。",
    "……这条路我走过。你走你的。",
]
TAILS = [
    "判完了。怎么走，你自己定。",
    "话就这一句。听不听随你。",
    "别问我第二遍。问了我也不改口。",
]


def register(api):
    async def judge(ctx):
        fact = safe_text(ctx.arg_text, 40)
        if not fact:
            await ctx.reply(
                "说事。\n"
                "别只给我两个字。\n"
                "判词要落在事上。空话我不说。\n"
                "你要的是判断，不是安慰。别让我催。\n"
                "我听着。"
            )
            return
        await ctx.reply(
            f"「{fact}」\n"
            "我听完了。别再补。补也改不了。\n"
            f"判：{random.choice(VERDICTS)}\n"
            f"{random.choice(TAILS)}"
        )

    api.on_command("判词", judge, help=".判词 [事情] —— 说清楚，我给一句", cooldown=5)
