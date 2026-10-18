"""茜特菈莉 · 唠叨：.唠叨

按钟点挑一句：深夜催睡、饭点催吃、清晨催穿外套、下午催喝水。
"""
import random
from datetime import datetime

NAME = "唠叨"
DESCRIPTION = "按现在几点，挑一句嘴硬的关心：.唠叨"
VERSION = "1.0"
CHARACTERS = ("citlali",)

TALKS = {
    "sleep": (
        ["都这个点了还没睡？\n你当自己是燃素做的，烧不完？\n躺下。灯关了，手机也放下。",
         "嗯……又熬夜。\n我不管你忙什么，天塌了也等睡醒再说。\n别逼我起来赶你。",
         "眼睛都红成那样了，还嘴硬说没事。\n去睡。我不说第二遍——第二遍要收费。"],
        "——不是关心你，是你倒下了我还得收拾。",
    ),
    "meal": (
        ["饭点了。\n别拿零食糊弄，那东西喂不饱人。\n吃完再来找我说话。",
         "你早饭是不是又没吃？\n我不问，你也别装。\n去，热的东西吃一口。",
         "这个点还不吃，是想饿到胃疼再来找我算账？\n我这儿没有药，只有酒。\n吃饭。"],
        "——吃你的，别看我。看我也没饭。",
    ),
    "coat": (
        ["外面冷。\n纳塔的早晚不是闹着玩的，风专挑脖子钻。\n外套穿上，围巾也带上。",
         "穿这么点就出门？\n你以为年轻就不怕冷，冷起来可不认人。\n回去加一件。"],
        "——冻着了别来找我哭，我这儿没热水。",
    ),
    "water": (
        ["嘴唇都干了。\n一天到晚不喝水，光喝那些甜的东西。\n倒杯温水，喝完再说话。",
         "下午最燥，人容易发昏。\n不是烟看出来的，是你说话都没力气了。\n去喝水。"],
        "——我不是怕你渴，是怕你说话难听。",
    ),
}


def _topic(hour):
    if 5 <= hour < 10:
        return "coat"
    if 10 <= hour < 14:
        return "meal"
    if 14 <= hour < 18:
        return "water"
    if 18 <= hour < 21:
        return "meal"
    return "sleep"


def register(api):
    async def nag(ctx):
        now = datetime.now()
        lines, tag = TALKS[_topic(now.hour)]
        await ctx.reply(
            f"🕯️ 现在 {now:%H:%M}，烟都快睡了。\n"
            f"{random.choice(lines)}\n{tag}"
        )

    api.on_command("唠叨", nag, help=".唠叨 —— 让她按钟点念你两句", cooldown=8)
