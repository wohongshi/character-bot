"""茜特菈莉 · 烟雾签：.烟雾签 [问题]、.熬夜

烟里看来的东西从来不直说——大萨满的职业道德。
"""
import random

from _plugstore import clip

NAME = "烟雾签"
DESCRIPTION = "点一炉烟，看一个模糊的答案：.烟雾签 [问题]、.熬夜"
VERSION = "1.0"
CHARACTERS = ("citlali",)

OMENS = [
    ("烟往东走", "东边有你要见的人。别急着开口，先听。", "宜：出门　忌：追问"),
    ("烟打了个旋", "事情会绕一圈回到原处，但你已经不是原来的你。", "宜：等　忌：硬拧"),
    ("烟灭了", "不是坏事。有些火本来就不该一直烧。", "宜：放下　忌：再点一次"),
    ("烟里有火", "你心里那点火还没灭，别假装它不在。", "宜：说实话　忌：装没事"),
    ("烟气发甜", "有人在偷偷对你好，你大概没发现。", "宜：回头看看　忌：理所当然"),
    ("烟分成两股", "两条路都能走，但只能选一条。今晚别选。", "宜：睡觉　忌：凌晨决定"),
    ("烟直直向上", "这次没有弯弯绕。想做就做。", "宜：动手　忌：再问别人"),
]

LATE = [
    "都几点了还不睡？烟都困了。",
    "熬夜的人，第二天看什么都是灰的。这不是占卜，这是常识。",
    "再刷一会儿？行啊，明天别来找我算「为什么这么累」。",
    "睡吧。天塌下来，也得等烟散尽再说。",
    "你们年轻人总拿命换时间，最后又拿时间换命。划算吗？",
]


def register(api):
    async def smoke(ctx):
        q = clip(ctx.arg_text, 40) or "最近该注意什么"
        omen, meaning, advice = random.choice(OMENS)
        await ctx.reply(
            f"🔮 烟雾签\n所问：{q}\n烟象：{omen}\n解曰：{meaning}\n{advice}\n"
            f"——别摇我，我只是把烟看见的说出来。"
        )

    async def late(ctx):
        await ctx.reply("🌙 " + random.choice(LATE) + "\n（本大萨满先去睡了，勿扰。）")

    api.on_command("烟雾签", smoke, aliases=("烟签",), help=".烟雾签 [问题] —— 茜特菈莉的模糊答案", cooldown=6)
    api.on_command("熬夜", late, aliases=("晚安",), help=".熬夜 —— 让大萨满催你去睡", cooldown=10)
