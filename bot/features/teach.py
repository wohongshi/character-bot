"""涂山红红 · 教一招：.教一招

她教东西不拆开讲。要领两句，警告一句，剩下自己练。
"""
import random

NAME = "教一招"
DESCRIPTION = "让她教你一招：.教一招"
VERSION = "1.0"
CHARACTERS = ("honghong",)

MOVES = [
    ("听风", ["先闭眼，听自己的呼吸。", "再听三步之外的动静。"], "听多了会睡不着。"),
    ("借力", ["别接。让它的力自己过去。", "手只负责改方向。"], "借错了，力会回到你身上。"),
    ("收势", ["出手留三分。", "收得住，才算会。"], "收不住的招式，别学第二遍。"),
    ("看人", ["先看他的手，不看他的嘴。", "再看他的脚。脚不会说谎。"], "看太清，会不想说话。"),
    ("守夜", ["坐高处，背靠实墙。", "眼睛别盯着火，火会骗人。"], "困了就换人。硬撑的守夜，等于没守。"),
]


def register(api):
    async def teach(ctx):
        name, tips, warn = random.choice(MOVES)
        await ctx.reply(
            f"🎴 教一招 · {name}\n"
            f"要领：{tips[0]}\n"
            f"{tips[1]}\n"
            f"警告：{warn}\n"
            "学不学，随你。"
        )

    api.on_command("教一招", teach, help=".教一招 —— 她教你一招（虚构）", cooldown=6)
