"""可莉抽卡：``.抽卡`` —— 十连开箱，看看今天的运气。

- 三档奖池：🌟5星（2%）、💎4星（15%）、🍀3星（其余）；十连保底 ≥1 个 4★+
- 结果渲染成**一张小卡片图**（PIL 从 pm-env 借包；失败自动回退纯文本）
- 纯娱乐、无账本；冷却 45s
"""

import asyncio
import logging
import random
import sys
from pathlib import Path

# _cardimg.py 与插件同目录：features 装载时会把这个目录加进 sys.path，
# 但直接按文件加载（testkit / 干跑）不会 —— 这里补一道兜底（append，别遮蔽 stdlib）。
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402

logger = logging.getLogger(__name__)

NAME = "抽卡"
DESCRIPTION = "可莉牌十连开箱：.抽卡（发一张小卡片图）"
VERSION = "1.1"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee', 'hutao', 'citlali')

FIVE = ["嘟嘟可（✨限定款）", "轰轰火花的火花", "蹦蹦炸弹·特制版",
        "艾莉丝的幸福手账", "四叶草徽章", "骑士团的嘉奖勋章"]
FOUR = ["烤鱼×10串", "鼓鼓的摩拉袋", "凯亚的冰棍(特大号)", "安柏的兔兔伯爵玩偶",
        "芭芭拉的润喉糖", "诺艾尔的下午茶券"]
THREE = ["甜甜花", "薄荷", "树莓", "蒲公英籽", "松果", "鸟蛋", "蘑菇"]

P_5 = 0.02
P_4 = 0.15

RARITY_ICON = {5: "🌟", 4: "💎", 3: "🍀"}

BASE = Path(__file__).resolve().parent / "state"

FONT_CUTE = _cardimg.FONT_CUTE
FONT_CJK = _cardimg.FONT_CJK
CJK_SC_INDEX = _cardimg.CJK_SC_INDEX
KEEP_CARDS = _cardimg.KEEP_CARDS


def _pick(pool, rng):
    """从池里取一个。rng 注入实现若返回 1.0，索引也要夹在界内（OCR 二轮 #1）。"""
    return pool[min(int(rng() * len(pool)), len(pool) - 1)]


def draw_ten(rng=None):
    """抽十连，返回 [(星级, 物品), ...]。rng 可注入（测试用），默认 random.random。"""
    rng = rng or random.random
    items = []
    for _ in range(10):
        r = rng()
        if r < P_5:
            star, pool = 5, FIVE
        elif r < P_5 + P_4:
            star, pool = 4, FOUR
        else:
            star, pool = 3, THREE
        items.append((star, _pick(pool, rng)))
    if all(s == 3 for s, _ in items):            # 十连保底：至少一个 4★+
        items[-1] = (4, _pick(FOUR, rng))
    return items


def format_result(name: str, items) -> str:
    icons = "".join(RARITY_ICON[s] for s, _ in items)
    names = [f"「{n}」" for _, n in items]
    lines = [f"🎁 {name}的可莉牌十连开箱！", icons,
             "".join(names[:5]), "".join(names[5:])]
    five = [n for s, n in items if s == 5]
    four = [n for s, n in items if s == 4]
    if five:
        lines.append(f"✨✨ 出金啦！！「{five[0]}」—— 运气爆棚，可莉都羡慕了！")
    elif four:
        lines.append(f"💎 最好的是「{four[0]}」，也很不错啦～")
    return "\n".join(lines)


# ---------- 卡片渲染（公共件在 _cardimg.py：PIL 借包 / 目录 / 命名 / 清洗 / 回收） ----------

def _cards_dir(settings: dict) -> Path:
    return _cardimg.cards_dir(settings, BASE)


def _load_pil():
    return _cardimg.load_pil()


def _clean(text: str, limit: int = 12) -> str:
    return _cardimg.clean(text, limit)


def _short(text: str, limit: int) -> str:
    return _cardimg.short(text, limit)


