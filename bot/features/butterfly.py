"""胡桃 · 蝴蝶签：.蝴蝶签 [问题]

向「那边」讨一句回话。答案模棱两可，态度一本正经。
"""
import random

from _plugstore import clip

NAME = "蝴蝶签"
DESCRIPTION = "烧一张蝴蝶签，问那边一个问题：.蝴蝶签 [问题]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

OMENS = [
    ("上上签", "蝴蝶落在你肩上", "答案在眼前，只是你还没肯看。", "宜：直说　忌：再等等"),
    ("中签", "蝴蝶绕了三圈才走", "会有回音，但要绕一点路。", "宜：耐心　忌：催"),
    ("下签", "蝴蝶停在窗棂上不动", "现在不是问的时候。先吃口热的。", "宜：睡觉　忌：深夜做决定"),
    ("怪签", "蝴蝶飞进了屋里又飞出去", "有人来过，又走了。别追。", "宜：收拾房间　忌：翻旧账"),
    ("空签", "没有蝴蝶", "那边今天忙。改天再问。", "宜：等　忌：硬求一个答案"),
    ("笑签", "蝴蝶停在本堂主鼻尖上", "噗——它说你想太多啦。", "宜：笑一笑　忌：绷着"),
]


def register(api):
    async def butterfly(ctx):
        q = clip(ctx.arg_text, 40) or "今天该做点什么"
        grade, scene, answer, advice = random.choice(OMENS)
        await ctx.reply(
            f"🦋 蝴蝶签 · {grade}\n"
            f"所问：{q}\n"
            f"签象：{scene}\n"
            f"解曰：{answer}\n"
            f"{advice}\n"
            f"——信则有，不信……也有，嘿嘿。"
        )

    api.on_command("蝴蝶签", butterfly, aliases=("蝶签",), help=".蝴蝶签 [问题] —— 问那边一句", cooldown=6)
