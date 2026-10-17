"""夸夸 & 摸摸头：``.夸夸 [@某人]`` / ``.摸摸头 [@某人]`` —— 轻轻抱一下。

- .夸夸：随机一句真心夸夸（可以 @ 一个人点名夸）
- .摸摸头：累了的时候来一下（别名 .摸摸 / .顺毛 / .安慰 / .揉揉）
- 纯文本、无状态；冷却 15s 防刷屏
"""

import random
import re

NAME = "夸夸摸摸"
DESCRIPTION = "夸夸和摸摸头：.夸夸 / .摸摸头 [@某人]"
VERSION = "1.0"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee',)

PRAISES = [
    "你今天也在很努力地生活呀，这份认真可莉全都看见啦！",
    "你笑起来的样子，比嘟嘟可还要亮闪闪，真的！",
    "就算今天有点笨手笨脚也没关系，你已经做得很棒啦。",
    "你身上有一种软软的、让人安心的气质，像刚晒过太阳的被子。",
    "可莉觉得，能遇到你是一件超幸运的事——比捡到四叶草还幸运！",
    "你帮别人的时候眼睛里有小星星，可莉偷偷看到啦。",
    "你不需要变成别的样子，你现在这样就已经很好了。",
    "你的脑瓜里总有好点子冒出来，像咕嘟咕嘟的小气泡水。",
    "世界这么大，你是很特别的那一个——可莉认证过的。",
    "累了就歇一歇，你已经做得比你以为的多得多啦。",
    "你的存在本身，就已经让某个人（比如可莉）今天过得更好了。",
    "你可太厉害了——可莉数了数，你身上至少有十个优点，但可莉只背下来三个：善良、认真、还有可爱！",
]

HEADPATS = [
    "摸摸头～今天辛苦啦，可莉给你递一颗甜甜的糖🍬",
    "摸摸（轻轻）。没关系的，慢慢来就好，可莉陪着你。",
    "呼噜呼噜毛，烦恼全跑掉～可莉的魔法，只对你生效哦。",
    "摸摸头！你这样蔫蔫的样子，可莉看着都心疼，抱一个吧。",
    "顺顺毛～要是累了，就先当一会儿没用的小猫也行，可莉给你放哨。",
    "抱住！可莉把今天份的阳光都分给你，要好好的哦。",
]


# 出站会解析 [CQ:at,qq=all] / [CQ:image,…] 这类富文本标记，而 who 来自用户自己敲的文本 ——
# 不洗的话 `.夸夸 @[CQ:at,qq=all]` 能让可莉 @全体成员 / 发任意图（OCR 二轮 #15）。
_UNSAFE = re.compile(r"[\[\]<>]|&#\d+;")


def _safe_name(raw: str) -> str:
    """昵称/参数里最长的安全片段：干掉方括号（CQ 标记的载体）与实体转义。"""
    return _UNSAFE.sub("", raw or "").strip()[:16]


def _target_name(ctx) -> str:
    """支持「.夸夸 @某人」——@ 经展平是「@名字」，取名字文本即可。"""
    tok = (ctx.arg_text or "").strip().split()[0] if (ctx.arg_text or "").strip() else ""
    if tok.startswith("@"):
        name = _safe_name(tok.lstrip("@"))
        if name:
            return name
    return ""


def register(api):
    async def praise_cmd(ctx):
        who = _target_name(ctx)
        line = random.choice(PRAISES)
        if who:
            await ctx.reply(f"好！可莉来夸夸 {who}：{line}")
        else:
            await ctx.reply(f"好！可莉来夸夸你：{line}")

    async def pat_cmd(ctx):
        who = _target_name(ctx)
        line = random.choice(HEADPATS)
        if who:
            await ctx.reply(f"{who}，{line}")
        else:
            await ctx.reply(line)

    api.on_command("夸夸", praise_cmd, aliases=("夸我", "夸奖"), cooldown=15,
                   help=".夸夸 [@某人] —— 随机一句真心夸夸")
    api.on_command("摸摸头", pat_cmd, aliases=("摸摸", "顺毛", "安慰", "揉揉", "抱抱"),
                   cooldown=15, help=".摸摸头 [@某人] —— 轻轻抱一下（别名 .摸摸/.安慰）")
