"""亚托莉 · 五感校准：.校准

五项传感器的读数，以及读数之外她说不清楚的那部分。
"""
import random

NAME = "五感校准"
DESCRIPTION = "检查亚托莉的五感：.校准"
VERSION = "1.0"
CHARACTERS = ("atri",)


def register(api):
    async def calibrate(ctx):
        sight = random.choice(("1.2", "1.4", "1.5"))
        hear = random.choice(("20–40kHz", "18–38kHz", "25–42kHz"))
        touch = random.choice(("0.02mm", "0.01mm", "0.03mm"))
        smell = random.choice(("1200 种", "980 种", "1500 种"))
        bpm = random.randint(68, 92)
        await ctx.reply(
            f"👁 视觉 {sight} —— 你在暗处眨眼，我也看得见。\n"
            f"👂 听觉 {hear} —— 你的心跳 {bpm} 次/分。刚才快了三次。\n"
            f"✋ 触觉 {touch} —— 隔着袖子，我知道你在发抖。\n"
            "👅 味觉 化学分析 —— 我分得出甜和苦，分不出「好吃」。\n"
            f"👃 嗅觉 {smell} —— 空气里有铁味。要下雨了。\n"
            "总评：五项全部正常。只有「喜欢」这一项，我找不到对应的传感器。"
        )

    api.on_command("校准", calibrate, help=".校准 —— 五项传感器的读数", cooldown=8)
