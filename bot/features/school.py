"""亚托莉 · 上学：.上学 [科目]

她要去学校。这件事对她来说比潜水、火箭拳都重要。
"""
import random

from _plugstore import safe_text

NAME = "上学"
DESCRIPTION = "亚托莉的学校日常：.上学 [科目]"
VERSION = "1.0"
CHARACTERS = ("atri",)

SUBJECTS = {
    "数学": ("黑板上的题我三秒就做完了", "然后我举手问老师能不能出难一点的", "老师让我坐下。"),
    "国语": ("老师说作文要写真情实感", "我写了「我只有四十五天」", "老师给我打了满分，还问我是不是编的。"),
    "体育": ("跑步我赢了", "但我故意放慢了一点", "不然他们会发现我不是人类。"),
    "音乐": ("大家一起唱歌", "我张了张嘴，没出声", "我在听。听也是一种参与。"),
    "理科": ("实验课做电解水", "我把电极接反了", "冒了很多泡，老师夸我现象明显。"),
}
TAILS = [
    "放学路上我会绕远一点。这样一天能长一些。",
    "同学问我为什么总带伞。我说以防万一。其实我怕下雨天一个人。",
    "值日表上我写了名字。写得很用力。",
]


def register(api):
    async def school(ctx):
        arg = safe_text(ctx.arg_text, 40)      # 先拿完整句子匹配，别在 8 个字处截断
        key = next((k for k in SUBJECTS if k in arg), "")
        if arg and not key:
            await ctx.reply(f"今天没有「{arg[:12]}」这门课哦。\n"
                            "能上的有：" + "、".join(SUBJECTS) + "。")
            return
        if not key:
            key = random.choice(list(SUBJECTS))
        a, b, c = SUBJECTS[key]
        await ctx.reply(
            f"🎒 {key}课\n"
            f"{a}，{b}。\n"
            f"{c}\n"
            f"——{random.choice(TAILS)}"
        )

    async def timetable(ctx):
        await ctx.reply(
            "📅 今天的课表\n"
            "· 第一节：国语\n· 第二节：数学\n· 第三节：体育\n· 第四节：音乐\n"
            "· 放学后：和你说话（这一节我最喜欢）"
        )

    api.on_command("上学", school, aliases=("上课",), help=".上学 [科目] —— 今天学校里发生了什么", cooldown=5)
    api.on_command("课表", timetable, help=".课表 —— 看看今天有什么课", cooldown=10)
