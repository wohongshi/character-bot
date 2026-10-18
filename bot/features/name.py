"""涂山红红 · 取名：.取名 [特征]

按特征给妖怪或宠物取名。名字跟一辈子，她不随便给。
"""
import random

from _plugstore import safe_text

NAME = "取名"
DESCRIPTION = "按特征给妖怪或宠物取名：.取名 [特征]"
VERSION = "1.0"
CHARACTERS = ("honghong",)

POOLS = {
    "山": [("岩生", "石缝里长出来的"), ("阿岭", "山脊上捡的"), ("石青", "青石的颜色")],
    "雪": [("雪见", "雪停那天生的"), ("白枝", "雪压过的枝"), ("霜降", "霜降那天来的")],
    "铃": [("小铃", "走路带响"), ("阿响", "哭声像铃"), ("铃生", "铃铛底下捡的")],
    "火": [("赤尾", "尾巴尖是红的"), ("焰", "火烧过的名字"), ("火尾", "土一点，好记")],
    "月": [("望月", "整夜看月亮"), ("月白", "毛色像月光"), ("阿满", "月圆那天来的")],
}

HINTS = [
    (("白", "雪", "冷", "冰", "霜"), ["雪", "月"]),
    (("红", "火", "热", "赤", "焰"), ["火", "山"]),
    (("小", "响", "叫", "声", "铃"), ["铃", "山"]),
    (("夜", "黑", "静", "暗"), ["月", "山"]),
    (("大", "高", "壮", "重"), ["山", "火"]),
]


def register(api):
    async def naming(ctx):
        feat = safe_text(ctx.arg_text, 12)
        if feat:
            picks = next((v for k, v in HINTS if any(w in feat for w in k)), None)
            pools = list(dict.fromkeys(picks)) if picks else []
        else:
            pools = []
        if not pools:
            pools = random.sample(list(POOLS), 2)
        want = random.choice((2, 3))
        while len(pools) < want:
            extra = random.choice([k for k in POOLS if k not in pools])
            pools.append(extra)
        picked = random.sample(pools, k=want)
        lines = []
        for origin in picked:
            name, why = random.choice(POOLS[origin])
            lines.append(f"{origin} · {name} —— {why}。")
        head = f"🖋 取名 · {feat}" if feat else "🖋 取名 · 先给我一个特征"
        await ctx.reply(
            f"{head}\n"
            + "\n".join(lines)
            + "\n挑一个。名字跟一辈子，定了不能改。"
        )

    api.on_command("取名", naming, help=".取名 [特征] —— 给妖怪或宠物取个名", cooldown=6)
