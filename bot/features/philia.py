"""昔莲 · 档案：.档案

PhiLia093 的身份卡。她不太愿意被当成一个编号来查，
但你要问，她还是会答。
"""
from _plugstore import clip

NAME = "档案"
DESCRIPTION = "查一份关于昔莲的档案：.档案"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FIELDS = [
    ("代号", "PhiLia093"),
    ("身份", "哀丽秘榭的女儿 · 黄金裔"),
    ("命途", "记忆"),
    ("同行者", "迷迷"),
    ("关联", "白厄 · 三月七 · 长夜月"),
    ("记录", "三千三百五十万五千五百三十六次永劫回归"),
    ("备注", "档案是别人写的。我不太认这个编号。"),
]


def register(api):
    async def file(ctx):
        arg = clip(ctx.arg_text, 12)
        rows = FIELDS
        if arg:
            rows = [r for r in FIELDS if arg in r[0] or arg in r[1]] or FIELDS
        body = "\n".join(f"· {k}：{v}" for k, v in rows)
        await ctx.reply(
            "📜 档案 · PhiLia093\n"
            f"{body}\n"
            "——你可以照着念，也可以不念。我更希望你叫我昔莲。"
        )

    api.on_command("档案", file, aliases=("身份卡",), help=".档案 [字段] —— 查我的档案", cooldown=8)
