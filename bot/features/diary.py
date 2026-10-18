"""昔莲 · 今天：.今天

她替你写今天的日记。同一天问几次，写出来的都是同一份。
"""
import datetime
import hashlib

NAME = "今天"
DESCRIPTION = "让昔莲替你写今天的日记：.今天"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

WEATHER = [
    "晴，风不大，云走得很慢",
    "阴，天压得有点低，但没下雨",
    "小雨，路面反光，像铺了一层薄薄的镜子",
    "多云，太阳偶尔探出来，又躲回去",
    "很冷，呼出的气在空中停了一下才散",
    "有风，把晾在外面的衣服吹得鼓起来",
    "晴得过分，连影子都显得干净",
    "雾，远处的楼只剩下轮廓",
]

EVENTS = [
    "你在路上遇到一只猫，它看了你一眼就走了，像只是来确认你还在。",
    "有人替你把门扶住，你说了谢谢，走出去以后才想起没看清对方的脸。",
    "你把一件拖了很久的小事做完了，没有人鼓掌，但你自己知道。",
    "中午吃饭时你走神了一会儿，回过神来，饭已经凉了。",
    "你路过一家店，闻到熟悉的味道，站了两秒，又继续走。",
    "你翻了翻旧聊天记录，看到一半关掉了，没有难过，只是有点安静。",
    "你和人说了一句很普通的话，说完觉得今天的重量轻了一点。",
    "你什么也没做成，但把窗开了，让屋子换了一次空气。",
]

HEARTS = [
    "你比自己以为的，更能撑住一些事。",
    "不必每一件都做好，今天过去了，也算数。",
    "有些人不在身边，但你把它们带得很好。",
    "慢一点也可以，路不会因为你走得慢就消失。",
    "你今天有没有好好吃饭？我记着呢。",
    "难过的时候不用解释，我听得懂。",
    "你不是被落下了，你只是走在自己的时间上。",
    "记得抬头看看。今天的天空，是限量的。",
]


def _pick(seq, seed, salt):
    digest = hashlib.sha256(f"{seed}:{salt}".encode("utf-8")).hexdigest()
    return seq[int(digest[:8], 16) % len(seq)]


def register(api):
    async def today(ctx):
        day = datetime.date.today()
        seed = day.isoformat()
        await ctx.reply(
            f"📔 {day.month}月{day.day}日。今天的这份，我替你写。\n"
            f"天气：{_pick(WEATHER, seed, 'w')}。\n"
            f"发生的事：{_pick(EVENTS, seed, 'e')}\n"
            f"心里的话：{_pick(HEARTS, seed, 'h')}"
        )

    api.on_command("今天", today, help=".今天 —— 让昔莲替你写今天的日记", cooldown=8)
