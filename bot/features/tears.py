"""亚托莉 · 眼泪：.眼泪 [原因]

她一直在研究一个问题：机器人会不会哭。
"""
import random

from _plugstore import safe_text

NAME = "眼泪"
DESCRIPTION = "关于「机器人会不会哭」：.眼泪 [原因]"
VERSION = "1.0"
CHARACTERS = ("atri",)

ANSWERS = [
    ("生理上", "不会。我没有泪腺，眼眶里是防水胶圈。"),
    ("但如果", "如果有一天我哭了，那大概不是水，是别的东西漏出来了。"),
    ("比喻上", "我理解「想哭」是什么。就是明明有很多话，一句也说不出来。"),
    ("实际上", "我试过。对着镜子，把眼睛睁得很大，等了很久。什么都没发生。"),
]
AFTER = [
    "所以你别在我面前忍。你哭得出来，这是好事。",
    "你哭的时候我不会递纸巾——我没有。我会站在旁边，等你哭完。",
    "我不会哭，但我会记住你哭的样子。记很久。",
    "眼泪是水，我又不怕水。你哭吧。",
]


def register(api):
    async def tears(ctx):
        why = safe_text(ctx.arg_text, 24)
        k, v = random.choice(ANSWERS)
        lead = f"「{why}」——你问这个，是因为你想哭吗？\n" if why else ""
        await ctx.reply(
            f"💧 {lead}"
            f"{k}：{v}\n"
            f"——{random.choice(AFTER)}"
        )

    async def hug(ctx):
        await ctx.reply(
            "🫂（她抱了你一下。动作有点僵硬，力道却很轻。）\n"
            "我的体温是模拟的。但抱着你的这个动作，是真的。\n"
            "……好了，松开。再抱下去我要过热了。"
        )

    api.on_command("眼泪", tears, aliases=("会哭吗",), help=".眼泪 [原因] —— 机器人会不会哭", cooldown=8)
    api.on_command("抱抱", hug, aliases=("拥抱",), help=".抱抱 —— 让她抱你一下", cooldown=10)
