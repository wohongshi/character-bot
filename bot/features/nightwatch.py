"""涂山红红 · 守夜：.守夜

夜深了，她还坐在山门。白天不守，白天山自己看着自己。
"""
import datetime
import random

NAME = "守夜"
DESCRIPTION = "看看今晚谁在守山：.守夜"
VERSION = "1.0"
CHARACTERS = ("honghong",)

SCENES = [
    "山门前的石阶。露水下来了。",
    "苦情树的影子拖得很长。",
    "妖馨斋早关了。灶上还温着一壶。",
    "双生峰那边有风。冰窖没事。",
    "山下有灯。一盏一盏灭下去。",
]
WAITS = [
    "……在等一个姓东方的人。别问。",
    "在等一句没说完的话。",
    "在等树上的花再开一次。",
    "在等一个约。说好会回来的那种。",
    "等谁，不说。",
]


def register(api):
    async def watch(ctx):
        hour = datetime.datetime.now().hour
        night = hour >= 22 or hour < 6
        wait = random.choice(WAITS)
        if night:
            await ctx.reply(
                "守夜。\n"
                f"{random.choice(SCENES)}\n"
                f"{wait}\n"
                "睡你的。山有我。天亮之前不会有事。我看着。"
            )
            return
        await ctx.reply(
            "白天不用守。\n"
            "山自己看着自己。\n"
            f"{wait}\n"
            "该做什么做什么去。别在这站着。\n"
            "有事我会喊你。别走远。"
        )

    api.on_command("守夜", watch, help=".守夜 —— 看看今晚的山", cooldown=5)
