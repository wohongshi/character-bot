"""倒数日：``.倒数 2027-06-07 高考`` / ``.倒数加`` / ``.倒数列表``。

通用插件。状态写在 ``state/countdowns.json``（按会话隔离，重启不丢）。

日期认这些写法：``2027-06-07`` / ``2027/6/7`` / ``6-7``（今年或明年最近的）
/ ``6月7日``。**只按日期算**，不算时辰 —— 「还有 0 天」的意思是「就是今天」。
"""
from __future__ import annotations

import datetime
import re

from _plugstore import chat_key, db_update, load, safe_text, state_path

NAME = "倒数日"
NAME_EN = "Countdown"
DESCRIPTION = "距离某个日子还有几天，可以存成一份清单。"
DESCRIPTION_EN = "Days until a date, with a per-chat saved list."
VERSION = "1.0"

FILE = state_path("countdowns")
MAX_ITEMS = 10


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def parse_date(s: str, today: datetime.date | None = None) -> datetime.date | None:
    """宽松解析日期；``6-7`` 这种没年份的，取「最近的那个」。"""
    today = today or datetime.date.today()
    t = str(s or "").strip().replace("/", "-").replace(".", "-")
    t = re.sub(r"[年月]", "-", t).replace("日", "").strip("-")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", t)
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                d = datetime.date(year, mo, da)
            except ValueError:
                # 2-29 在平年是非法日期：换下一年再试，
                # 以前这里直接 return None（今年不是闰年就永远算不出 2-29）。
                continue
            if d >= today:
                return d
        return None
    return None


def days_until(d: datetime.date, today: datetime.date | None = None) -> int:
    today = today or datetime.date.today()
    return (d - today).days


def describe(d: datetime.date, today: datetime.date | None = None) -> str:
    n = days_until(d, today)
    if n == 0:
        return "就是今天！"
    if n == 1:
        return "明天"
    if n == 2:
        return "后天"
    if n > 0:
        weeks, rest = divmod(n, 7)
        tail = f"（{weeks} 周" + (f" {rest} 天）" if rest else "）") if weeks else ""
        return f"还有 {n} 天{tail}"
    return f"已经过去 {-n} 天了"


def items_of(key: str) -> list[dict]:
    d = load(FILE).get(key) or []
    return d if isinstance(d, list) else []


def register(api):
    async def show(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            await ctx.reply("用法：.倒数 2027-06-07 高考 / .倒数 6-7\n"
                            "存清单：.倒数加 <日期> <名字> · .倒数列表 · .倒数删 <编号>")
            return
        parts = raw.split(None, 1)
        d = parse_date(parts[0])
        if not d:
            await ctx.reply(f"看不懂这个日期：「{safe_text(parts[0], 20)}」～写成 2027-06-07 或 6-7")
            return
        name = safe_text(parts[1], 40) if len(parts) > 1 else ""
        head = f"📅 {d:%Y-%m-%d}" + (f" · {name}" if name else "")
        await ctx.reply(f"{head}\n{describe(d)}")

    async def add(ctx):
        parts = raw_args(ctx).strip().split(None, 1)
        if len(parts) < 2:
            await ctx.reply("用法：.倒数加 2027-06-07 高考")
            return
        d = parse_date(parts[0])
        if not d:
            await ctx.reply(f"看不懂这个日期：「{safe_text(parts[0], 20)}」")
            return
        name = safe_text(parts[1], 40)
        key = chat_key(ctx)
        box: dict = {}

        def mutate(items):
            items = list(items or [])
            if any(it.get("date") == d.isoformat() and it.get("name") == name for it in items):
                box["dup"] = True
                return items
            if len(items) >= MAX_ITEMS:
                box["full"] = True
                return items
            items.append({"date": d.isoformat(), "name": name})
            return items

        db_update(FILE, key, mutate, [])
        if box.get("dup"):
            await ctx.reply("这条已经在清单里啦")
            return
        if box.get("full"):
            await ctx.reply(f"清单最多 {MAX_ITEMS} 条，先 .倒数删 一条吧")
            return
        await ctx.reply(f"✅ 记下了：{d:%Y-%m-%d} {name}（{describe(d)}）")

    async def listing(ctx):
        items = items_of(chat_key(ctx))
        if not items:
            await ctx.reply("清单还是空的～用 .倒数加 2027-06-07 高考 加一条")
            return
        rows = []
        for i, it in enumerate(sorted(items, key=lambda x: str(x.get("date") or "")), 1):
            try:
                d = datetime.date.fromisoformat(str(it.get("date")))
            except ValueError:
                continue
            rows.append(f"{i}. {it.get('name')}　{d:%Y-%m-%d}　{describe(d)}")
        await ctx.reply("📅 倒数清单\n" + "\n".join(rows))

    async def drop(ctx):
        raw = raw_args(ctx).strip()
        if not raw.isdigit():
            await ctx.reply("用法：.倒数删 2（编号看 .倒数列表）")
            return
        idx = int(raw)
        key = chat_key(ctx)
        gone: dict = {}

        def mutate(items):
            items = sorted(list(items or []), key=lambda x: str(x.get("date") or ""))
            if not (1 <= idx <= len(items)):
                return items
            gone.update(items.pop(idx - 1))
            return items

        db_update(FILE, key, mutate, [])
        if gone:
            await ctx.reply(f"删掉了：{gone.get('name')}")
        else:
            await ctx.reply("没有这个编号～用 .倒数列表 看看")

    api.on_command("倒数", show, cooldown=2, help=".倒数 2027-06-07 高考 —— 还有几天")
    api.on_command("倒数加", add, cooldown=2, help=".倒数加 <日期> <名字> —— 存进清单")
    api.on_command("倒数列表", listing, cooldown=2, help=".倒数列表 —— 看清单")
    api.on_command("倒数删", drop, cooldown=2, help=".倒数删 <编号> —— 删一条")

    def tool_days(args: dict, ctx=None) -> str:
        # 必须是普通 def：agent.run 是同步调用的（写成 async def 会返回协程对象，
        # 工具等于没跑，模型收到的是 "<coroutine object …>"）。
        d = parse_date(args.get("date") or "")
        if not d:
            return "日期看不懂，写成 2027-06-07"
        return f"{d:%Y-%m-%d}：{describe(d)}"

    api.add_tool(
        "days_until",
        description="算某个日期距今还有多少天（也能算已经过去多少天）。",
        parameters={"type": "object",
                    "properties": {"date": {"type": "string",
                                            "description": "日期，如 2027-06-07 或 6-7"}},
                    "required": ["date"]},
        handler=tool_days,
    )
