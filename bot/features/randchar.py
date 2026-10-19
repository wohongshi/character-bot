"""随机角色 —— `.随机角色 [N]`：从全角色池随机抽 N 个（默认 1），带头像卡片。

用途：群里「今晚练谁 / 随机玩谁」的拍板工具。
数据：`genshin_data/chars.json`（134 角色：名字/元素/图标/稀有度；旅行者不入池）。
头像：复用练度（panel）的图标缓存与下载（`state/genshin_cache/`，能拉才贴、拉不到画占位圆）。
卡片：N=1 出 560×420 大卡（圆头像 + 名字 + 元素/星级 + 可莉评语）；N≥2 出 3 列网格卡。
纯随机、无状态；渲染/发送失败回退文本；`settings.card=false` 只发文本。

命令：`.随机角色 [N]`（别名 .今晚练谁 / .随机人物），N ∈ 1~12。
"""
from __future__ import annotations

import asyncio
import logging
import random
import sys
import threading
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402
import os

logger = logging.getLogger(__name__)

NAME = "随机角色"
DESCRIPTION = "从全角色池随机抽签：.随机角色 [N] —— 今晚练谁就用它拍板"
VERSION = "1.0"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee', 'hutao', 'citlali')

def _state_root() -> "Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台/冒烟靠它把状态写到临时目录，
    别让「跑一遍测试」改到真实战绩和抽卡记录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
MAX_N = 12

W = 560
SKY_TOP, SKY_BOTTOM = (26, 28, 58), (48, 44, 96)
GOLD, GOLD_DIM = (232, 196, 106), (150, 124, 66)
LIGHT, MUTED = (232, 228, 246), (152, 144, 190)
PANEL = (34, 34, 66)
CHIP_TXT = (250, 246, 235)

ELEMENT_CN = {"Fire": "火", "Water": "水", "Electric": "雷", "Grass": "草",
              "Wind": "风", "Rock": "岩", "Ice": "冰"}
ELEMENT_COLOR = {"Fire": (255, 130, 80), "Water": (86, 170, 255), "Electric": (185, 140, 245),
                 "Grass": (130, 210, 90), "Wind": (100, 215, 200), "Rock": (250, 200, 90),
                 "Ice": (140, 200, 240)}

_POOL = None
_POOL_LOCK = threading.Lock()


def char_pool() -> list[dict]:
    """全角色池 [{"name","element","icon","rarity"}]（旅行者除外；按名字去重）。"""
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            return _POOL
        pool, seen = [], set()
        try:
            _cardimg.ensure_sibling_path(__file__)           # 装载后目录会被移出 sys.path → 自愈
            import panel  # noqa: PLC0415 —— 与练度同目录，借角色表
            for ent in panel.chars_db().values():
                if not isinstance(ent, dict):
                    continue
                nm = ent.get("n")
                icon = ent.get("i") or ""
                if not isinstance(nm, str) or not nm or nm == "旅行者" or nm in seen:
                    continue
                if not icon.startswith("UI_AvatarIcon"):
                    continue
                q = str(ent.get("q") or "")
                rarity = 5 if "ORANGE" in q else (4 if "PURPLE" in q else 4)
                seen.add(nm)
                pool.append({"name": nm, "element": str(ent.get("e") or ""),
                             "icon": icon, "rarity": rarity})
        except Exception as e:                                # noqa: BLE001
            logger.warning("随机角色：角色表读取失败（%s: %s）", type(e).__name__, e)
        pool.sort(key=lambda c: c["name"])
        _POOL = pool
        return pool


def pick_chars(pool: list[dict], n: int, rng=None) -> list[dict]:
    """随机抽 n 个（不重复）；池不够就全给。n<=0 → 空。"""
    n = int(n)
    if n <= 0 or not pool:
        return []
    rng = rng or random
    n = min(n, len(pool))
    return rng.sample(pool, n)


def _icon_name(icon: str):
    """侧脸图标名 → 正脸缓存名；不是角色图标 → None。"""
    if not isinstance(icon, str):
        return None
    i = icon
    if i.startswith("UI_AvatarIcon_Side_"):
        i = "UI_AvatarIcon_" + i[len("UI_AvatarIcon_Side_"):]
    if not i.startswith("UI_AvatarIcon_") or "Side" in i:
        return None
    return i


