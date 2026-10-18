"""胡桃 · 纸扎铺：.纸扎 [物件]

铺子有现成样子，也能照着客官说的现扎一件。价钱用摩拉，概不赊账。
"""
import random

from _plugstore import safe_text

NAME = "纸扎铺"
DESCRIPTION = "纸马纸屋纸手机，也能现扎一件：.纸扎 [物件]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

MENU = [
    ("纸马", 999, "脚程快，认路，不尥蹶子"),
    ("纸屋", 3999, "三进三出带院子，就是不能住人"),
    ("纸手机", 1288, "永远满电，永远有信号，就是打不通"),
    ("纸猫", 666, "蹲在门口，不抓沙发，也不掉毛"),
    ("纸船", 1888, "顺水走，不回头，适合送远客"),
    ("纸伞", 888, "撑开挡雨，收起来不占地方"),
    ("纸书", 777, "字都认得，故事自己编"),
    ("纸灯笼", 555, "夜里照路，白天收进柜子"),
    ("纸酒壶", 480, "倒得出酒香，喝不出醉"),
]
BLESS = [
    "到了那边也用得上，用不上就当个念想。",
    "扎得粗糙，心意是真的。",
    "路上带着，别嫌沉——纸做的，不沉。",
    "愿你在哪边，都有个能坐下的地方。",
    "烧的时候别回头，火会替你送到。",
    "这一件不收加急费，本堂主今天心情好。",
]


def _price(item):
    """按物件名定个稳定价钱，同一件东西不会今天一个价明天一个价。"""
    total = sum((i + 1) * ord(c) for i, c in enumerate(item))
    return 288 + (total % 240) * 8


def register(api):
    async def paper(ctx):
        item = safe_text(ctx.arg_text, 12)
        if not item:
            menu = "\n".join(
                " · ".join(f"{n} {p}" for n, p, _ in MENU[i:i + 3])
                for i in range(0, len(MENU), 3)
            )
            await ctx.reply(
                "🏮 往生堂纸扎铺 · 今日可扎\n"
                f"{menu}\n"
                "客官要什么，说一声「.纸扎 物件」，本堂主现扎，加急不加钱。"
            )
            return

        known = next((row for row in MENU if row[0] == item), None)
        if known:
            name, price, desc = known
        else:
            name, price = item, _price(item)
            desc = "照着客官比划的样子裁的，边角多糊了一层，结实"
        await ctx.reply(
            f"🏮 纸扎铺 · 现扎「{name}」\n"
            f"描金、糊壳、晾半炷香——{desc}。\n"
            f"祝福：{random.choice(BLESS)}\n"
            f"价钱：{price} 摩拉，概不赊账。"
        )

    api.on_command("纸扎", paper, help=".纸扎 [物件] —— 现扎一件，附祝福与价钱", cooldown=5)
