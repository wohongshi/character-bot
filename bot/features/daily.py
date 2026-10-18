"""每日素材 —— `.每日素材 [明天]`：今天（或明天）开放的天赋书 / 武器突破秘境一览。

数据：`genshin_data/daily.json`（由 `~/.hermes/scripts/klee-genshin-daily-update.py`
从 biligame 原神 WIKI 抓取：每个秘境页有「所属地区 + 周一周四掉落 / 周二周五掉落 /
周三周六掉落」结构化字段）。轮换固定：一/四、二/五、三/六 三组，周日全开。

出图：560 宽自适应长卡（星空底 + 金框，和祈愿/帮助同族）；渲染失败回退文本。
性能：走 `_cardimg` 的字体/图标缓存 + `rcache_render`（同一天同内容秒回）。

用法：`.每日素材` / `.每日素材 明天`（别名 .今日素材 / .素材）
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
from pathlib import Path

import _cardimg

logger = logging.getLogger(__name__)

VERSION = "1.0"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee', 'hutao', 'citlali')
BASE = Path(__file__).resolve().parent
DATA_DIR = Path(__file__).resolve().parent / "genshin_data"
DAILY_CACHE_V = 1                   # 卡面版式版本：改设计就 +1（渲染缓存 key 的一部分）

W = 560
SKY_TOP, SKY_BOTTOM = (26, 28, 58), (48, 44, 96)
GOLD, GOLD_DIM = (232, 196, 106), (150, 124, 66)
LIGHT, MUTED = (232, 228, 246), (152, 144, 190)
CHIP_TXT = (250, 246, 235)

# 地区 → 卡片徽章色（Candy 主题同族；相邻地区色相拉开，「挪德卡莱」用靛蓝和蒙德的紫罗兰区分）
REGION_COLORS = {
    "蒙德": (168, 132, 200), "璃月": (200, 150, 96), "稻妻": (172, 108, 168),
    "须弥": (110, 168, 120), "枫丹": (96, 140, 196), "纳塔": (204, 116, 96),
    "至冬": (120, 158, 190), "挪德卡莱": (92, 104, 214),
}
FALLBACK_REGION_COLOR = (120, 130, 170)

WEEK_CN = ("一", "二", "三", "四", "五", "六", "日")

_db_cache: dict = {}


def load_db(path: Path | None = None):
    """读 daily.json（按 mtime 缓存换新）。坏文件回 None，调用方回退文案。"""
    target = Path(path) if path else DATA_DIR / "daily.json"
    try:
        mtime = target.stat().st_mtime_ns
    except OSError:
        return None
    hit = _db_cache.get("db")
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("每日素材：数据读取失败（%s）", e)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
        return None
    _db_cache["db"] = (mtime, data)
    return data


def _now() -> _dt.datetime:
    """当前时间（测试可猴补丁）。"""
    return _dt.datetime.now()


def day_key(d: _dt.date) -> str:
    """周几 → 组号："1"（一/四）/ "2"（二/五）/ "3"（三/六）/ "0"（周日全开）。"""
    wd = d.weekday()                # 0=周一 … 6=周日
    return {0: "1", 3: "1", 1: "2", 4: "2", 2: "3", 5: "3", 6: "0"}[wd]


def rows_for(d: _dt.date, db: dict) -> list[tuple[str, str, str, str]]:
    """→ [(地区, 天赋文案, 武器文案, 组号)]；组号 "0" 表示周日（全部开放）。"""
    key = day_key(d)
    out = []
    for ent in db.get("regions") or []:
        region = str(ent.get("region") or "?")
        cells = []
        for kind in ("talent", "weapon"):
            dom = ent.get(kind) or {}
            name = str(dom.get("domain") or "?")
            series = (dom.get("series") or {}).get(key)
            if key == "0":
                cells.append(f"{name}|全开")
            else:
                cells.append(f"{name}|{series or '?'}")
        out.append((region, cells[0], cells[1], key))
    return out


def fmt_daily(d: _dt.date, db: dict, when: str = "今天") -> str:
    """文本回退（卡片发不出去时用）。"""
    key = day_key(d)
    date_cn = f"{d.month}月{d.day}日 周{WEEK_CN[d.weekday()]}"
    lines = [f"🌸 {when}（{date_cn}）的秘境："]
    if key == "0":
        lines.append("今天是周日——所有秘境全部开放！")
    for region, t, w in [(r[0], r[1], r[2]) for r in rows_for(d, db)]:
        t_dom, t_s = t.split("|", 1)
        w_dom, w_s = w.split("|", 1)
        lines.append(f"· {region} 天赋「{t_dom}」→{t_s}　武器「{w_dom}」→{w_s}")
    if when == "今天":
        lines.append("想看明天就发：.每日素材 明天")
    return "\n".join(lines)


def _bg(h):
    """星空渐变底（与祈愿/帮助同族）→ (image, draw)。"""
    Image, ImageDraw, _ = _cardimg.load_pil()
    im = Image.new("RGB", (W, h))
    dr = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        dr.line([(0, y), (W, y)],
                fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    return im, dr


def render_daily(db: dict, d: _dt.date, out_path, when: str = "今天") -> bool:
    """画素材卡并落盘。任何异常 → False（调用方回退文本）。"""
    try:
        Image, ImageDraw, _ = _cardimg.load_pil()
        f_title = _cardimg.font_cute(40)
        f_sub = _cardimg.font_cjk(18)
        f_chip = _cardimg.font_cute(21)
        f_tag = _cardimg.font_cjk(16)
        f_dom = _cardimg.font_cute(21)
        f_ser = _cardimg.font_cute(22)
        f_foot = _cardimg.font_cjk(16)
        f_brand = _cardimg.font_cute(16)
    except Exception as e:                                # noqa: BLE001
        logger.warning("每日素材：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        rows = rows_for(d, db)
        key = day_key(d)
        date_cn = f"{d.month}月{d.day}日 周{WEEK_CN[d.weekday()]}"
        sunday = key == "0"

        H = 210 + (34 if sunday else 0) + len(rows) * 84 + 104
        canvas, dr = _bg(H)
        # 金框
        dr.rounded_rectangle([10, 10, W - 11, H - 11], radius=22, outline=GOLD_DIM, width=3)
        dr.rounded_rectangle([16, 16, W - 17, H - 17], radius=18, outline=(72, 66, 118), width=1)

        # 头部
        dr.text((34, 36), f"素材小抄", font=f_title, fill=GOLD)
        dr.text((36, 90), f"{when}（{date_cn}）", font=f_sub, fill=LIGHT)
        hint = "周日全开——想刷什么刷什么！" if sunday else "按地区排好了～"
        dr.text((36, 116), hint, font=f_tag, fill=MUTED)
        dr.line([(34, 152), (W - 34, 152)], fill=GOLD_DIM, width=2)

        y = 176
        if sunday:
            dr.rounded_rectangle([34, y, W - 34, y + 40], radius=12, fill=(66, 60, 108))
            dr.text((52, y + 8), "☀ 今天是周日——所有秘境全部开放", font=f_tag, fill=GOLD)
            y += 56

        for region, t, w in [(r[0], r[1], r[2]) for r in rows]:
            color = REGION_COLORS.get(region, FALLBACK_REGION_COLOR)
            # 地区徽章：字号全列统一 + 宽度给足（「挪德卡莱」4 字不缩字、不顶边）
            dr.rounded_rectangle([34, y + 6, 138, y + 66], radius=14, fill=color)
            tw = dr.textlength(region, font=f_chip)
            dr.text((34 + (104 - tw) / 2, y + 23), region, font=f_chip, fill=CHIP_TXT)
            # 天赋 / 武器两行
            for idx, (tag, cell, tag_color) in enumerate(
                    ((f"天赋", t, GOLD), (f"武器", w, (165, 205, 245)))):
                yy = y + 8 + idx * 36
                dr.text((146, yy + 3), tag, font=f_tag, fill=tag_color)
                dom, _, ser = cell.partition("|")
                dr.text((186, yy - 3), f"「{dom}」", font=f_dom, fill=LIGHT)
                dx = 186 + dr.textlength(f"「{dom}」", font=f_dom) + 6
                dr.text((dx, yy - 3), "→", font=f_ser, fill=MUTED)
                dx += dr.textlength("→", font=f_ser) + 6
                dr.text((dx, yy - 4), ser, font=f_ser, fill=(250, 214, 130) if ser != "全开" else GOLD)
            y += 84

        # 页脚
        fy = H - 84
        dr.line([(34, fy), (W - 34, fy)], fill=(72, 66, 118), width=1)
        dr.text((34, fy + 12), "轮换：一/四 · 二/五 · 三/六，周日全开", font=f_foot, fill=MUTED)
        dr.text((34, fy + 38), "数据：biligame 原神 WIKI", font=f_foot, fill=(120, 114, 158))
        dr.text((W - 130, fy + 38), "素材日历", font=f_brand, fill=GOLD_DIM)

        _cardimg.save_img(canvas, out_path)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warning("每日素材：渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def _opt_bool(v, default: bool = True) -> bool:
    """settings 里读开关：缺省/坏值走 default，只有明确「假」才关（与 wish/help 同口径）。"""
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

    async def daily_cmd(ctx):
        db = load_db()
        if not db:
            await ctx.reply("素材表暂时没找到呀……让主人检查一下 daily.json 好不好～")
            return
        arg = (ctx.arg_text or "").strip()
        tomorrow = arg.startswith(("明天", "明日", "明儿"))
        d = (_now() + _dt.timedelta(days=1 if tomorrow else 0)).date()
        when = "明天" if tomorrow else "今天"
        if card_on:
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "daily", ctx.user_id)
            except OSError as e:
                logger.warning("每日素材：卡片目录不可用（%s）→ 回退文本", e)
                out = None
            if out is not None:
                key = _cardimg.rcache_key("daily", DAILY_CACHE_V, db.get("updated"), d.isoformat(), when)
                if await asyncio.to_thread(_cardimg.rcache_render, settings, BASE, "daily", key, out,
                                           lambda: render_daily(db, d, out, when)):
                    caption = f"🌸 每日素材小抄（{d.month}月{d.day}日 周{WEEK_CN[d.weekday()]}·{when}）"
                    if await _cardimg.send_card(ctx, out, caption, pattern="daily-*.png"):
                        return
        await ctx.reply(fmt_daily(d, db, when))

    api.on_command("每日素材", daily_cmd, aliases=("今日素材", "素材", "今日秘境", "每日秘境"),
                   cooldown=8,
                   help=".每日素材 [明天] —— 今天/明天该刷哪个天赋书、武器突破秘境（她帮你查好）")
