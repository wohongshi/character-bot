"""涂山红红 · 狐火：.狐火

六种颜色，六种用途。火是真的，烧起来也是真的。
"""

NAME = "狐火"
DESCRIPTION = "看一簇狐火：.狐火"
VERSION = "1.0"
CHARACTERS = ("honghong",)

FIRES = [
    ("青", "照路"),
    ("白", "驱邪"),
    ("金", "暖手"),
    ("赤", "示警"),
    ("幽蓝", "引路"),
    ("暗红", "哄睡"),
]


def register(api):
    async def fire(ctx):
        lines = ["狐火六色。"]
        lines.append("。".join(f"{c}{u}" for c, u in FIRES[:3]) + "。")
        lines.append("。".join(f"{c}{u}" for c, u in FIRES[3:]) + "。")
        lines.append("别乱放火。烧了山，我赔不起，你更赔不起。")
        await ctx.reply("\n".join(lines))

    api.on_command("狐火", fire, help=".狐火 —— 看六色狐火各做什么用", cooldown=5)
