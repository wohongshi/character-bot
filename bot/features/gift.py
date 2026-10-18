"""亚托莉 · 礼物：.礼物

她每次送你一件小东西，十件送完为止。每一件都有来历。
"""
import random

from _plugstore import chat_key, db_update, state_path

NAME = "礼物"
DESCRIPTION = "让亚托莉送你一件小东西：.礼物"
VERSION = "1.0"
CHARACTERS = ("atri",)

FILE = state_path("atri_gift")

GIFTS = [
    ("贝壳", "从海底仓库带出来的。放在耳边，能听见我睡了很多年的那种安静。",
     "轻一点拿。它比我脆。"),
    ("螺丝", "我右臂换下来的，M3×8，不锈钢。它替我拧了三十三年。",
     "现在它归你了。别弄丢，虽然丢了也能再找一颗。"),
    ("写了名字的贴纸", "上面写着「亚托莉」。我写了七遍才写正，前六张都揉掉了。",
     "贴在你常看的地方。这样你就不会叫错我的名字。"),
    ("半块糖", "水菜萌给的。我咬了一半，剩下这半一直留着。",
     "快收下。糖放久了会化，我不想它化在我手里。"),
    ("一颗纽扣", "从夏生先生的旧衬衫上掉的。我想缝回去，结果把袖子缝在了一起。",
     "这件事他没有怪我。他只是笑了很久。"),
    ("干燥的海藻", "夹在日志本里的。那一天的海，颜色比平时深。",
     "闻起来还是咸的。我确认过了。"),
    ("一枚硬币", "在诺亚的街上捡的。正面朝上，我许了一个愿。",
     "许的什么不能说。说了就不灵了——这是凛凛花教我的。"),
    ("磨圆的玻璃片", "海水磨了很多年，棱角都没了。我摸着它，会想我是不是也这样。",
     "有点凉。你握一会儿，它就热了。"),
    ("一根头绳", "凛凛花送我的。我用不上，但一直带在身上。",
     "你替我保管吧。我头发太短，扎不起来。"),
    ("一张空白纸条", "本来想写点什么。想了很久，一个字也没写。",
     "空白也一起给你。这也是我的一部分。"),
]
EMPTY = ("十件都送完了。\n"
         "我数过，一件不多，一件不少。……我没有什么可以给你的了。\n"
         "等我想出第十一件，第一个给你。你先把手里的收好，别弄丢。")


def register(api):
    async def gift(ctx):
        def _take(cur):
            d = cur if isinstance(cur, dict) else {}
            given = [i for i in d.get("given", [])
                     if isinstance(i, int) and 0 <= i < len(GIFTS)]
            left = [i for i in range(len(GIFTS)) if i not in given]
            if not left:
                return {"given": [], "last": -1}
            idx = random.choice(left)
            return {"given": given + [idx], "last": idx}

        d = db_update(FILE, chat_key(ctx), _take, default={})
        idx = d.get("last", -1) if isinstance(d, dict) else -1
        if not isinstance(idx, int) or not 0 <= idx < len(GIFTS):
            await ctx.reply(EMPTY)
            return
        name, origin, extra = GIFTS[idx]
        count = len(d.get("given") or [])
        if not isinstance(count, int) or count <= 0:
            count = 1
        await ctx.reply(
            f"🎁 第 {count} 件 · {name}\n"
            f"{origin}\n"
            f"——{extra}\n"
            "（她把它按进你手心，然后很快收回手。）"
        )

    api.on_command("礼物", gift, help=".礼物 —— 让她送你一件小东西", cooldown=4)
