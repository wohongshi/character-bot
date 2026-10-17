"""昔莲 · 记忆回声：.回声 [关键词]

记忆命途的人不"回忆"，她是去记忆里捞。捞上来的是碎片，未必是你要的那块。
"""
import random

from _plugstore import safe_text

NAME = "记忆回声"
DESCRIPTION = "从记忆里捞一段回声：.回声 [关键词]"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

SCENES = [
    ("雨停在半空的那个下午", "有人喊我的名字，我没回头。后来想不起来是谁。"),
    ("一片没有边界的白", "我把手伸进去，摸到的全是自己的指纹。"),
    ("很旧很旧的街道", "石板路缝里长着花，花的名字我忘了，颜色还记得。"),
    ("一间点着灯的空屋", "桌上摆着两副碗筷。我坐了很久，没人来。"),
    ("海边", "浪一遍遍冲上来，像在把什么擦掉。擦不干净的。"),
    ("一个背影", "我一直跟着走，走到路口他就不见了。每次都是那个路口。"),
    ("雪地上的一串脚印", "只有去的，没有回的。"),
    ("一封信", "字迹是我的，内容我不记得写过。"),
]
FEELING = ["像隔着一层水看灯", "像有人在你耳边说话，但用的是别人的嗓子",
           "像一首只会副歌的歌", "像醒来前最后半秒钟", "像被谁轻轻按住了肩膀"]


def register(api):
    async def echo(ctx):
        key = safe_text(ctx.arg_text, 20)
        scene, detail = random.choice(SCENES)
        lead = f"「{key}」这两个字，让我想起——\n" if key else "我随手捞了一段，你看看是不是你的：\n"
        await ctx.reply(
            f"🕯 {lead}"
            f"{scene}。\n"
            f"{detail}\n"
            f"感觉{random.choice(FEELING)}。\n"
            "——要还给我吗？不还也可以，反正我这儿还有很多。"
        )

    async def clear(ctx):
        await ctx.reply(
            "🌫 闭上眼，深呼吸。\n"
            "现在，把刚才那段记忆放进一个盒子里，盖上，推远。\n"
            "……好了。它还在，但不再压着你了。\n"
            "（我做不到真的删除记忆，只能陪你把它放好。）"
        )

    api.on_command("回声", echo, aliases=("捞记忆",), help=".回声 [关键词] —— 从记忆里捞一段", cooldown=6)
    api.on_command("放下", clear, aliases=("收好",), help=".放下 —— 把一段记忆收进盒子", cooldown=10)