def render_card(name: str, items, out_path) -> bool:
    """把十连结果画成一张卡片图；任何环节失败都返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _load_pil()
        f_head = _cardimg.font_cute(44)
        f_sub = _cardimg.font_cjk(19)
        f_star = _cardimg.font_cjk(18)
        f_star_big = _cardimg.font_cjk(30)
        f_item = _cardimg.font_cjk(18)
        f_item_big = _cardimg.font_cjk(24)
        f_item3 = _cardimg.font_cjk(16)
        f_foot = _cardimg.font_cjk(20)
    except Exception:                                # noqa: BLE001
        return False
    try:
        cream, paper, ink = (255, 246, 232), (255, 253, 248), (74, 52, 42)
        red, gold = (232, 72, 79), (238, 168, 44)
        leaf_border, leaf_star = (200, 176, 146), (158, 124, 88)
        line = (242, 220, 196)
        # 稀有度语义色（原神惯例）：5★ 金 / 4★ 紫 / 3★ 灰棕
        box = {5: (255, 246, 216), 4: (247, 242, 255), 3: paper}
        cap = {5: (232, 160, 30), 4: (165, 125, 210), 3: (128, 102, 74)}   # 角标实底
        star_col = {5: (214, 140, 10), 4: (138, 96, 180), 3: (142, 116, 86)}
        border = {5: (232, 160, 30), 4: (165, 125, 210), 3: leaf_border}
        items = sorted(items, key=lambda t: -t[0])      # 出金置顶
        hero = items[0] if items and items[0][0] == 5 else None
        rest = items[1:] if hero else items
        cols = 3 if hero else 2
        cw, ch, gx = (161, 96, 10) if hero else (240, 106, 24)
        gy = 10
        x0 = 28
        y0 = 262 if hero else 124
        rows = 3 if hero else 5
        H = 712 if hero else 816
        im = Image.new("RGB", (560, H), cream)
        d = ImageDraw.Draw(im)
        # 顶栏
        d.rounded_rectangle([18, 16, 542, 108], radius=24, fill=paper, outline=line, width=3)
        d.text((280, 56), "可莉牌 · 十连开箱", font=f_head, fill=red, anchor="mm")
        d.text((280, 90), f"—— {_clean(name, 10)} 的十连 ——", font=f_sub,
               fill=(148, 116, 90), anchor="mm")
        # 出金英雄卡（跨两列）
        if hero:
            d.rounded_rectangle([22, 118, 538, 258], radius=28, fill=(255, 241, 205))   # 外发光
            d.rounded_rectangle([28, 124, 532, 252], radius=22, fill=(255, 247, 221),
                                outline=(232, 160, 30), width=4)
            d.rounded_rectangle([37, 133, 523, 243], radius=17, fill=(255, 250, 232),
                                outline=(250, 214, 120), width=2)
            d.text((280, 152), "★★★★★", font=f_star_big, fill=(214, 140, 10), anchor="mm")
            d.text((280, 194), f"「{_short(hero[1], 14)}」", font=f_item_big,
                   fill=(186, 116, 8), anchor="mm")
            d.text((280, 229), "—— 出 · 金 啦 ——", font=f_foot, fill=(224, 150, 24),
                   anchor="mm")
        # 其余格子
        for i, (star, item) in enumerate(rest):
            cx = x0 + (i % cols) * (cw + gx)
            cy = y0 + (i // cols) * (ch + gy)
            d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=16,
                                fill=box[star], outline=border[star], width=3)
            d.text((cx + 14, cy + 12), "★" * star, font=f_star, fill=star_col[star])
            d.rounded_rectangle([cx + cw - 54, cy + 9, cx + cw - 12, cy + 33],
                                radius=12, fill=cap[star])
            d.text((cx + cw - 33, cy + 21), f"{star}★", font=f_star,
                   fill=(255, 255, 255), anchor="mm")
            nfont = f_item3 if hero else f_item
            d.text((cx + 14, cy + 45), f"「{_short(item, 7 if hero else 12)}」",
                   font=nfont, fill=ink)
        grid_bottom = y0 + (rows - 1) * (ch + gy) + ch
        # 统计行
        five = [n for s, n in items if s == 5]
        four = [n for s, n in items if s == 4]
        d.text((280, grid_bottom + 22),
               f"本次十连：5★×{len(five)} · 4★×{len(four)} · 3★×{10 - len(five) - len(four)}",
               font=f_sub, fill=(148, 116, 90), anchor="mm")
        # 底部结论
        if five:
            foot, fc = "快截图去群里炫耀吧——嘟嘟可都看呆啦！", (186, 116, 8)
        elif four:
            foot, fc = f"最好的是「{_short(four[0], 14)}」，也很不错嘛～", (154, 109, 21)
        else:
            foot, fc = "……都是小东西，下次一定！", leaf_star
        pt = H - 82
        d.rounded_rectangle([18, pt, 542, pt + 62], radius=22,
                            fill=(255, 243, 214), outline=(240, 220, 174), width=2)
        d.text((280, pt + 31), foot, font=f_foot, fill=fc, anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception:                                # noqa: BLE001
        return False


def _cleanup_cards(d: Path) -> None:
    _cardimg.cleanup_cards(d, "tenpull-*.png", KEEP_CARDS)


def register(api):
    settings = dict(getattr(api, "settings", None) or {})

    async def draw_cmd(ctx):
        items = draw_ten()
        name = _clean(ctx.user_name or "你", 10)          # 昵称也洗一遍：出站会解析 [CQ:…]
        out = _cardimg.card_path(_cards_dir(settings), "tenpull", ctx.user_id)
        ok = await asyncio.to_thread(render_card, name, items, out)
        if ok and await _cardimg.send_card(ctx, out, f"🎁 {name}的可莉牌十连开箱！",
                                           pattern="tenpull-*.png", keep=KEEP_CARDS):
            return
        await ctx.reply(format_result(name, items))

    api.on_command("抽卡", draw_cmd, aliases=("十连", "开箱"), cooldown=45,
                   help=".抽卡 —— 可莉牌十连开箱（发一张卡片图）")
