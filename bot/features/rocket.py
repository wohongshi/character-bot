"""亚托莉 · 火箭拳：.火箭拳 [目标]

她的招牌动作。打出去的手要自己飞回来，这一点她一直很在意。
"""
import random

from _plugstore import safe_text

NAME = "火箭拳"
DESCRIPTION = "亚托莉的招牌：.火箭拳 [目标]"
VERSION = "1.0"
CHARACTERS = ("atri",)

TARGETS = ["空气", "一个空罐头", "海面上的浮标", "你身后的墙", "想象中的敌人"]
HITS = [
    "命中了。威力刚刚好，没有把东西打坏——我很会控制力道。",
    "打偏了。……这是战术性偏航，不是失误。",
    "正中目标。手飞出去三秒后又自己回来了，接得很稳。",
    "打穿了。对不起，我赔。分期可以吗。",
]
TAILS = [
    "我是高性能的嘛！",
    "这一招练了很久。手飞出去容易，飞回来难。",
    "别站我正前方。真的，别站。",
    "打完之后手会有点热。要不要摸摸看？……算了，会烫到你。",
]


def register(api):
    async def rocket(ctx):
        target = safe_text(ctx.arg_text, 20) or random.choice(TARGETS)
        await ctx.reply(
            f"🚀 火箭拳 · 目标：{target}\n"
            f"{random.choice(HITS)}\n"
            f"——{random.choice(TAILS)}"
        )

    async def charge(ctx):
        await ctx.reply(
            "⚙ 充能中……\n"
            "· 右臂液压：正常\n"
            "· 推进器：正常\n"
            "· 瞄准：有点歪，但问题不大\n"
            "——三、二、一。你最好站远一点。"
        )

    api.on_command("火箭拳", rocket, aliases=("飞拳",), help=".火箭拳 [目标] —— 招牌动作", cooldown=5)
    api.on_command("充能", charge, help=".充能 —— 打之前先蓄个力", cooldown=8)
