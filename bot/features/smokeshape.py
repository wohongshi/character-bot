"""茜特菈莉 · 烟形：.烟形

一炉烟能看出什么，全看你敢不敢认。
"""
import hashlib
import random
from datetime import date

NAME = "烟形"
DESCRIPTION = "看一炉烟像什么，顺便看看今天风往哪吹：.烟形"
VERSION = "1.0"
CHARACTERS = ("citlali",)

# (烟形, 解读)
SHAPES = [
    ("一只趴着的狗", "有人一直守在你门口，你没当回事。"),
    ("一条船", "水路自己会开，你别硬划。"),
    ("一只手", "有人想拉你一把。先看清是谁的手。"),
    ("没写完的字", "话说到一半停了。剩下那半句，你自己清楚。"),
    ("一棵歪脖子树", "长得歪不妨碍活得久。你也一样。"),
    ("一只闭着的眼", "有些事你不想看，烟就替你把眼睛闭上了。"),
    ("一座塔", "堆得太高的东西，风一吹最先倒。"),
    ("两股拧在一起的绳", "两个人的事，解不开就先别解。"),
    ("一头睡着的龙", "别去戳，哪怕它看着像猫。"),
]

# (方位, 一句)
WINDS = [
    ("东", "那边有人正在念你的名字。"),
    ("南", "火气重，今天少说两句。"),
    ("西", "有旧东西要回来，别急着关门。"),
    ("北", "冷，也清净，适合一个人待着。"),
    ("东南", "路是通的，只是绕。"),
    ("东北", "有个消息在路上，慢，但会到。"),
    ("西南", "水气重，适合把话讲开。"),
    ("西北", "夜里会想起一个人，别急着找。"),
]


def _wind(day):
    seed = hashlib.sha256(f"wind|{day}".encode("utf-8")).hexdigest()
    return WINDS[int(seed[:10], 16) % len(WINDS)]


def register(api):
    async def shape(ctx):
        name, read = random.choice(SHAPES)
        where, hint = _wind(date.today().isoformat())
        await ctx.reply(
            f"🌫️ 烟形图鉴\n"
            f"这一炉像：{name}\n"
            f"解：{read}\n"
            f"今天烟往{where}飘：{hint}\n"
            "——烟只替你看一眼，路还得你自己走。"
        )

    api.on_command("烟形", shape, help=".烟形 —— 看一炉烟像什么", cooldown=5)
