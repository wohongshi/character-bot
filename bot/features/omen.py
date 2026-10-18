"""茜特菈莉 · 兆头：.兆头

同一天问几次，烟给的都是同一句——她不屑于改口。
"""
import hashlib
from datetime import date

NAME = "兆头"
DESCRIPTION = "今日宜忌，一天只算一次：.兆头"
VERSION = "1.0"
CHARACTERS = ("citlali",)

YI = [
    "把没说完的话说完",
    "早睡，把灯留着",
    "沿有水的地方走",
    "收下一件旧东西",
    "一个人吃顿热的",
    "把欠的酒还了",
    "开一条窗缝，让烟出去",
    "给很久没联系的人递一句话",
    "慢慢走，别赶",
    "看一场火，看到它自己灭",
]

JI = [
    "在烟里找确定的答案",
    "凌晨三点做决定",
    "把别人的客气当承诺",
    "空着肚子喝酒",
    "翻旧账",
    "回头追一个已经走远的人",
    "把话咽下去，又说自己委屈",
    "独自走夜路，尤其是火山那边",
    "答应得太快",
    "把火点在自己家里",
]

READS = [
    "烟往低处走，说明今天该收，不该争。",
    "这一炉烧得直，事情比你想的顺，别自己添乱。",
    "烟里有断口，话说一半会有人接不上，重要的事写下来。",
    "灰是温的，今天遇到的人里有一个是旧相识。",
    "烟散得慢，急也没用，等的人会自己来。",
    "火苗偏了一下，今天少说一句，能省三天麻烦。",
    "这炉烟干净，适合把拖着的事收个尾。",
    "烟绕了半圈，路是通的，只是不直。",
]


def _pick(day, salt, pool):
    seed = hashlib.sha256(f"{day}|{salt}".encode("utf-8")).hexdigest()
    return pool[int(seed[:12], 16) % len(pool)]


def register(api):
    async def omen(ctx):
        day = date.today().isoformat()
        await ctx.reply(
            f"🔮 今日兆头 · {day}\n"
            f"宜：{_pick(day, 'yi', YI)}\n"
            f"忌：{_pick(day, 'ji', JI)}\n"
            f"解：{_pick(day, 'read', READS)}\n"
            "——一天问八遍也一样。烟不改口。"
        )

    api.on_command("兆头", omen, help=".兆头 —— 今日宜忌，同一天答案不变", cooldown=5)
