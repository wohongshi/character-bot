"""亚托莉 · 潜水：.潜水 [深度]

防水 30 米。再深就要进水了，她自己会提醒你。
"""
import random
import re

from _plugstore import safe_text

NAME = "潜水"
DESCRIPTION = "跟着亚托莉下一次水：.潜水 [米数]"
VERSION = "1.0"
CHARACTERS = ("atri",)

LIMIT = 30
SCENES = [
    ("5 米", "光还很亮，水是浅蓝色的，能看见沙子在动", "有鱼从你耳边游过去，尾巴扫了一下"),
    ("15 米", "蓝色变深了，声音开始变得闷闷的", "她敲了敲自己的胸口，示意你听——里面有心跳一样的声音"),
    ("25 米", "周围安静得只剩自己的呼吸", "她伸手牵住你，手心是温的"),
    ("30 米", "到极限了", "她指了指上面，意思是：该回去了"),
]
WARN = [
    "再往下我就要进水了。进水以后我会说胡话，很难看。",
    "我是机器人，但我也会怕。怕的不是水，是上不去。",
    "在海里的时候，我总算不用想「还剩多少天」这件事。",
]


def register(api):
    async def dive(ctx):
        arg = safe_text(ctx.arg_text, 12)
        depth = 10
        m = re.search(r"\d+", arg)          # 只认 ASCII 数字：`³`.isdigit() 为真但 int() 会炸
        if m:
            depth = max(1, int(m.group(0)))  # 只夹下界：超上限走下面那段「会进水」的回复
        if depth > LIMIT:
            await ctx.reply(
                f"🌊 {depth} 米……不行。\n"
                f"我的防水规格是 {LIMIT} 米。再往下，水会从接缝进去，"
                "然后我就会说一些不该说的话。\n"
                "（她把你往回拉了一点。）"
            )
            return
        label, desc, extra = next((s for s in SCENES if s[0] == f"{depth} 米"),
                                  (f"{depth} 米", "水里很安静", "她一直看着你，确认你还好"))
        await ctx.reply(
            f"🌊 下潜 · {label}\n"
            f"{desc}。\n"
            f"{extra}。\n"
            f"——{random.choice(WARN)}"
        )

    api.on_command("潜水", dive, aliases=("下潜",), help=".潜水 [米数] —— 不超过 30 米", cooldown=6)
