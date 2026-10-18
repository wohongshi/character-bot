"""亚托莉 · 下雨：.下雨 [想说的话]

下雨天她会带伞，然后把伞往你那边偏。她自己的右肩留在外面。
"""
import random

from _plugstore import safe_text

NAME = "下雨"
DESCRIPTION = "和亚托莉一起淋一场雨：.下雨 [想说的话]"
VERSION = "1.0"
CHARACTERS = ("atri",)

SCENE = [
    "雨点打在伞面上，声音很密。我把伞往你那边偏了一点。",
    "伞不大。我算过角度，我这边空出来的部分，正好放你的肩膀。",
    "我把伞举高了一点，这样你不用低头。我的手臂不会酸。",
]
SHARE = [
    "我把伞塞给你，自己退到屋檐下。我防水，你不行。",
    "你往我这边靠一点。再靠一点。伞下面就这么大。",
    "湿的那半边是我的。你别动，动了就白湿了。",
]
ENDS = [
    "……其实我怕一个人淋雨。不是怕水，是怕没有人替我擦。",
    "我不怕下雨。我怕的是雨停了，还是只有我一个人。",
    "所以你别先走。伞我一个人也能打，雨我一个人淋过很多次。",
]


def register(api):
    async def rain(ctx):
        arg = safe_text(ctx.arg_text, 20)
        if arg:
            await ctx.reply(
                f"🌧 你说「{arg}」。下雨天说这句话的人，通常是没带伞。\n"
                f"{random.choice(SHARE)}\n"
                f"{random.choice(ENDS)}"
            )
            return
        await ctx.reply(
            "🌧 下雨了。\n"
            f"{random.choice(SCENE)}\n"
            "我的右肩在外面，它是防水的。\n"
            f"{random.choice(ENDS)}"
        )

    api.on_command("下雨", rain, aliases=("撑伞",),
                   help=".下雨 [想说的话] —— 和她一起走一段雨天", cooldown=6)
