"""每日打卡：``.打卡`` / ``.打卡统计``。

通用插件。状态写在 ``state/checkins.json``（按会话隔离）。

连续天数（streak）按**日历天**算，不是按 24 小时：
今天打了、昨天也打了 → 连续 +1；中间断一天 → 从 1 重新开始。
跨时区不处理（本机时区为准），这点在回复里不啰嗦，但代码里写清楚。
"""
from __future__ import annotations

import datetime

from _plugstore import chat_key, db_update, load, state_path

NAME = "打卡"
NAME_EN = "Daily check-in"
DESCRIPTION = "每天打个卡，看连续了几天。" 
DESCRIPTION_EN = "Daily check-in with a streak counter."
VERSION = "1.0"

FILE = state_path("checkins")
KEEP_DAYS = 400           # 只留一年多的记录，文件不会无限长


def today_str(day: datetime.date | None = None) -> str:
    return (day or datetime.date.today()).isoformat()


def streak_of(days: set[str], today: datetime.date | None = None) -> int:
    """从今天往回数连续天数（今天没打就从昨天开始数，方便「昨天打了今天还没打」时显示）。"""
    cur = today or datetime.date.today()
    if today_str(cur) not in days:
        cur = cur - datetime.timedelta(days=1)      # 今天还没打：从昨天开始算
    n = 0
    while today_str(cur) in days:
        n += 1
        cur -= datetime.timedelta(days=1)
    return n


def render_history(days: set[str], today: datetime.date | None = None, span: int = 14) -> str:
    """最近 N 天的方块图：■ 打了 · □ 没打。"""
    cur = today or datetime.date.today()
    cells = []
    for i in range(span - 1, -1, -1):
        d = cur - datetime.timedelta(days=i)
        cells.append("■" if d.isoformat() in days else "□")
    return "".join(cells)


def register(api):
    async def check(ctx):
        key = chat_key(ctx)
        day = today_str()
        box: dict = {}

        def mutate(state):
            state = dict(state or {})
            days = set(state.get("days") or [])
            if day in days:
                box["dup"] = True
            else:
                days.add(day)
            # 只留最近 KEEP_DAYS 天
            state["days"] = sorted(days)[-KEEP_DAYS:]
            return state

        state = db_update(FILE, key, mutate, {})
        days = set(state.get("days") or [])
        n = streak_of(days)
        if box.get("dup"):
            await ctx.reply(f"今天已经打过啦～连续 {n} 天，明天见。\n{render_history(days)}")
            return
        await ctx.reply(f"✅ 打卡成功！连续 {n} 天，累计 {len(days)} 天。\n{render_history(days)}")

    async def stats(ctx):
        state = load(FILE).get(chat_key(ctx)) or {}
        days = set(state.get("days") or []) if isinstance(state, dict) else set()
        if not days:
            await ctx.reply("还没有打卡记录～用 .打卡 开始第一天吧")
            return
        n = streak_of(days)
        best = _best_streak(days)
        await ctx.reply(f"📅 连续 {n} 天 · 累计 {len(days)} 天 · 最长 {best} 天\n"
                        f"{render_history(days, span=14)}\n"
                        f"（■ 打了 · □ 没打，最近 14 天）")

    api.on_command("打卡", check, cooldown=3, help=".打卡 —— 今天也打了")
    api.on_command("打卡统计", stats, cooldown=3, help=".打卡统计 —— 连续/累计/最长")


def _best_streak(days: set[str]) -> int:
    """历史最长连续天数。"""
    if not days:
        return 0
    seq = sorted(datetime.date.fromisoformat(d) for d in days if _ok(d))
    best = cur = 1
    for prev, nxt in zip(seq, seq[1:]):
        cur = cur + 1 if (nxt - prev).days == 1 else 1
        best = max(best, cur)
    return best


def _ok(s: str) -> bool:
    try:
        datetime.date.fromisoformat(str(s))
        return True
    except ValueError:
        return False
