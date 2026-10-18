"""可莉塔罗：``.塔罗 [问的事]`` —— 抽一张大阿卡纳，看看现在的暗示。

- 22 张大阿卡纳内置牌义（正位/逆位两版，可莉口吻），全离线、零依赖
- 结果渲染成一张塔罗卡片（深紫夜空 + 金色描边；逆位时星星倒转）
- 纯娱乐、无状态（不写任何文件）；冷却 12 秒
"""

import asyncio
import logging
import math
import random
import sys
from pathlib import Path

# _cardimg.py 与插件同目录（兜底 sys.path，见 gacha.py 同一段说明）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402

logger = logging.getLogger(__name__)

NAME = "塔罗"
DESCRIPTION = "塔罗占卜：.塔罗 [问的事] —— 抽一张大阿卡纳（发小卡片）"
VERSION = "1.0"

CHARACTERS = ('klee',)

BASE = Path(__file__).resolve().parent / "state"
FONT_CUTE = _cardimg.FONT_CUTE
FONT_CJK = _cardimg.FONT_CJK
CJK_SC_INDEX = _cardimg.CJK_SC_INDEX
KEEP_CARDS = _cardimg.KEEP_CARDS

W, H = 560, 816

# (罗马数字, 名字, 正位短义, 逆位短义) —— 牌义取常用通说，文案写成可莉口吻
CARDS = [
    ("0", "愚者", "新的旅程在招手，大胆迈出第一步就好", "别急着出发，先看看包里少没少东西"),
    ("I", "魔术师", "万事俱备，你比想象中更有办法", "小心夸下的海口，先做再说"),
    ("II", "女祭司", "安静下来，答案藏在直觉里", "别把感觉闷太久，说出来会好一些"),
    ("III", "女皇", "被温柔包围的一天，对自己也好一点", "别光顾着付出，你也值得被宠"),
    ("IV", "皇帝", "稳住阵脚，按计划一步步来", "太硬的绳子容易断，松一松"),
    ("V", "教皇", "听听前辈的话，老办法今天很灵", "规则是死的，偶尔也要有自己的主意"),
    ("VI", "恋人", "心之所向，做让你开心的选择", "选择前先问问自己：真的喜欢吗"),
    ("VII", "战车", "冲劲十足，适合把难事一口气拿下", "别横冲直撞，看一眼路再跑"),
    ("VIII", "力量", "温柔比蛮力更有用，你可以的", "别硬撑，累了就说累"),
    ("IX", "隐士", "独处充电时间到，一个人也很好", "别把自己关太久，留一盏灯给朋友"),
    ("X", "命运之轮", "齿轮转到了你这边，顺势而为", "运气在打盹，自己多走两步"),
    ("XI", "正义", "坦坦荡荡，该你的跑不掉", "心里那杆秤要端平，别亏待自己"),
    ("XII", "倒吊人", "换个角度看看，难题会自己松开", "干等着可不行，动一动脚"),
    ("XIII", "死神", "结束是新的开始，该放的手要放", "别抱着旧东西不撒手呀"),
    ("XIV", "节制", "慢慢来，一点点调和刚刚好", "别走极端，中间那条路更宽"),
    ("XV", "恶魔", "小心上瘾的东西，玩要玩得有分寸", "挣脱束缚的好时机，趁机跑掉"),
    ("XVI", "高塔", "有点小意外，但塌下来的都是该拆的", "虚惊一场，拍拍灰继续走"),
    ("XVII", "星星", "愿望开花的一天，许个愿吧", "别急，星星还在赶来的路上"),
    ("XVIII", "月亮", "看不清就先别急着下结论", "迷雾要散了，再等一等"),
    ("XIX", "太阳", "大晴天！做什么都有劲儿", "云会散的，先晒晒心情"),
    ("XX", "审判", "该醒来啦，有一件重要的事在等你", "再想想也没关系，别勉强自己"),
    ("XXI", "世界", "圆满了！这一程值得好好庆祝", "还差最后一步，别在终点前松劲"),
]


def draw(rng=None):
    """抽一张牌。返回 ``(牌, 是否逆位)``。

    rng 连调两次：第一次定位（可夹紧不越界），第二次 < 0.5 算逆位。
    """
    rng = rng or random.random
    idx = min(int(rng() * len(CARDS)), len(CARDS) - 1)
    rev = rng() < 0.5
    return CARDS[idx], rev


def _star_points(cx, cy, r, points=5, inner=0.42, rot_deg=-90):
    pts = []
    for i in range(points * 2):
        ang = math.radians(rot_deg + i * (180.0 / points))
        rr = r if i % 2 == 0 else r * inner
        pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
    return pts


