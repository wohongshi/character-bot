"""定时任务的时间规则（纯函数，好测）。

和 `.提醒` 的区别：
- 提醒是**对你说一句话**；定时任务是**让她去做一件事**（跑一轮 agent，结果进对话）；
- 提醒是「一次/每天/每周」；这里多一种「每隔 N 分钟」。

规则的写法（都收）：
    每天 8:00 · 每日8:00 · 8:00（= 每天一次）
    每周一 8:00 · 每周日 21:30
    每 30m · 每2h · 每 90 分钟
"""
from __future__ import annotations

import datetime
import re
from typing import Optional

WEEKDAY_CN = "一二三四五六日"
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_DAILY = re.compile(r"^(?:每天|每日|天天)?\s*(\d{1,2})[:：](\d{2})$")
_WEEKLY = re.compile(r"^每周\s*([一二三四五六日天])\s*(\d{1,2})[:：](\d{2})$")
_EVERY = re.compile(r"^每\s*(\d{1,4})\s*(m|分|分钟|h|小时|min|hour|s|秒)?$", re.I)
_UNIT = {"": 60, "m": 1, "分": 1, "分钟": 1, "min": 1, "h": 60, "小时": 60,
         "hour": 60, "s": 1 / 60, "秒": 1 / 60}

MIN_EVERY_MIN = 5          # 再密就是刷屏了
MAX_EVERY_MIN = 7 * 24 * 60


def parse_when(raw: str) -> Optional[dict]:
    """'每周一 8:00' / '每天8:00' / '每2h' / '8:00' → 规则 dict；认不出返回 None。"""
    s = re.sub(r"\s+", " ", str(raw or "").strip())
    if not s:
        return None
    m = _WEEKLY.match(s)
    if m:
        hh, mm = int(m.group(2)), int(m.group(3))
        if hh > 23 or mm > 59:
            return None
        return {"kind": "weekly", "weekday": _WEEKDAY[m.group(1)], "at": f"{hh:02d}:{mm:02d}"}
    m = _EVERY.match(s.replace(" ", ""))
    if m:
        n = int(m.group(1))
        unit = (m.group(2) or "m").lower()
        minutes = max(MIN_EVERY_MIN, min(MAX_EVERY_MIN, int(round(n * _UNIT.get(unit, 1)))))
        return {"kind": "every", "minutes": minutes}
    m = _DAILY.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            return None
        return {"kind": "daily", "at": f"{hh:02d}:{mm:02d}"}
    return None


def _at_hm(spec: dict) -> tuple[int, int]:
    hh, mm = (int(x) for x in str(spec.get("at") or "00:00").split(":"))
    return hh, mm


def next_due(spec: dict, now: Optional[float] = None) -> float:
    """下一次该跑的时间戳；永远严格大于 now。"""
    import time as _t
    ts = now if now is not None else _t.time()
    base = datetime.datetime.fromtimestamp(ts)
    kind = str(spec.get("kind") or "daily")
    if kind == "every":
        minutes = _minutes(spec)
        # 从「下一个整分」起算，别每次调度都因为几秒抖动往后滑
        return (base + datetime.timedelta(minutes=minutes)).replace(
            second=0, microsecond=0).timestamp()
    hh, mm = _at_hm(spec)
    target = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "weekly":
        target += datetime.timedelta(days=(_wd(spec) - target.weekday()) % 7)
    if target <= base:
        target += datetime.timedelta(days=1 if kind == "daily" else 7)
    return target.timestamp()


def _int_field(spec: dict, key: str, default: int = 0) -> int:
    """脏 json 里 `Infinity` / `"abc"` 都不能让调度循环整体中断。

    `int(float("inf"))` 抛的是 `OverflowError`（既不是 TypeError 也不是 ValueError），
    以前 `next_due` 会把它抛给调度线程 → 整轮 tick 挂掉，坏任务也不会被停用。
    """
    try:
        return int(spec.get(key) if spec.get(key) not in (None, "") else default)
    except (TypeError, ValueError, OverflowError):
        return default


def _minutes(spec: dict) -> int:
    """「每 N 分钟」的 N：钳到合法区间。

    只挡「转不成 int」不够：`{"minutes": 1e20}` 能转成 int，但
    `timedelta(minutes=10**20)` 抛 OverflowError（days 超上限），
    一样会穿过所有兜底、把整轮调度带崩。夹住之后显示和调度也一致了
    （以前 describe 里 `0` 会显示成「每 0 天」）。
    """
    return max(MIN_EVERY_MIN, min(MAX_EVERY_MIN, _int_field(spec, "minutes", 60)))


def _wd(spec: dict) -> int:
    """星期几：**0=周一 … 6=周日**（面板/parse_when 的约定）。

    7 也按周日算（很多人这么写）；其它越界值一律钳进来 ——
    以前直接把 int() 丢给 `WEEKDAY_CN[wd]`，手改成 7 就是 IndexError。
    """
    wd = _int_field(spec, "weekday", 0)
    return 6 if wd == 7 else max(0, min(6, wd))


def describe(spec: dict) -> str:
    kind = str(spec.get("kind") or "daily")
    if kind == "every":
        m = _minutes(spec)
        if m % 1440 == 0:
            return f"每 {m // 1440} 天"
        if m % 60 == 0:
            return f"每 {m // 60} 小时"
        return f"每 {m} 分钟"
    at = str(spec.get("at") or "00:00")
    if kind == "weekly":
        return f"每周{WEEKDAY_CN[_wd(spec)]} {at}"
    return f"每天 {at}"


def next_text(spec: dict, now: Optional[float] = None) -> str:
    """「还有 3 小时」这种，面板要显示。"""
    import time as _t
    left = next_due(spec, now) - (_t.time() if now is None else now)
    if left <= 0:
        return "马上"
    if left < 60:
        return f"{max(1, int(left))} 秒后"
    if left < 3600:
        return f"{int(left // 60)} 分钟后"
    if left < 86400:
        return f"{int(left // 3600)} 小时后"
    return f"{int(left // 86400)} 天后"
