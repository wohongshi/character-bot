"""时间戳换算：``.时间戳 1700000000`` ⇄ ``.时间戳 2026-10-19 12:00``。

通用插件。纯离线。

自动判别方向：纯数字（10 位=秒、13 位=毫秒）当时间戳，其余当日期时间。
日期按**本机时区**解释，同时附上 UTC —— 跨时区对时间最常错的就是这一步。
"""
from __future__ import annotations

import datetime
import re
import time

NAME = "时间戳"
NAME_EN = "Timestamp"
DESCRIPTION = "Unix 时间戳和日期时间互转（本地 + UTC）。" 
DESCRIPTION_EN = "Convert between Unix timestamps and datetimes (local + UTC)."
VERSION = "1.0"


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def from_epoch(n: int) -> list[str]:
    if n > 10 ** 11:                     # 13 位毫秒
        n = n / 1000.0
    # 超范围的数字（16 位、或者手滑多打了几位）会让 fromtimestamp 抛
    # OverflowError/OSError，命令就变成「一个字都不回」。这里自己先兜住。
    try:
        local = datetime.datetime.fromtimestamp(n)
        utc = datetime.datetime.fromtimestamp(n, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ValueError("这个时间戳超出可表示范围了（秒 9~11 位、毫秒 12~13 位）") from None
    now = time.time()
    diff = n - now
    lines = [f"🕒 {n:.0f}",
             f"本地：{local:%Y-%m-%d %H:%M:%S}（周{'一二三四五六日'[local.weekday()]}）",
             f"UTC：{utc:%Y-%m-%d %H:%M:%S}"]
    if abs(diff) < 60:
        lines.append("相对：就是现在")
    elif diff > 0:
        lines.append(f"相对：{_human(diff)}后")
    else:
        lines.append(f"相对：{_human(-diff)}前")
    return lines


def _human(sec: float) -> str:
    sec = int(abs(sec))
    if sec < 3600:
        return f"{sec // 60} 分钟"
    if sec < 86400:
        return f"{sec // 3600} 小时"
    if sec < 86400 * 60:
        return f"{sec // 86400} 天"
    return f"{sec // (86400 * 30)} 个月"


def parse_dt(raw: str) -> datetime.datetime:
    s = str(raw or "").strip().replace("/", "-").replace(".", "-")
    s = re.sub(r"[年月]", "-", s).replace("日", "").strip()
    s = " ".join(s.split())
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%m-%d %H:%M", "%m-%d", "%H:%M"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        today = datetime.datetime.now()
        if fmt in ("%m-%d %H:%M", "%m-%d"):
            dt = dt.replace(year=today.year)
            if dt < today - datetime.timedelta(days=1):
                dt = dt.replace(year=today.year + 1)
        elif fmt == "%H:%M":
            dt = today.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        return dt
    raise ValueError(f"看不懂这个时间：{raw}")


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            now = time.time()
            await ctx.reply("\n".join(from_epoch(int(now)) +
                                      [f"毫秒：{int(now * 1000)}"]))
            return
        t = raw.replace(",", "").strip()
        if re.fullmatch(r"-?\d{9,16}", t):
            try:
                await ctx.reply("\n".join(from_epoch(int(t))))
            except ValueError as e:
                await ctx.reply(str(e))
            return
        try:
            dt = parse_dt(t)
        except ValueError as e:
            await ctx.reply(f"{e}\n例：.时间戳 1700000000 / .时间戳 2026-10-19 12:00")
            return
        epoch = int(dt.timestamp())
        await ctx.reply(f"🕒 {dt:%Y-%m-%d %H:%M:%S}（本地）\n"
                        f"时间戳：{epoch}\n"
                        f"毫秒：{epoch * 1000}\n"
                        f"UTC：{datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc):%Y-%m-%d %H:%M:%S}")

    api.on_command("时间戳", cmd, aliases=("ts", "epoch"), cooldown=2,
                   help=".时间戳 [1700000000 | 2026-10-19 12:00]")
