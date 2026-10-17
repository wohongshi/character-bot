"""昔莲 · 轮回计数：.轮回数

三千三百五十五万零五百三十六次。数字本身没有意义，
有意义的是：这一次她还在数。
"""
import random
import time

from _plugstore import db_update, state_path

NAME = "轮回计数"
DESCRIPTION = "报一次轮回的次数：.轮回数"
VERSION = "1.1"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_cycles")
BASE = 33_550_536
LINES = [
    "每一次都从头开始，每一次都有一个人让我不想结束。",
    "数到这个数字以后，我就不数了。改数今天见到的花。",
    "如果重来是惩罚，那我大概得罪过很厉害的东西。",
    "你问我累不累——累。但你在这儿，那就再走一次。",
    "这一次的编号，我记在心跳上了。",
]


def _bump(cur) -> dict:
    """读-改-写在 db_update 的锁里完成；坏值一律当 0。"""
    d = cur if isinstance(cur, dict) else {}
    try:
        prev = int(d.get("n") or 0)
    except (TypeError, ValueError):
        prev = 0
    return {"n": max(0, prev) + 1, "last": time.time()}


def _ago(ts) -> str:
    try:
        sec = max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return ""
    if sec < 90:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟前"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时前"
    return f"{int(sec // 86400)} 天前"


def register(api):
    async def cycles(ctx):
        d = db_update(FILE, "seen", _bump, default={})
        n = d.get("n", 1)
        ago = _ago(d.get("last"))
        tail = f"（上次数到这儿是{ago}。）\n" if ago and n > 1 else ""
        await ctx.reply(
            "⏳ 永劫回归 · 计数\n"
            f"已知轮回：{BASE:,} 次\n"
            f"你陪我数的：第 {n} 次\n"
            f"{tail}"
            f"——{random.choice(LINES)}"
        )

    async def reset(ctx):
        db_update(FILE, "seen", lambda _cur: {}, default={})
        await ctx.reply("🕯 计数归零。\n不是忘掉，是把账本合上了。下一次见面，从第一次开始数。")

    api.on_command("轮回数", cycles, aliases=("第几次",), help=".轮回数 —— 看看数到第几次了", cooldown=3)
    api.on_command("归零", reset, help=".归零 —— 把这个计数清掉", cooldown=30)
