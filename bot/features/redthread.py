"""涂山红红 · 红线：.红线 [名字]

牵一段缘。线头在哪、什么颜色、缠成什么样，最后给一句判词。
"""
import random

from _plugstore import safe_text

NAME = "红线"
DESCRIPTION = "替人看一眼红线：.红线 [名字]"
VERSION = "1.0"
CHARACTERS = ("honghong",)

HEADS = [
    "左手小指",
    "右手腕内侧",
    "无名指根",
    "心口衣襟下",
    "左脚脚踝",
    "后颈",
    "右手小指第二节",
]
COLORS = [
    ("暗红", "旧血那种红"),
    ("朱砂", "点上去就洗不掉"),
    ("月白", "淡得像没有"),
    ("墨黑", "灯下才看得出红"),
    ("浅金", "断口那一段是亮的"),
    ("灰蓝", "快褪完了"),
]
KNOTS = [
    "绕了三圈，打的是死结。",
    "缠得松，一阵风就能扯开。",
    "中间有一道结，是后来接上的。",
    "顺着骨节长进去，扯不动。",
    "另一头不在这里。",
    "缠在旧伤上。那地方本来该疼。",
    "两股并成一股。分不出哪根是你的。",
]
VERDICTS = [
    "断过的线，接上会有一道结。别嫌它丑。",
    "缠得紧，不代表不会松。",
    "这根不归我管。它自己会动。",
    "线的另一头有人。别拽。",
    "……这根我见过。后来断了。",
    "还早。急不来。",
    "一头在你手里。另一头，你自己清楚。",
    "线是好线。人得配得上。",
]


def register(api):
    async def red(ctx):
        who = safe_text(ctx.arg_text, 12)
        head = random.choice(HEADS)
        color, gloss = random.choice(COLORS)
        knot = random.choice(KNOTS)
        verdict = random.choice(VERDICTS)
        lead = f"「{who}」的。" if who else "你自己的。"
        await ctx.reply(
            f"{lead}\n"
            f"线头：{head}。\n"
            f"线的颜色：{color}。{gloss}。\n"
            f"缠在哪：{knot}\n"
            f"判词：{verdict}"
        )

    api.on_command("红线", red, help=".红线 [名字] —— 看一眼那个人的红线", cooldown=5)
