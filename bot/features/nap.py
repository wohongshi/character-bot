"""茜特菈莉 · 打盹：.打盹

她睡着了会说梦话。最后那一句，是突然清醒的。
"""
import random

NAME = "打盹"
DESCRIPTION = "别摇她，听两句梦话：.打盹"
VERSION = "1.0"
CHARACTERS = ("citlali",)

DROWSY = [
    "嗯……别摇……这炉烟还没看完……",
    "……火别灭。灭了我就找不到路了。",
    "欧洛伦，把窗关上……风把牌吹乱了。",
    "我没醉。我只是……想躺一会儿。",
    "轻小说第七卷……谁放我枕头底下的……",
    "星星掉进杯子里了……捞一下……",
    "记账……今天谁欠我一壶……",
    "……嗯？我没睡。我在听。你接着说。",
]

AWAKE = "……你刚才什么都没听见。"


def register(api):
    async def nap(ctx):
        first, second = random.sample(DROWSY, 2)
        await ctx.reply(
            "😴（她歪在椅子上，书盖在脸上）\n"
            f"「{first}」\n「{second}」\n「{AWAKE}」\n"
            "（说完睁开眼，眼神清醒得吓人。）"
        )

    api.on_command("打盹", nap, help=".打盹 —— 听她说两句梦话", cooldown=6)