def icon_for(char: dict, cache_root=None):
    """头像路径（缓存优先，缺了才下载）；任何失败 → None。"""
    try:
        _cardimg.ensure_sibling_path(__file__)               # 同上：运行期自愈
        import panel  # noqa: PLC0415
        nm = _icon_name(char.get("icon"))
        if nm is None:
            return None
        root = Path(cache_root) if cache_root else BASE / "genshin_cache"
        return panel.icon_path(nm, root)
    except Exception as e:                                    # noqa: BLE001
        logger.debug("随机角色：头像获取失败（%s: %s）", type(e).__name__, e)
        return None


def collect_icons(chars: list[dict], cache_root=None) -> list:
    """逐个取头像路径（失败 None），供渲染用。"""
    return [icon_for(c, cache_root) for c in chars]


# ---------- 渲染 ----------

def _bg(H):
    Image, ImageDraw, _ = _cardimg.load_pil()
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / max(1, H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    srng = random.Random(20261017)
    for _ in range(int(H * 0.04)):
        x = srng.randint(24, W - 24)
        y = srng.randint(20, H - 20)
        r = srng.choice((1, 1, 2))
        v = srng.randint(110, 210)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(v, v, min(255, v + 30)))
    return im, d


def _frame(d, H):
    d.rounded_rectangle([14, 14, W - 14, H - 14], radius=26, outline=GOLD, width=3)
    d.rounded_rectangle([25, 25, W - 25, H - 25], radius=22, outline=GOLD_DIM, width=1)


def _chip(d, fonts, cx, top, text, color):
    """居中小胶囊（元素/星级标签）。"""
    f = fonts
    tw = d.textlength(text, font=f) + 28
    x0 = int(cx - tw / 2)
    d.rounded_rectangle([x0, top, x0 + tw, top + 30], radius=15, fill=color)
    d.text((cx, top + 15), text, font=f, fill=CHIP_TXT, anchor="mm")


