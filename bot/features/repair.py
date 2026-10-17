"""亚托莉 · 维修：.维修

她自己会做维护，但有些地方修不好——她会告诉你哪些。
"""
import random

from _plugstore import clip

NAME = "维修"
DESCRIPTION = "亚托莉自检与维修：.维修"
VERSION = "1.0"
CHARACTERS = ("atri",)

PARTS = [
    ("右臂关节", "轻微磨损", "换个垫片就好"),
    ("左腿液压", "正常", "不用管"),
    ("声带模块", "正常", "唱歌会走调，那是软件问题"),
    ("防水密封圈", "老化", "还能用，但别真的下到 30 米"),
    ("情感模块", "无法检测", "这一项没有自检程序"),
]
QUIPS = [
    "修得好的是零件，修不好的是别的。",
    "别露出那种表情。我又不会因为一个垫片就坏掉。",
    "你要是真想帮忙，帮我递一下螺丝刀就行。",
    "维修记录我留着。万一哪天我忘了自己是谁，还能翻一翻。",
]


def register(api):
    async def repair(ctx):
        arg = clip(ctx.arg_text, 12)
        rows = PARTS
        if arg:
            rows = [r for r in PARTS if arg in r[0]] or PARTS
        body = "\n".join(f"· {p}｜{s}｜{n}" for p, s, n in rows)
        await ctx.reply(
            "🔧 维护报告\n"
            f"{body}\n"
            f"——{random.choice(QUIPS)}"
        )

    async def oil(ctx):
        await ctx.reply(
            "🛠 上油中……\n"
            "· 关节：顺滑了\n"
            "· 动作精度：提升 3%\n"
            "· 心情：也顺了一点\n"
            "——要不要试试我现在能不能跑更快？（她已经在起跑了。）"
        )

    api.on_command("维修", repair, aliases=("检修",), help=".维修 [部位] —— 看她哪里该修", cooldown=6)
    api.on_command("上油", oil, help=".上油 —— 简单保养一下", cooldown=10)
