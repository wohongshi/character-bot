"""涂山红红 · 涂山：.涂山

山门里的五处地方。随便看看可以，乱走不行。
"""
import random

from _plugstore import safe_text

NAME = "涂山"
DESCRIPTION = "在涂山转一圈：.涂山"
VERSION = "1.0"
CHARACTERS = ("honghong",)

PLACES = [
    ("苦情树", "山心那棵。叶子落下来像蒲公英，许愿的人排到山门。"),
    ("妖馨斋", "厨子在后头剁骨头。人吃的也有，别挑。"),
    ("古宝斋", "架子上什么都有。看可以，别伸手。"),
    ("约会楼", "红灯笼挂三层。谈事的、等人的，都在里头。"),
    ("双生峰", "山顶有冰。没事别上去。"),
]
TAILS = [
    "别乱走。走丢了，我不去找。",
    "别乱走。山认路，不认人。",
    "别乱走。天黑之前回山门。",
]


def register(api):
    async def tour(ctx):
        who = safe_text(ctx.arg_text, 8)
        place, desc = random.choice(PLACES)
        lead = f"「{who}」跟着。" if who else "跟着。"
        await ctx.reply(
            f"{lead}\n"
            "涂山。\n"
            f"{place}：{desc}\n"
            "山上不打仗。要打，下山去打。\n"
            f"{random.choice(TAILS)}"
        )

    api.on_command("涂山", tour, help=".涂山 —— 听一处地方的来历", cooldown=5)
