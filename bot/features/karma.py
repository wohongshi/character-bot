"""涂山红红 · 姻缘簿：.姻缘 [名字]、.姻缘

簿子认名字，不认人。同一个名字查几次，都是同一行字。
"""
import hashlib

from _plugstore import chat_key, db_get, db_update, safe_text, state_path

NAME = "姻缘簿"
DESCRIPTION = "翻一页姻缘簿：.姻缘 [名字]，不带名字看本会话查过谁"
VERSION = "1.0"
CHARACTERS = ("honghong",)

FILE = state_path("honghong_karma")
MAX = 5
STATUS = ("未起", "将断", "已续", "无缘")
NOTES = {
    "未起": [
        "线还没牵上。急也没用。",
        "两个人还没走到同一条路上。",
        "簿子上是空的。空着也是一种写法。",
    ],
    "将断": [
        "再拽一次，就断了。",
        "有一头已经在松。",
        "中间隔着东西。不是人，是话没说清。",
    ],
    "已续": [
        "续过一次。这次别再弄丢。",
        "结打上了。剩下的靠人。",
        "线是新的。人还是旧的。",
    ],
    "无缘": [
        "不是错过。是本来就没有。",
        "名字并排放着，线不挨着。",
        "这一页到此为止。翻过去。",
    ],
}


def _status(name: str) -> str:
    """名字定状态：同一名字跨进程也是同一结果（hash 会加盐，不能用）。"""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return STATUS[int(digest[:8], 16) % len(STATUS)]


def register(api):
    async def look(ctx):
        who = safe_text(ctx.arg_text, 12)
        key = chat_key(ctx)
        if not who:
            rows = [x for x in (db_get(FILE, key, []) or []) if isinstance(x, dict)]
            if not rows:
                await ctx.reply(
                    "姻缘簿。\n"
                    "本会话还没查过谁。\n"
                    "报个名字。我给你翻一页。\n"
                    "名字要报全。同名的人多。\n"
                    "翻过的人，我会记着。"
                )
                return
            items = [f"{i + 1}. {r.get('n', '')} · {r.get('s', '')}"
                     for i, r in enumerate(rows)]
            chunks = ["｜".join(items[i:i + 3]) for i in range(0, len(items), 3)]
            await ctx.reply(
                f"姻缘簿 · 本会话查过 {len(rows)} 条。\n"
                + "\n".join(chunks)
                + "\n想再查，报名字。"
            )
            return

        status = _status(who)
        note = NOTES[status][int(hashlib.sha1(who.encode("utf-8")).hexdigest()[8:12], 16) % 3]

        def push(cur):
            rows = [x for x in (cur or []) if isinstance(x, dict) and x.get("n") != who]
            rows.append({"n": who, "s": status})
            return rows[-MAX:]

        db_update(FILE, key, push, [])
        await ctx.reply(
            f"「{who}」\n"
            f"状态：{status}。\n"
            f"{note}\n"
            "簿子只记线的走向，不记人心里想什么。\n"
            "簿子上这么写。"
        )

    api.on_command("姻缘", look, help=".姻缘 [名字] —— 查一页簿子", cooldown=5)
