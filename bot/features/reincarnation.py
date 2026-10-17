"""昔莲 · 轮回计数：.轮回

全服共享一个计数器；每次 +1，并随机给一句「上一次轮回」的碎片。
"""
import random

from _plugstore import db_get, db_set, state_path

NAME = "轮回计数"
DESCRIPTION = "数一数这个世界走过多少轮：.轮回"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_cycles")
TOTAL = 33_550_536          # 她记得的那个数字；从这里继续往上数

FRAGMENTS = [
    "那一次，哀丽秘榭的花开得比今年早。",
    "那一次，有人在书的最后一页写了「别怕」。",
    "那一次，白厄没有回头。",
    "那一次，三月七问我，粉色的花叫什么名字。",
    "那一次，我学会了一个新词：舍不得。",
    "那一次，整个世界只剩下一盏灯。",
    "那一次，我数错了。于是又数了一遍。",
    "那一次，有人叫了我的名字，我就醒了过来。",
]


def register(api):
    async def cycle(ctx):
        try:
            n = max(0, int(db_get(FILE, "count", 0) or 0)) + 1
        except (TypeError, ValueError):
            n = 1
        db_set(FILE, "count", n)
        total = TOTAL + n
        await ctx.reply(
            f"🌀 第 {total:,} 次轮回\n"
            f"这一次，你在这里。（这是有人陪我数的第 {n} 次。）\n"
            f"碎片：{random.choice(FRAGMENTS)}\n"
            f"——数字很大，但每一次都是真的。"
        )

    api.on_command("轮回", cycle, aliases=("数轮回",), help=".轮回 —— 为这个世界数一轮", cooldown=3)
