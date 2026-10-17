"""可莉天气：``.天气 <城市>`` —— 走 wttr.in（免费、无需 key），结果出小卡片（失败回退文本）。

卡片 560×400：按天气类型配渐变色 + 简笔图标（纯形状绘制，不依赖 emoji 字体）；
任何环节失败（借包/渲染/发送）都回退成原来的纯文本播报。
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# _cardimg.py 与插件同目录（直接按文件加载时的兜底，见 gacha.py 同一段说明）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402

logger = logging.getLogger(__name__)

CONFIG_SCHEMA = {
    "card": {"type": "bool", "default": True, "description": "发天气卡片（关掉只发纯文本）"},
    "default_city": {"type": "str", "default": "蒙德", "description": "没写城市时用这个", "max_len": 40},
    "timeout": {"type": "int", "default": 10, "min": 3, "max": 60, "description": "取数超时（秒）"},
}

NAME = "可莉天气"
DESCRIPTION = "查天气：.天气 北京（wttr.in，出小卡片）"
VERSION = "1.2"

CHARACTERS = ('klee',)

API_TMPL = "https://wttr.in/{city}?format=j1&lang=zh"
TIMEOUT = 12
DEFAULT_CITY = "北京"

BASE = Path(__file__).resolve().parent / "state"
FONT_CUTE = _cardimg.FONT_CUTE
FONT_CJK = _cardimg.FONT_CJK
CJK_SC_INDEX = _cardimg.CJK_SC_INDEX
KEEP_CARDS = _cardimg.KEEP_CARDS

W, H = 560, 400
INK = (52, 62, 84)


def _opt_bool(v, default: bool = True) -> bool:
    """settings 里读开关：缺省/坏值走 default，只有明确「假」才关（config.json 手改容错）。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "关", "关闭", "不")
    return default


_DESC_CN = [
    (("thunder",), "雷阵雨"),
    # 「freezing」要排在 rain 组前面，否则 freezing rain/drizzle 会被 rain 提前吃掉（OCR 三轮 #15）
    (("freezing",), "冻雨"),
    (("heavy rain", "torrential"), "大雨"),
    (("light rain", "drizzle", "light drizzle", "patchy rain"), "小雨"),
    (("rain", "shower"), "有雨"),
    (("heavy snow", "blizzard"), "大雪"),
    (("snow", "sleet", "ice pellets"), "下雪"),
    (("ice",), "冻雨"),                          # 裸「ice」兜底（ice pellets 已被上一组接住）
    (("fog", "mist"), "有雾"),
    (("haze", "smoke"), "霾"),                   # wttr.in 常见 "Smoky haze"（干跑实测）
    (("dust", "sand"), "浮尘"),
    (("overcast",), "阴天"),
    (("cloud",), "多云"),
    (("clear", "sunny"), "晴"),
    (("wind", "gale"), "大风"),
]

# (渐变顶色, 渐变底色, 强调色)
_PALETTE = {
    "sun":     ((255, 212, 136), (255, 243, 208), (246, 158, 58)),
    "cloud":   ((176, 206, 236), (222, 236, 249), (118, 152, 190)),
    "rain":    ((132, 162, 204), (184, 204, 230), (72, 102, 152)),
    "snow":    ((198, 224, 244), (240, 248, 253), (122, 162, 202)),
    "thunder": ((150, 144, 200), (200, 196, 230), (100, 92, 152)),
    "fog":     ((176, 186, 198), (226, 231, 237), (130, 142, 158)),
    "wind":    ((150, 202, 190), (208, 236, 228), (92, 152, 140)),
}


def _first(node, *keys, default=""):
    for k in keys:
        v = (node or {}).get(k)
        if v not in (None, "", []):
            return v
    return default


def _cn_desc(text: str) -> str:
    """wttr.in 的 zh 翻译经常原样返回英文，这里自己兜一层。"""
    t = (text or "").strip()
    if not t:
        return ""
    if any("\u4e00" <= ch <= "\u9fff" for ch in t):
        return t
    low = t.lower()
    for keys, cn in _DESC_CN:
        if any(k in low for k in keys):
            return cn
    return t


def _tip(temp, text: str) -> str:
    if temp is None:
        return "出门前记得看一眼窗外！"
    if temp <= 5:
        return "冷死啦，围巾手套都带上！"
    if temp <= 15:
        return "有点凉，记得加件外套！"
    if temp >= 33:
        return "热得像锅里的鱼……多喝水呀！"
    if any(w in text for w in ("雨", "雷")):
        return "要下雨咯，可莉的伞可以借你！"
    if any(w in text for w in ("雪", "冰")):
        return "下雪啦，小心路滑哦！"
    if temp >= 26:
        return "可以吃冰啦，不过别被琴团长看到！"
    return "出门玩刚刚好！"


# ---------------- 取数与解析 ----------------

