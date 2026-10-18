"""涂山红红 · 妖盟令：.妖盟令

妖盟的令从她嘴里出来就是定局。一句正文，一句「照办。」
"""
import random

from _plugstore import chat_key, db_update, state_path

NAME = "妖盟令"
DESCRIPTION = "领一道妖盟令：.妖盟令"
VERSION = "1.0"
CHARACTERS = ("honghong",)

FILE = state_path("honghong_order")

ORDERS = [
    ("宵禁", "日落之后，涂山境内不许走动。有事的写下来，贴在门上。"),
    ("封山", "后山今夜封了。谁翻墙，自己把腿接回去。"),
    ("查黑狐", "进出涂山的，都要验一遍影子。影子不对的，留下。"),
    ("护人", "山下那几户人家，划进涂山的地界。动他们的，先来找我。"),
    ("收租", "这个月的租，照旧。拿不出的说一声，不催第二次。"),
    ("清点红线", "红线房的红绳，一根一根数。少一根，全山不许睡。"),
    ("巡山", "夜里三班，铃响换人。误了班的，自己去领罚。"),
    ("修桥", "东边那座桥断了。明天日出前修好，人要过得去。"),
]


def register(api):
    async def order(ctx):
        key = "n:" + chat_key(ctx)
        n = db_update(FILE, key, lambda v: int(v or 0) + 1, default=0)
        title, body = random.choice(ORDERS)
        await ctx.reply(
            f"📜 妖盟令 · 第{n}道 · {title}\n"
            "都听着。我只说一遍。\n"
            f"{body}\n"
            "照办。别让我再说第二遍。"
        )

    api.on_command("妖盟令", order, help=".妖盟令 —— 领一道妖盟令", cooldown=6)
