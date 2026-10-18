"""胡桃 · 斗诗：.斗诗 [上句]

八组对句，她接下句，押韵、带一点暗黑幽默。客官给句就接，不给就她先出。
"""
import random

from _plugstore import safe_text

NAME = "斗诗"
DESCRIPTION = "客官出上句，本堂主接下句：.斗诗 [上句]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

COUPLETS = [
    ("台上灯红照夜长", "台下人走茶就凉"),
    ("清明时节雨纷纷", "路上行人半是魂"),
    ("活着总嫌夜太长", "睡下又嫌灯太亮"),
    ("一碗热汤端上手", "黄泉路上不回头"),
    ("世人只道黄泉冷", "我道黄泉也省灯"),
    ("纸钱烧作蝴蝶飞", "那边收件要排队"),
    ("三杯酒来一炷香", "送君送到桥中央"),
    ("夜半敲门莫要开", "门外未必是人来"),
]


def register(api):
    async def duel(ctx):
        line = safe_text(ctx.arg_text, 24)
        if not line:
            first, _ = random.choice(COUPLETS)
            await ctx.reply(
                "🖌 斗诗 · 本堂主先出上句\n"
                f"「{first}」\n"
                "客官接下半句？押不上韵也不打紧，本堂主笑完就忘。\n"
                "想让我接你的，就说「.斗诗 你的上句」。"
            )
            return

        # 客官的句子里有哪个字眼，就挑一联气质相近的；挑不着就随手一联。
        pool = [c for c in COUPLETS if any(ch in line for ch in c[0][:3])]
        _, second = random.choice(pool or COUPLETS)
        await ctx.reply(
            f"🖌 客官出句：「{line}」\n"
            f"本堂主接：「{second}」\n"
            "——押上了吧？嘿嘿，这两句挂门口，门神都能省一对。\n"
            "客官再来一句，本堂主今晚诗兴正旺。"
        )

    api.on_command("斗诗", duel, help=".斗诗 [上句] —— 你出上句，她接下句", cooldown=5)
