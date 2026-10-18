"""昔莲 · 点灯：.点灯

忆域里没有白天，所以得有人点灯。点着的灯她替你数着。
"""
import random

from _plugstore import chat_key, db_update, state_path

NAME = "点灯"
DESCRIPTION = "点一盏灯，她替你数着：.点灯"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_lanterns")

LIGHTS = [
    ("琥珀色", "像把一小块黄昏收进了纸里"),
    ("雪白", "不烫手，只是安静地亮着"),
    ("苔绿", "像春天从指缝里漏了出来"),
    ("浅金", "和你名字里那个字，是同一个颜色"),
    ("靛青", "深得像一口井，井底有光"),
    ("绯红", "有一点烫，可还是舍不得放手"),
]

MILESTONES = {
    10: "十盏了。凑近了看，像一小片没被吹散的星空。",
    50: "五十盏。这一带的路，以后不会有人走丢了。",
    100: "一百盏。我把它们都留着，你随时回来都能认出路。",
}


def register(api):
    async def light(ctx):
        key = chat_key(ctx)
        n = db_update(FILE, key, lambda v: int(v or 0) + 1, default=0)
        color, line = random.choice(LIGHTS)
        extra = ("\n" + MILESTONES[n]) if n in MILESTONES else ""
        await ctx.reply(
            f"🏮 灯亮了，{color}。\n"
            f"{line}。\n"
            f"这是你点的第 {n} 盏。{extra}\n"
            "我把它搁在窗台上。风进来的时候，它会晃一下，但不会灭。"
        )

    api.on_command("点灯", light, help=".点灯 —— 点一盏灯，她替你数着", cooldown=4)