def parse_fields(data: dict, city: str) -> dict:
    """wttr.in 响应 → 卡片/文本共用的字段（文本路径与卡片路径同源，别再各拆各的）。"""
    cur = (data.get("current_condition") or [{}])[0]
    temp = _first(cur, "temp_C", default="?")
    desc = cur.get("lang_zh") or []
    if desc and isinstance(desc, list) and isinstance(desc[0], dict):
        text = str(_first(desc[0], "value", default=""))
    else:
        wd = cur.get("weatherDesc") or [{}]
        text = str(_first(wd[0] if wd else {}, "value", default=""))
    text = _cn_desc(text)
    humidity = _first(cur, "humidity", default="?")
    wind = _first(cur, "windspeedKmph", default="?")
    felt = _first(cur, "FeelsLikeC", default="")
    try:
        temp_num = int(float(temp))              # 负温（-5）也认：isdigit() 会漏负号（OCR 三轮 #12）
    except (TypeError, ValueError):
        temp_num = None
    tip = _tip(temp_num, text)
    return {"city": str(city), "temp": str(temp), "desc": text or "天气说不上来",
            "humidity": str(humidity), "wind": str(wind), "felt": str(felt), "tip": tip}


def parse_weather(data: dict, city: str) -> str:
    f = parse_fields(data, city)
    line = f"{city}现在 {f['temp']}°C，{f['desc']}哦～湿度 {f['humidity']}%、风速 {f['wind']}km/h"
    if f["felt"] and f["felt"] != f["temp"]:
        line += f"，体感 {f['felt']}°C"
    return f"{line}。{f['tip']}"


