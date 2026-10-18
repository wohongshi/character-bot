"""胡桃 · 生死簿：.生死簿 [名字]

按名字算一份命格与阳寿。同名同结果——簿子不认第二遍，也不改口。
"""
import hashlib

from _plugstore import safe_text

NAME = "生死簿"
DESCRIPTION = "翻一页簿子，看命格与阳寿：.生死簿 [名字]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

FATES = [
    "火里栽莲",
    "早发晚成",
    "劳碌命",
    "清贵命",
    "福薄缘厚",
    "一生替人撑伞",
    "半世糊涂半世明",
    "走得慢但走得远",
]
NOTES = [
    "名字写得歪，是替自己写的。",
    "前头欠的那顿饭，有人一直记着。",
    "走到哪儿都有人等，只是你总走太快。",
    "账算得清，情算不清，偏要两样都算。",
    "一辈子怕麻烦别人，最后麻烦了所有人。",
    "嘴上说不在意，心里一笔一笔都记着。",
    "眼泪少，心事多，睡得晚。",
    "下笔太狠，落款太轻。",
]
_CN = "零一二三四五六七八九"


def _seed(name):
    """内置 hash() 带进程盐，重启就变；用 sha256 才能同名同结果。"""
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)


def _cn(n):
    if n < 10:
        return _CN[n]
    if n < 20:
        return "十" + (_CN[n % 10] if n % 10 else "")
    return _CN[n // 10] + "十" + (_CN[n % 10] if n % 10 else "")


def register(api):
    async def ledger(ctx):
        who = safe_text(ctx.arg_text, 12) or "无名氏"
        seed = _seed(who)
        fate = FATES[seed % len(FATES)]
        life = 58 + (seed // 8) % 36
        note = NOTES[(seed // 97) % len(NOTES)]
        opening = ("本堂主翻了三页，才在夹缝里找着你。"
                   if (ctx.arg_text or "").strip() else "簿子上没名字的，都记在这一页。")
        await ctx.reply(
            f"📖 生死簿 · 「{who}」\n"
            f"{opening}\n"
            f"命格：{fate}\n"
            f"阳寿：{_cn(life)}\n"
            f"备注：{note}\n"
            "——簿子上是墨，日子是自己过的。"
        )

    api.on_command("生死簿", ledger, help=".生死簿 [名字] —— 翻一页簿子", cooldown=5)
