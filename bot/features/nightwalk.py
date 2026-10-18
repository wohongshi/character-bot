"""胡桃 · 夜游：.夜游

按本机钟点分叉：凌晨真带你出门，白天劝你回去睡，入夜准备出发。
"""
import random
from datetime import datetime

NAME = "夜游"
DESCRIPTION = "看当下几点，决定带不带你出门：.夜游"
VERSION = "1.0"
CHARACTERS = ("hutao",)

SCENES = [
    "巷口的灯笼还亮着，风一吹，光就往墙上淌。",
    "更夫敲过三下，尾音掉进水里，没人去捞。",
    "野猫蹲在墙头，眼睛比灯还亮，看你一眼就走。",
    "石板路还留着白天的热气，踩上去像踩着别人的梦。",
    "河面上起了薄雾，船绳自己晃，船却不动。",
    "卖夜宵的摊子收了，只剩一盏灯替老板守着。",
    "拐角有一小堆纸灰，伸手一探，还是温的。",
    "谁家窗里还亮着灯，影子在墙上来回走，走得比人慢。",
]


def register(api):
    async def walk(ctx):
        hour = datetime.now().hour

        if 0 <= hour <= 4:
            picks = random.sample(SCENES[:3], 2) + [random.choice(SCENES[3:])]
            random.shuffle(picks)
            await ctx.reply(
                "🌙 夜游 · 这个点，本堂主真带你出门\n"
                + "\n".join(f"· {s}" for s in picks)
                + "\n客官跟紧点，别踩自己的影子——踩了，它今晚就不肯回去。"
            )
        elif 18 <= hour <= 23:
            await ctx.reply(
                "🌙 夜游 · 准备出发\n"
                "灯刚亮，人还没散，正是出门的好时候。\n"
                "先系紧鞋带，带上那盏旧灯笼；本堂主要走的那条巷子没有路灯。\n"
                "客官要是怕，就抓着本堂主的袖子——只准抓袖子。"
            )
        else:
            await ctx.reply(
                "☀️ 现在出门叫赶集，不叫夜游。\n"
                "日头底下，灯笼是摆设，更夫在睡觉，野猫都嫌热。\n"
                "夜游得等灯亮、人散、风凉下来——客官先回去睡一觉，天黑再来找本堂主。"
            )

    api.on_command("夜游", walk, help=".夜游 —— 看时辰，决定带不带你出门", cooldown=5)
