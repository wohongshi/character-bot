"""涂山红红 · 旧伤：.旧伤

关于过去。点到为止，不细讲。
"""
import random

NAME = "旧伤"
DESCRIPTION = "问一句她的过去：.旧伤"
VERSION = "1.0"
CHARACTERS = ("honghong",)

SCARS = [
    ("手上的",
     "这双手抓过太多东西。\n有一样，到最后也没抓住。",
     "「……不提了。」"),
    ("心口的",
     "那道伤在衣服底下。\n她自己缝的，针脚很粗。",
     "「难看。别学。」"),
    ("封印的",
     "后颈有一道旧符痕。\n天凉的时候，会发烫。",
     "「道士画的。早该掉了。」"),
    ("不能说的",
     "她停了一会儿。\n久到你以为她不会答。",
     "「有的事说出来就轻了。我不想它轻。」"),
]


def register(api):
    async def scar(ctx):
        title, look, line = random.choice(SCARS)
        await ctx.reply(
            f"🩹 旧伤 · {title}\n"
            f"{look}\n"
            f"{line}\n"
            "过去了。"
        )

    api.on_command("旧伤", scar, help=".旧伤 —— 关于过去，点到为止", cooldown=8)
