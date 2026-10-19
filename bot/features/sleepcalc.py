"""睡眠计算：``.睡眠 7:00`` / ``.睡眠 现在``。

通用插件。纯离线。

按 90 分钟的睡眠周期倒推 —— 这不是玄学，是「在浅睡阶段醒来更清醒」这个
说法的实用近似。给的是**入睡时间**（已经预留了 15 分钟入睡缓冲），
不是「躺下时间」，这点在回复里写明，免得有人按错。
"""
from __future__ import annotations

import datetime
import re

NAME = "睡眠计算"
NAME_EN = "Sleep calculator"
DESCRIPTION = "按 90 分钟睡眠周期，算出该几点睡或几点起。"
DESCRIPTION_EN = "Work out bedtimes or wake times from 90-minute sleep cycles."
VERSION = "1.0"

CYCLE_MIN = 90
FALL_ASLEEP_MIN = 15
CYCLES = (6, 5, 4)          # 从多到少列：先说够睡的


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def parse_clock(s: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", str(s or "").strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    return hh, mm


def bedtimes(wake: tuple[int, int], now: datetime.datetime | None = None) -> list[tuple[str, int]]:
    """给定起床时间，倒推出睡时间列表 [(HH:MM, 周期数)]。"""
    now = now or datetime.datetime.now()
    wake_dt = now.replace(hour=wake[0], minute=wake[1], second=0, microsecond=0)
    if wake_dt <= now:
        wake_dt += datetime.timedelta(days=1)
    out = []
    for c in CYCLES:
        t = wake_dt - datetime.timedelta(minutes=CYCLE_MIN * c + FALL_ASLEEP_MIN)
        out.append((t.strftime("%H:%M"), c))
    return out


def wakes(bed: tuple[int, int], now: datetime.datetime | None = None) -> list[tuple[str, int]]:
    """给定入睡时间，推算起床时间列表。"""
    now = now or datetime.datetime.now()
    bed_dt = now.replace(hour=bed[0], minute=bed[1], second=0, microsecond=0)
    if bed_dt <= now:
        bed_dt += datetime.timedelta(days=1)
    out = []
    for c in CYCLES:
        t = bed_dt + datetime.timedelta(minutes=CYCLE_MIN * c + FALL_ASLEEP_MIN)
        out.append((t.strftime("%H:%M"), c))
    return out


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            await ctx.reply("用法：\n"
                            ".睡眠 7:00 —— 想 7 点起，几点该睡\n"
                            ".睡眠 现在 —— 现在就睡，几点起合适\n"
                            ".睡眠 睡 23:30 —— 23:30 睡，几点起")
            return
        now = datetime.datetime.now()
        if raw in ("现在", "now", "立即"):
            times = wakes((now.hour, now.minute), now)
            lines = ["🛏 现在睡，这些时间醒（含 15 分钟入睡）"]
            lines += [f"  {t}（{c} 个周期，{c * 1.5:.1f} 小时）" for t, c in times]
            await ctx.reply("\n".join(lines))
            return
        if raw.startswith(("睡", "入睡")):
            raw = raw[1:].strip() if raw[0] == "睡" else raw[2:].strip()
            hhmm = parse_clock(raw)
            if not hhmm:
                await ctx.reply("时间写成 23:30 这样")
                return
            times = wakes(hhmm, now)
            lines = [f"🛏 {raw} 入睡，这些时间醒"]
            lines += [f"  {t}（{c} 个周期）" for t, c in times]
            await ctx.reply("\n".join(lines))
            return
        hhmm = parse_clock(raw)
        if not hhmm:
            await ctx.reply("看不懂这个时间～写成 7:00 这样，或者用「.睡眠 现在」")
            return
        times = bedtimes(hhmm, now)
        lines = [f"⏰ 想 {raw} 起，这些时间该**躺下**（已含 15 分钟入睡）"]
        lines += [f"  {t} 睡（{c} 个周期，{c * 1.5:.1f} 小时）" for t, c in times]
        lines.append("提示：睡前 1 小时少看屏幕，比多睡半小时更管用。")
        await ctx.reply("\n".join(lines).replace("**", ""))

    api.on_command("睡眠", cmd, aliases=("sleep",), cooldown=3,
                   help=".睡眠 7:00 —— 倒推该几点睡（按 90 分钟周期）")
