"""今日运势：``.运势`` / ``.jrrp`` —— 每人每天一签（QQ 号+日期做种子，稳定不随机）。

结果画成一张小卡片图（PIL 借包，失败自动回退纯文本）。
"""

import asyncio
import datetime
import hashlib
import logging
import sys
from pathlib import Path

# _cardimg.py 与插件同目录（兜底 sys.path，见 gacha.py 同一段说明）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402
import os

logger = logging.getLogger(__name__)

NAME = "今日运势"
DESCRIPTION = "今日运势：.运势 / .jrrp —— 一人一天一签（发小卡片）"
VERSION = "1.1"

CHARACTERS = ('klee',)

_LEVELS = [
    (95, "大吉", "今天连琴团长都会对你笑！适合去星落湖炸鱼（小声）"),
    (80, "吉", "诸事顺遂，能捡到三朵蒲公英那么好的运气"),
    (60, "小吉", "平平无奇的好日子，嘟嘟可都想跟你玩"),
    (40, "平", "适合和可莉一起发呆看云"),
    (20, "小凶", "蹦蹦炸弹可能变哑炮，出门记得带伞"),
    (0, "凶", "离禁闭室远一点……别问可莉为什么知道"),
]

def _state_root() -> "Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台/冒烟靠它把状态写到临时目录，
    别让「跑一遍测试」改到真实战绩和抽卡记录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
FONT_CUTE = _cardimg.FONT_CUTE
FONT_CJK = _cardimg.FONT_CJK
CJK_SC_INDEX = _cardimg.CJK_SC_INDEX
KEEP_CARDS = _cardimg.KEEP_CARDS

_TIER_COLOR = {"大吉": (232, 72, 79), "吉": (238, 168, 44), "小吉": (205, 158, 60),
               "平": (150, 128, 106), "小凶": (122, 134, 153), "凶": (90, 107, 128)}


def _cards_dir(settings: dict) -> Path:
    return _cardimg.cards_dir(settings, BASE)


def _load_pil():
    return _cardimg.load_pil()


def _short(text: str, limit: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


# 出站会解析 [CQ:at,qq=all] / [CQ:image,…]（见 adapter._parse_rich）；昵称与 user_id 都可能带这种标记
def _safe_name(raw: str, limit: int = 12) -> str:
    return _cardimg.safe_name(raw, limit)


def _safe_token(raw) -> str:
    return _cardimg.safe_token(raw)


def _stamp() -> str:
    return _cardimg.stamp()


def _wrap(text: str, per: int, max_lines: int):
    text = (text or "").strip()
    lines = []
    while text and len(lines) < max_lines:
        if len(text) <= per:
            lines.append(text)
            text = ""
            break
        cut = per
        # 避头尾：行首别落在右括号/句读上，行尾别留着左括号
        while cut > 1 and text[cut] in "）】》、。，！？…」":
            cut -= 1
        while cut > 1 and text[cut - 1] in "（【《「":
            cut -= 1
        lines.append(text[:cut])
        text = text[cut:]
    if text and lines:
        lines[-1] = lines[-1][: per - 1] + "…"
    return lines


def render_fortune(name: str, tag: str, n: int, tip: str, out_path) -> bool:
    """运势小卡片；任何环节失败返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _load_pil()
        f_head = _cardimg.font_cute(40)
        f_sub = _cardimg.font_cjk(18)
        f_tag = _cardimg.font_cute(64)
        f_num = _cardimg.font_cjk(20)
        f_tip = _cardimg.font_cjk(19)
        f_foot = _cardimg.font_cjk(16)
    except Exception:                                # noqa: BLE001
        return False
    try:
        W, H = 560, 470
        cream, paper, ink = (255, 246, 232), (255, 253, 248), (74, 52, 42)
        line = (242, 220, 196)
        col = _TIER_COLOR.get(tag, (150, 128, 106))
        im = Image.new("RGB", (W, H), cream)
        d = ImageDraw.Draw(im)
        # 顶栏
        d.rounded_rectangle([18, 16, 542, 96], radius=24, fill=paper, outline=line, width=3)
        d.text((280, 46), "可莉的今日运势", font=f_head, fill=(232, 72, 79), anchor="mm")
        d.text((280, 78), f"—— {_short(name, 12)} ——", font=f_sub,
               fill=(148, 116, 90), anchor="mm")
        # 大签
        d.rounded_rectangle([28, 112, 532, 262], radius=22, fill=paper, outline=col, width=4)
        d.text((280, 174), tag, font=f_tag, fill=col, anchor="mm")
        d.text((280, 226), f"人品 {n}/100", font=f_num, fill=(148, 116, 90), anchor="mm")
        # 人品进度条
        x0, x1, y0, y1 = 150, 410, 240, 254
        d.rounded_rectangle([x0, y0, x1, y1], radius=7, fill=(246, 236, 220))
        if n > 0:
            d.rounded_rectangle([x0, y0, x0 + int((x1 - x0) * n / 100), y1], radius=7, fill=col)
        # 批语
        for i, ln in enumerate(_wrap(tip, 21, 2)):
            d.text((280, 300 + i * 30), ln, font=f_tip, fill=ink, anchor="mm")
        # 落款
        d.text((280, H - 44), f"{datetime.date.today():%Y-%m-%d} · 可莉掐指一算",
               font=f_foot, fill=(186, 156, 126), anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception:                                # noqa: BLE001
        return False


def _cleanup(d: Path) -> None:
    _cardimg.cleanup_cards(d, "fortune-*.png", KEEP_CARDS)


def register(api):
    def live_settings() -> dict:
        """每次实时读配置（热更新即时生效），并容忍 settings 为 None。"""
        return dict(getattr(api, "settings", None) or {})

    async def fortune(ctx):
        day = datetime.date.today().isoformat()
        salt = str(live_settings().get("salt") or "klee")
        n = int(hashlib.sha256(f"{ctx.user_id}:{day}:{salt}".encode()).hexdigest()[:8], 16) % 101
        tag, tip = next((t, p) for th, t, p in _LEVELS if n >= th)
        name = _safe_name(ctx.user_name or "你", 12)
        fallback = f"🍀 {name} 的今日运势：{tag}（人品 {n}/100）\n—— {tip}"
        try:
            # 卡片目录建不出来（权限/磁盘）也必须退回文本 —— 不能把异常抛给调用方
            out = _cardimg.card_path(_cards_dir(live_settings()), "fortune", ctx.user_id)
        except OSError as e:
            logger.warning("运势：卡片目录不可用（%s）→ 回退文本", e)
            out = None
        if out is not None and await asyncio.to_thread(render_fortune, name, tag, n, tip, out):
            if await _cardimg.send_card(ctx, out, f"🍀 {name} 的今日运势——{tag}！",
                                        pattern="fortune-*.png", keep=KEEP_CARDS):
                return
        await ctx.reply(fallback)

    api.on_command("运势", fortune, aliases=("jrrp", "人品", "今日运势"),
                   help=".运势 —— 可莉帮你算今日人品（发小卡片）")