def render_tarot(num: str, name: str, meaning: str, reversed_flag: bool,
                 question: str, out_path) -> bool:
    """塔罗小卡片（560×816）；任何环节失败返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _cardimg.load_pil()
        f_num = _cardimg.font_cute(46)
        f_name = _cardimg.font_cute(64)
        f_q = _cardimg.font_cjk(18)
        f_tag = _cardimg.font_cjk(22)
        f_text = _cardimg.font_cjk(24)
        f_small = _cardimg.font_cjk(15)
    except Exception as e:                                # noqa: BLE001
        logger.warning("塔罗：卡片字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        gold = (232, 196, 106)
        gold_dim = (150, 124, 66)
        light = (232, 228, 246)
        muted = (152, 144, 190)
        top, bottom = (28, 24, 52), (52, 38, 84)

        im = Image.new("RGB", (W, H), top)
        d = ImageDraw.Draw(im)
        for y in range(H):                               # 夜空渐变
            t = y / (H - 1)
            d.line([(0, y), (W, y)],
                   fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        # 星星（固定种子 → 每张卡一致，不闪）
        srng = random.Random(20261013)
        for _ in range(34):
            x = srng.randint(42, W - 42)
            y = srng.randint(40, 470)
            r = srng.choice((1, 1, 2))
            v = srng.randint(120, 225)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(v, v, min(255, v + 30)))
        for cx, cy, s in ((112, 176, 9), (448, 132, 7), (492, 420, 6)):
            d.line([cx - s, cy, cx + s, cy], fill=gold_dim, width=2)
            d.line([cx, cy - s, cx, cy + s], fill=gold_dim, width=2)
        # 双层金边
        d.rounded_rectangle([16, 16, W - 16, H - 16], radius=28, outline=gold, width=3)
        d.rounded_rectangle([28, 28, W - 28, H - 28], radius=24, outline=gold_dim, width=1)
        # 罗马数字
        d.text((W // 2, 92), num, font=f_num, fill=gold, anchor="mm")
        # 问题（可选，最多两行）
        q = (question or "").strip()
        if q:
            per = 26
            lines = [q[i:i + per] for i in range(0, min(len(q), per * 2), per)]
            y0 = 152 - (len(lines) - 1) * 13
            for i, ln in enumerate(lines):
                d.text((W // 2, y0 + i * 26), ("——" if i == 0 else "") + ln + ("——" if i == len(lines) - 1 else ""),
                       font=f_q, fill=muted, anchor="mm")
        # 中央：圆环 + 五角星（逆位倒转）
        cx, cy = W // 2, 330
        d.ellipse([cx - 100, cy - 100, cx + 100, cy + 100], outline=gold, width=2)
        d.ellipse([cx - 108, cy - 108, cx + 108, cy + 108], outline=gold_dim, width=1)
        d.polygon(_star_points(cx, cy, 74, rot_deg=(-90 + 180) if reversed_flag else -90), fill=gold)
        # 牌名
        d.text((W // 2, 522), name, font=f_name, fill=light, anchor="mm")
        # 正位 / 逆位 徽记
        tag = "逆位" if reversed_flag else "正位"
        tag_col = (206, 176, 232) if reversed_flag else gold   # 逆位用亮丁香紫：关键信息不能最弱（视觉评审一轮）
        d.rounded_rectangle([W // 2 - 92, 558, W // 2 + 92, 604], radius=22,
                            outline=tag_col, width=3)
        d.text((W // 2, 581), tag, font=f_tag, fill=tag_col, anchor="mm")
        # 牌义（最多三行，居中）
        per = 18
        lines = [meaning[i:i + per] for i in range(0, min(len(meaning), per * 3), per)]
        y0 = 660 - (len(lines) - 1) * 20
        for i, ln in enumerate(lines):
            d.text((W // 2, y0 + i * 40), ln, font=f_text, fill=light, anchor="mm")
        # 落款
        d.text((W // 2, 768), "可莉塔罗 · 仅供娱乐", font=f_small, fill=muted, anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                               # noqa: BLE001
        logger.warning("塔罗：卡片渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def register(api):
    settings = dict(getattr(api, "settings", None) or {})

    async def tarot_cmd(ctx):
        q = " ".join((ctx.arg_text or "").split())
        q = _cardimg.safe_name(q, 48)                    # 去 CQ 标记；太长截断（卡片支持两行）
        card, rev = draw()
        num, name, up_text, rev_text = card
        meaning = rev_text if rev else up_text
        tag = "逆位" if rev else "正位"
        if q:
            fallback = f"🔮 关于「{q}」，可莉抽到：{name}（{tag}）—— {meaning}"
        else:
            fallback = f"🔮 可莉塔罗：{name}（{tag}）—— {meaning}"
        # 卡片优先：目录建不出来 / 渲染失败 / 发不出去 → 回退文本
        try:
            out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "tarot", ctx.user_id)
        except OSError as e:
            logger.warning("塔罗：卡片目录不可用（%s）→ 回退文本", e)
            out = None
        if out is not None and await asyncio.to_thread(
                render_tarot, num, name, meaning, rev, q, out):
            caption = f"🔮 可莉塔罗 · {name}（{tag}）" + (f" —— 问：「{q}」" if q else "")
            if await _cardimg.send_card(ctx, out, caption, pattern="tarot-*.png", keep=KEEP_CARDS):
                return
        await ctx.reply(fallback)

    api.on_command("塔罗", tarot_cmd, aliases=("tarot", "抽塔罗", "占卜"), cooldown=12,
                   help=".塔罗 [问的事] —— 抽一张大阿卡纳（发小卡片）")
