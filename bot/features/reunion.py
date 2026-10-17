"""昔莲 · 重逢：.重逢 [名字]

她记得每一次告别，也记得每一次没能说出口的话。
"""
import random

from _plugstore import safe_text

NAME = "重逢"
DESCRIPTION = "和记忆里的某个人重逢：.重逢 [名字]"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

MEETINGS = [
    ("在一条没有尽头的走廊上", "你从对面走来，跟记忆里一模一样", "我张了张嘴，发现自己没准备好台词"),
    ("在一场下不完的雪里", "你先认出了我", "你说：你一点都没变。我说：你也是"),
    ("在一张很长的桌子两端", "桌上摆着很多空杯子", "我们谁都没提那些杯子是谁的"),
    ("在梦的入口", "你背对着我，正在系鞋带", "我站在原地，等了你很久，你一直没系完"),
]
ENDINGS = [
    "后来我醒了。这次没有哭。",
    "我们聊了很久，聊的都是无关紧要的事。这就够了。",
    "临走时你说下次见。我知道「下次」是哪一次。",
    "我没有回头。回头的话，这条路就要重新走一遍。",
]


def register(api):
    async def meet(ctx):
        who = safe_text(ctx.arg_text, 16) or "那个人"
        where, what, talk = random.choice(MEETINGS)
        await ctx.reply(
            f"🕯 重逢 · {who}\n"
            f"{where}，{what}。\n"
            f"{talk}。\n"
            f"{random.choice(ENDINGS)}"
        )

    async def miss(ctx):
        await ctx.reply(
            "🌫 想见一个回不来的人，是很正常的事。\n"
            "你可以在这里写一句想对他说的话，我不评价，也不转达。\n"
            "（写成「.存档 想说的话」，我会替你收着。）"
        )

    api.on_command("重逢", meet, aliases=("梦见",), help=".重逢 [名字] —— 在记忆里见一面", cooldown=8)
    api.on_command("想念", miss, help=".想念 —— 想说却说不出口的话，她会听", cooldown=10)
