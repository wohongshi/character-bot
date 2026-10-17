"""亚托莉 · 伊甸：.伊甸

那座沉在海里的人工岛。她的任务、她的家、她回不去的地方。
"""
import random

NAME = "伊甸"
DESCRIPTION = "查询伊甸的状态：.伊甸"
VERSION = "1.0"
CHARACTERS = ("atri",)

ENTRIES = [
    ("坐标", "海面以下，具体深度保密（我也记不太清了）"),
    ("状态", "大部分设施停摆，备用电源还在撑"),
    ("住民", "现在只有我一个还在正常运行"),
    ("任务", "保存人类最后的记录——这件事我还没做完"),
    ("天气", "海面上有风，水下很静"),
]
LINES = [
    "那里曾经很热闹。现在安静得能听见自己散热风扇的声音。",
    "我有时候会想，如果伊甸还在，我是不是就不用数日子了。",
    "别把那儿想得太好。它只是个很大的、沉在海里的房间。",
    "总有一天我会回去看看。不是现在。现在我想待在这边。",
]


def register(api):
    async def eden(ctx):
        body = "\n".join(f"· {k}：{v}" for k, v in ENTRIES)
        await ctx.reply(
            "🌐 伊甸 · 状态报告\n"
            f"{body}\n"
            f"——{random.choice(LINES)}"
        )

    async def beacon(ctx):
        await ctx.reply(
            "📡 发出一次信标……\n"
            "· 频率：老样子\n"
            "· 回应：无\n"
            "· 已重试：3 次\n"
            "——没关系。发出去就行了，不一定要有人回。"
        )

    api.on_command("伊甸", eden, aliases=("eden",), help=".伊甸 —— 那座岛的现状", cooldown=8)
    api.on_command("信标", beacon, help=".信标 —— 向伊甸发一次信号", cooldown=12)