def render_single(char: dict, icon, out_path) -> bool:
    """N=1 大卡：圆头像 + 名字 + 元素/星级。任何异常 → False。"""
    try:
        _, _, _ = _cardimg.load_pil()
        f_head = _cardimg.font_cute(38)
        f_name = _cardimg.font_cute(46)
        f_chip = _cardimg.font_cjk(17)
        f_note = _cardimg.font_cjk(18)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("随机角色：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        H = 430
        im, d = _bg(H)
        _frame(d, H)
        d.text((W // 2, 54), "随机角色 · 大冒险", font=f_head, fill=GOLD, anchor="mm")
        el = ELEMENT_CN.get(char.get("element"), "?")
        ec = ELEMENT_COLOR.get(char.get("element"), (150, 154, 190))
        r = 88
        cx, cy = W // 2, 206
        if icon is not None:
            _cardimg.paste_icon(im, icon, cx, cy - r, r * 2, radius=r, log=logger)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD_DIM, width=3)
        d.text((cx, 322), f"「{_cardimg.short(char['name'], 10)}」", font=f_name,
               fill=(240, 226, 190), anchor="mm")
        _chip(d, f_chip, 226, 352, f"{el}元素", ec)
        _chip(d, f_chip, 352, 352, f"{char.get('rarity', 4)}★", (168, 132, 200))
        d.text((W // 2, 400), "掷骰子的手气交给可莉，练 TA 准没错！", font=f_note,
               fill=MUTED, anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                    # noqa: BLE001
        logger.warning("随机角色：单卡渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def render_grid(chars: list, icons: list, out_path) -> bool:
    """N≥2 网格卡：3 列，圆头像 + 名字 + 元素/星级。任何异常 → False。"""
    try:
        _, _, _ = _cardimg.load_pil()
        f_head = _cardimg.font_cute(36)
        f_name = _cardimg.font_cute(22)
        f_chip = _cardimg.font_cjk(14)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("随机角色：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        cols = 3
        tile_w, tile_h = 176, 196
        x0, gap_x = 30, 8
        rows = (len(chars) + cols - 1) // cols
        H = 130 + rows * (tile_h + 6) + 56
        im, d = _bg(H)
        _frame(d, H)
        d.text((W // 2, 52), f"随机角色 ×{len(chars)}", font=f_head, fill=GOLD, anchor="mm")
        d.text((W // 2, 92), "—— 可莉摇了一大把骰子 ——", font=f_chip, fill=MUTED, anchor="mm")
        for i, ch in enumerate(chars):
            cx = x0 + (i % cols) * (tile_w + gap_x) + tile_w // 2
            cy = 130 + (i // cols) * (tile_h + 6)
            d.rounded_rectangle([cx - tile_w // 2, cy, cx + tile_w // 2, cy + tile_h],
                                radius=20, fill=PANEL, outline=(70, 66, 110), width=2)
            r = 52
            icx, icy = cx, cy + 66
            icon = icons[i] if i < len(icons) else None
            if icon is not None:
                _cardimg.paste_icon(im, icon, icx, icy - r, r * 2, radius=r, log=logger)
            d.ellipse([icx - r, icy - r, icx + r, icy + r], outline=GOLD_DIM, width=2)
            d.text((cx, cy + 136), f"「{_cardimg.short(ch['name'], 7)}」", font=f_name,
                   fill=(240, 226, 190), anchor="mm")
            el = ELEMENT_CN.get(ch.get("element"), "?")
            ec = ELEMENT_COLOR.get(ch.get("element"), (150, 154, 190))
            _chip(d, f_chip, cx - 36, cy + 152, el, ec)
            _chip(d, f_chip, cx + 40, cy + 152, f"{ch.get('rarity', 4)}★", (168, 132, 200))
        d.text((W // 2, H - 44), "挑顺眼的那个，就是今天的命定角色啦～", font=f_chip,
               fill=(178, 170, 210), anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                    # noqa: BLE001
        logger.warning("随机角色：网格卡渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


# ---------- 文案 / 命令 ----------

def fmt_random(chars: list) -> str:
    if not chars:
        return "诶……一个角色都没抽到，好奇怪呀。"
    def one(c: dict) -> str:
        el = ELEMENT_CN.get(c.get("element"), "?")
        return f"「{c['name']}」（{el}·{c.get('rarity', 4)}★）"
    if len(chars) == 1:
        return f"🎲 可莉掷骰子啦——就决定是你了：{one(chars[0])}！\n今晚练 TA 准没错！"
    return ("🎲 可莉摇了一大把骰子，给你抽到这些：\n"
            + "\n".join(f"  · {one(c)}" for c in chars)
            + "\n挑顺眼的那个，就是今天的命定角色啦～")


def _opt_bool(v, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "关", "关闭", "不")
    return default


def register(api):
    settings = dict(getattr(api, "settings", None) or {})
    card_on = _opt_bool(settings.get("card"), True)

    def parse_n(arg: str):
        s = (arg or "").strip().lower().replace("x", "").replace("×", "").rstrip("个只位名")
        if not s:
            return 1, None
        if s in ("十", "十个"):
            return 10, None
        if s.isdecimal():
            n = int(s)
            if 1 <= n <= MAX_N:
                return n, None
            if n > MAX_N:
                return MAX_N, None
        return None, f"诶……最多一次抽 {MAX_N} 个哦。试试「.随机角色」「.随机角色 3」？"

    async def cmd(ctx):
        pool = char_pool()
        if not pool:
            await ctx.reply("角色表没找到呀……让主人看看 genshin_data/chars.json 好不好～")
            return
        n, err = parse_n(ctx.arg_text)
        if err:
            await ctx.reply(err)
            return
        chars = pick_chars(pool, n)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        fallback = f"{name}，{fmt_random(chars)}"
        if card_on:
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "rand", ctx.user_id)
            except OSError as e:
                logger.warning("随机角色：卡片目录不可用（%s）→ 回退文本", e)
                out = None
            if out is not None:
                icons = await asyncio.to_thread(collect_icons, chars)
                if len(chars) == 1:
                    ok = await asyncio.to_thread(render_single, chars[0], icons[0], out)
                    caption = f"🎲 {name} 的随机角色 —— 「{chars[0]['name']}」！"
                else:
                    ok = await asyncio.to_thread(render_grid, chars, icons, out)
                    caption = f"🎲 {name} 的随机角色 ×{len(chars)}！"
                if ok and await _cardimg.send_card(ctx, out, caption, pattern="rand-*.png"):
                    return
        await ctx.reply(fallback)

    api.on_command("随机角色", cmd, aliases=("今晚练谁", "随机人物"), cooldown=5,
                   help=".随机角色 [N] —— 从全角色池随机抽 N 个（默认 1，最多 12）")
