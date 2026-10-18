"""涂山红红 · 喝酒：.喝酒

她喝一点。话多一点，或者少一点。
"""
import random

from _plugstore import chat_key, db_update, state_path

NAME = "喝酒"
DESCRIPTION = "陪她喝一点：.喝酒"
VERSION = "1.0"
CHARACTERS = ("honghong",)

FILE = state_path("honghong_wine")

# (标题, 景象, 她的话)
REACTIONS = [
    ("话多了", "第三杯下去，她话多了。\n说的都是不相干的小事。", "「山下的面，还是太咸。」"),
    ("说起从前", "她忽然提起一个名字。\n说到一半停住，换了个说法。", "「……以前有个人，也爱坐这儿。」"),
    ("盯着月亮", "她盯着月亮看了很久。\n杯子在手里，一直没动。", "「月亮这东西，看久了像人。」"),
    ("问了一句", "她转过脸，很认真地看你。\n看得你不敢接话。", "「你有没有一件事，到死都不打算说。」"),
    ("按住了杯子", "她把手按在杯口上。\n酒就不晃了。", "「今天到这儿。」"),
]


def register(api):
    async def wine(ctx):
        key = "last:" + chat_key(ctx)
        last = db_update(FILE, key, lambda v: v, default=-1)
        pool = [i for i in range(len(REACTIONS)) if i != last] or list(range(len(REACTIONS)))
        idx = random.choice(pool)
        db_update(FILE, key, lambda v: idx, default=-1)
        title, scene, line = REACTIONS[idx]
        await ctx.reply(
            f"🍶 喝酒 · {title}\n"
            f"{scene}\n"
            f"{line}\n"
            "就当没听见。"
        )

    api.on_command("喝酒", wine, help=".喝酒 —— 她喝一点，你听着", cooldown=8)
