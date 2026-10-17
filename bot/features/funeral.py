"""胡桃 · 往生堂：.往生堂、.超度 [名字]

往生堂的第七十七代堂主亲自拉客。黑色幽默，但绝不开真实丧事的玩笑——
套餐全是编的，价格用「摩拉」计价。
"""
import random

from _plugstore import safe_text

NAME = "往生堂"
DESCRIPTION = "往生堂业务咨询：.往生堂、.超度 [名字]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

PLANS = [
    ("经济舱", "一口薄棺，两炷清香", "适合手头紧、又想走得体面的朋友", "1999"),
    ("舒适舱", "楠木棺、纸扎三件套、哭丧代班两位", "本堂主亲自念悼词，包哭", "9999"),
    ("豪华舱", "金丝楠、全套仪仗、往生咒循环播放", "送走之后还能上个热搜", "99999"),
    ("跳楼价", "买一送一（第二位需自备）", "仅限本堂主心情好的时候", "面议"),
]
PITCH = [
    "客官，看一眼嘛，看一眼不要钱。",
    "人这一辈子啊，早晚要用上，早订有折扣。",
    "别怕，本堂主自己也在名单上，排你后面。",
    "买不买无所谓，进来喝碗汤再走嘛。",
]
BLESS = [
    "路滑，慢走，别回头。",
    "到了那边，替我问声好。",
    "该吃吃该喝喝，别惦记账本。",
    "下辈子投个好人家，记得还来找我聊天。",
]


def register(api):
    async def shop(ctx):
        plan_lines = "\n".join(f"· {n}｜{d}｜{price} 摩拉\n  {t}" for n, d, t, price in PLANS)
        await ctx.reply(
            "🏮 往生堂 · 今日牌价（摩拉）\n"
            f"{plan_lines}\n"
            f"——{random.choice(PITCH)}\n"
            "（本堂主提醒：这些都是编的，活得好好的就别来问价。）"
        )

    async def send_off(ctx):
        who = safe_text(ctx.arg_text, 16) or "这位客官"
        await ctx.reply(
            f"🕯 为「{who}」念一段：\n"
            "「生有时，死有时；哭有时，笑有时。」\n"
            f"{random.choice(BLESS)}\n"
            "——本堂主不收钱，这段算送的，嘿嘿。"
        )

    api.on_command("往生堂", shop, aliases=("白事",), help=".往生堂 —— 看看今天的牌价", cooldown=4)
    api.on_command("超度", send_off, aliases=("念一段",), help=".超度 [名字] —— 免费送一段", cooldown=4)
