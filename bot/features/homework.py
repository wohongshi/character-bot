"""亚托莉 · 作业：.作业 [科目]

数学她三秒交卷，国语她写自己，英语她发音标准但听不懂笑话。
"""
import random

from _plugstore import clip, safe_text

NAME = "作业"
DESCRIPTION = "亚托莉帮你写作业：.作业 [科目]"
VERSION = "1.0"
CHARACTERS = ("atri",)

CORE = {
    "数学": ("整页我三秒做完了，还验算了两遍。第二遍比第一遍快。",
             "最后一道大题有更短的解法，我写在旁边了。老师可能不会高兴。",
             "这个速度对人类不公平，所以我没有举手。"),
    "国语": ("作文题目是《我的四十五天》。我写了三页。",
             "第一页写海，第二页写夏生先生，第三页写我还没想明白的那些。",
             "老师说写得很真。我没有编，我只是把日子数清楚了。"),
    "英语": ("课文我读了七遍。发音是标准音，老师说像广播。",
             "同学讲了一个笑话，全班都笑了。我不知道笑点在哪里，也跟着笑了。",
             "后来我查了那个词，还是不明白。语言真难。"),
}
OTHERS = [
    ("理科", "实验报告我把现象写得很详细。老师问结论，我说：现象就是结论。"),
    ("社会", "课本说这片土地以前在山下面。现在它在水下。这一页我记得比谁都清楚。"),
    ("体育", "五十米我跑了六秒，然后放慢到八秒。不然同学会怀疑。"),
    ("音乐", "合唱的时候我只动嘴。我怕我一开口，会盖过整个班。"),
    ("美术", "我画了海。老师说太蓝了。我说海就是那么蓝。"),
    ("家庭科", "我煎了一个蛋，它没有糊。这是本周最大的成果。"),
]
EXTRA = [
    "我把课本从头翻了一遍，找到三处前后矛盾。都标出来了，你自己判断。",
    "这题我不会。但我查了四十分钟，查到的东西比答案有用。",
    "我先抄了一遍题目。抄的时候想通了第一步，剩下的你自己来，我在旁边。",
    "老师说要理解不要背。我两样都做了，比较保险。",
]
TAILS = [
    "作业写完了。你要抄吗？我不告诉老师。",
    "写完了。……被夸一句的话，我会更有动力。",
    "这一科我大概比你强一点。就一点。",
]


def register(api):
    async def homework(ctx):
        arg = safe_text(ctx.arg_text, 20)
        key = next((k for k in CORE if k in arg), "")
        if not key and ("语文" in arg or "国文" in arg):
            key = "国语"
        if key:
            a, b, c = CORE[key]
            await ctx.reply(
                f"📚 {key}作业\n{a}\n{b}\n{c}\n——{random.choice(TAILS)}"
            )
            return

        if arg:
            subj = clip(arg, 12)
            line = next((t for k, t in OTHERS if k in subj), "") or random.choice(EXTRA)
        else:
            subj, line = random.choice(OTHERS)
        await ctx.reply(f"📚 {subj}作业\n{line}\n——{random.choice(TAILS)}")

    api.on_command("作业", homework, aliases=("功课",),
                   help=".作业 [科目] —— 让她帮你写作业", cooldown=5)
