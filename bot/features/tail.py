"""涂山红红 · 尾巴：.尾巴

尾巴的状态就是心情。看一眼可以，摸不行。
"""
import random

from _plugstore import safe_text

NAME = "尾巴"
DESCRIPTION = "看一眼尾巴：.尾巴"
VERSION = "1.0"
CHARACTERS = ("honghong",)

STATES = [
    ("在。", "今天没藏。风大，毛有点乱。"),
    ("收起来了。", "不想给人看。"),
    ("被踩过。现在一碰就炸。", "谁的脚，我记着。"),
    ("不给摸。", "问也没用。"),
]
STEPS = [
    "站远一点。我不咬你。",
    "等我睡着。",
    "先把手洗干净。",
    "下次带壶酒来。",
]


def register(api):
    async def tail(ctx):
        state, detail = random.choice(STATES)
        who = safe_text(ctx.arg_text, 8)
        head = f"「{who}」，手放下。" if who else "手放下。"
        await ctx.reply(
            "尾巴。\n"
            f"状态：{state}\n"
            f"{detail}\n"
            f"{head}不给摸。\n"
            f"{random.choice(STEPS)}"
        )

    api.on_command("尾巴", tail, help=".尾巴 —— 看看今天给不给看", cooldown=5)
