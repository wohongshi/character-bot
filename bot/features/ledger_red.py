"""涂山红红 · 续缘：.续缘 [名字]

说明续缘的流程。她只铺路，不替人做决定。
"""
import random

from _plugstore import safe_text

NAME = "续缘"
DESCRIPTION = "问续缘的流程：.续缘 [名字]"
VERSION = "1.0"
CHARACTERS = ("honghong",)

VERDICTS = [
    "心不诚的，续不上。",
    "记不住的，续了也是白续。",
    "一世不够，就再来一世。代价自己担。",
    "红线不是绳。拽太紧，会断。",
    "愿意等的，我等。等不了的，别来。",
]


def register(api):
    async def ledger(ctx):
        who = safe_text(ctx.arg_text, 12)
        head = f"🧵 续缘 · {who}" if who else "🧵 续缘 · 无名"
        tail = "要本人来。替人问的，我不答。" if who else "名字。要本人来，自己说。"
        await ctx.reply(
            f"{head}\n"
            "苦情树下许愿，说清楚要续给谁。\n"
            "法宝一分为二，一人一半，各自收好。\n"
            f"红线仙主持。判词：{random.choice(VERDICTS)}\n"
            f"{tail}"
        )

    api.on_command("续缘", ledger, help=".续缘 [名字] —— 续缘的流程与判词", cooldown=8)
