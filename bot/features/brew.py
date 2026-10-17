"""茜特菈莉 · 小酌：.小酌 [口味/心情]、.干杯

她退休后最大的爱好是酒。这里只写虚构的调酒配方和微醺台词，
不涉及任何酒精购买渠道，也不会怂恿谁去喝。
"""
import random

from _plugstore import clip

NAME = "小酌"
DESCRIPTION = "茜特菈莉的酒单：.小酌 [口味]、.干杯"
VERSION = "1.0"
CHARACTERS = ("citlali",)

FLAVORS = {
    "甜": ("「蜜酿月光」", "甜酒打底，加一勺蜂蜜，最后放一颗会发光的果子", "甜得像个不靠谱的承诺"),
    "苦": ("「旧信」", "苦艾一小杯，配一片烤焦的橘子皮", "苦完会回甘，跟人生一样俗"),
    "酸": ("「酸雨」", "青柠、气泡水、一点点盐", "喝完会想起某个不告而别的人"),
    "辣": ("「火之高兴」", "辣椒泡酒，加冰，别问为什么", "喉咙烧起来的时候，脑子反而清醒"),
    "淡": ("「白开水·高级版」", "冰镇白开水，杯子要好看", "承认吧，你就是想喝水"),
}
MOODS = ["累", "烦", "开心", "失眠", "想人", "没事"]
TOASTS = [
    "敬还活着的，也敬已经不用早起的。",
    "敬所有说「就一杯」结果喝到天亮的人。",
    "敬没回消息的人——他大概睡了，挺好。",
    "敬我自己，三百年了还没退休成功。",
    "敬明天。明天要是也不太行，那就再敬后天。",
]
TIPS = [
    "喝完别开车，别做决定，别给前任发消息。",
    "我这杯是编的，真酒伤身，量力而行。",
    "喝酒解决不了问题，但能让你暂时不想它——这不是一回事。",
]


def _pick(arg: str):
    for k, v in FLAVORS.items():
        if k in arg:
            return v
    return random.choice(list(FLAVORS.values()))


def register(api):
    async def sip(ctx):
        arg = clip(ctx.arg_text, 20)
        name, recipe, taste = _pick(arg or "")
        mood = next((m for m in MOODS if m in arg), "")
        tail = f"\n看你现在是「{mood}」的状态，这杯刚好。" if mood else ""
        await ctx.reply(
            f"🍸 茜特菈莉的酒单 · {name}\n"
            f"配方：{recipe}\n"
            f"口感：{taste}{tail}\n"
            f"——{random.choice(TIPS)}"
        )

    async def cheer(ctx):
        await ctx.reply(f"🥂 {random.choice(TOASTS)}\n（我举杯，你随意。）")

    api.on_command("小酌", sip, aliases=("来一杯", "酒单"), help=".小酌 [甜/苦/酸/辣/淡] —— 点一杯", cooldown=5)
    api.on_command("干杯", cheer, aliases=("敬一杯",), help=".干杯 —— 说句祝酒词", cooldown=6)
