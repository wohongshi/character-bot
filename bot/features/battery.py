"""亚托莉 · 电量：.电量

她会认真报告电量，然后假装不在意「快没电了」这件事。
"""
import time

from _plugstore import db_update, state_path

NAME = "电量"
DESCRIPTION = "看看亚托莉还剩多少电：.电量"
VERSION = "1.0"
CHARACTERS = ("atri",)

FILE = state_path("atri_battery")
DRAIN_PER_HOUR = 2.2          # 纯装饰：让她看起来真的在耗电


def register(api):
    async def battery(ctx):
        now = time.time()

        def _tick(cur):
            d = cur if isinstance(cur, dict) else {}
            try:
                last = float(d.get("ts") or now)
            except (TypeError, ValueError):
                last = now
            try:
                pct = float(d.get("pct", 100.0))
            except (TypeError, ValueError):
                pct = 100.0
            elapsed = min(max(0.0, now - last), 30 * 86400)   # 时钟回拨/未来时间都不算
            pct = pct - elapsed / 3600.0 * DRAIN_PER_HOUR
            pct = min(100.0, max(3.0, pct))                   # 上下都要夹：>100% 会把电量条撑爆
            return {"ts": now, "pct": round(pct, 1)}

        d = db_update(FILE, "state", _tick, default={})
        pct = float(d.get("pct", 100.0))

        if pct > 70:
            mood = "电量充足，可以陪你很久。"
        elif pct > 35:
            mood = "还够用。别担心。"
        elif pct > 15:
            mood = "有点低了……我说话会慢一点，省电。"
        else:
            mood = "快没电了。你要是还有话，现在说。"
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        await ctx.reply(
            f"🔋 亚托莉 · 电量 {pct:.0f}%\n"
            f"[{bar}]\n"
            f"{mood}\n"
            "——（她不说「充电」这两个字。你最好也别问。）"
        )

    async def charge(ctx):
        db_update(FILE, "state", lambda _c: {"ts": time.time(), "pct": 100.0}, default={})
        await ctx.reply("🔌 充好了。100%。\n……谢谢。这句是真心的，不是程序写的。")

    api.on_command("电量", battery, aliases=("battery",), help=".电量 —— 她还剩多少", cooldown=4)
    api.on_command("充电", charge, aliases=("充满",), help=".充电 —— 给她充满", cooldown=30)
