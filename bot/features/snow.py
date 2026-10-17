"""昔莲 · 雪：.雪

哀丽秘榭的雪下得很慢，慢到能看清每一片。
"""
import random

from _plugstore import safe_text

NAME = "雪"
DESCRIPTION = "看一眼哀丽秘榭的雪：.雪"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

SCENES = [
    ("雪落在钟楼上", "钟没有响", "整座城安静得像在等一个人开口"),
    ("雪落在河面上", "河水还在流", "雪一碰到水就没了，像没来过"),
    ("雪落在旧台阶上", "一级一级被填平", "走上去的人，脚印很快也不见了"),
    ("雪停了一会儿", "云散开一条缝", "光落下来，照亮了半座城，另外半座还在暗着"),
]
LINES = [
    "这里的雪不冷。冷的是雪化了以后。",
    "下雪的时候，所有的声音都会变轻。包括告别。",
    "我数过雪。数到一半就忘了数字，只记得很白。",
    "如果你那边也下雪，就当我们看的是同一场。",
]


def register(api):
    async def snow(ctx):
        arg = safe_text(ctx.arg_text, 16)
        where, what, mood = random.choice(SCENES)
        lead = f"「{arg}」那边也在下雪吗？\n" if arg else ""
        await ctx.reply(
            f"❄ {lead}"
            f"{where}，{what}。\n"
            f"{mood}。\n"
            f"——{random.choice(LINES)}"
        )

    api.on_command("雪", snow, aliases=("下雪",), help=".雪 [地点] —— 看一眼这边的雪", cooldown=6)
