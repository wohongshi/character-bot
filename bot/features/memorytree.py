"""涂山红红 · 苦情树：.苦情树

涂山的根。叶子、许愿、续缘的法宝，都在这棵树上。
"""

NAME = "苦情树"
DESCRIPTION = "讲一讲苦情树：.苦情树"
VERSION = "1.0"
CHARACTERS = ("honghong",)

LINES = [
    "叶子像蒲公英。风一吹，满山都是。",
    "树下许愿。灵不灵，看你自己信不信。",
    "续缘的法宝一分为二。一半拿着，一半留下。",
]


def register(api):
    async def tree(ctx):
        await ctx.reply(
            "苦情树。\n"
            f"{LINES[0]}\n"
            f"{LINES[1]}\n"
            f"{LINES[2]}\n"
            "想续缘，先想清楚。"
        )

    api.on_command("苦情树", tree, help=".苦情树 —— 听一段树的事", cooldown=5)
