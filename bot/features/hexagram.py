"""胡桃 · 铜钱卜卦：.卜卦 [问题]

三枚铜钱掷六回，起一卦。卦是真摇的，解的是本堂主当时的心情。
"""
import random

from _plugstore import safe_text

NAME = "铜钱卜卦"
DESCRIPTION = "三枚铜钱掷六回，起一卦：.卜卦 [问题]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

# （卦名，卦象一句，断辞一句）
HEXAGRAMS = [
    ("乾为天", "天行健，日头不等人。", "该动就动，别把劲攒着发霉。"),
    ("坤为地", "地势厚，什么都能接住。", "慢一点无妨，稳住就算赢。"),
    ("水雷屯", "云雷交加，草木刚破土。", "开头难，难在开头，过了就好。"),
    ("山水蒙", "山下出泉，水还小。", "不懂就问，别装懂，装懂最贵。"),
    ("水天需", "云上于天，等一场该来的雨。", "急不得，先吃饭，雨自己会落。"),
    ("天水讼", "天水相背，话赶话。", "这口气先咽下，赢了也伤和气。"),
    ("地水师", "地中有水，人多心要齐。", "别单干，找个靠得住的搭子。"),
    ("水地比", "水在地上，挨着流。", "该靠近的人，别端着了。"),
    ("风天小畜", "风在天上，云攒着不落。", "小有积蓄，别急着一次花完。"),
    ("天泽履", "踩着虎尾，还没被咬。", "路窄，走稳，别回头张望。"),
    ("地天泰", "天地相交，气通了。", "顺的时候别飘，顺手把事办了。"),
    ("天地否", "天地不交，话传不到。", "说不通就先别说，等个缝。"),
    ("地山谦", "山藏在地下，有本事不露。", "低一头不吃亏，抬太高容易撞梁。"),
    ("雷地豫", "雷出地奋，心里先响。", "想玩就玩，别边玩边愧疚。"),
    ("泽雷随", "泽中有雷，跟着走。", "跟对人，比跑得快重要。"),
    ("山风蛊", "山下有风，旧事翻出来了。", "该收拾的旧账，趁今天收拾。"),
]


def _cast():
    """三枚铜钱一回：三正为老阳，三背为老阴，其余按多数。"""
    lines = []
    for _ in range(6):
        heads = sum(1 for _ in range(3) if random.random() < 0.5)
        if heads == 3:
            lines.append(("老阳", True))
        elif heads == 0:
            lines.append(("老阴", False))
        elif heads == 2:
            lines.append(("阳", True))
        else:
            lines.append(("阴", False))
    return lines


def register(api):
    async def divine(ctx):
        q = safe_text(ctx.arg_text, 24) or "今日该做点什么"
        lines = _cast()
        value = sum(1 << i for i, (_, yang) in enumerate(lines) if yang)
        name, image, verdict = HEXAGRAMS[value % len(HEXAGRAMS)]
        marks = " ".join(label for label, _ in lines)
        await ctx.reply(
            f"🪙 铜钱六掷 · 所问：{q}\n"
            f"自下而上：{marks}\n"
            f"本卦：{name}\n"
            f"卦象：{image}\n"
            f"断辞：{verdict}\n"
            "——信不信随你，本堂主只负责摇铜钱。"
        )

    api.on_command("卜卦", divine, aliases=("摇一卦",), help=".卜卦 [问题] —— 三枚铜钱，摇六回", cooldown=6)
