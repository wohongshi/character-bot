"""胡桃 · 打油诗：.打油诗 [主题]

四句押韵的即兴打油诗，主题由用户给；没有主题就随机。
"""
import random

from _plugstore import clip

NAME = "打油诗"
DESCRIPTION = "小巷派暗黑诗人胡桃，现场来四句：.打油诗 [主题]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

TOPICS = ["生死", "月亮", "加班", "火锅", "蝴蝶", "穷", "失眠", "下雨", "考试", "猫"]
RHYMES = [
    ("台上灯红照夜长", "台下人走茶就凉", "莫问归途何处是", "一程风雨一程香"),
    ("生也茫茫死也茫", "中间这段最荒唐", "若把日子过成诗", "管他明日短与长"),
    ("天上月圆缺又圆", "人间聚散总无端", "且把今宵杯中酒", "敬给回不来的伴"),
    ("一盏灯来一碗汤", "一身风雨一身霜", "世人只道黄泉冷", "我道黄泉也寻常"),
    ("蝴蝶飞过旧墙头", "带来那边一句愁", "我问它愁什么呀", "它说那边也加班"),
    ("春来无事只为花", "夏来无事只想趴", "秋来无事数落叶", "冬来无事等放假"),
]


def register(api):
    async def poem(ctx):
        topic = clip(ctx.arg_text, 16) or random.choice(TOPICS)
        a, b, c, d = random.choice(RHYMES)
        if ctx.arg_text:
            d = f"{d}——关于「{topic}」，本堂主只能编到这儿了"
        await ctx.reply(
            f"🖌 小巷派暗黑诗人 · 新作\n"
            f"《{topic}》\n"
            f"{a}，\n{b}。\n{c}，\n{d}。\n"
            f"——怎么样？不好笑的话，本堂主再给你来一首更冷的。"
        )

    api.on_command("打油诗", poem, aliases=("来首诗",), help=".打油诗 [主题] —— 胡桃现场编四句", cooldown=5)
