"""Cron 表达式：``.cron "*/5 * * * *"`` —— 说人话 + 算出接下来几次。

通用插件。纯离线。

支持标准 5 段（分 时 日 月 周），认 ``*`` ``*/n`` ``a-b`` ``a,b`` ``a-b/n``，
也认 ``@daily`` / ``@hourly`` / ``@weekly`` 这类简写。第 5 段里 7 和 0 都表示周日。

「接下来几次」用逐分钟试跑算出来（最多往后找 400 天），这样任何写法都不会算错 ——
比手写「下一次是哪天」的推导靠谱得多。
"""
from __future__ import annotations

import datetime

NAME = "Cron 表达式"
NAME_EN = "Cron explainer"
DESCRIPTION = "把 cron 表达式翻译成人话，并算出接下来几次执行时间。" 
DESCRIPTION_EN = "Explain a cron expression and show the next run times."
VERSION = "1.0"

MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
          "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
          "@hourly": "0 * * * *"}
RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
NAMES = ("分", "时", "日", "月", "周")
DOW_CN = "日一二三四五六"


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _field(spec: str, idx: int) -> set[int]:
    """解析一段字段 → 允许的取值集合（同时校验范围）。"""
    lo, hi = RANGES[idx]
    out: set[int] = set()
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            raise ValueError("有空字段")
        step = 1
        if "/" in part:
            part, _, st = part.partition("/")
            if not st.isdigit() or int(st) == 0:
                raise ValueError(f"步长不对：{st}")
            step = int(st)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_s, _, b_s = part.partition("-")
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(part)
        if idx == 4:                       # 周：7 也当周日
            a, b = (0 if a == 7 else a), (0 if b == 7 else b)
        if a < lo or b > hi or a > b:
            raise ValueError(f"{NAMES[idx]} 超出范围（{lo}~{hi}）：{part}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> tuple[set[int], ...]:
    e = str(expr or "").strip()
    e = MACROS.get(e.lower(), e)
    fields = e.split()
    if len(fields) == 6:                   # 有人把「秒」也写上：丢掉第一段
        fields = fields[1:]
    if len(fields) != 5:
        raise ValueError("要 5 段：分 时 日 月 周（例：*/5 * * * *）")
    return tuple(_field(f, i) for i, f in enumerate(fields))


def matches(sets, dt: datetime.datetime) -> bool:
    minutes, hours, dom, month, dow = sets
    if dt.minute not in minutes or dt.hour not in hours or dt.month not in month:
        return False
    cron_dow = (dt.weekday() + 1) % 7      # Python 周一=0 → cron 周日=0
    dom_ok = dt.day in dom
    dow_ok = cron_dow in dow
    # 标准语义：日 和 周 都被限定（不是 *）时，任一命中即可
    if dom != set(RANGES[2][0:1]) | set(range(1, 32)) and dow != set(range(0, 7)):
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def next_runs(expr: str, count: int = 3, start: datetime.datetime | None = None) -> list[datetime.datetime]:
    sets = parse(expr)
    cur = (start or datetime.datetime.now()).replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    out = []
    end = cur + datetime.timedelta(days=400)
    while cur < end and len(out) < count:
        if matches(sets, cur):
            out.append(cur)
        cur += datetime.timedelta(minutes=1)
    return out


def _step_of(values: set[int], lo: int, hi: int) -> int:
    """若集合正好是 lo 开始、等步长的等差数列，返回步长；否则 0。"""
    if not values:
        return 0
    seq = sorted(values)
    if seq[0] != lo:
        return 0
    gaps = {b - a for a, b in zip(seq, seq[1:])}
    if len(gaps) != 1:
        return 0
    step = gaps.pop()
    return step if seq[-1] + step > hi else 0


def describe(expr: str) -> str:
    """一句人话：频率 + 时刻。"""
    sets = parse(expr)
    minutes, hours, dom, month, dow = sets
    every_min = minutes == set(range(60))
    every_hour = hours == set(range(24))
    if every_min and every_hour:
        return "每分钟一次"
    if every_hour:
        step = _step_of(minutes, 0, 59)
        if step:
            return f"每 {step} 分钟一次"
        if len(minutes) == 1:
            return f"每小时的第 {next(iter(minutes))} 分（等于每小时一次）"
        return f"每小时里 {len(minutes)} 次"
    if len(minutes) == 1:
        mm = next(iter(minutes))
        hh = sorted(hours)
        step_h = _step_of(hours, 0, 23)
        if step_h:
            return f"每 {step_h} 小时的 {mm:02d} 分"
        if len(hh) <= 6:
            return "每天的 " + "、".join(f"{h:02d}:{mm:02d}" for h in hh) + " 各一次"
        return f"{len(hh)} 个整点的第 {mm} 分"
    if len(minutes) > 1 and len(hours) <= 3:
        return f"每小时 {len(minutes)} 次，只在 {len(hours)} 个钟点内"
    return f"每小时里 {len(minutes)} 次"



def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip().strip('"').strip("'")
        if not raw:
            await ctx.reply("用法：.cron \"*/5 * * * *\"\n"
                            "也可用 @daily / @hourly / @weekly 这些简写")
            return
        try:
            desc = describe(raw)
            runs = next_runs(raw, 3)
        except (ValueError, TypeError) as e:
            await ctx.reply(f"这个表达式有问题：{e}")
            return
        if not runs:
            await ctx.reply(f"{desc}\n但未来 400 天里一次都不会触发（日期组合永远撞不上）")
            return
        rows = "\n".join(f"  {r:%Y-%m-%d %H:%M}（周{DOW_CN[(r.weekday() + 1) % 7]}）"
                         for r in runs)
        await ctx.reply(f"⏰ {raw}\n{desc}\n接下来：\n{rows}")

    api.on_command("cron", cmd, aliases=("定时表达式",), cooldown=3,
                   help=".cron \"*/5 * * * *\" —— 翻译并算出下一次")
