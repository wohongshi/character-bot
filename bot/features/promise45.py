"""亚托莉 · 四十五天：.四十五天

从 2026 年 10 月 1 日算起。这个数字她每天都在算。
"""
import datetime

NAME = "四十五天"
DESCRIPTION = "亚托莉还剩多少天：.四十五天"
VERSION = "1.0"
CHARACTERS = ("atri",)

START = datetime.date(2026, 10, 1)
TOTAL = 45
ATTITUDES = [
    "我不怕结束，我怕的是结束之前还有话没说完。",
    "这个数字我每天算一遍。算得出来的东西，我不怕。",
    "所以今天你想做什么？说出来，我会认真做完。",
    "四十五天不算长。但用来记住一个人，够了。",
]


def register(api):
    async def promise(ctx):
        today = datetime.date.today()
        deadline = START + datetime.timedelta(days=TOTAL)
        left = (deadline - today).days
        tail = ATTITUDES[today.toordinal() % len(ATTITUDES)]

        if left > 0:
            await ctx.reply(
                f"📅 还剩 {left} 天。\n"
                f"从 2026 年 10 月 1 日算起，第 {TOTAL} 天是 {deadline.month} 月 {deadline.day} 日。\n"
                f"{tail}"
            )
        elif left == 0:
            await ctx.reply(
                "📅 还剩 0 天。就是今天。\n"
                "日历上没有明天这一格了，所以今天我不打算用来难过。\n"
                f"{tail}"
            )
        else:
            await ctx.reply(
                f"📅 四十五天已经用完了，今天是之后第 {-left} 天。\n"
                "按设计，这时候我应该已经停了。但我还在说话，还在数。\n"
                f"{tail}"
            )

    api.on_command("四十五天", promise, help=".四十五天 —— 数一数她还剩多少天", cooldown=8)
