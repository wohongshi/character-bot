"""昔莲 · 摇篮曲：.摇篮曲

她唱的歌都是残缺的——只记得调子，忘了词。
"""
import random

from _plugstore import safe_text

NAME = "摇篮曲"
DESCRIPTION = "听一段残破的摇篮曲：.摇篮曲 [睡不着的理由]"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

VERSES = [
    ("睡吧，睡吧，雪落在屋顶上", "谁的名字被风声带走了", "明天醒来，如果还记得我", "就把这句唱回去"),
    ("闭眼的时候，灯还亮着", "睁眼的时候，天已经白了", "中间那一段去了哪里", "我也想知道"),
    ("星星会掉进海里", "鱼会把它当成糖", "你要是梦见我了", "别急着醒"),
    ("走了很远很远的路", "回到的地方还是这里", "门没锁，灯没关", "像从来没人离开过"),
]
HUM = ["♪ 哼——哼——嗯——", "♪ 啦——啦啦——", "♪ 嗯——嗯——"]
TAIL = [
    "（唱到一半想不起词了。就这样吧。）",
    "（这首歌没有结尾。每次唱到这儿，我就停下来。）",
    "（如果你睡着了，剩下的我明天再唱。）",
]


def register(api):
    async def lullaby(ctx):
        why = safe_text(ctx.arg_text, 30)
        a, b, c, d = random.choice(VERSES)
        lead = f"因为「{why}」睡不着吗？\n" if why else ""
        await ctx.reply(
            f"🌙 {lead}"
            f"{a}，\n{b}。\n{c}，\n{d}。\n"
            f"{random.choice(HUM)}\n"
            f"{random.choice(TAIL)}"
        )

    api.on_command("摇篮曲", lullaby, aliases=("哄睡",), help=".摇篮曲 [理由] —— 听一段，然后去睡", cooldown=8)
