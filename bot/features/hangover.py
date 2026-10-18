"""茜特菈莉 · 解酒：.解酒 [谁]

四套土方子，外加一句她非说不可的数落。
"""
import random

from _plugstore import safe_text

NAME = "解酒"
DESCRIPTION = "四套土方子，配一句数落：.解酒 [谁]"
VERSION = "1.0"
CHARACTERS = ("citlali",)

# (名目, 方子, 数落)
CURES = [
    ("蜂蜜水",
     "温水化两勺蜂蜜，小口小口地喝。别仰头灌，灌了也是白灌。",
     "谁让你把酒当水喝的？我的酒是留着我自己喝的。"),
    ("热汤",
     "找一碗热的、咸的、带点油的，慢慢喝完，别烫着。",
     "空腹喝酒，你当自己的胃是熔炉？"),
    ("睡觉",
     "把窗帘拉上，手机扣过去，睡到自然醒。醒了先喝水。",
     "熬到天亮的是我，不是你。你凑什么热闹。"),
    ("别再喝了",
     "杯子放下，手离开桌沿，人去门口吹吹风。",
     "还喝？行啊，喝到明天别叫我解，我这儿不接回头客。"),
]


def register(api):
    async def cure(ctx):
        who = safe_text(ctx.arg_text, 12)
        name, recipe, scold = random.choice(CURES)
        head = f"🍯 解酒 · {name}" + (f"（给{who}）" if who else "")
        await ctx.reply(
            f"{head}\n方子：{recipe}\n数落：{scold}\n"
            "——土方子，不包好。真难受就去找会看病的人。"
        )

    api.on_command("解酒", cure, help=".解酒 [谁] —— 一套土方子加一句数落", cooldown=6)
