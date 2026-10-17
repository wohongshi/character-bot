"""茜特菈莉 · 观星：.观星 [星座/问题]

纳塔的「大萨满」其实更像个熬夜看星星的老太太。星象解释一本正经地胡说，
唯一确定的是：她真的会看天气。
"""
import random

from _plugstore import safe_text

NAME = "观星"
DESCRIPTION = "夜观星象，替你解一句：.观星 [星座或问题]"
VERSION = "1.0"
CHARACTERS = ("citlali",)

SIGNS = ["白羊", "金牛", "双子", "巨蟹", "狮子", "处女",
         "天秤", "天蝎", "射手", "摩羯", "水瓶", "双鱼"]
OMENS = [
    ("星子很亮", "今晚适合说话，说真话", "别在群里装深沉，直接讲"),
    ("有云过境", "有人在你背后嚼舌根", "当没听见，回头他自己会噎住"),
    ("流星一颗", "会收到一条迟到的消息", "别急着回，先想好要不要接"),
    ("月晕", "钱要出一次门", "不是被骗，是你自愿的"),
    ("北斗偏移", "旧人旧事会冒头", "翻出来看看也行，别又收进去"),
    ("无星", "今夜不宜做决定", "睡觉。睡醒还是那个问题，但你有力气了"),
    ("星落", "有件事要结束了", "结束不是坏事，是腾地方"),
]
SNARK = [
    "星星不背这个锅，是你自己想太多。",
    "我看了三百年星象，最准的一条是：人总在问已经知道答案的问题。",
    "别问我准不准，问就是准。问第二次就要收钱了。",
]


def register(api):
    async def gaze(ctx):
        arg = safe_text(ctx.arg_text, 24)
        sign = next((s for s in SIGNS if s in arg), "") or random.choice(SIGNS)
        omen, meaning, advice = random.choice(OMENS)
        # 识别出的星座一定要出现在结果里，否则「.观星 狮子」和乱问一句没区别
        q = f"{sign}座：{arg}" if arg else f"{sign}座今天的运势"
        await ctx.reply(
            f"🔭 茜特菈莉的观星台\n"
            f"所问：{q}\n"
            f"星象：{omen}\n"
            f"解曰：{meaning}\n"
            f"建议：{advice}\n"
            f"——{random.choice(SNARK)}"
        )

    async def sky(ctx):
        await ctx.reply(
            "🌌 今夜星图（纳塔·夜神之国视角）\n"
            "· 天顶：织女三连星，适合许愿\n"
            "· 东南：一团看不清的东西，可能是云，也可能是龙\n"
            "· 地平线：有颗星在闪，那是大气湍流，不是神明眨眼\n"
            "——看完就去睡，别熬夜。我熬就行了，我习惯了。"
        )

    api.on_command("观星", gaze, aliases=("星象",), help=".观星 [星座或问题] —— 解一句", cooldown=5)
    api.on_command("星图", sky, help=".星图 —— 今夜能看到什么", cooldown=8)
