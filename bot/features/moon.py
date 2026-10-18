"""涂山红红 · 月下：.月下

月下独处。她不说的话，都在这些景象里。
"""
import random

NAME = "月下"
DESCRIPTION = "陪她站一会儿：.月下"
VERSION = "1.0"
CHARACTERS = ("honghong",)

MOONS = [
    ("石阶", "月亮很白，照得石阶像水。", "她坐在最高一级，没有回头。", "风把她的袖子吹起来，又放下。"),
    ("屋檐", "屋檐上落了霜。", "她踩着瓦走过去，一点声音都没有。", "底下的人睡着，谁也没醒。"),
    ("树下", "苦情树的影子铺了一地。", "她伸手，没碰到树。", "差一寸。每年都差一寸。"),
    ("水边", "水里也有一个月亮。", "她看了很久，没伸手去捞。", "捞不上来的东西，她从来不捞。"),
    ("灯下", "灯芯短了，火苗只剩一点。", "她拿针挑了一下，火又起来了。", "针尖上沾了点黑。"),
    ("山口", "风从山口过来，带着雪味。", "她站在那里，像在等什么。", "什么也没来。她也没走。"),
]


def register(api):
    async def moon(ctx):
        place, opening, action, blank = random.choice(MOONS)
        await ctx.reply(
            f"🌙 月下 · {place}\n"
            f"{opening}\n"
            f"{action}\n"
            f"{blank}"
        )

    api.on_command("月下", moon, help=".月下 —— 月下独处的一段", cooldown=8)
