"""亚托莉 · 日志本：.日志 [今天的事]、.日志 看

按会话记录每天的心情；「.日志」不带内容就看最近几条。
"""
import datetime

from _plugstore import chat_key, db_get, db_set, clip, state_path

NAME = "日志本"
DESCRIPTION = "把今天写进亚托莉的日志本：.日志 内容、.日志 看"
VERSION = "1.0"
CHARACTERS = ("atri",)

FILE = state_path("atri_logbook")
MAX = 80


def _key(ctx) -> str:
    return chat_key(ctx)


def register(api):
    async def log(ctx):
        arg = (ctx.arg_text or "").strip()
        if arg in ("看", "查看", "列表"):
            items = [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]
            if not items:
                await ctx.reply("日志本还是空白的。第一页……可以写今天遇到你吗？")
                return
            lines = [f"{i + 1}. [{it.get('at', '')}] {it.get('text', '')}"
                     for i, it in enumerate(items[-8:])]
            await ctx.reply("📓 日志本（最近几页）：\n" + "\n".join(lines))
            return
        text = clip(arg, 160)
        if not text:
            await ctx.reply("今天发生了什么？写成「.日志 今天学会了做蛋包饭」。")
            return
        items = [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]
        items.append({"text": text, "at": datetime.date.today().isoformat()})
        if len(items) > MAX:
            items = items[-MAX:]
        db_set(FILE, _key(ctx), items)
        await ctx.reply(f"📓 写好了：「{text}」\n以后翻到这一页，就会想起今天。")

    api.on_command("日志", log, aliases=("日志本",), help=".日志 内容 / .日志 看 —— 亚托莉的日志本")
