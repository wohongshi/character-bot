"""亚托莉 · 哼歌：.哼歌

她唱歌。调子从第二句开始逃跑，但她唱得很认真。
"""
import random

NAME = "哼歌"
DESCRIPTION = "听亚托莉哼一段（会走调）：.哼歌"
VERSION = "1.0"
CHARACTERS = ("atri",)

LYRICS = [
    "海の……海の色は、蓝——\n（后面那句是日语还是汉语，我忘了）",
    "如果明天不下雨，我就带你去——\n……去哪里来着。",
    "啦啦啦——啦。\n（她停住）后面是「啦」，还是「啦」？",
    "把贝壳放在耳边，能听见很远的地方。\n那个地方，叫什么名字。",
    "四十五天，四十五个早晨。\n数到第三个，我就乱了。",
    "我是高性能的机器人～\n（这里本来应该有一个很高的音）",
    "水下的光啊，请你慢一点暗。\n再慢一点，再慢一点。",
    "夏生先生、夏生先生，你的拐杖——\n不对。这句不是歌。",
]


def register(api):
    async def sing(ctx):
        drift = random.randint(3, 9) * 10
        await ctx.reply(
            "🎤 亚托莉，开始唱。\n"
            f"{random.choice(LYRICS)}\n"
            f"（音准偏差 {drift}%。我记录下来了，但不打算改。）\n"
            "——……调子对吗？"
        )

    api.on_command("哼歌", sing, aliases=("唱首歌",), help=".哼歌 —— 听她哼一段（会走调）", cooldown=6)