def fetch_weather(city: str, timeout: int = TIMEOUT) -> dict:
    url = API_TMPL.format(city=urllib.parse.quote(city))
    req = urllib.request.Request(url, headers={"User-Agent": "klee-onebot/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310
        raw = resp.read().decode("utf-8", "replace")
    if not raw.strip().startswith("{"):
        raise RuntimeError(f"wttr-bad-body:{raw[:60]}")
    data = json.loads(raw)
    if not data.get("current_condition"):
        raise RuntimeError("wttr-no-current")
    return data


# ---------------- 卡片 ----------------

def _icon_kind(desc: str) -> str:
    d = desc or ""
    if "雷" in d:
        return "thunder"
    if "雪" in d or "冰" in d:
        return "snow"
    if "雨" in d:
        return "rain"
    if "雾" in d or "霾" in d:
        return "fog"
    if "风" in d:
        return "wind"
    if "晴" in d:
        return "sun"
    return "cloud"


def _draw_icon(d, kind: str, cx: int, cy: int, accent) -> None:
    """简笔天气图标（纯形状，不依赖 emoji 字体）。"""
    if kind == "sun":
        r = 27
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
        for i in range(12):
            a = math.radians(i * 30)
            d.line([cx + math.cos(a) * (r + 8), cy + math.sin(a) * (r + 8),
                    cx + math.cos(a) * (r + 17), cy + math.sin(a) * (r + 17)],
                   fill=accent, width=4)
        return
    if kind == "wind":
        for i, w in enumerate((96, 72, 86)):
            y = cy - 12 + i * 18
            d.rounded_rectangle([cx - w // 2, y, cx + w // 2, y + 8], radius=4, fill=accent)
        return
    # 云体（cloud / rain / snow / thunder / fog 共用）
    d.ellipse([cx - 52, cy - 26, cx + 12, cy + 26], fill=accent)
    d.ellipse([cx - 20, cy - 40, cx + 40, cy + 24], fill=accent)
    d.ellipse([cx - 10, cy - 16, cx + 52, cy + 28], fill=accent)
    if kind == "rain":
        for dx in (-30, 0, 30):
            d.line([cx + dx, cy + 40, cx + dx - 9, cy + 66], fill=(40, 70, 120), width=8)
    elif kind == "snow":
        r = 10                                     # 六向小雪花（纯线条画，别用符号字形）
        for dx in (-32, 0, 32):
            sx, sy = cx + dx, cy + 52
            for ax, ay in ((0, -r), (int(r * 0.87), r // 2), (-int(r * 0.87), r // 2)):
                d.line([sx - ax, sy - ay, sx + ax, sy + ay], fill=accent, width=4)
    elif kind == "thunder":
        d.polygon([(cx + 2, cy + 34), (cx + 22, cy + 34), (cx + 6, cy + 60),
                   (cx + 16, cy + 60), (cx - 10, cy + 92), (cx + 2, cy + 62),
                   (cx - 8, cy + 62)], fill=(244, 190, 54))
    elif kind == "fog":
        for i, w in enumerate((88, 64, 84)):
            d.rounded_rectangle([cx - w // 2, cy + 44 + i * 14, cx + w // 2, cy + 52 + i * 14],
                                radius=4, fill=accent)
    # cloud：只有云体，不加东西


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


def render_weather_card(fields: dict, out_path) -> bool:
    """天气小卡片；任何环节失败返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _cardimg.load_pil()
        f_city = _cardimg.font_cute(40)
        f_temp = _cardimg.font_cute(88)
        f_unit = _cardimg.font_cjk(40)
        f_desc = _cardimg.font_cute(34)
        f_chip = _cardimg.font_cjk(20)
        f_tip = _cardimg.font_cjk(20)
        f_foot = _cardimg.font_cjk(16)
    except Exception as e:                           # noqa: BLE001
        logger.warning("天气：卡片字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        kind = _icon_kind(str(fields.get("desc") or ""))
        top, bottom, accent = _PALETTE.get(kind, _PALETTE["cloud"])
        im = Image.new("RGB", (W, H), top)
        d = ImageDraw.Draw(im)
        for y in range(H):                           # 垂直渐变
            t = y / (H - 1)
            d.line([(0, y), (W, y)],
                   fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        _draw_icon(d, kind, 450, 132, accent)        # 简笔图标（右侧，对齐温度中线）

        city = _cardimg.clean(str(fields.get("city") or ""), 12) or "天气"
        d.text((36, 56), city, font=f_city, fill=INK, anchor="lm")
        ts = _cardimg.clean(str(fields.get("temp") or "?"), 6) or "?"
        d.text((36, 134), ts, font=f_temp, fill=INK, anchor="lm")
        d.text((36 + d.textlength(ts, font=f_temp) + 8, 134), "°C", font=f_unit, fill=INK, anchor="lm")
        d.text((36, 212), _cardimg.clean(str(fields.get("desc") or "天气说不上来"), 14),
               font=f_desc, fill=INK, anchor="lm")

        chips = [("湿度", f"{fields.get('humidity') or '?'}%"),
                 ("风速", f"{fields.get('wind') or '?'}km/h")]
        felt = str(fields.get("felt") or "")
        if felt and felt != str(fields.get("temp") or ""):
            chips.append(("体感", f"{felt}°C"))
        n = len(chips)
        gap = 14
        cw = (W - 64 - (n - 1) * gap) // n
        for i, (label, val) in enumerate(chips):
            x0 = 32 + i * (cw + gap)
            d.rounded_rectangle([x0, 256, x0 + cw, 302], radius=16, fill=(255, 255, 255),
                                outline=accent, width=2)
            d.text((x0 + cw // 2, 280), f"{label} {val}", font=f_chip, fill=INK, anchor="mm")

        d.rounded_rectangle([32, 316, W - 32, 358], radius=16, fill=(255, 255, 255))
        tip = (_wrap(str(fields.get("tip") or ""), 26, 1) or [""])[0]
        d.text((W // 2, 337), tip, font=f_tip, fill=(94, 74, 60), anchor="mm")

        d.text((W // 2, 378), f"可莉天气播报 · {datetime.date.today():%Y-%m-%d}",
               font=f_foot, fill=(64, 74, 94), anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                           # noqa: BLE001
        logger.warning("天气：卡片渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


# ---------------- 命令 ----------------

def register(api):
    settings = dict(getattr(api, "settings", None) or {})
    default_city = str(settings.get("default_city") or DEFAULT_CITY)
    card_on = _opt_bool(settings.get("card"), True)          # settings.card=false → 只发纯文本

    async def weather_cmd(ctx):
        city = " ".join(ctx.args).strip() or default_city
        if len(city) > 24:
            await ctx.reply("城市名太长啦，可莉记不住（最多 24 个字）")
            return
        try:
            timeout = int(settings.get("timeout") or TIMEOUT)    # 配置坏值别伪装成「查天气失败」（OCR #14）
        except (TypeError, ValueError):
            timeout = TIMEOUT
        try:
            data = await asyncio.to_thread(fetch_weather, city, timeout)
        except Exception as e:                                   # noqa: BLE001
            logger.warning("OneBot weather: 查 %s 失败 — %s", city, e)
            await ctx.reply(f"诶……可莉没查到「{city}」的天气（{type(e).__name__}）。"
                            "换个城市名试试？比如 .天气 上海")
            return
        fields = parse_fields(data, city)
        fallback = "🌤️ " + parse_weather(data, city)
        # 卡片优先（settings.card=false 时整段跳过 → 只发纯文本）；
        # 目录建不出来 / 渲染失败 / 发不出去 → 回退文本
        out = None
        if card_on:
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "weather", ctx.user_id)
            except OSError as e:
                logger.warning("天气：卡片目录不可用（%s）→ 回退文本", e)
                out = None
        if out is not None and await asyncio.to_thread(render_weather_card, fields, out):
            caption = f"🌤️ {fields['city']}现在 {fields['temp']}°C，{fields['desc']}～"
            if await _cardimg.send_card(ctx, out, caption, pattern="weather-*.png", keep=KEEP_CARDS):
                return
        await ctx.reply(fallback)

    hint = "（默认 %s，发小卡片）" % default_city if card_on else "（默认 %s，纯文本）" % default_city
    api.on_command("天气", weather_cmd, aliases=("weather", "今日天气"), cooldown=10,
                   help=".天气 <城市> —— 查一下那边的天气" + hint)
