"""今天吃什么：``.吃什么`` / ``.吃什么 辣``。

通用插件。纯离线（内置菜名库，不联网）。

和 `.帮我选` 的区别：那个是「你给选项我来挑」，这个是「连选项都想不出来」时用的。
所以库里按口味分好组，`.吃什么 辣` 只会从辣的里挑。

菜名只收**公认的家常菜/小吃**，不编奇怪的名字 —— 免得给出一个「听着像菜、
其实不存在」的东西。
"""
from __future__ import annotations

import random

NAME = "吃什么"
NAME_EN = "What to eat"
DESCRIPTION = "想不出吃什么的时候，让她报几个。" 
DESCRIPTION_EN = "Suggest dishes when you cannot decide."
VERSION = "1.0"

DISHES: dict[str, list[str]] = {
    "辣": ["麻婆豆腐", "水煮鱼", "辣子鸡", "回锅肉", "毛血旺", "酸菜鱼", "小炒肉",
           "麻辣香锅", "冒菜", "剁椒鱼头", "口水鸡", "串串香", "夫妻肺片"],
    "清淡": ["番茄炒蛋", "清蒸鲈鱼", "白灼虾", "冬瓜排骨汤", "蒸蛋羹", "青菜豆腐汤",
             "香菇滑鸡", "上汤娃娃菜", "小米粥配小菜"],
    "面": ["牛肉面", "炸酱面", "番茄鸡蛋面", "葱油拌面", "担担面", "热干面", "刀削面",
           "阳春面", "螺蛳粉", "桂林米粉"],
    "饭": ["扬州炒饭", "煲仔饭", "黄焖鸡米饭", "卤肉饭", "咖喱饭", "蛋包饭", "盖浇饭",
           "石锅拌饭"],
    "汤": ["番茄蛋汤", "紫菜蛋花汤", "玉米排骨汤", "冬瓜丸子汤", "酸辣汤", "罗宋汤"],
    "早点": ["豆浆油条", "小笼包", "煎饼果子", "生煎", "肠粉", "皮蛋瘦肉粥", "馄饨"],
    "夜宵": ["烧烤", "小龙虾", "炒年糕", "关东煮", "炸鸡配啤酒", "烤冷面", "砂锅粥"],
    "素": ["干煸豆角", "地三鲜", "手撕包菜", "蒜蓉西兰花", "凉拌黄瓜", "虎皮青椒"],
    "肉": ["红烧肉", "糖醋排骨", "可乐鸡翅", "黑椒牛柳", "孜然羊肉", "酱牛肉"],
    "甜": ["红豆双皮奶", "杨枝甘露", "酒酿圆子", "绿豆糕", "蛋挞", "芒果班戟"],
}


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def suggest(kind: str = "", n: int = 3, rng=None) -> list[str]:
    rng = rng or random
    pool = list(DISHES.get(kind) or [])
    if not pool:                                  # 没指定口味：每类抽一个，尽量不重样
        cats = list(DISHES)
        rng.shuffle(cats)
        pool = [rng.choice(DISHES[c]) for c in cats[:n]]
    rng.shuffle(pool)
    return pool[:max(1, n)]


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip()
        kinds = "、".join(DISHES)
        if raw in ("菜", "有哪些", "列表"):
            await ctx.reply("能按这些口味挑：" + kinds)
            return
        kind = ""
        if raw:
            for k in DISHES:
                if k in raw:
                    kind = k
                    break
            if not kind:
                await ctx.reply(f"没有「{raw[:12]}」这一类～能按 {kinds} 挑，或者直接 .吃什么")
                return
        picks = suggest(kind, 3)
        head = f"🍽 今天吃{kind}的话：" if kind else "🍽 今天吃这些怎么样："
        rows = "\n".join(f"· {p}" for p in picks)
        await ctx.reply(f"{head}\n{rows}\n（都不想吃就再问一次）")

    api.on_command("吃什么", cmd, aliases=("eat",), cooldown=2,
                   help=".吃什么 [辣|清淡|面|饭|汤|早点|夜宵|素|肉|甜]")
