"""昔莲 · 时间胶囊：.胶囊 [内容]

埋一句话下去，交给以后。一个会话最多十个，她替你数着日子。
"""
import datetime
import random

from _plugstore import chat_key, clip, db_update, safe_text, state_path

NAME = "时间胶囊"
DESCRIPTION = "埋一句话给以后：.胶囊 [内容]，不带参数查看"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

FILE = state_path("cyrene_capsules")
MAX = 10
TERMS = (30, 90, 180, 365)


def _rows(value):
    return [x for x in (value or []) if isinstance(x, dict)]


def register(api):
    async def capsule(ctx):
        key = chat_key(ctx)
        arg = safe_text(ctx.arg_text, 80)
        today = datetime.date.today()

        if not arg:
            items = _rows(db_update(FILE, key, lambda v: _rows(v), default=[]))
            if not items:
                await ctx.reply(
                    "🕰 你还没埋过什么。\n"
                    "想留一句话给以后，就发「.胶囊 明天要记得抬头看云」。\n"
                    "我替你埋，也替你数日子。"
                )
                return
            lines = []
            for it in items[-3:]:
                try:
                    open_at = datetime.date.fromisoformat(str(it.get("open_at", "")))
                except ValueError:
                    continue
                left = (open_at - today).days
                when = f"还有 {left} 天可以打开" if left > 0 else "已经可以打开了"
                lines.append(f"· 「{clip(it.get('text', ''), 14)}」—— {when}")
            older = len(items) - len(lines)
            tail = f"\n更早的 {older} 个，埋在更深的地方。" if older > 0 else ""
            await ctx.reply(f"🕰 你埋下的 {len(items)} 个：\n" + "\n".join(lines) + tail)
            return

        def bury(value):
            items = _rows(value)
            if len(items) >= MAX:
                return items, 0
            term = random.choice(TERMS)
            items.append({
                "text": arg,
                "at": today.isoformat(),
                "open_at": (today + datetime.timedelta(days=term)).isoformat(),
            })
            return items, term

        items, term = db_update(FILE, key, bury, default=[])
        if not term:
            await ctx.reply(
                f"🕰 这里已经埋了 {len(items)} 个，满了。\n"
                "十个是我的上限——再多，我怕自己记混了谁的是谁的。\n"
                "先让其中一个到期吧，我会替你看着。"
            )
            return
        await ctx.reply(
            f"🕰 埋好了，第 {len(items)} 个。\n"
            f"「{arg}」\n"
            f"{term} 天以后可以打开。这期间我会替你记着，不会忘。"
        )

    api.on_command("胶囊", capsule, help=".胶囊 [内容] —— 埋一句话给以后 / 查看已埋的", cooldown=5)
