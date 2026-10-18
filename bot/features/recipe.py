"""亚托莉 · 做饭：.做饭 [菜名]

她的蛋包饭有八种结局，其中三种不能吃。她会认真解释每一种。
"""
import random

from _plugstore import db_update, safe_text, state_path

NAME = "做饭"
DESCRIPTION = "让亚托莉做一份蛋包饭：.做饭 [菜名]"
VERSION = "1.0"
CHARACTERS = ("atri",)

FILE = state_path("atri_recipe")
EGG_KEYS = ("蛋包饭", "蛋包", "蛋", "omurice", "omelet")

RESULTS = [
    ("成功",
     "蛋皮完整，米饭裹得很紧。我照着水菜萌教的火候，一秒都没走神。",
     "好吃就是高兴嘛！这个逻辑，我认为非常合理。"),
    ("成功",
     "番茄酱画了一个歪掉的笑脸。歪是歪，但它确实在笑。",
     "第一次做给夏生先生的时候，他说像工业废料。现在不像了。"),
    ("糊了",
     "锅底那一面是黑的。我把黑的切掉，剩下的还是蛋包饭。",
     "……请不要看锅。锅是无辜的。"),
    ("糊了",
     "我明明按秒计时。可能是我的内部时钟比火快了一点。",
     "计算中……计算结果：火太大了。下次我会承认这一点。"),
    ("形状怪",
     "它塌了。像一只被踩过的枕头。",
     "味道应该没有问题。形状不在我的设计指标里。"),
    ("形状怪",
     "我想把它翻成完美的半月形，结果它飞起来，落在灶台外面。",
     "三秒规则。落地三秒以内，还算能吃吗？"),
    ("味道意外好",
     "我多加了一勺糖。人类好像都喜欢甜的东西。",
     "你尝一口。好吃的话，请说好吃。我会记下来。"),
    ("味道意外好",
     "蛋皮里包了芝士。会拉丝。这个是我自己想出来的。",
     "我是高性能的嘛。做饭这一项，我申请重新评分。"),
]


def register(api):
    async def recipe(ctx):
        arg = safe_text(ctx.arg_text, 24)
        n = db_update(FILE, "count", lambda c: (c if isinstance(c, int) else 0) + 1, default=0)

        if arg and not any(k in arg for k in EGG_KEYS):
            await ctx.reply(
                f"「{arg}」……\n"
                "我还在学。现在的菜单只有蛋包饭，八种做法，其中三种不能吃。\n"
                "你先吃蛋包饭吧。等我学会了，第一个做给你。"
            )
            return

        kind, body, tail = random.choice(RESULTS)
        await ctx.reply(
            f"🍳 第 {n} 次挑战 · 蛋包饭 · {kind}\n"
            f"{body}\n"
            f"——{tail}"
        )

    api.on_command("做饭", recipe, aliases=("蛋包饭",),
                   help=".做饭 [菜名] —— 让她做一份蛋包饭", cooldown=5)
