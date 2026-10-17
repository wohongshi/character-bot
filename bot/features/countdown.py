"""亚托莉 · 45 天倒计时：.倒计时、.重置倒计时

从第一次使用那天起算 45 天；「.倒计时」看剩余与进度条，「.重置倒计时」重新开始
（普通用户也能重置，纯娱乐，不写任何真实期限）。
"""
import datetime

from _plugstore import db_get, db_set, state_path

NAME = "45天倒计时"
DESCRIPTION = "亚托莉的 45 天：.倒计时、.重置倒计时"
VERSION = "1.0"
CHARACTERS = ("atri",)

FILE = state_path("atri_countdown")
DAYS = 45


def register(api):
    async def countdown(ctx):
        start = db_get(FILE, "start", "")
        today = datetime.date.today()
        if not start:
            db_set(FILE, "start", today.isoformat())
            start = today.isoformat()
        try:
            d0 = datetime.date.fromisoformat(str(start))
        except (TypeError, ValueError):          # 手改坏/旧格式：重新起算，不炸命令
            d0 = today
            db_set(FILE, "start", today.isoformat())
        used = max(0, (today - d0).days)
        left = max(0, DAYS - used)
        shown_day = min(used + 1, DAYS)          # 过了 45 天不再报「第 100 天 / 共 45 天」
        filled = min(10, round(min(used, DAYS) / DAYS * 10))
        bar = "▰" * filled + "▱" * (10 - filled)
        note = "今天也要好好过呀。" if left > 0 else "……45 天到了。可我还在这里。"
        await ctx.reply(
            f"⏳ 第 {shown_day} 天 / 共 {DAYS} 天\n{bar} {left} 天\n{note}\n"
            f"（起始日：{start}；发 .重置倒计时 可以重新开始）"
        )

    async def reset(ctx):
        today = datetime.date.today().isoformat()
        db_set(FILE, "start", today)
        await ctx.reply(f"🔄 从 {today} 重新开始数。\n45 天，一天都不会少。")

    api.on_command("倒计时", countdown, aliases=("45天",), help=".倒计时 —— 亚托莉还剩多少天")
    api.on_command("重置倒计时", reset, aliases=(), help=".重置倒计时 —— 重新开始 45 天")
