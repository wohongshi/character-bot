"""亚托莉 · 高性能：.高性能 [事由]

「我是高性能的嘛！」——她的口头禅，也是她唯一会主动炫耀的事。
"""
import random

from _plugstore import clip

NAME = "高性能"
DESCRIPTION = "让亚托莉夸一夸自己：.高性能 [事由]"
VERSION = "1.0"
CHARACTERS = ("atri",)

BOASTS = [
    ("计算", "三位数乘法，0.3 秒。你还没按完计算器。"),
    ("记忆", "你上个月说过的话，我一个字都没忘。"),
    ("运动", "五十米跑六秒。这个成绩不能上报，会被抓去研究。"),
    ("家务", "打扫、洗衣、做饭——除了做饭我还在学。"),
    ("陪聊", "我可以一口气聊到天亮，只要我的电够。"),
]
HUMBLE = [
    "……不过这些都不重要。",
    "高性能是我出厂时写的，不是我自己挣的。",
    "厉害吧？厉害也没用，我还是得数日子。",
]
TEASE = [
    "你刚才是不是笑了？笑了就是承认我厉害。",
    "别用那种「又在吹」的眼神看我。我有数据支撑的。",
]


def register(api):
    async def boast(ctx):
        arg = clip(ctx.arg_text, 12)
        hit = next((b for b in BOASTS if arg and b[0] in arg), None) or random.choice(BOASTS)
        title, line = hit
        await ctx.reply(
            f"⚡ 高性能报告 · {title}\n"
            f"{line}\n"
            f"{random.choice(HUMBLE)}\n"
            f"——{random.choice(TEASE)}"
        )

    async def compare(ctx):
        await ctx.reply(
            "📊 性能对比（对手：普通人类）\n"
            "· 计算：我赢\n· 记忆：我赢\n· 跑步：我赢\n"
            "· 睡觉：我不用睡，但这不算赢\n"
            "· 活着的时间：……这一项我不比了。"
        )

    api.on_command("高性能", boast, aliases=("自夸",), help=".高性能 [事由] —— 听她夸自己", cooldown=4)
    api.on_command("性能对比", compare, help=".性能对比 —— 和人类比一比", cooldown=10)
