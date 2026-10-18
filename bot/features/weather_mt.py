"""涂山红红 · 山雨：.山雨

涂山今日天气。按日期定，同一天问几次都一样。
"""
import datetime
import hashlib

NAME = "山雨"
DESCRIPTION = "看涂山今天的天气：.山雨"
VERSION = "1.0"
CHARACTERS = ("honghong",)

WEATHER = [
    ("晴", "日头好。山道是干的，走起来快。", "晒得动。崖边的残雪化了一半。"),
    ("山雨", "雨从后山过来，来得急。", "石阶滑。上来的时候扶着点。"),
    ("雾", "雾压在山腰，看不见三步外。", "跟着铃铛走。听不见就站着别动。"),
    ("雪", "雪下了一夜，还没停。", "后山封了。今天不上山。"),
]


def register(api):
    async def weather(ctx):
        day = datetime.date.today()
        digest = hashlib.md5(day.isoformat().encode("utf-8")).hexdigest()
        name, first, second = WEATHER[int(digest[:8], 16) % len(WEATHER)]
        await ctx.reply(
            f"🌦 涂山 · {day.month} 月 {day.day} 日\n"
            f"{name}。\n"
            f"{first}\n"
            f"{second}\n"
            "山门照开。"
        )

    api.on_command("山雨", weather, help=".山雨 —— 涂山今日天气", cooldown=6)
